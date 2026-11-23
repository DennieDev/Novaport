# SPDX-License-Identifier: AGPL-3.0-or-later
from fastapi import FastAPI

from novaport import __version__
from novaport.config import Settings, get_settings
from novaport.server.api.health import Health


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    app = FastAPI(
        title="Novaport agent",
        version=__version__,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = settings

    @app.get("/healthz")
    async def healthz() -> Health:
        return Health(status="ok", version=__version__)

    return app
