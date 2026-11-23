# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from novaport.auth.access import Access
from novaport.auth.passwords import hash_password
from novaport.models import (
    AuditEntry,
    Group,
    GroupMember,
    Repository,
    Revision,
    Role,
    RoleBinding,
    Stack,
    User,
)
from novaport.server.app import create_app
from tests.conftest import client_for, make_settings
from tests.fakes import FakeReader
from tests.test_pipeline import FakeCli

PASSWORD = "correct horse battery"


def test_stack_role_adds_to_global_role() -> None:
    stack = uuid.uuid4()
    access = Access(User(username="u"), Role.VIEWER, {stack: Role.EDITOR})
    assert access.can(Role.VIEWER)
    assert not access.can(Role.OPERATOR)
    assert access.can(Role.EDITOR, stack)
    assert not access.can(Role.ADMIN, stack)
    assert access.role_for(uuid.uuid4()) == Role.VIEWER


def test_no_role_means_nothing() -> None:
    access = Access(User(username="u"), None)
    assert not access.can(Role.VIEWER)
    assert access.role_for(uuid.uuid4()) is None


@dataclass
class World:
    url: str
    maker: async_sessionmaker[AsyncSession]
    shop: uuid.UUID
    blog: uuid.UUID

    async def user(self, username: str, *bindings: tuple[Role, uuid.UUID | None]) -> User:
        async with self.maker() as db:
            user = User(username=username, password_hash=hash_password(PASSWORD))
            db.add(user)
            await db.flush()
            for role, stack_id in bindings:
                db.add(RoleBinding(user_id=user.id, role=role, stack_id=stack_id))
            await db.commit()
            return user

    async def audit(self, action: str) -> list[AuditEntry]:
        async with self.maker() as db:
            entries = await db.scalars(
                select(AuditEntry).where(AuditEntry.action == action).order_by(AuditEntry.id)
            )
            return list(entries)


pytestmark = pytest.mark.postgres


@pytest.fixture
async def world(clean_db: str) -> AsyncIterator[World]:
    engine = create_async_engine(clean_db)
    maker = async_sessionmaker(engine, expire_on_commit=False)
    async with maker() as db:
        repo = Repository(name="infra", url="file:///nowhere", webhook_secret="x")
        db.add(repo)
        await db.flush()
        stacks = []
        for name in ("shop", "blog"):
            stack = Stack(repository_id=repo.id, path=f"stacks/{name}", name=name)
            db.add(stack)
            await db.flush()
            db.add(
                Revision(
                    stack_id=stack.id,
                    commit="a" * 40,
                    tree="b" * 40,
                    committed_at=datetime.now(UTC),
                )
            )
            stacks.append(stack.id)
        await db.commit()
    try:
        yield World(clean_db, maker, *stacks)
    finally:
        await engine.dispose()


@pytest.fixture
async def app(world: World) -> AsyncIterator[httpx.AsyncClient]:
    settings = make_settings(
        database_url=SecretStr(world.url), initial_admin_password=SecretStr(PASSWORD)
    )
    async for client in client_for(create_app(settings, reader=FakeReader(), cli=FakeCli())):
        yield client


async def login(client: httpx.AsyncClient, username: str) -> None:
    response = await client.post(
        "/api/auth/login", json={"username": username, "password": PASSWORD}
    )
    assert response.status_code == 204, response.text


async def test_stack_viewer_only_sees_their_stack(app: httpx.AsyncClient, world: World) -> None:
    await world.user("vera", (Role.VIEWER, world.shop))
    await login(app, "vera")

    stacks = (await app.get("/api/stacks")).json()
    assert [s["name"] for s in stacks] == ["shop"]
    assert (await app.get(f"/api/stacks/{world.blog}")).status_code == 403
    assert (await app.get(f"/api/stacks/{world.shop}/revisions")).status_code == 200

    me = (await app.get("/api/auth/me")).json()
    assert me["global_role"] is None
    assert me["stack_roles"] == {str(world.shop): "viewer"}


async def test_stack_viewer_sees_only_their_services(app: httpx.AsyncClient, world: World) -> None:
    await world.user("vera", (Role.VIEWER, world.shop))
    await login(app, "vera")

    services = (await app.get("/api/services")).json()
    assert [s["name"] for s in services] == ["shop_web"]
    assert (await app.get("/api/services/svc1/logs")).status_code == 200
    assert (await app.get("/api/services/svc2")).status_code == 403
    assert (await app.get("/api/nodes")).status_code == 403


async def test_viewer_cannot_deploy_and_is_audited(app: httpx.AsyncClient, world: World) -> None:
    viewer = await world.user("vera", (Role.VIEWER, world.shop))
    await login(app, "vera")

    response = await app.post(f"/api/stacks/{world.shop}/deploy", json={})
    assert response.status_code == 403

    [denied] = await world.audit("stack.deploy")
    assert denied.outcome == "denied"
    assert denied.user_id == viewer.id
    assert denied.stack_id == world.shop


async def test_operator_deploys_and_is_audited(app: httpx.AsyncClient, world: World) -> None:
    await world.user("otto", (Role.OPERATOR, world.shop))
    await login(app, "otto")

    response = await app.post(f"/api/stacks/{world.shop}/deploy", json={})
    assert response.status_code == 202, response.text
    assert (await app.post(f"/api/stacks/{world.blog}/deploy", json={})).status_code == 403

    entries = await world.audit("stack.deploy")
    assert [(e.outcome, e.target) for e in entries] == [("success", "shop"), ("denied", None)]
    assert entries[0].details["commit"] == "a" * 40
    assert entries[0].details["deployment"] == response.json()["id"]


