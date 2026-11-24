# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json
import time
import uuid
from collections.abc import AsyncIterator, Iterator
from datetime import UTC, datetime

import pytest
from pydantic import SecretStr
from sqlalchemy import text, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from starlette.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from novaport.auth.access import Access
from novaport.auth.passwords import hash_password
from novaport.events import RESYNC, Change, EventHub, install, parse, payloads
from novaport.migrate import upgrade
from novaport.models import (
    Deployment,
    DeploymentEvent,
    DeploymentStatus,
    Repository,
    Revision,
    Role,
    RoleBinding,
    Stack,
    User,
)
from novaport.server.api.live import visible
from novaport.server.app import create_app
from tests.conftest import TEST_DATABASE_URL, make_settings
from tests.fakes import FakeReader

PASSWORD = "correct horse battery"


def test_payloads_stay_under_the_notify_limit() -> None:
    changes = [Change("revisions", str(uuid.uuid4()), str(uuid.uuid4())) for _ in range(500)]
    result = payloads(changes)
    assert len(result) > 1
    assert all(len(p) < 8000 for p in result)
    assert {c for p in result for c in parse(p)} == set(changes)


def test_parse_ignores_garbage() -> None:
    assert parse("not json") == []
    assert parse('{"topic": "stacks"}') == []
    assert parse('[{"id": "x"}, {"topic": "stacks"}]') == [Change("stacks")]


def test_visibility_follows_stack_roles() -> None:
    shop, blog = uuid.uuid4(), uuid.uuid4()
    vera = Access(User(username="vera"), None, {shop: Role.VIEWER})
    assert visible(vera, Change("deployments", "d", str(shop)))
    assert not visible(vera, Change("deployments", "d", str(blog)))
    assert visible(vera, Change("repositories", "r"))
    nobody = Access(User(username="nobody"), None)
    assert not visible(nobody, Change("repositories", "r"))
    assert visible(Access(User(username="admin"), Role.ADMIN), Change("stacks", "s", str(blog)))


@pytest.fixture
async def maker(clean_db: str) -> AsyncIterator[async_sessionmaker[AsyncSession]]:
    install()
    engine = create_async_engine(clean_db)
    try:
        yield async_sessionmaker(engine, expire_on_commit=False)
    finally:
        await engine.dispose()


@pytest.fixture
async def hub(maker: async_sessionmaker[AsyncSession]) -> AsyncIterator[EventHub]:
    assert maker.kw["bind"] is not None
    hub = EventHub(maker.kw["bind"])
    hub.start()
    await asyncio.wait_for(hub.listening.wait(), 5)
    try:
        yield hub
    finally:
        await hub.stop()


async def _received(queue: asyncio.Queue[Change]) -> set[Change]:
    changes = {await asyncio.wait_for(queue.get(), 5)}
    # One commit can carry several changes; give the rest a moment to arrive.
    await asyncio.sleep(0.2)
    while not queue.empty():
        changes.add(queue.get_nowait())
    return changes


async def _stack(maker: async_sessionmaker[AsyncSession], name: str = "shop") -> Stack:
    async with maker() as db:
        repo = Repository(name=f"repo-{name}", url="file:///nowhere", webhook_secret="x")
        db.add(repo)
        await db.flush()
        stack = Stack(repository_id=repo.id, path=f"stacks/{name}", name=name)
        db.add(stack)
        await db.commit()
        return stack


@pytest.mark.postgres
async def test_commit_notifies_changed_rows(
    maker: async_sessionmaker[AsyncSession], hub: EventHub
) -> None:
    with hub.subscribe() as queue:
        stack = await _stack(maker)
        changes = await _received(queue)
        assert Change("stacks", str(stack.id), str(stack.id)) in changes
        assert Change("repositories", str(stack.repository_id)) in changes


@pytest.mark.postgres
async def test_rollback_sends_nothing(
    maker: async_sessionmaker[AsyncSession], hub: EventHub
) -> None:
    with hub.subscribe() as queue:
        async with maker() as db:
            db.add(Repository(name="gone", url="file:///nowhere", webhook_secret="x"))
            await db.flush()
            await db.rollback()
            await db.execute(text("SELECT 1"))
            await db.commit()
        stack = await _stack(maker)
        changes = await _received(queue)
        assert {c.topic for c in changes} == {"stacks", "repositories"}
        assert Change("repositories", str(stack.repository_id)) in changes
        assert len([c for c in changes if c.topic == "repositories"]) == 1


