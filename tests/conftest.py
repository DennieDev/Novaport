# SPDX-License-Identifier: AGPL-3.0-or-later
import os
from collections.abc import AsyncIterator

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr

from novaport.config import Settings
from novaport.server.app import create_app

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


def make_settings(**overrides: object) -> Settings:
    values: dict[str, object] = {
        "database_url": SecretStr(TEST_DATABASE_URL or UNREACHABLE_DATABASE_URL),
        "migrate_on_start": False,
    }
    values.update(overrides)
    return Settings.model_validate(values)


async def client_for(app: FastAPI) -> AsyncIterator[httpx.AsyncClient]:
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            yield client


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async for c in client_for(create_app(make_settings())):
        yield c