async def test_roles_through_groups(app: httpx.AsyncClient, world: World) -> None:
    user = await world.user("gina")
    async with world.maker() as db:
        group = Group(name="ops")
        db.add(group)
        await db.flush()
        db.add(GroupMember(group_id=group.id, user_id=user.id))
        db.add(RoleBinding(group_id=group.id, role=Role.OPERATOR))
        await db.commit()
    await login(app, "gina")

    assert (await app.get("/api/auth/me")).json()["global_role"] == "operator"
    assert (await app.post(f"/api/stacks/{world.blog}/deploy", json={})).status_code == 202
    assert (await app.get("/api/repositories")).status_code == 403


async def test_secrets_reveal_needs_editor(app: httpx.AsyncClient, world: World) -> None:
    await world.user("vera", (Role.OPERATOR, world.shop))
    await login(app, "vera")
    response = await app.get(f"/api/stacks/{world.shop}/secrets", params={"reveal": "true"})
    assert response.status_code == 403
    edit = await app.post(
        f"/api/stacks/{world.shop}/files",
        json={"base_commit": "a" * 40, "changes": [{"path": "compose.yaml", "content": ""}]},
    )
    assert edit.status_code == 403


async def test_user_without_roles_sees_nothing(app: httpx.AsyncClient, world: World) -> None:
    await world.user("nobody")
    await login(app, "nobody")
    assert (await app.get("/api/stacks")).json() == []
    assert (await app.get("/api/services")).json() == []
    assert (await app.get("/api/deployments")).json() == []
    assert (await app.get("/api/users")).status_code == 403


async def test_admin_manages_bindings(app: httpx.AsyncClient, world: World) -> None:
    user = await world.user("vera")
    await login(app, "admin")

    created = await app.post(
        "/api/role-bindings",
        json={"user_id": str(user.id), "role": "viewer", "stack_id": str(world.shop)},
    )
    assert created.status_code == 201, created.text
    binding = created.json()
    assert binding["subject"] == "vera"
    assert binding["stack_name"] == "shop"

    duplicate = await app.post(
        "/api/role-bindings",
        json={"user_id": str(user.id), "role": "editor", "stack_id": str(world.shop)},
    )
    assert duplicate.status_code == 409

    changed = await app.patch(f"/api/role-bindings/{binding['id']}", json={"role": "editor"})
    assert changed.json()["role"] == "editor"

    listed = await app.get("/api/role-bindings", params={"stack_id": str(world.shop)})
    assert [b["subject"] for b in listed.json()] == ["vera"]

    assert (await app.delete(f"/api/role-bindings/{binding['id']}")).status_code == 204
    actions = [e.action for e in await world.audit("role_binding.update")]
    assert actions == ["role_binding.update"]


async def test_last_admin_is_protected(app: httpx.AsyncClient, world: World) -> None:
    await login(app, "admin")
    bindings = (await app.get("/api/role-bindings")).json()
    [own] = [b for b in bindings if b["subject"] == "admin"]

    assert (await app.delete(f"/api/role-bindings/{own['id']}")).status_code == 409
    downgrade = await app.patch(f"/api/role-bindings/{own['id']}", json={"role": "editor"})
    assert downgrade.status_code == 409

    users = (await app.get("/api/users")).json()
    [admin] = [u for u in users if u["username"] == "admin"]
    assert (
        await app.patch(f"/api/users/{admin['id']}", json={"disabled": True})
    ).status_code == 409

    second = await world.user("root", (Role.ADMIN, None))
    assert (await app.delete(f"/api/role-bindings/{own['id']}")).status_code == 204
    assert second.id


async def test_disabling_a_user_ends_their_session(app: httpx.AsyncClient, world: World) -> None:
    user = await world.user("vera", (Role.VIEWER, None))
    settings = make_settings(database_url=SecretStr(world.url))
    async for other in client_for(create_app(settings, reader=FakeReader())):
        await login(other, "vera")
        assert (await other.get("/api/stacks")).status_code == 200

        await login(app, "admin")
        response = await app.patch(f"/api/users/{user.id}", json={"disabled": True})
        assert response.json()["disabled"] is True
        assert (await other.get("/api/stacks")).status_code == 401


async def test_groups_and_members(app: httpx.AsyncClient, world: World) -> None:
    user = await world.user("vera")
    await login(app, "admin")
    group = (await app.post("/api/groups", json={"name": "ops"})).json()
    assert (await app.post("/api/groups", json={"name": "ops"})).status_code == 409

    added = await app.put(f"/api/groups/{group['id']}/members/{user.id}")
    assert added.json()["members"] == [
        {"user_id": str(user.id), "username": "vera", "synced": False}
    ]
    users = (await app.get("/api/users")).json()
    assert [g["name"] for u in users if u["username"] == "vera" for g in u["groups"]] == ["ops"]

    removed = await app.delete(f"/api/groups/{group['id']}/members/{user.id}")
    assert removed.json()["members"] == []


async def test_audit_log(app: httpx.AsyncClient, world: World) -> None:
    bad = await app.post("/api/auth/login", json={"username": "admin", "password": "nope nope"})
    assert bad.status_code == 401
    await login(app, "admin")

    entries = (await app.get("/api/audit", params={"action": "auth.login"})).json()
    assert [(e["username"], e["outcome"]) for e in entries] == [
        ("admin", "success"),
        ("admin", "failed"),
    ]
    assert entries[0]["details"] == {"method": "password"}

    older = (await app.get("/api/audit", params={"before": entries[0]["id"]})).json()
    assert [e["id"] for e in older] == [entries[1]["id"]]
    prefixed = (await app.get("/api/audit", params={"action": "auth."})).json()
    assert len(prefixed) == 2
