# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator
from pathlib import Path

import httpx
import pytest

from swarmer.server.app import create_app
from tests.conftest import client_for, make_settings


@pytest.fixture
async def spa_client(tmp_path: Path) -> AsyncIterator[httpx.AsyncClient]:
    (tmp_path / "index.html").write_text("<html>swarmer</html>")
    (tmp_path / "app.js").write_text("console.log(1)")
    async for client in client_for(create_app(make_settings(static_dir=tmp_path))):
        yield client


async def test_serves_files(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.get("/app.js")
    assert response.status_code == 200
    assert response.text == "console.log(1)"


async def test_client_routes_fall_back_to_index(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.get("/stacks/traefik")
    assert response.status_code == 200
    assert "swarmer" in response.text


async def test_unknown_api_routes_stay_404(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.get("/api/does-not-exist")
    assert response.status_code == 404


async def test_api_still_routed(spa_client: httpx.AsyncClient) -> None:
    response = await spa_client.get("/api/healthz")
    assert response.status_code == 200
