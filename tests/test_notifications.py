# SPDX-License-Identifier: AGPL-3.0-or-later
import copy
import hashlib
import hmac
import json
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
from fastapi import FastAPI
from pydantic import SecretStr
from sqlalchemy import select

from novaport.deploy.drift import snapshot
from novaport.models import (
    Deployment,
    DeploymentStatus,
    EventType,
    Repository,
    Revision,
    Stack,
)
from novaport.monitor import Monitor, update_event
from novaport.notifications import Event, Notifier
from novaport.server.app import create_app
from novaport.updates.checker import UpdateEvent
from tests.conftest import client_for, make_settings
from tests.fakes import FakeAgents, FakeReader

pytestmark = pytest.mark.postgres

PASSWORD = "correct horse battery"
HOOK = "https://hooks.example.com/novaport"
SLACK = "https://hooks.slack.com/services/T0/B0/xyz"


class Endpoints:
    def __init__(self) -> None:
        self.received: list[httpx.Request] = []
        self.fail = False

    def transport(self) -> httpx.MockTransport:
        def handle(request: httpx.Request) -> httpx.Response:
            self.received.append(request)
            return httpx.Response(500 if self.fail else 200)

        return httpx.MockTransport(handle)

    def bodies(self, url: str) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.received if str(r.url) == url]


@dataclass
class Env:
    app: FastAPI
    api: httpx.AsyncClient
    endpoints: Endpoints
    reader: FakeReader
    stack_id: str

    @property
    def notifier(self) -> Notifier:
        notifier: Notifier = self.app.state.notifier
        return notifier

    def monitor(self, agents: FakeAgents | None = None) -> Monitor:
        return Monitor(
            self.app.state.engine,
            self.app.state.sessionmaker,
            self.notifier,
            reader=lambda: self.reader,
            agents=lambda: agents,
        )

    async def channel(self, **body: Any) -> dict[str, Any]:
        response = await self.api.post("/api/notification-channels", json=body)
        assert response.status_code == 201, response.text
        data: dict[str, Any] = response.json()
        return data


@pytest.fixture
async def env(clean_db: str) -> AsyncIterator[Env]:
    endpoints = Endpoints()
    reader = FakeReader()
    settings = make_settings(
        database_url=SecretStr(clean_db),
        initial_admin_password=SecretStr(PASSWORD),
        public_url="https://novaport.example.com",
    )
    app = create_app(settings, reader=reader, http_transport=endpoints.transport())
    async for api in client_for(app):
        await api.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        async with app.state.sessionmaker() as db:
            repo = Repository(name="infra", url="file:///x", webhook_secret="x")
            db.add(repo)
            await db.flush()
            stack = Stack(repository_id=repo.id, path="stacks/shop", name="shop")
            db.add(stack)
            await db.commit()
        yield Env(app, api, endpoints, reader, str(stack.id))


def event(kind: EventType = EventType.DEPLOY_FAILED, stack_id: Any = None) -> Event:
    return Event(kind, "Deploying shop failed", "boom", stack_id=stack_id, link="/deployments/1")


async def test_webhook_is_signed(env: Env) -> None:
    await env.channel(
        name="ops",
        kind="webhook",
        url=HOOK,
        signing_secret="s3cret",
        subscriptions=[{"event": "*"}],
    )
    env.notifier.emit(event())
    await env.notifier.drain()

    [request] = env.endpoints.received
    expected = "sha256=" + hmac.new(b"s3cret", request.content, hashlib.sha256).hexdigest()
    assert request.headers["x-novaport-signature"] == expected
    assert request.headers["x-novaport-event"] == "deploy.failed"
    body = json.loads(request.content)
    assert body["title"] == "Deploying shop failed"
    assert body["link"] == "https://novaport.example.com/deployments/1"


async def test_subscriptions_filter_by_event_and_stack(env: Env) -> None:
    await env.channel(
        name="shop-only",
        kind="slack",
        url=SLACK,
        subscriptions=[{"event": "deploy.failed", "stack_id": env.stack_id}],
    )
    other_stack = "00000000-0000-0000-0000-000000000000"
    env.notifier.emit(event(EventType.DEPLOY_SUCCEEDED, env.stack_id))
    env.notifier.emit(event(EventType.DEPLOY_FAILED, other_stack))
    env.notifier.emit(event(EventType.DEPLOY_FAILED, env.stack_id))
    await env.notifier.drain()

    [body] = env.endpoints.bodies(SLACK)
    assert body["text"].startswith("*<https://novaport.example.com/deployments/1|Deploying")


