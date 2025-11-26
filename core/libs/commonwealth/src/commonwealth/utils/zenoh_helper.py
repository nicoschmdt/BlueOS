# pylint: skip-file
import asyncio
from typing import Any, Callable, Tuple
import json
import concurrent.futures
import fastapi
import zenoh
from loguru import logger


class ZenohSession:
    session: zenoh.Session | None
    _executor: concurrent.futures.ThreadPoolExecutor | None = None

    def __init__(self):
        ZenohSession.zenoh_config()
        ZenohSession.session = zenoh.open(ZenohSession.config)

        ZenohSession._executor = concurrent.futures.ThreadPoolExecutor(
            max_workers=4,
            thread_name_prefix="zenoh-",
        )

    def close(self) -> None:
        if ZenohSession.session:
            ZenohSession.session.close()  # type: ignore[no-untyped-call]
            ZenohSession.session = None
        if ZenohSession._executor:
            ZenohSession._executor.shutdown()
            ZenohSession._executor = None

    def zenoh_config() -> None:
        configuration = {
            "mode": "client",
            "connect/endpoints": ["tcp/127.0.0.1:7447"],
            "adminspace": {"enabled": True},
            "metadata": {"name": "zenoh-queryables"},
        }

        config = zenoh.Config()
        for key, value in configuration.items():
            config.insert_json5(key, json.dumps(value))

        ZenohSession.config = config


zenoh_session = ZenohSession()


class ZenohRouter:
    prefix: str
    routes: Tuple[str, Callable[..., Any]] = []

    def __init__(self, prefix: str):
        self.prefix = prefix

    def queryable(self) -> Callable[[Callable[..., Any]], Callable[[zenoh.Query], None]]:
        def decorator(func: Callable[..., Any]) -> Callable[[zenoh.Query], None]:
            route_path = getattr(func, "_route_path", None)
            if route_path and route_path[0] == "/":
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

                if zenoh_session._executor:
                    zenoh_session._executor.submit(run_async)

            self.routes.append((route_path, wrapper))
            return wrapper

        return decorator

    def declare(self) -> None:
        for path, func in self.routes:
            full_path = f"{self.prefix}/{path}"
            logger.error(f"Declaring queryable: {full_path}")
            zenoh_session.session.declare_queryable(full_path, func)


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
