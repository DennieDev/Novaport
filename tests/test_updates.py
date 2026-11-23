# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from novaport.crypto import Cipher
from novaport.deploy.preflight import preflight
from novaport.git import Identity
from novaport.models import DeployMode, ImageCheck, Repository, Stack, StackType
from novaport.registries.client import RegistryClient
from novaport.server.app import create_app
from novaport.sops import Sops
from novaport.stacks import editing
from novaport.stacks.editing import StackEditor
from novaport.stacks.sync import Syncer
from novaport.updates.checker import UpdateChecker, UpdateEvent
from tests.conftest import TEST_SECRET_KEY, client_for, make_settings
from tests.fakes import FakeReader
from tests.gitrepo import Repo
from tests.registry_fake import FakeRegistry, digest_of
from tests.test_pipeline import FakeCli

pytestmark = pytest.mark.postgres

IMAGE = "registry.example.com/acme/api"
PASSWORD = "correct horse battery"


def compose(policy: str, image: str = f"{IMAGE}:1.0.0") -> str:
    return f"""# api stack
services:
  api:
    image: {image}  # keep this comment
    x-updates: {policy}
  sidecar:
    image: busybox
"""


class Env:
    def __init__(self, url: str, origin: Repo, registry: FakeRegistry) -> None:
        self.origin = origin
        self.registry = registry
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = make_settings(database_url=SecretStr(url))
        cipher = Cipher(TEST_SECRET_KEY)
        self.syncer = Syncer(self.settings, self.engine, self.maker, cipher)
        self.cli = FakeCli()
        editor = StackEditor(
            self.settings,
            self.engine,
            self.maker,
            self.syncer,
            cipher,
            Sops("sops", None),
            self.cli,
        )
        self.http = httpx.AsyncClient(transport=registry.transport())
        self.checker = UpdateChecker(
            self.engine,
            self.maker,
            editor,
            RegistryClient(self.http),
            reader=lambda: None,
            author=Identity("Novaport", "np@example.com"),
        )
        self.events: list[UpdateEvent] = []

        async def hook(event: UpdateEvent) -> None:
            self.events.append(event)

        self.checker.hooks.append(hook)

    async def setup(self, **repo_fields: Any) -> Stack:
        async with self.maker() as db:
            repo = Repository(
                name="infra",
                url=self.origin.url,
                webhook_secret="x",
                default_deploy_mode=DeployMode.MANUAL,
                **repo_fields,
            )
            db.add(repo)
            await db.commit()
        await self.syncer.sync(repo.id)
        async with self.maker() as db:
            stack = await db.scalar(select(Stack).options(selectinload(Stack.repository)))
            assert stack is not None
            return stack

    async def check(self, stack: Stack) -> dict[str, ImageCheck]:
        return {c.service: c for c in await self.checker.check(stack.id)}

    async def close(self) -> None:
        await self.http.aclose()
        await self.engine.dispose()


@pytest.fixture
def registry() -> FakeRegistry:
    registry = FakeRegistry()
    registry.push("acme/api", "1.0.0", "1.0.1", "1.1.0", "2.0.0")
    return registry


@pytest.fixture
async def make_env(clean_db: str, tmp_path: Path, registry: FakeRegistry) -> AsyncIterator[Any]:
    envs: list[Env] = []

    def make(compose_text: str) -> Env:
        origin = Repo(tmp_path / "origin")
        origin.write("stacks/api/compose.yaml", compose_text)
        origin.commit("Initial")
        env = Env(clean_db, origin, registry)
        envs.append(env)
        return env

    yield make
    for env in envs:
        await env.close()


async def test_commit_action_updates_git_once(make_env: Any) -> None:
    env: Env = make_env(compose("{mode: version, allow: minor, action: commit}"))
    stack = await env.setup()

    found = await env.check(stack)
    api = found["api"]
    assert api.status == "available"
    assert api.candidate_ref == f"{IMAGE}:1.1.0"
    assert api.action_taken == "committed"
    assert api.action_ref == env.origin.head()
    assert env.origin.show("stacks/api/compose.yaml") == compose(
        "{mode: version, allow: minor, action: commit}", f"{IMAGE}:1.1.0"
    )
    assert env.origin.log("%an|%s") == "Novaport|Update api in api to 1.1.0"
    assert [(e.service, e.action_taken) for e in env.events] == [("api", "committed")]

    again = await env.check(stack)
    assert again["api"].status == "current"
    assert again["api"].current_ref == f"{IMAGE}:1.1.0"
    assert len(env.events) == 1
    assert "sidecar" not in again


