# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import uuid
from collections.abc import AsyncIterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from novaport.crypto import Cipher
from novaport.deploy.pipeline import Pipeline
from novaport.jobs import Jobs
from novaport.models import (
    Deployment,
    DeploymentEvent,
    DeploymentStatus,
    DeployMode,
    Repository,
    Stack,
    StackType,
)
from novaport.process import Result
from novaport.stacks.sync import Syncer, mirror_for
from tests.conftest import TEST_SECRET_KEY, make_settings
from tests.fakes import FakeReader
from tests.gitrepo import COMPOSE, Repo

pytestmark = pytest.mark.postgres


def _ok(stdout: str = "") -> Result:
    return Result(("docker",), 0, stdout, "")


@dataclass
class FakeCli:
    config_result: Result | None = None
    apply_result: Result = field(default_factory=lambda: _ok("Creating service shop_web"))
    calls: list[str] = field(default_factory=list)
    env_seen: dict[str, str] = field(default_factory=dict)
    rendered: dict[str, Any] = field(default_factory=dict)

    async def config(
        self,
        stack_type: StackType,
        name: str,
        folder: Path,
        compose_file: str,
        env: Mapping[str, str],
    ) -> Result:
        self.calls.append(f"config {stack_type} {name} {compose_file}")
        self.env_seen = dict(env)
        if self.config_result is not None:
            return self.config_result
        return _ok((folder / compose_file).read_text())

    async def apply(
        self, stack_type: StackType, name: str, folder: Path, compose_file: str, wait_seconds: int
    ) -> Result:
        self.calls.append(f"apply {stack_type} {name} {compose_file}")
        self.rendered = yaml.safe_load((folder / compose_file).read_text())
        return self.apply_result


def converged_service(stack: str) -> dict[str, Any]:
    return {
        "ID": "svc-new",
        "Spec": {
            "Name": f"{stack}_web",
            "Labels": {"com.docker.stack.namespace": stack},
            "Mode": {"Replicated": {"Replicas": 1}},
        },
        "ServiceStatus": {"RunningTasks": 1, "DesiredTasks": 1},
    }


class Env:
    def __init__(self, url: str, origin: Repo) -> None:
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = make_settings(
            database_url=SecretStr(url), deploy_timeout=timedelta(seconds=2)
        )
        self.syncer = Syncer(self.settings, self.engine, self.maker, Cipher(TEST_SECRET_KEY))
        self.origin = origin
        self.cli = FakeCli()
        self.reader = FakeReader()
        self.reader.service_list = [converged_service("web")]
        self.reader.task_list = []
        self.pipeline = Pipeline(
            self.settings,
            self.maker,
            self.cli,
            reader=lambda: self.reader,
            mirror=lambda stack: mirror_for(self.settings, stack.repository),
        )

    async def setup(self) -> uuid.UUID:
        async with self.maker() as db:
            repo = Repository(
                name="infra",
                url=self.origin.url,
                webhook_secret="x",
                default_deploy_mode=DeployMode.AUTO,
            )
            db.add(repo)
            await db.commit()
            repo_id = repo.id
        await self.syncer.sync(repo_id)
        async with self.maker() as db:
            deployment = await db.scalar(select(Deployment))
            assert deployment is not None
            return deployment.id

    async def deployment(self, deployment_id: uuid.UUID) -> Deployment:
        async with self.maker() as db:
            d = await db.scalar(
                select(Deployment)
                .options(selectinload(Deployment.stack), selectinload(Deployment.events))
                .where(Deployment.id == deployment_id)
            )
            assert d is not None
            return d


@pytest.fixture
async def env(clean_db: str, tmp_path: Path) -> AsyncIterator[Env]:
    origin = Repo(tmp_path / "origin")
    origin.write("stacks/web/compose.yaml", COMPOSE + "    environment:\n      MODE: ${MODE}\n")
    origin.write("stacks/web/.env", "MODE=prod\n")
    origin.commit("Add web")
    e = Env(clean_db, origin)
    yield e
    await e.engine.dispose()


async def test_successful_swarm_deploy(env: Env) -> None:
    deployment_id = await env.setup()
    status = await env.pipeline.run(deployment_id)

    assert status == DeploymentStatus.SUCCEEDED
    assert env.cli.calls == [
        "config swarm web compose.yaml",
        "apply swarm web .novaport-compose.yaml",
    ]
    assert env.cli.env_seen == {"MODE": "prod"}
    labels = env.cli.rendered["services"]["web"]["deploy"]["labels"]
    assert labels["novaport.stack"] == "web"
    assert len(labels["novaport.commit"]) == 40

    d = await env.deployment(deployment_id)
    assert d.stack.last_good_revision_id == d.revision_id
    assert d.finished_at is not None
    assert [e.stage for e in d.events][:3] == ["resolve", "validate", "apply"]
    assert d.events[-1].message == "All services are running."


