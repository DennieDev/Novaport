# SPDX-License-Identifier: AGPL-3.0-or-later
import copy
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from swarmer.auth.passwords import hash_password
from swarmer.models import Repository, Role, RoleBinding, Stack, StackType, User
from swarmer.server.api.containers import ContainerRow, ServiceRow, stack_health
from swarmer.server.app import create_app
from tests.conftest import client_for, make_settings
from tests.fakes import TASKS, FakeAgents, FakeReader
from tests.test_pipeline import FakeCli

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"
BLOG = {"com.docker.compose.project": "blog", "com.docker.compose.service": "web"}


def blog_container(cid: str, state: str, status: str) -> dict[str, Any]:
    return {
        "Id": cid,
        "Names": [f"/blog-web-{cid[-1]}"],
        "Image": "nginx:1.27",
        "State": state,
        "Status": status,
        "Created": 1_790_000_000,
        "Labels": BLOG,
        "Ports": [{"PrivatePort": 80, "PublicPort": 8080, "Type": "tcp"}],
    }


def blog_inspect(cid: str) -> dict[str, Any]:
    return {
        "Id": cid,
        "Name": f"/blog-web-{cid[-1]}",
        "Created": "2026-11-24T10:00:00.123456789Z",
        "State": {
            "Status": "running",
            "StartedAt": "2026-11-24T10:00:01.5Z",
            "FinishedAt": "0001-01-01T00:00:00Z",
            "ExitCode": 0,
            "Health": {"Status": "healthy", "Log": [{"Output": "ok\n"}]},
        },
        "RestartCount": 2,
        "Config": {"Image": "nginx:1.27", "Cmd": ["nginx"], "Env": ["A=1"], "Labels": BLOG},
        "Mounts": [{"Type": "volume", "Name": "blog_html", "Destination": "/html", "RW": True}],
        "NetworkSettings": {
            "Networks": {"blog_default": {"IPAddress": "172.20.0.2"}},
            "Ports": {"80/tcp": [{"HostIp": "0.0.0.0", "HostPort": "8080"}]},
        },
    }


@dataclass
class World:
    client: httpx.AsyncClient
    cli: FakeCli
    agents: FakeAgents
    shop: uuid.UUID
    blog: uuid.UUID
    url: str


@pytest.fixture
async def world(clean_db: str) -> AsyncIterator[World]:
    engine = create_async_engine(clean_db)
    async with async_sessionmaker(engine, expire_on_commit=False)() as db:
        repo = Repository(name="infra", url="file:///nowhere", webhook_secret="x")
        db.add(repo)
        await db.flush()
        shop = Stack(repository_id=repo.id, path="stacks/shop", name="shop")
        blog = Stack(repository_id=repo.id, path="stacks/blog", name="blog", type=StackType.COMPOSE)
        db.add_all([shop, blog])
        await db.commit()
    await engine.dispose()

    reader = FakeReader()
    reader.task_list = copy.deepcopy(TASKS)
    for t in reader.task_list:
        t["Spec"]["ContainerSpec"]["Labels"] = {"com.docker.stack.namespace": "shop"}
    reader.container_list = [
        blog_container("blog1", "running", "Up 3 minutes (healthy)"),
        blog_container("blog2", "exited", "Exited (1) 2 minutes ago"),
    ]
    reader.container_inspect = {c: blog_inspect(c) for c in ("blog1", "blog2")}
    agents = FakeAgents({"node1": set()})
    cli = FakeCli()
    settings = make_settings(
        database_url=SecretStr(clean_db), initial_admin_password=SecretStr(PASSWORD)
    )
    async for client in client_for(create_app(settings, reader=reader, cli=cli, agents=agents)):
        response = await client.post(
            "/api/auth/login", json={"username": "admin", "password": PASSWORD}
        )
        assert response.status_code == 204
        yield World(client, cli, agents, shop.id, blog.id, clean_db)


async def test_swarm_stack_lists_current_tasks_before_history(world: World) -> None:
    data = (await world.client.get(f"/api/stacks/{world.shop}/containers")).json()
    assert data["type"] == "swarm"
    assert data["agents"] is True
    [web] = data["services"]
    assert (web["name"], web["full_name"], web["image"]) == ("web", "shop_web", "nginx:1.29")
    assert [(c["ref"], c["current"]) for c in web["containers"]] == [
        ("task-new", True),
        ("task-old", False),
    ]
    assert web["containers"][0]["name"] == "shop_web.1"
    assert web["containers"][1]["error"].startswith("invalid mount config")


async def test_compose_stack_groups_containers_by_service(world: World) -> None:
    data = (await world.client.get(f"/api/stacks/{world.blog}/containers")).json()
    [web] = data["services"]
    assert (web["mode"], web["replicas_running"], web["replicas_desired"]) == ("compose", 1, 2)
    assert web["ports"] == [{"published": 8080, "target": 80, "protocol": "tcp", "mode": "host"}]
    first, second = web["containers"]
    assert (first["name"], first["state"], first["health"], first["node"]) == (
        "blog-web-1",
        "running",
        "healthy",
        "mgr-1",
    )
    assert second["state"] == "exited"


