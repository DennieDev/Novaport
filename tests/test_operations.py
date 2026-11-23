# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from collections.abc import AsyncIterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy import func, select, update

from novaport.models import (
    AuditEntry,
    Deployment,
    DeploymentStatus,
    DeployMode,
    Outcome,
    Revision,
    Role,
    Stack,
    StackType,
    User,
)
from novaport.process import Result
from novaport.retention import Retention, prune
from novaport.server.app import create_app
from tests import test_access
from tests.conftest import client_for, make_settings
from tests.fakes import FakeReader
from tests.test_access import World, login
from tests.test_pipeline import FakeCli

pytestmark = pytest.mark.postgres

world = test_access.world

PASSWORD = "correct horse battery"


@dataclass
class Env:
    api: httpx.AsyncClient
    cli: FakeCli
    reader: FakeReader


@pytest.fixture
async def env(world: World) -> AsyncIterator[Env]:
    settings = make_settings(
        database_url=SecretStr(world.url),
        initial_admin_password=SecretStr(PASSWORD),
        separate_approver=True,
    )
    cli = FakeCli()
    reader = FakeReader()
    async for client in client_for(create_app(settings, reader=reader, cli=cli)):
        yield Env(client, cli, reader)


async def set_author(world: World, stack_id: uuid.UUID, email: str) -> None:
    async with world.maker() as db:
        await db.execute(
            update(Revision).where(Revision.stack_id == stack_id).values(author_email=email)
        )
        await db.execute(
            update(Stack).where(Stack.id == stack_id).values(deploy_mode=DeployMode.APPROVE)
        )
        await db.commit()


async def queue_for_approval(world: World, stack_id: uuid.UUID) -> uuid.UUID:
    async with world.maker() as db:
        revision = await db.scalar(select(Revision).where(Revision.stack_id == stack_id))
        assert revision is not None
        deployment = Deployment(
            stack_id=stack_id,
            revision_id=revision.id,
            status=DeploymentStatus.AWAITING_APPROVAL,
            trigger="git",
        )
        db.add(deployment)
        await db.commit()
        return deployment.id


async def test_author_cannot_approve_their_own_change(env: Env, world: World) -> None:
    author = await world.user("otto", (Role.OPERATOR, world.shop))
    await world.user("olga", (Role.OPERATOR, world.shop))
    async with world.maker() as db:
        await db.execute(update(User).where(User.id == author.id).values(email="Otto@Example.com"))
        await db.commit()
    await set_author(world, world.shop, "otto@example.com")
    deployment_id = await queue_for_approval(world, world.shop)

    await login(env.api, "otto")
    refused = await env.api.post(f"/api/deployments/{deployment_id}/approve")
    assert refused.status_code == 403
    assert "Someone other than the author" in refused.json()["detail"]
    assert (await env.api.post(f"/api/stacks/{world.shop}/deploy", json={})).status_code == 403
    [denied] = await world.audit("deployment.approve")
    assert denied.outcome == Outcome.DENIED

    await login(env.api, "olga")
    approved = await env.api.post(f"/api/deployments/{deployment_id}/approve")
    assert approved.status_code == 200, approved.text


async def test_edits_made_in_novaport_count_as_the_users(env: Env, world: World) -> None:
    await world.user("otto", (Role.OPERATOR, world.shop))
    await set_author(world, world.shop, "otto@novaport.local")
    deployment_id = await queue_for_approval(world, world.shop)
    await login(env.api, "otto")
    assert (await env.api.post(f"/api/deployments/{deployment_id}/approve")).status_code == 403


async def test_other_deploy_modes_are_not_affected(env: Env, world: World) -> None:
    await world.user("otto", (Role.OPERATOR, world.shop))
    async with world.maker() as db:
        await db.execute(update(Revision).values(author_email="otto@novaport.local"))
        await db.commit()
    await login(env.api, "otto")
    assert (await env.api.post(f"/api/stacks/{world.shop}/deploy", json={})).status_code == 202


async def test_restart_a_service(env: Env, world: World) -> None:
    await world.user("otto", (Role.OPERATOR, world.shop))
    await world.user("vera", (Role.VIEWER, world.shop))

    await login(env.api, "vera")
    assert (await env.api.post("/api/services/svc1/restart")).status_code == 403

    await login(env.api, "otto")
    assert (await env.api.post("/api/services/svc1/restart")).status_code == 204
    assert (await env.api.post("/api/services/svc2/restart")).status_code == 403
    assert env.cli.calls == ["restart-service shop_web"]
    entries = await world.audit("service.restart")
    assert [(e.outcome, e.target, e.stack_id) for e in entries] == [
        ("denied", "shop_web", None),
        ("success", "shop_web", world.shop),
        ("denied", "agent", None),
    ]


