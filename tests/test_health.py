# SPDX-License-Identifier: AGPL-3.0-or-later
import httpx
import pytest
from pydantic import SecretStr

from novaport import __version__
from novaport.server.app import create_app
from tests.conftest import UNREACHABLE_DATABASE_URL, client_for, make_settings


async def test_healthz(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/healthz")
    assert response.status_code == 200
    assert response.json() == {"status": "ok", "version": __version__}


async def test_readyz_without_database() -> None:
    settings = make_settings(database_url=SecretStr(UNREACHABLE_DATABASE_URL))
    async for client in client_for(create_app(settings)):
        response = await client.get("/api/readyz")
        assert response.status_code == 503
        assert response.json()["status"] == "database unavailable"


@pytest.mark.postgres
async def test_readyz_with_database(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/readyz")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


async def test_openapi_is_under_api(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/openapi.json")
    assert response.status_code == 200
    assert response.json()["info"]["title"] == "Novaport"
