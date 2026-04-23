import asyncio
import inspect
import json
import re
import threading
from concurrent.futures import Future
from typing import Any, AsyncGenerator, Callable, Coroutine, Optional

import fastapi
import zenoh
from fastapi.routing import APIRoute
from loguru import logger

from .Singleton import Singleton

PARAM_REGEX = r"{[a-zA-Z0-9_]+}"


class ZenohSession(metaclass=Singleton):
    session: zenoh.Session | None = None
    config: zenoh.Config
    _loop: asyncio.AbstractEventLoop | None = None
    _loop_thread: threading.Thread | None = None
    _loop_ready: threading.Event

    def __init__(self, service_name: str) -> None:
        if self.session is not None:
            return

        self.zenoh_config(service_name)
        self.session = zenoh.open(self.config)

        self._loop_ready = threading.Event()
        self._loop_thread = threading.Thread(
            target=self._run_loop,
            name="zenoh-loop",
            daemon=True,
        )
        self._loop_thread.start()
        self._loop_ready.wait()

    def _run_loop(self) -> None:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        self._loop = loop
        self._loop_ready.set()
        try:
            loop.run_forever()
        finally:
            try:
                # Cancel anything still pending so close() can return promptly.
                pending = asyncio.all_tasks(loop)
                for task in pending:
                    task.cancel()
                if pending:
                    loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            finally:
                loop.close()

    def submit_coroutine(self, coroutine: Coroutine[Any, Any, Any]) -> Optional["Future[Any]"]:
        """
        Schedule `coroutine` on the shared Zenoh event loop from any thread (including Zenoh's
        own callback threads). Returns a concurrent `Future` for callers that care about
        completion/errors; otherwise we log exceptions so fire-and-forget work isn't silent.
        """
        loop = self._loop
        if loop is None or not loop.is_running():
            logger.warning("Zenoh session loop is not available, task will not be scheduled.")
            coroutine.close()
            return None
        try:
            future = asyncio.run_coroutine_threadsafe(coroutine, loop)
        except RuntimeError as e:
            # Loop was closed between the is_running() check and submission (shutdown race).
            logger.warning(f"Could not schedule coroutine on Zenoh loop: {e}")
            coroutine.close()
            return None

        def _log_if_failed(fut: "Future[Any]") -> None:
            if fut.cancelled():
                return
            exc = fut.exception()
            if exc is not None:
                logger.opt(exception=exc).error("Unhandled error in Zenoh background task")

        future.add_done_callback(_log_if_failed)
        return future

    def close(self) -> None:
        if self.session:
            self.session.close()  # type: ignore[no-untyped-call]
            self.session = None
        loop = self._loop
        if loop is not None and loop.is_running():
            loop.call_soon_threadsafe(loop.stop)
        if self._loop_thread is not None:
            self._loop_thread.join(timeout=2.0)
            self._loop_thread = None
        self._loop = None

    def zenoh_config(self, service_name: str) -> None:
        configuration = {
            "mode": "client",
            "connect/endpoints": ["tcp/127.0.0.1:7447"],
            "adminspace": {"enabled": True},
            "metadata": {"name": service_name},
        }

        config = zenoh.Config()
        for key, value in configuration.items():
            config.insert_json5(key, json.dumps(value))

        self.config = config


class ZenohRouter:
    prefix: str
    zenoh_session: ZenohSession

    def __init__(self, service_name: str):
        self.prefix = service_name
        self.zenoh_session = ZenohSession(service_name)

    def add_queryable(self, path: str, func: Callable[..., Any], has_payload: bool = False) -> None:
        """
        Register a zenoh queryable at `prefix/path`. The handler receives the query's URL parameters
        as keyword arguments. When `has_payload` is True, the query's binary payload is also
        forwarded as the first positional argument (useful for file uploads).

        If `func` is an async generator, each yielded value is sent as an individual reply to the
        query, enabling streaming progress updates (zenoh natively supports multi-reply queries).
        Otherwise the handler's return value is sent as a single reply.
        """
        full_path = self.prefix
        if path:
            full_path += f"/{path}"

        def wrapper(query: zenoh.Query) -> None:
            params = dict(query.parameters)  # type: ignore
            key_expr = query.selector.key_expr

            async def _handle_async(q: zenoh.Query) -> None:
                with q:
                    try:
                        args = (bytes(q.payload) if q.payload else b"",) if has_payload else ()
                        result = func(*args, **params)
                        if inspect.isasyncgen(result):
                            async for item in result:
                                if item is not None:
                                    q.reply(key_expr, json.dumps(item, default=str))
                        else:
                            response = await result
                            if response is not None:
                                q.reply(key_expr, json.dumps(response, default=str))
                    except Exception as e:
                        logger.exception(f"Error in zenoh query handler: {key_expr}")
                        error_response = {
                            "error": str(e),
                            "error_type": type(e).__name__,
                        }
                        try:
                            q.reply(key_expr, json.dumps(error_response))
                        except Exception:
                            logger.exception(f"Failed to send error reply for {key_expr}")

            self.zenoh_session.submit_coroutine(_handle_async(query))

        if self.zenoh_session.session:
            self.zenoh_session.session.declare_queryable(full_path, wrapper)

    def publish_from_generator(
        self, topic: str, generator: AsyncGenerator[str, None], on_complete: Optional[str] = None
    ) -> None:
        async def _run() -> None:
            session = self.zenoh_session.session
            if session is None:
                async for _ in generator:
                    pass
                return

            with session.declare_publisher(topic) as publisher:
                async for chunk in generator:
                    publisher.put(chunk)
                if on_complete:
                    publisher.put(on_complete)

        self.zenoh_session.submit_coroutine(_run())

    def add_routes_to_zenoh(self, app: fastapi.FastAPI) -> None:
        queryables = []
        for route in app.router.routes:
            route_type = type(route)
            if (
                isinstance(route, APIRoute)
                and route_type.__name__ == "VersionedAPIRoute"
                and "fastapi_versioning" in route_type.__module__
                and "GET" in route.methods
            ):
                queryables.append((clean_path(route.path), route.endpoint))

        for path, func in queryables:
            self.add_queryable(path, func)


def clean_path(path: str) -> str:
    path = path.removeprefix("/").removesuffix("/")

    zenoh_path = re.sub(PARAM_REGEX, "*", path)
    zenoh_path = zenoh_path.replace("*/*", "**")

    return zenoh_path