async def test_failures_are_recorded(env: Env) -> None:
    channel = await env.channel(
        name="ops", kind="webhook", url=HOOK, subscriptions=[{"event": "*"}]
    )
    env.endpoints.fail = True
    env.notifier.emit(event())
    await env.notifier.drain()
    [listed] = (await env.api.get("/api/notification-channels")).json()
    assert listed["last_error"] == "The endpoint answered HTTP 500."
    assert listed["has_url"] is True
    assert "url" not in listed

    test = await env.api.post(f"/api/notification-channels/{channel['id']}/test")
    assert test.json()["ok"] is False


async def test_email_needs_smtp(env: Env) -> None:
    channel = await env.channel(name="mail", kind="email", recipients="ops@example.com")
    test = await env.api.post(f"/api/notification-channels/{channel['id']}/test")
    assert test.json() == {"ok": False, "error": "NOVAPORT_SMTP_HOST is not set."}


async def test_channel_validation(env: Env) -> None:
    missing_url = await env.api.post(
        "/api/notification-channels", json={"name": "x", "kind": "slack"}
    )
    assert missing_url.status_code == 422
    unknown = await env.api.post(
        "/api/notification-channels",
        json={"name": "x", "kind": "slack", "url": SLACK, "subscriptions": [{"event": "nope"}]},
    )
    assert unknown.status_code == 422
    events = (await env.api.get("/api/notification-channels/events")).json()
    assert "drift.detected" in events


async def test_update_events() -> None:
    base = UpdateEvent(uuid.uuid4(), "shop", "api", "api:1.0", "api:1.1", "committed", "abc123")
    found = update_event(base)
    assert found is not None
    assert found.type == EventType.UPDATE_COMMITTED
    assert "api:1.0 -> api:1.1" in found.text
    base.action_taken = "recorded"
    assert update_event(base) is None


async def test_drift_is_detected_once(env: Env) -> None:
    await env.channel(name="ops", kind="webhook", url=HOOK, subscriptions=[{"event": "*"}])
    running = copy.deepcopy(env.reader.service_list[0])
    async with env.app.state.sessionmaker() as db:
        stack = await db.get(Stack, uuid.UUID(env.stack_id))
        assert stack is not None
        stack.deployed_spec = snapshot([running], "shop")
        await db.commit()

    monitor = env.monitor()
    await monitor.check_drift()
    assert (await env.api.get(f"/api/stacks/{env.stack_id}")).json()["drift"] == []

    changed = copy.deepcopy(running)
    changed["Spec"]["Mode"] = {"Replicated": {"Replicas": 9}}
    env.reader.service_list = [changed, *env.reader.service_list[1:]]
    await monitor.check_drift()
    await monitor.check_drift()
    await env.notifier.drain()

    stack_out = (await env.api.get(f"/api/stacks/{env.stack_id}")).json()
    [line] = stack_out["drift"]
    assert line.startswith("web: Mode.Replicated.Replicas changed from ")
    assert line.endswith(" to 9")
    bodies = env.endpoints.bodies(HOOK)
    assert [b["event"] for b in bodies] == ["drift.detected"]


async def test_approvals_are_announced_once(env: Env) -> None:
    await env.channel(name="ops", kind="slack", url=SLACK, subscriptions=[{"event": "*"}])
    async with env.app.state.sessionmaker() as db:
        stack_id = uuid.UUID(env.stack_id)
        revision = Revision(
            stack_id=stack_id,
            commit="c" * 40,
            tree="t" * 40,
            message="Bump api",
            committed_at=datetime.now(UTC),
        )
        db.add(revision)
        await db.flush()
        db.add(
            Deployment(
                stack_id=stack_id,
                revision_id=revision.id,
                status=DeploymentStatus.AWAITING_APPROVAL,
                trigger="git",
            )
        )
        await db.commit()

    monitor = env.monitor()
    await monitor.announce_approvals()
    await monitor.announce_approvals()
    await env.notifier.drain()
    [body] = env.endpoints.bodies(SLACK)
    assert "shop is waiting for approval" in body["text"]
    assert "Bump api" in body["text"]
    async with env.app.state.sessionmaker() as db:
        assert (await db.scalar(select(Deployment))).approval_notified  # type: ignore[union-attr]


async def test_nodes_going_down(env: Env) -> None:
    await env.channel(name="ops", kind="webhook", url=HOOK, subscriptions=[{"event": "node.down"}])
    worker = copy.deepcopy(env.reader.node_list[0])
    worker["Status"] = {"State": "ready"}
    env.reader.node_list = [worker, *env.reader.node_list[1:]]
    monitor = env.monitor()
    await monitor.check_health()
    worker = copy.deepcopy(worker)
    worker["Status"] = {"State": "down"}
    env.reader.node_list = [worker, *env.reader.node_list[1:]]
    await monitor.check_health()
    await monitor.check_health()
    await env.notifier.drain()
    assert [b["title"] for b in env.endpoints.bodies(HOOK)] == ["Node wrk-1 is down"]
