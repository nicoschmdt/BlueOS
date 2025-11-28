# pylint: skip-file
import asyncio
from typing import Any, Callable, List, Tuple
import json
import concurrent.futures
import fastapi
import zenoh
from loguru import logger


class ZenohSession:
    session: zenoh.Session | None
    config: zenoh.Config
    _executor: concurrent.futures.ThreadPoolExecutor | None = None

    def __init__(self) -> None:
        self.zenoh_config()
        self.session = zenoh.open(self.config)

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="zenoh-",
        )

    def close(self) -> None:
        if self.session:
            self.session.close()  # type: ignore[no-untyped-call]
            self.session = None
        if self._executor:
            self._executor.shutdown()
            self._executor = None

    def zenoh_config(self) -> None:
        configuration = {
            "mode": "client",
            "connect/endpoints": ["tcp/127.0.0.1:7447"],
            "adminspace": {"enabled": True},
            "metadata": {"name": "zenoh-queryables"},
        }

        config = zenoh.Config()
        for key, value in configuration.items():
            config.insert_json5(key, json.dumps(value))

        self.config = config


zenoh_session = ZenohSession()


class ZenohRouter:
    prefix: str
    routes: List[Tuple[str, Callable[..., Any]]]

    def __init__(self, prefix: str):
        self.prefix = prefix
        self.routes = []

    def queryable(self) -> Callable[[Callable[..., Any]], Callable[[zenoh.Query], None]]:
        def decorator(func: Callable[..., Any]) -> Callable[[zenoh.Query], None]:
            route_path = getattr(func, "_route_path", None)
            if route_path and route_path[0] == "/":
                route_path = route_path[1:]

            if route_path and route_path[-1] == "/":
                route_path = route_path[:-1]

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

                if zenoh_session._executor:
                    zenoh_session._executor.submit(run_async)

            if route_path is not None:
                self.routes.append((route_path, wrapper))  # type: ignore[arg-type]
            return wrapper

        return decorator

    def declare(self) -> None:
        if zenoh_session.session is None:
            logger.error(
                "Zenoh session is not initialized, skipping declaration"
            )  # temporary fix, check what should be done here
            return

        for path, func in self.routes:
            full_path = self.prefix
            if path:
                full_path += f"/{path}"

            logger.error(f"Declaring queryable: {full_path}")
            zenoh_session.session.declare_queryable(full_path, func)

    def include_router(self, router: "ZenohRouter") -> None:
        logger.error(f"prefix: {router.prefix}")
        to_be_added = set()
        logger.error(len(router.routes))
        for path, func in router.routes:

            full_path = router.prefix
            if path:
                full_path += f"/{path}"
            logger.error(f"path: {full_path}")

            to_be_added.add((full_path, func))
        self.routes = list(set(self.routes) | to_be_added)


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


def apply_route_decorator_router(app: fastapi.APIRouter) -> fastapi.APIRouter:
    setattr(app, "get", route_info_decorator(app.get))
    setattr(app, "post", route_info_decorator(app.post))
    setattr(app, "put", route_info_decorator(app.put))
    setattr(app, "delete", route_info_decorator(app.delete))
    setattr(app, "patch", route_info_decorator(app.patch))
    return app
