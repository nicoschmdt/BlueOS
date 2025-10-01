# pylint: skip-file
import asyncio
from typing import Any, Callable, List, Tuple
import json
import concurrent.futures
import fastapi
import zenoh
from loguru import logger

PENDING_QUERYABLES: List[Tuple[str, Callable[..., Any]]] = []


class ZenohRoute:
    def __init__(self, path: str, func: Callable[..., Any]):
        self.path = path
        self.func = func


class ZenohRouter:
    def __init__(self, prefix: str = ""):
        self.prefix = prefix
        self.routes: List[ZenohRoute] = []

    def queryable(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            route_path = getattr(func, "_route_path", "")
            if route_path and route_path[0] == "/":
                logger.info(f"Route path: {route_path}")  # ver depois pq o route path não tá sendo configurado
                route_path = route_path[1:]

            self.routes.append(ZenohRoute(route_path, func))
            return func

        return decorator

    def include_router(self, router: "ZenohRouter", prefix: str = "") -> None:
        combined_prefix = f"{self.prefix}{prefix}{router.prefix}"
        for route in router.routes:
            new_path = f"{combined_prefix}{route.path}"
            self.routes.append(ZenohRoute(new_path, route.func))


class ZenohSession:
    session: zenoh.Session | None
    _executor: concurrent.futures.ThreadPoolExecutor | None = None

    def __init__(self, service_name: str):
        self.routes: List[ZenohRoute] = []
        self.zenoh_config(service_name)

    def start(self) -> None:
        self.session = zenoh.open(self.config)

        for route in self.routes:
            logger.info(f"Declaring queryable: {route.path}")
            self.session.declare_queryable(route.path, self.zenoh_wrapper(route.func))

        self._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="zenoh-",
        )

    def include_router(self, router: ZenohRouter, prefix: str = "") -> None:
        for route in router.routes:
            final_path = f"{prefix}{router.prefix}{route.path}"
            self.routes.append(ZenohRoute(final_path, route.func))

    def close(self) -> None:
        if self.session:
            self.session.close()  # type: ignore[no-untyped-call]
            self.session = None
        if self._executor:
            self._executor.shutdown()
            self._executor = None

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

    def queryable(self) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
        def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
            route_path = getattr(func, "_route_path", None)
            if route_path and route_path[0] == "/":
                logger.info(f"Route path: {route_path}")
                route_path = route_path[1:]

            if route_path:
                self.routes.append(ZenohRoute(route_path, func))

            return func

        return decorator

    def zenoh_wrapper(self, func: Callable[..., Any]) -> Callable[[zenoh.Query], None]:
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

        return wrapper


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
