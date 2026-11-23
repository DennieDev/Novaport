# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator

import httpx
import pytest

from novaport.docker.client import DockerUnavailable, NotSwarmManager
from novaport.server.app import create_app
from tests.conftest import as_admin, client_for, make_settings
from tests.fakes import FakeReader


@pytest.fixture
async def api(reader: FakeReader) -> AsyncIterator[httpx.AsyncClient]:
    async for client in client_for(as_admin(create_app(make_settings(), reader=reader))):
        yield client


async def test_requires_login(client: httpx.AsyncClient) -> None:
    response = await client.get("/api/services")
    assert response.status_code == 401


async def test_system(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/system")
    assert response.status_code == 200
    assert response.json()["swarm"]["state"] == "active"


async def test_nodes_managers_first(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/nodes")
    assert [n["hostname"] for n in response.json()] == ["mgr-1", "wrk-1"]


async def test_services_grouped_by_stack(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/services")
    assert [s["name"] for s in response.json()] == ["agent", "shop_web"]


async def test_service_detail_resolves_network_names(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/services/svc1")
    assert response.status_code == 200
    assert response.json()["networks"][0]["name"] == "shop_default"


async def test_unknown_service(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/services/nope")
    assert response.status_code == 404


async def test_service_tasks_only_exact_service(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/services/svc1/tasks")
    ids = [t["id"] for t in response.json()]
    assert ids == ["task-new", "task-old"]


async def test_service_logs(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/services/svc1/logs", params={"tail": 50})
    assert response.json() == {"lines": ["line one", "line two", "line three"]}


async def test_logs_tail_is_bounded(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/services/svc1/logs", params={"tail": 0})
    assert response.status_code == 422


async def test_secrets_are_metadata_only(api: httpx.AsyncClient) -> None:
    response = await api.get("/api/secrets")
    assert set(response.json()[0]) == {"id", "name", "stack", "labels", "created_at", "updated_at"}


async def test_not_a_manager(api: httpx.AsyncClient, reader: FakeReader) -> None:
    reader.error = NotSwarmManager("This node is not a swarm manager.")
    response = await api.get("/api/nodes")
    assert response.status_code == 409
    assert "not a swarm manager" in response.json()["detail"]


async def test_docker_down(api: httpx.AsyncClient, reader: FakeReader) -> None:
    reader.error = DockerUnavailable("connection refused")
    response = await api.get("/api/services")
    assert response.status_code == 503


async def test_no_docker_configured() -> None:
    app = as_admin(create_app(make_settings()))
    async for client in client_for(app):
        app.state.docker = None
        response = await client.get("/api/services")
        assert response.status_code == 503