async def test_container_info(world: World) -> None:
    task = (await world.client.get(f"/api/stacks/{world.shop}/containers/task-new")).json()
    assert (task["service"], task["state"], task["node"]) == ("web", "running", "mgr-1")
    assert task["container_id"] == "c1"

    container = (await world.client.get(f"/api/stacks/{world.blog}/containers/blog1")).json()
    assert container["command"] == ["nginx"]
    assert container["restart_count"] == 2
    assert container["networks"] == [{"name": "blog_default", "addresses": ["172.20.0.2"]}]
    assert container["mounts"][0]["source"] == "blog_html"
    assert (container["health"], container["health_log"]) == ("healthy", "ok")
    assert container["started_at"].startswith("2026-11-24T10:00:01")
    assert container["finished_at"] is None


async def test_containers_of_other_stacks_are_not_found(world: World) -> None:
    response = await world.client.get(f"/api/stacks/{world.shop}/containers/blog1")
    assert response.status_code == 404
    response = await world.client.get(f"/api/stacks/{world.blog}/containers/task-new")
    assert response.status_code == 404


async def test_logs_come_from_the_containers_node(world: World) -> None:
    response = await world.client.get(f"/api/stacks/{world.shop}/containers/task-new/logs?tail=5")
    assert response.status_code == 200
    assert world.agents.requests[-1] == (
        "node1",
        "/containers/c1/logs",
        {"tail": 5, "timestamps": True},
    )
    await world.client.get(f"/api/stacks/{world.blog}/containers/blog1/logs")
    assert world.agents.requests[-1][:2] == ("node1", "/containers/blog1/logs")


async def test_restarts(world: World) -> None:
    response = await world.client.post(f"/api/stacks/{world.blog}/containers/blog2/restart")
    assert response.status_code == 204
    response = await world.client.post(f"/api/stacks/{world.shop}/containers/task-new/restart")
    assert response.status_code == 409
    response = await world.client.post(f"/api/stacks/{world.shop}/services/web/restart")
    assert response.status_code == 204
    response = await world.client.post(f"/api/stacks/{world.blog}/services/web/restart")
    assert response.status_code == 204
    assert world.cli.calls == [
        "restart-containers blog2",
        "restart-service shop_web",
        "restart-containers blog1 blog2",
    ]
    response = await world.client.post(f"/api/stacks/{world.shop}/services/nope/restart")
    assert response.status_code == 404


async def test_stack_roles_apply(world: World) -> None:
    engine = create_async_engine(world.url)
    async with async_sessionmaker(engine)() as db:
        vera = User(username="vera", password_hash=hash_password(PASSWORD))
        db.add(vera)
        await db.flush()
        db.add(RoleBinding(user_id=vera.id, role=Role.VIEWER, stack_id=world.blog))
        await db.commit()
    await engine.dispose()
    await world.client.post("/api/auth/login", json={"username": "vera", "password": PASSWORD})

    assert (await world.client.get(f"/api/stacks/{world.blog}/containers")).status_code == 200
    assert (await world.client.get(f"/api/stacks/{world.shop}/containers")).status_code == 403
    response = await world.client.post(f"/api/stacks/{world.blog}/containers/blog1/restart")
    assert response.status_code == 403


def row(name: str, running: int, desired: int | None, *containers: ContainerRow) -> ServiceRow:
    return ServiceRow(
        name=name,
        full_name=name,
        service_id=None,
        image="busybox",
        mode="replicated",
        replicas_desired=desired,
        replicas_running=running,
        ports=[],
        update_status=None,
        containers=list(containers),
    )


def task_row(state: str, current: bool, minutes_ago: int) -> ContainerRow:
    return ContainerRow(
        ref=uuid.uuid4().hex,
        name="x",
        container_id=None,
        node=None,
        node_id=None,
        state=state,
        desired_state=None,
        status=None,
        health=None,
        error=None,
        image="busybox",
        updated_at=datetime.now(UTC) - timedelta(minutes=minutes_ago),
        current=current,
    )


def test_stack_health_rules() -> None:
    assert stack_health([]).state == "stopped"
    assert stack_health([row("web", 2, 2)]).state == "healthy"
    short = stack_health([row("web", 1, 2)])
    assert (short.state, short.problems) == ("degraded", ["web: 1/2 running"])
    assert stack_health([row("web", 0, 2)]).state == "down"

    looping = stack_health(
        [row("job", 1, 1, task_row("failed", False, 1), task_row("failed", False, 2))]
    )
    assert (looping.state, looping.problems) == (
        "degraded",
        ["job: 2 failed in the last 10 minutes"],
    )
    old = stack_health([row("job", 1, 1, task_row("failed", False, 30))])
    assert old.state == "healthy"


async def test_health_is_reported_per_stack(world: World) -> None:
    data = (await world.client.get(f"/api/stacks/{world.blog}/containers")).json()
    assert data["health"] == {
        "state": "degraded",
        "problems": ["web: 1/2 running", "web: 1 exited"],
    }

    health = (await world.client.get("/api/stacks-health")).json()
    assert health[str(world.blog)]["state"] == "degraded"
    assert health[str(world.shop)]["state"] in {"healthy", "degraded"}
