# pylint: skip-file
import asyncio
from typing import Any, Callable, List, Tuple
import json
import concurrent.futures
import fastapi
import zenoh
from loguru import logger

PENDING_QUERYABLES: List[Tuple[str, Callable[..., Any]]] = []


class ZenohSession:
    session: zenoh.Session | None
    _executor: concurrent.futures.ThreadPoolExecutor | None = None
    _health_check_task: asyncio.Task[None] | None = None

    def __init__(self, service_name: str):
        config = self.zenoh_config(service_name)

        ZenohSession.session = zenoh.open(config)
        ZenohSession._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="zenoh-",
        )

        ZenohSession._health_check_task = asyncio.create_task(self.health_check())

    def close(self) -> None:
        if self.session:
            self.session.close()  # type: ignore[no-untyped-call]
            self.session = None
        if self._executor:
            self._executor.shutdown()
            self._executor = None
        if ZenohSession._health_check_task:
            ZenohSession._health_check_task.cancel()
            ZenohSession._health_check_task = None

    async def health_check(self) -> None:
        while True:
            await asyncio.sleep(60)
            if self.session:
                for pending_queryable, zenoh_wrapper in PENDING_QUERYABLES:
                    self.session.declare_queryable(pending_queryable, zenoh_wrapper)

    def zenoh_config(self, service_name: str) -> zenoh.Config:
        configuration = {
            "mode": "client",
            "connect/endpoints": ["tcp/127.0.0.1:7447"],
            "adminspace": {"enabled": True},
            "metadata": {"name": service_name},
        }

        config = zenoh.Config()
        for key, value in configuration.items():
            config.insert_json5(key, json.dumps(value))

        return config

    @classmethod
    def zenoh_queryable(cls) -> Callable[[Callable[..., Any]], Callable[[zenoh.Query], None]]:
        def decorator(func: Callable[..., Any]) -> Callable[[zenoh.Query], None]:
            route_path = getattr(func, "_route_path", None)
            if route_path and route_path[0] == "/":
                logger.info(f"Route path: {route_path}")
                route_path = route_path[1:]

            def wrapper(query: zenoh.Query) -> None:
                async def _handle_async() -> None:
                    try:
                        response = await func()
                        if response is not None:
                            query.reply(query.selector.key_expr, json.dumps(response, default=str))
                    except Exception as e:
                        error_response = {"error": str(e)}
                        query.reply(query.selector.key_expr, json.dumps(error_response))

                def run_async() -> None:
                    asyncio.run(_handle_async())

                if ZenohSession._executor:
                    ZenohSession._executor.submit(run_async)

            if route_path:
                PENDING_QUERYABLES.append((route_path, wrapper))
                if ZenohSession.session:
                    try:
                        ZenohSession.session.declare_queryable(route_path, wrapper)
                    except Exception as e:
                        logger.error(f"Error declaring queryable {route_path}: {e}")

            return wrapper

        return decorator

    @classmethod
    def register_pending_queryables(cls) -> None:
        if not ZenohSession.session:
            return

        for route_path, zenoh_wrapper in PENDING_QUERYABLES:
            try:
                ZenohSession.session.declare_queryable(route_path, zenoh_wrapper)
            except Exception as e:
                logger.error(f"Error declaring queryable {route_path}: {e}")


def route_info_decorator(deco: Callable[..., Any]) -> Callable[..., Any]:
    def wrapper(path: str, *args: Any, **kwargs: Any) -> Callable[..., Any]:
        def inner(func: Callable[..., Any]) -> Any:
            func._route_path = path  # type: ignore[attr-defined]
            return deco(path, *args, **kwargs)(func)

        return inner

    return wrapper


def apply_route_decorator(app: fastapi.FastAPI) -> fastapi.FastAPI:
    setattr(app, "get", route_info_decorator(app.get))
    setattr(app, "post", route_info_decorator(app.post))
    setattr(app, "put", route_info_decorator(app.put))
    setattr(app, "delete", route_info_decorator(app.delete))
    setattr(app, "patch", route_info_decorator(app.patch))
    return app
