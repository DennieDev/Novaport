# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import atexit
import os
import shutil
import tempfile
import uuid
from collections.abc import AsyncIterator
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from swarmer.auth.access import Access
from swarmer.config import Settings
from swarmer.crypto import generate_key
from swarmer.migrate import upgrade
from swarmer.models import Role, User
from swarmer.server.app import create_app
from swarmer.server.deps import current_access, current_user
from tests.fakes import FakeReader

TEST_DATABASE_URL = os.environ.get("SWARMER_TEST_DATABASE_URL")
SOPS_BINARY = os.environ.get("SWARMER_TEST_SOPS") or shutil.which("sops")
TEST_DOCKER_HOST = os.environ.get("SWARMER_TEST_DOCKER_HOST")

# Nothing listens on port 1, so connections fail immediately.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://swarmer:swarmer@127.0.0.1:1/swarmer"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    no_db = pytest.mark.skip(reason="SWARMER_TEST_DATABASE_URL is not set")
    no_sops = pytest.mark.skip(reason="sops is not installed (or set SWARMER_TEST_SOPS)")
    no_swarm = pytest.mark.skip(reason="SWARMER_TEST_DOCKER_HOST is not set")
    for item in items:
        if "postgres" in item.keywords and not TEST_DATABASE_URL:
            item.add_marker(no_db)
        if "sops" in item.keywords and not SOPS_BINARY:
            item.add_marker(no_sops)
        if "swarm" in item.keywords and not TEST_DOCKER_HOST:
            item.add_marker(no_swarm)


TEST_SECRET_KEY = generate_key()
TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="swarmer-tests-"))
atexit.register(shutil.rmtree, TEST_DATA_ROOT, True)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": SecretStr(TEST_DATABASE_URL or UNREACHABLE_DATABASE_URL),
        "migrate_on_start": False,
        "background_jobs": False,
        "data_dir": Path(tempfile.mkdtemp(dir=TEST_DATA_ROOT)),
        "allow_local_repositories": True,
        "secret_key": SecretStr(TEST_SECRET_KEY),
        "deploy_stable_for": timedelta(0),
    }
    values.update(overrides)
    return Settings.model_validate(values)


def as_admin(app: FastAPI) -> FastAPI:
    """Skip login and the database lookup of roles: every request is a global admin."""
    user = User(id=uuid.uuid4(), username="admin", disabled=False)
    access = Access(user=user, global_role=Role.ADMIN)
    app.dependency_overrides[current_user] = lambda: user
    app.dependency_overrides[current_access] = lambda: access
    return app


async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
def reader() -> FakeReader:
    return FakeReader()


@pytest.fixture
async def client(reader: FakeReader) -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(create_app(make_settings(), reader=reader)):
        yield c


@pytest.fixture
async def clean_db() -> AsyncIterator[str]:
    """A migrated test database with no users or sessions."""
    assert TEST_DATABASE_URL
    # Alembic's env.py runs its own event loop, so it can't run on this one.
    await asyncio.to_thread(upgrade, TEST_DATABASE_URL)
    engine = create_async_engine(TEST_DATABASE_URL)
    try:
        async with engine.begin() as conn:
            await conn.execute(
                text(
                    "TRUNCATE users, sessions, repositories, stacks, revisions, deployments,"
                    " deployment_events, groups, oidc_providers, audit_log, registries,"
                    " notification_channels, endpoints CASCADE"
                )
            )
        yield TEST_DATABASE_URL
    finally:
        await engine.dispose()