async def test_notify_action_only_records(make_env: Any) -> None:
    env: Env = make_env(compose("{mode: version, allow: major}"))
    stack = await env.setup()
    head = env.origin.head()

    api = (await env.check(stack))["api"]
    assert api.candidate_ref == f"{IMAGE}:2.0.0"
    assert api.action_taken == "notified"
    assert env.origin.head() == head
    assert len(env.events) == 1

    await env.check(stack)
    assert len(env.events) == 1

    env.registry.push("acme/api", "2.1.0")
    env.checker.registry.clear_cache()
    api = (await env.check(stack))["api"]
    assert api.candidate_ref == f"{IMAGE}:2.1.0"
    assert len(env.events) == 2


async def test_digest_mode_pins_new_digest(make_env: Any) -> None:
    old = digest_of("old build")
    env: Env = make_env(compose("{mode: digest, action: commit}", f"{IMAGE}:1.0.0@{old}"))
    stack = await env.setup()

    api = (await env.check(stack))["api"]
    new = digest_of("acme/api:1.0.0")
    assert api.candidate_ref == f"{IMAGE}:1.0.0@{new}"
    assert f"image: {IMAGE}:1.0.0@{new}  # keep" in env.origin.show("stacks/api/compose.yaml")
    assert (await env.check(stack))["api"].status == "current"


async def test_pr_action(make_env: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    env: Env = make_env(compose("{mode: version, allow: patch, action: pr}"))
    stack = await env.setup(forge="github", forge_token=Cipher(TEST_SECRET_KEY).encrypt("ghp_x"))
    opened: list[dict[str, Any]] = []

    async def fake_open(forge: Any, api_url: Any, token: str, repo_url: str, **kw: Any) -> str:
        opened.append(kw)
        return "https://github.com/acme/infra/pull/7"

    monkeypatch.setattr(editing, "open_pull_request", fake_open)
    head = env.origin.head()

    api = (await env.check(stack))["api"]
    assert api.action_taken == "pr_opened"
    assert api.action_ref == "https://github.com/acme/infra/pull/7"
    assert env.origin.head() == head
    assert opened[0]["head"] == "novaport/update-api-api-1.0.1"
    assert f"{IMAGE}:1.0.1" in opened[0]["body"]

    await env.check(stack)
    assert len(opened) == 1


async def test_failed_action_is_retried(make_env: Any) -> None:
    env: Env = make_env(compose("{mode: version, action: pr}"))
    stack = await env.setup()

    api = (await env.check(stack))["api"]
    assert api.action_taken == "failed"
    assert "forge token" in (api.error or "")
    assert env.events == []
    assert (await env.check(stack))["api"].action_taken == "failed"


async def test_problems_are_recorded(make_env: Any) -> None:
    env: Env = make_env(
        """services:
  api:
    image: registry.example.com/acme/missing:1.0.0
    x-updates: {mode: version}
  bad:
    image: nginx
    x-updates: {mode: version, allow: everything}
  paused:
    image: nginx
    x-updates: none
"""
    )
    stack = await env.setup()
    found = await env.check(stack)
    assert found["api"].status == "error"
    assert "doesn't exist" in (found["api"].error or "")
    assert found["bad"].status == "error"
    assert "allow" in (found["bad"].error or "")
    assert found["paused"].status == "disabled"


async def test_preflight_checks_images(registry: FakeRegistry) -> None:
    config = {
        "services": {
            "api": {"image": f"{IMAGE}:1.0.0"},
            "gone": {"image": f"{IMAGE}:9.9.9"},
        }
    }
    async with httpx.AsyncClient(transport=registry.transport()) as http:
        client = RegistryClient(http)
        swarm = await preflight(config, StackType.SWARM, "s", FakeReader(), None, client)
        compose_report = await preflight(config, StackType.COMPOSE, "s", FakeReader(), None, client)
    assert swarm.errors == [f"gone: image {IMAGE}:9.9.9 does not exist in registry.example.com."]
    assert compose_report.errors == []
    assert "built on this host" in compose_report.warnings[0]


async def test_registries_api(clean_db: str, registry: FakeRegistry) -> None:
    settings = make_settings(
        database_url=SecretStr(clean_db), initial_admin_password=SecretStr(PASSWORD)
    )
    app = create_app(settings, reader=FakeReader(), http_transport=registry.transport())
    async for client in client_for(app):
        await client.post("/api/auth/login", json={"username": "admin", "password": PASSWORD})
        created = await client.post(
            "/api/registries",
            json={"host": "https://Registry.Example.com/", "username": "bot", "password": "pw"},
        )
        assert created.status_code == 201, created.text
        data = created.json()
        assert data["host"] == "registry.example.com"
        assert "password" not in data

        duplicate = await client.post(
            "/api/registries",
            json={"host": "registry.example.com", "username": "x", "password": "y"},
        )
        assert duplicate.status_code == 409

        check = await client.post(f"/api/registries/{data['id']}/check")
        assert check.json() == {"ok": True, "error": None}

        assert (await client.get("/api/updates")).json() == []
        assert (await client.delete(f"/api/registries/{data['id']}")).status_code == 204
