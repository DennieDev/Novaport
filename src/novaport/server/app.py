# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from sqlalchemy.ext.asyncio import async_sessionmaker

from novaport import __version__
from novaport.config import Settings, get_settings
from novaport.db import create_engine
from novaport.server.api import health
from novaport.server.static import SPAStaticFiles


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url.get_secret_value())
        app.state.engine = engine
        app.state.sessionmaker = async_sessionmaker(engine, expire_on_commit=False)
        try:
            yield
        finally:
            await engine.dispose()

    app = FastAPI(
        title="Novaport",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.include_router(health.router)

    if settings.static_dir is not None:
        app.mount("/", SPAStaticFiles(directory=settings.static_dir, html=True), name="frontend")

    return app
