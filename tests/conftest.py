# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import atexit
import os
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from novaport.config import Settings
from novaport.crypto import generate_key
from novaport.migrate import upgrade
from novaport.server.app import create_app
from tests.fakes import FakeReader

TEST_DATABASE_URL = os.environ.get("NOVAPORT_TEST_DATABASE_URL")

# Nothing listens on port 1, so connections fail immediately.
UNREACHABLE_DATABASE_URL = "postgresql+asyncpg://novaport:novaport@127.0.0.1:1/novaport"


def pytest_collection_modifyitems(items: list[pytest.Item]) -> None:
    if TEST_DATABASE_URL:
        return
    skip = pytest.mark.skip(reason="NOVAPORT_TEST_DATABASE_URL is not set")
    for item in items:
        if "postgres" in item.keywords:
            item.add_marker(skip)


TEST_SECRET_KEY = generate_key()
TEST_DATA_ROOT = Path(tempfile.mkdtemp(prefix="novaport-tests-"))
atexit.register(shutil.rmtree, TEST_DATA_ROOT, True)


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": SecretStr(TEST_DATABASE_URL or UNREACHABLE_DATABASE_URL),
        "migrate_on_start": False,
        "background_jobs": False,
        "data_dir": Path(tempfile.mkdtemp(dir=TEST_DATA_ROOT)),
        "allow_local_repositories": True,
        "secret_key": SecretStr(TEST_SECRET_KEY),
    }
    values.update(overrides)
    return Settings.model_validate(values)


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
                    " deployment_events CASCADE"
                )
            )
        yield TEST_DATABASE_URL
    finally:
        await engine.dispose()