async def test_restart_a_stack(env: Env, world: World) -> None:
    await world.user("otto", (Role.OPERATOR, world.shop))
    await login(env.api, "otto")
    assert (await env.api.post(f"/api/stacks/{world.shop}/restart")).status_code == 204
    assert env.cli.calls == ["restart-service shop_web"]

    nothing = await env.api.post(f"/api/stacks/{world.blog}/restart")
    assert nothing.status_code == 403


async def test_restart_a_compose_project(env: Env, world: World) -> None:
    async with world.maker() as db:
        await db.execute(update(Stack).where(Stack.id == world.blog).values(type=StackType.COMPOSE))
        await db.commit()
    env.reader.container_list = [
        {"Id": "c1", "Labels": {"com.docker.compose.project": "blog"}},
        {"Id": "c2", "Labels": {"com.docker.compose.project": "other"}},
    ]
    await world.user("otto", (Role.OPERATOR, world.blog))
    await login(env.api, "otto")
    assert (await env.api.post(f"/api/stacks/{world.blog}/restart")).status_code == 204
    assert env.cli.calls == ["restart-containers c1"]


async def test_failed_restart_is_reported(env: Env, world: World) -> None:
    await world.user("otto", (Role.OPERATOR, world.shop))
    await login(env.api, "otto")

    async def fail(name: str) -> Result:
        return Result(("docker",), 1, "", "no such service")

    env.cli.restart_service = fail  # type: ignore[method-assign]
    response = await env.api.post("/api/services/svc1/restart")
    assert response.status_code == 502
    assert response.json()["detail"] == "no such service"
    [entry] = await world.audit("service.restart")
    assert entry.outcome == Outcome.FAILED


async def test_security_headers(env: Env) -> None:
    response = await env.api.get("/api/health")
    policy = response.headers["content-security-policy"]
    assert "script-src 'self'" in policy
    assert "frame-ancestors 'none'" in policy
    assert response.headers["x-content-type-options"] == "nosniff"
    docs = await env.api.get("/api/docs")
    assert "content-security-policy" not in docs.headers


async def test_retention(world: World) -> None:
    long_ago = datetime.now(UTC) - timedelta(days=200)
    async with world.maker() as db:
        stack = await db.get(Stack, world.shop)
        assert stack is not None
        revisions = []
        for i in range(5):
            revision = Revision(
                stack_id=world.shop,
                commit=f"{i}" * 40,
                tree=f"{i}" * 40,
                committed_at=long_ago,
                created_at=long_ago + timedelta(minutes=i),
            )
            db.add(revision)
            revisions.append(revision)
        await db.flush()
        stack.last_good_revision_id = revisions[0].id
        for i, revision in enumerate(revisions[:4]):
            db.add(
                Deployment(
                    stack_id=world.shop,
                    revision_id=revision.id,
                    status=DeploymentStatus.AWAITING_APPROVAL
                    if i == 3
                    else DeploymentStatus.SUCCEEDED,
                    trigger="git",
                    created_at=long_ago + timedelta(minutes=i),
                )
            )
        db.add(AuditEntry(action="old", outcome=Outcome.SUCCESS, at=long_ago - timedelta(days=200)))
        db.add(AuditEntry(action="new", outcome=Outcome.SUCCESS))
        await db.commit()
        first_revision = revisions[0].id

    async with world.maker() as db:
        pruned = await prune(db, Retention(timedelta(days=90), keep=2, audit=timedelta(days=365)))

    # The two newest deployments stay, one of them still waiting for approval.
    assert pruned.deployments == 2
    assert pruned.audit_entries == 1
    async with world.maker() as db:
        left = set(await db.scalars(select(Revision.commit).where(Revision.stack_id == world.shop)))
        assert await db.scalar(select(func.count()).select_from(Deployment)) == 2
        assert await db.get(Revision, first_revision) is not None
        assert list(await db.scalars(select(AuditEntry.action))) == ["new"]
    # Revision 1 lost its deployment. 0 is the last good one, 2 and 3 still have deployments,
    # and the fixture's own revision and 4 are the two newest.
    assert left == {"a" * 40, "0" * 40, "2" * 40, "3" * 40, "4" * 40}
    assert pruned.revisions == 1

    async with world.maker() as db:
        nothing = await prune(db, Retention(timedelta(0), keep=1, audit=timedelta(0)))
    assert nothing == type(nothing)()
