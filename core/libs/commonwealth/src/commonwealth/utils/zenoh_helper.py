# pylint: skip-file
import asyncio
from typing import Any, Callable
import json
import concurrent.futures
import fastapi
import zenoh
from loguru import logger


class ZenohSession:
    session: zenoh.Session | None
    _executor: concurrent.futures.ThreadPoolExecutor | None = None

    def __init__(self, configuration: dict[str, Any]):
        config = zenoh.Config()
        for key, value in configuration.items():
            config.insert_json5(key, json.dumps(value))

        ZenohSession.session = zenoh.open(config)
        ZenohSession._executor = concurrent.futures.ThreadPoolExecutor(
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

    @classmethod
    def zenoh_queryable(cls) -> Callable[[Callable[..., Any]], Callable[[zenoh.Query], None]]:
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

                if ZenohSession._executor:
                    ZenohSession._executor.submit(run_async)

            if route_path and ZenohSession.session:
                try:
                    ZenohSession.session.declare_queryable(route_path, wrapper)
                except Exception as e:
                    logger.error(f"Error declaring queryable {route_path}: {e}")

            return wrapper

        return decorator


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
