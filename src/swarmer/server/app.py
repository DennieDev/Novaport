# SPDX-License-Identifier: AGPL-3.0-or-later
import logging
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import aiodocker
import httpx
from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy.ext.asyncio import async_sessionmaker

from swarmer import __version__
from swarmer.agents.pool import AgentPool, Agents
from swarmer.auth.access import Forbidden, record
from swarmer.auth.oidc import OidcClient
from swarmer.auth.ratelimit import LoginThrottle
from swarmer.auth.users import ensure_initial_admin
from swarmer.config import Settings, get_settings
from swarmer.crypto import Cipher
from swarmer.db import create_engine
from swarmer.deploy.cli import DockerCli, SubprocessDockerCli
from swarmer.deploy.pipeline import Pipeline
from swarmer.docker.client import (
    AiodockerReader,
    ClusterReader,
    DockerUnavailable,
    NotFound,
    NotSwarmManager,
)
from swarmer.edge.hub import EdgeHub, Target, Targets
from swarmer.events import EventHub
from swarmer.events import install as install_events
from swarmer.git import Identity
from swarmer.jobs import Jobs
from swarmer.models import Outcome
from swarmer.monitor import Monitor, update_event
from swarmer.notifications import Notifier
from swarmer.registries.client import Login, RegistryClient
from swarmer.registries.store import all_logins, login_lookup
from swarmer.retention import Retention
from swarmer.server.api import access as access_api
from swarmer.server.api import adopt as adopt_api
from swarmer.server.api import agents as agents_api
from swarmer.server.api import (
    auth,
    cluster,
    editing,
    health,
    repositories,
    stacks,
    webhooks,
)
from swarmer.server.api import containers as containers_api
from swarmer.server.api import endpoints as endpoints_api
from swarmer.server.api import live as live_api
from swarmer.server.api import notifications as notifications_api
from swarmer.server.api import registries as registries_api
from swarmer.server.api import restart as restart_api
from swarmer.server.api import tokens as tokens_api
from swarmer.server.api import updates as updates_api
from swarmer.server.origin import SAFE_METHODS, SameOriginMiddleware
from swarmer.server.security import (
    SecurityHeadersMiddleware,
    content_security_policy,
    inline_script_hashes,
)
from swarmer.server.static import SPAStaticFiles
from swarmer.sops import Sops
from swarmer.stacks.editing import StackEditor
from swarmer.stacks.sync import Syncer, mirror_for
from swarmer.updates.checker import UpdateChecker, UpdateEvent

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

    @app.exception_handler(Forbidden)
    async def forbidden(request: Request, exc: Forbidden) -> JSONResponse:
        access = getattr(request.state, "access", None)
        if access is not None and request.method not in SAFE_METHODS:
            async with request.app.state.sessionmaker() as db:
                record(
                    db,
                    exc.action or "request",
                    user=access.user,
                    ip=access.ip,
                    outcome=Outcome.DENIED,
                    target=exc.target,
                    stack_id=exc.stack_id,
                    details=access.with_token({"method": request.method, "path": request.url.path}),
                )
                await db.commit()
        return _error(status.HTTP_403_FORBIDDEN, str(exc))

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
    http_transport: httpx.AsyncBaseTransport | None = None,
) -> FastAPI:
    settings = settings or get_settings()
    install_events()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        engine = create_engine(settings.database_url.get_secret_value())
        maker = async_sessionmaker(engine, expire_on_commit=False)
        app.state.engine = engine
        app.state.sessionmaker = maker
        app.state.events = EventHub(engine)
        app.state.events.start()

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

        http = httpx.AsyncClient(timeout=10, transport=http_transport)
        app.state.oidc = OidcClient(http)

        edge = EdgeHub(
            Path(tempfile.mkdtemp(prefix="swarmer-edge-")),
            settings.command_timeout.total_seconds(),
        )
        app.state.edge = edge

        cipher = Cipher(settings.secret_key.get_secret_value() if settings.secret_key else None)
        syncer = Syncer(settings, engine, maker, cipher)
        notifier = Notifier(settings, maker, cipher, http)
        app.state.notifier = notifier
        registry = RegistryClient(http, login_lookup(maker, cipher))
        app.state.registry = registry

        async def logins() -> dict[str, Login]:
            async with maker() as db:
                return await all_logins(db, cipher)

        local_cli = cli or SubprocessDockerCli(
            settings.docker_host, settings.command_timeout.total_seconds()
        )
        targets = Targets(
            lambda: Target("local", local_cli, app.state.docker, app.state.agents), edge
        )
        app.state.targets = targets
        pipeline = Pipeline(
            settings,
            maker,
            local_cli,
            reader=lambda: app.state.docker,
            mirror=lambda stack: mirror_for(settings, stack.repository),
            agents=lambda: app.state.agents,
            sops=lambda: app.state.sops,
            ensure_commit=lambda stack, commit: syncer.ensure_commit(stack.repository, commit),
            registry=lambda: registry,
            logins=logins,
            notify=notifier.emit,
            targets=targets,
        )
        sops = Sops(
            settings.sops_binary,
            settings.age_key.get_secret_value() if settings.age_key else None,
        )
        app.state.sops = sops
        app.state.editor = StackEditor(settings, engine, maker, syncer, cipher, sops, pipeline.cli)
        app.state.cipher = cipher
        app.state.syncer = syncer
        app.state.pipeline = pipeline
        app.state.webhook_tasks = set()
        app.state.updates = UpdateChecker(
            engine,
            maker,
            app.state.editor,
            registry,
            reader=lambda: app.state.docker,
            author=Identity(settings.commit_name, settings.commit_email),
            targets=targets,
        )

        async def announce_update(event: UpdateEvent) -> None:
            if (found := update_event(event)) is not None:
                notifier.emit(found)

        app.state.updates.hooks.append(announce_update)
        monitor = Monitor(
            engine,
            maker,
            notifier,
            reader=lambda: app.state.docker,
            agents=lambda: app.state.agents,
            targets=targets,
            edge=edge,
        )
        app.state.jobs = (
            Jobs(
                engine,
                maker,
                syncer,
                pipeline,
                app.state.updates,
                settings.update_interval,
                monitor=monitor,
                drift_interval=settings.drift_interval,
                retention=Retention(
                    settings.deployment_retention,
                    settings.keep_deployments,
                    settings.audit_retention,
                ),
            )
            if settings.background_jobs
            else None
        )
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
            await edge.close()
            await app.state.events.stop()
            await notifier.drain()
            await http.aclose()
            await engine.dispose()

    app = FastAPI(
        title="Swarmer",
        version=__version__,
        lifespan=lifespan,
        docs_url="/api/docs",
        redoc_url=None,
        openapi_url="/api/openapi.json",
    )
    app.state.settings = settings
    app.state.login_throttle = LoginThrottle()
    app.add_middleware(SameOriginMiddleware)
    hashes = inline_script_hashes(settings.static_dir / "index.html") if settings.static_dir else []
    app.add_middleware(SecurityHeadersMiddleware, policy=content_security_policy(hashes))
    _install_error_handlers(app)
    agents_api.install_error_handlers(app)

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(tokens_api.router)
    app.include_router(access_api.router)
    app.include_router(cluster.router)
    app.include_router(cluster.ws_router)
    app.include_router(repositories.router)
    app.include_router(stacks.router)
    app.include_router(webhooks.router)
    app.include_router(editing.router)
    app.include_router(registries_api.router)
    app.include_router(updates_api.router)
    app.include_router(notifications_api.router)
    app.include_router(restart_api.router)
    app.include_router(endpoints_api.router)
    app.include_router(endpoints_api.ws_router)
    app.include_router(agents_api.router)
    app.include_router(agents_api.ws_router)
    app.include_router(live_api.ws_router)
    app.include_router(adopt_api.router)
    app.include_router(containers_api.router)
    app.include_router(containers_api.ws_router)

    if settings.static_dir is not None:
        app.mount("/", SPAStaticFiles(directory=settings.static_dir, html=True), name="frontend")

    return app