@pytest.mark.postgres
async def test_deployment_progress_carries_the_stack(
    maker: async_sessionmaker[AsyncSession], hub: EventHub
) -> None:
    stack = await _stack(maker)
    with hub.subscribe() as queue:
        async with maker() as db:
            revision = Revision(
                stack_id=stack.id, commit="a" * 40, tree="b" * 40, committed_at=datetime.now(UTC)
            )
            db.add(revision)
            await db.flush()
            deployment = Deployment(
                stack_id=stack.id,
                revision_id=revision.id,
                status=DeploymentStatus.RUNNING,
                trigger="manual",
            )
            db.add(deployment)
            await db.commit()
            assert {c.topic for c in await _received(queue)} == {"revisions", "deployments"}

            db.add(
                DeploymentEvent(
                    deployment_id=deployment.id,
                    stage="apply",
                    level="info",
                    message="hi",
                    at=datetime.now(UTC),
                )
            )
            await db.commit()
            assert await _received(queue) == {
                Change("deployments", str(deployment.id), str(stack.id))
            }

            await db.execute(
                update(Deployment)
                .where(Deployment.id == deployment.id)
                .values(status=DeploymentStatus.FAILED)
            )
            await db.commit()
            assert await _received(queue) == {Change("deployments")}


def test_full_queue_turns_into_a_resync() -> None:
    async def run() -> list[Change]:
        hub = EventHub(None)  # type: ignore[arg-type]
        with hub.subscribe() as queue:
            hub.publish([Change("stacks", str(i)) for i in range(1000)])
            return [queue.get_nowait() for _ in range(queue.qsize())]

    assert asyncio.run(run())[-1] == RESYNC


async def _users_and_stacks(url: str) -> tuple[uuid.UUID, uuid.UUID]:
    install()
    engine = create_async_engine(url)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    try:
        shop = await _stack(maker, "shop")
        blog = await _stack(maker, "blog")
        async with maker() as db:
            vera = User(username="vera", password_hash=hash_password(PASSWORD))
            db.add(vera)
            await db.flush()
            db.add(RoleBinding(user_id=vera.id, role=Role.VIEWER, stack_id=shop.id))
            await db.commit()
        return shop.id, blog.id
    finally:
        await engine.dispose()


async def _rename(url: str, stack_id: uuid.UUID, mode: str) -> None:
    engine = create_async_engine(url)
    try:
        async with async_sessionmaker(engine)() as db:
            stack = await db.get(Stack, stack_id)
            assert stack is not None
            stack.deploy_mode = mode  # type: ignore[assignment]
            await db.commit()
    finally:
        await engine.dispose()


async def _truncate(url: str) -> None:
    engine = create_async_engine(url)
    try:
        async with engine.begin() as conn:
            await conn.execute(text("TRUNCATE users, sessions, repositories CASCADE"))
    finally:
        await engine.dispose()


@pytest.fixture
def live() -> Iterator[tuple[TestClient, uuid.UUID, uuid.UUID]]:
    assert TEST_DATABASE_URL
    upgrade(TEST_DATABASE_URL)
    asyncio.run(_truncate(TEST_DATABASE_URL))
    shop, blog = asyncio.run(_users_and_stacks(TEST_DATABASE_URL))
    settings = make_settings(initial_admin_password=SecretStr(PASSWORD))
    with TestClient(create_app(settings, reader=FakeReader())) as client:
        deadline = time.monotonic() + 5
        while not client.app.state.events.listening.is_set():  # type: ignore[attr-defined]
            assert time.monotonic() < deadline, "event hub did not start"
            time.sleep(0.05)
        yield client, shop, blog


@pytest.mark.postgres
def test_events_stream_only_visible_changes(
    live: tuple[TestClient, uuid.UUID, uuid.UUID],
) -> None:
    client, shop, blog = live
    response = client.post("/api/auth/login", json={"username": "vera", "password": PASSWORD})
    assert response.status_code == 204
    with client.websocket_connect("/api/events") as ws:
        asyncio.run(_rename(TEST_DATABASE_URL or "", blog, "auto"))
        asyncio.run(_rename(TEST_DATABASE_URL or "", shop, "auto"))
        assert json.loads(ws.receive_text()) == {
            "topic": "stacks",
            "id": str(shop),
            "stack": str(shop),
        }


@pytest.mark.postgres
def test_events_require_login(live: tuple[TestClient, uuid.UUID, uuid.UUID]) -> None:
    client, _, _ = live
    with pytest.raises(WebSocketDisconnect) as exc, client.websocket_connect("/api/events") as ws:
        ws.receive_text()
    assert exc.value.code == 1008
