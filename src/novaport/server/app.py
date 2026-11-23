# SPDX-License-Identifier: AGPL-3.0-or-later
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import aiodocker
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from novaport import __version__
from novaport.agents.pool import AgentPool, Agents
from novaport.auth.ratelimit import LoginThrottle
from novaport.auth.users import ensure_initial_admin
from novaport.config import Settings, get_settings
from novaport.crypto import Cipher
from novaport.db import create_engine
from novaport.deploy.cli import DockerCli, SubprocessDockerCli
from novaport.deploy.pipeline import Pipeline
from novaport.docker.client import (
    AiodockerReader,
    ClusterReader,
    DockerUnavailable,
    NotFound,
    NotSwarmManager,
)
from novaport.jobs import Jobs
from novaport.server.api import agents as agents_api
from novaport.server.api import auth, cluster, health, repositories, stacks, webhooks
from novaport.server.origin import SameOriginMiddleware
from novaport.server.static import SPAStaticFiles
from novaport.stacks.sync import Syncer, mirror_for

log = logging.getLogger(__name__)


def _error(status_code: int, detail: str) -> JSONResponse:
    return JSONResponse({"detail": detail}, status_code=status_code)


def _install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound) -> JSONResponse:
        return _error(status.HTTP_404_NOT_FOUND, str(exc) or "Not found.")

    @app.exception_handler(NotSwarmManager)
    async def not_manager(_: Request, exc: NotSwarmManager) -> JSONResponse:
        return _error(status.HTTP_409_CONFLICT, str(exc))

    @app.exception_handler(DockerUnavailable)
    async def unavailable(_: Request, exc: DockerUnavailable) -> JSONResponse:
        log.warning("Docker unavailable: %s", exc)
        return _error(status.HTTP_503_SERVICE_UNAVAILABLE, "Docker is unavailable.")

    @app.exception_handler(aiodocker.DockerError)
    async def docker_error(_: Request, exc: aiodocker.DockerError) -> JSONResponse:
        return _error(status.HTTP_502_BAD_GATEWAY, exc.message)


def _connect_docker(settings: Settings) -> aiodocker.Docker | None:
    try:
        return aiodocker.Docker(url=settings.docker_host)
    except ValueError as exc:
        log.warning("No Docker connection: %s", exc)
        return None


def create_app(
    settings: Settings | None = None,
    reader: ClusterReader | None = None,
    cli: DockerCli | None = None,
    agents: Agents | None = None,
) -> FastAPI:
    settings = settings or get_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url.get_secret_value())
        maker = async_sessionmaker(engine, expire_on_commit=False)
        app.state.engine = engine
        app.state.sessionmaker = maker

        try:
            async with maker() as db:
                password = settings.initial_admin_password
                await ensure_initial_admin(
                    db,
                    settings.initial_admin_username,
                    password.get_secret_value() if password else None,
                )
        except (SQLAlchemyError, OSError) as exc:
            log.warning("Database unavailable at startup: %s", exc)

        docker = None if reader is not None else _connect_docker(settings)
        app.state.docker = reader or (AiodockerReader(docker) if docker else None)

        pool = AgentPool(settings) if agents is None else None
        app.state.agents = agents or pool

        cipher = Cipher(settings.secret_key.get_secret_value() if settings.secret_key else None)
        syncer = Syncer(settings, engine, maker, cipher)
        pipeline = Pipeline(
            settings,
            maker,
            cli
            or SubprocessDockerCli(settings.docker_host, settings.command_timeout.total_seconds()),
            reader=lambda: app.state.docker,
            mirror=lambda stack: mirror_for(settings, stack.repository),
            agents=lambda: app.state.agents,
        )
        app.state.cipher = cipher
        app.state.syncer = syncer
        app.state.pipeline = pipeline
        app.state.webhook_tasks = set()
        app.state.jobs = Jobs(engine, maker, syncer, pipeline) if settings.background_jobs else None
        if app.state.jobs is not None:
            app.state.jobs.start()
        try:
            yield
        finally:
            if app.state.jobs is not None:
                await app.state.jobs.stop()
            if pool is not None:
                await pool.close()
            if docker is not None:
                await docker.close()
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
    app.state.login_throttle = LoginThrottle()
    app.add_middleware(SameOriginMiddleware)
    _install_error_handlers(app)
    agents_api.install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(cluster.router)
    app.include_router(cluster.ws_router)
    app.include_router(repositories.router)
    app.include_router(stacks.router)
    app.include_router(webhooks.router)
    app.include_router(agents_api.router)
    app.include_router(agents_api.ws_router)

    if settings.static_dir is not None:
        app.mount("/", SPAStaticFiles(directory=settings.static_dir, html=True), name="frontend")

    return app
