from contextlib import asynccontextmanager
from os import path
from typing import AsyncGenerator

# Routers
from api.v1.routers import (
    bootstrap_router_v1,
    docker_router_v1,
    index_router_v1,
    version_router_v1,
)
from commonwealth.utils.apis import GenericErrorHandlingRoute, PrettyJSONResponse
from commonwealth.utils.zenoh_helper import ZenohSession, apply_route_decorator
from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from fastapi_versioning import VersionedFastAPI

zenoh_config = ZenohSession(
    configuration={
        "mode": "client",
        "connect/endpoints": ["tcp/127.0.0.1:7447"],
        "adminspace": {"enabled": True},
        "metadata": {"name": "version-chooser"},
    }
)


@asynccontextmanager
async def lifespan(fastapi_app: FastAPI) -> AsyncGenerator[None, None]:  # pylint: disable=unused-argument
    yield
    zenoh_config.close()


original_application = FastAPI(
    title="Version Chooser API",
    description="Version Chooser is the BlueOS service responsible for managing BlueOS versions",
    default_response_class=PrettyJSONResponse,
    lifespan=lifespan,
)
application = apply_route_decorator(original_application)
application.router.route_class = GenericErrorHandlingRoute

# API v1
application.include_router(index_router_v1)
application.include_router(docker_router_v1)
application.include_router(version_router_v1)
application.include_router(bootstrap_router_v1)


application = VersionedFastAPI(application, prefix_format="/v{major}.{minor}", enable_latest=True)


@application.get("/", status_code=200)
async def root() -> HTMLResponse:
    html_content = """
    <html>
        <head>
            <title>Version Chooser</title>
        </head>
    </html>
    """
    return HTMLResponse(content=html_content)


# Mount static files
application.mount(
    "/static",
    StaticFiles(directory=path.join(path.dirname(__file__), "static")),
    name="static",
)