async def test_validation_failure_stops_before_apply(env: Env) -> None:
    env.cli.config_result = Result(("docker",), 1, "", "services.web.image must be a string")
    deployment_id = await env.setup()
    status = await env.pipeline.run(deployment_id)

    assert status == DeploymentStatus.FAILED
    assert all(not c.startswith("apply") for c in env.cli.calls)
    d = await env.deployment(deployment_id)
    assert d.error == "services.web.image must be a string"
    assert d.stage == "validate"
    assert d.stack.last_good_revision_id is None


async def test_rejected_task_fails_fast_with_its_error(env: Env) -> None:
    env.reader.service_list = [
        {**converged_service("web"), "ServiceStatus": {"RunningTasks": 0, "DesiredTasks": 1}}
    ]
    env.reader.task_list = [
        {
            "ID": "t1",
            "ServiceID": "svc-new",
            "CreatedAt": (datetime.now(UTC) + timedelta(seconds=1)).isoformat(),
            "Status": {
                "State": "rejected",
                "Err": 'invalid mount config for type "bind": bind source path does not exist',
            },
        }
    ]
    deployment_id = await env.setup()
    status = await env.pipeline.run(deployment_id)

    assert status == DeploymentStatus.FAILED
    d = await env.deployment(deployment_id)
    assert d.error is not None
    assert "web_web: rejected: invalid mount config" in d.error


async def test_timeout_when_replicas_never_come_up(env: Env) -> None:
    env.reader.service_list = [
        {**converged_service("web"), "ServiceStatus": {"RunningTasks": 0, "DesiredTasks": 2}}
    ]
    deployment_id = await env.setup()
    assert await env.pipeline.run(deployment_id) == DeploymentStatus.FAILED
    d = await env.deployment(deployment_id)
    assert d.error is not None
    assert d.error.startswith("Timed out waiting for web_web: 0/2 running")


async def test_secrets_env_is_refused_for_now(env: Env) -> None:
    env.origin.write("stacks/web/secrets.env", "TOKEN=ENC[...]\n")
    env.origin.commit("Add secrets")
    deployment_id = await env.setup()
    assert await env.pipeline.run(deployment_id) == DeploymentStatus.FAILED
    d = await env.deployment(deployment_id)
    assert d.error is not None
    assert "secrets.env" in d.error


async def test_compose_stack_labels_containers(env: Env) -> None:
    env.origin.write("stacks/web/novaport.yaml", "type: compose\n")
    env.origin.commit("Compose")
    deployment_id = await env.setup()
    assert await env.pipeline.run(deployment_id) == DeploymentStatus.SUCCEEDED
    assert env.cli.env_seen == {}  # docker compose reads .env itself
    assert env.cli.rendered["services"]["web"]["labels"]["novaport.stack"] == "web"


async def test_only_pending_deployments_run(env: Env) -> None:
    deployment_id = await env.setup()
    async with env.maker() as db:
        d = await db.get(Deployment, deployment_id)
        assert d is not None
        d.status = DeploymentStatus.CANCELLED
        await db.commit()
    assert await env.pipeline.run(deployment_id) == DeploymentStatus.CANCELLED
    assert env.cli.calls == []
    async with env.maker() as db:
        assert await db.scalar(select(DeploymentEvent)) is None
    async with env.maker() as db:
        stack = await db.scalar(select(Stack))
        assert stack is not None


async def test_worker_runs_pending_and_fails_orphaned(env: Env) -> None:
    deployment_id = await env.setup()
    jobs = Jobs(env.engine, env.maker, env.syncer, env.pipeline)
    await jobs.run_deployments()
    await asyncio.gather(*jobs._deploying.values())
    assert (await env.deployment(deployment_id)).status == DeploymentStatus.SUCCEEDED

    async with env.maker() as db:
        d = await db.get(Deployment, deployment_id)
        assert d is not None
        d.status = DeploymentStatus.RUNNING
        await db.commit()
    await jobs.run_deployments()
    await asyncio.gather(*jobs._deploying.values())
    orphan = await env.deployment(deployment_id)
    assert orphan.status == DeploymentStatus.FAILED
    assert orphan.error is not None
    assert "stopped while this deployment was running" in orphan.error
