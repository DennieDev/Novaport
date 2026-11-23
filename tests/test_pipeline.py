# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json
import shutil
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
from novaport.deploy.compose import secret_name
from novaport.deploy.pipeline import Pipeline
from novaport.docker.views import STACK_LABEL
from novaport.jobs import Jobs
from novaport.models import (
    Deployment,
    DeploymentEvent,
    DeploymentStatus,
    DeployMode,
    EventType,
    Repository,
    Stack,
    StackType,
)
from novaport.notifications import Event
from novaport.process import Result
from novaport.registries.client import Login
from novaport.sops import Sops, generate_age_key
from novaport.stacks.sync import Syncer, mirror_for, mirror_path
from tests.conftest import SOPS_BINARY, TEST_SECRET_KEY, make_settings
from tests.fakes import FakeAgents, FakeReader
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
    apply_env: dict[str, str] = field(default_factory=dict)
    secret_files: dict[str, str] = field(default_factory=dict)
    removed_secrets: list[str] = field(default_factory=list)

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
        self,
        stack_type: StackType,
        name: str,
        folder: Path,
        compose_file: str,
        wait_seconds: int,
        env: Mapping[str, str],
    ) -> Result:
        self.calls.append(f"apply {stack_type} {name} {compose_file}")
        self.rendered = yaml.safe_load((folder / compose_file).read_text())
        self.apply_env = dict(env)
        secrets_dir = folder / ".novaport-secrets"
        if secrets_dir.is_dir():
            self.secret_files = {f.name: f.read_text() for f in secrets_dir.iterdir()}
        return self.apply_result

    async def remove_secrets(self, names: list[str]) -> Result:
        self.calls.append(f"remove-secrets {' '.join(sorted(names))}")
        self.removed_secrets = list(names)
        return _ok()

    async def restart_service(self, name: str) -> Result:
        self.calls.append(f"restart-service {name}")
        return _ok()

    async def restart_containers(self, ids: list[str]) -> Result:
        self.calls.append(f"restart-containers {' '.join(ids)}")
        return _ok()


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
    assert [e.stage for e in d.events][:4] == ["resolve", "validate", "preflight", "apply"]
    assert d.events[-1].message == "All services are running."


async def test_success_records_baseline_and_notifies(env: Env) -> None:
    events: list[Event] = []
    env.pipeline.notify = events.append
    deployment_id = await env.setup()
    assert await env.pipeline.run(deployment_id) == DeploymentStatus.SUCCEEDED

    d = await env.deployment(deployment_id)
    assert d.stack.deployed_spec is not None
    assert set(d.stack.deployed_spec) == {"web"}
    assert d.stack.drift == []
    [event] = events
    assert event.type == EventType.DEPLOY_SUCCEEDED
    assert event.link == f"/deployments/{deployment_id}"


async def test_failure_notifies(env: Env) -> None:
    events: list[Event] = []
    env.pipeline.notify = events.append
    env.cli.config_result = Result(("docker",), 1, "", "bad compose")
    await env.pipeline.run(await env.setup())
    assert [e.type for e in events] == [EventType.DEPLOY_FAILED]
    assert "bad compose" in events[0].text


async def test_registry_logins_reach_the_deploy(env: Env) -> None:
    seen: dict[str, str] = {}
    original = env.cli.apply

    async def apply(*args: Any) -> Result:
        config = Path(args[-1]["DOCKER_CONFIG"]) / "config.json"
        seen.update(json.loads(config.read_text())["auths"])
        return await original(*args)

    async def logins() -> dict[str, Login]:
        return {"ghcr.io": Login("bot", "pw")}

    env.cli.apply = apply  # type: ignore[method-assign]
    env.pipeline.logins = logins
    status = await env.pipeline.run(await env.setup())
    assert status == DeploymentStatus.SUCCEEDED
    assert list(seen) == ["ghcr.io"]


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


async def test_missing_bind_mount_stops_before_apply(env: Env) -> None:
    env.origin.write(
        "stacks/web/compose.yaml", COMPOSE + "    volumes:\n      - /srv/missing:/data\n"
    )
    env.origin.commit("Mount data")
    env.pipeline.agents = lambda: FakeAgents({"node1": set()})
    deployment_id = await env.setup()

    assert await env.pipeline.run(deployment_id) == DeploymentStatus.FAILED
    assert all(not c.startswith("apply") for c in env.cli.calls)
    d = await env.deployment(deployment_id)
    assert d.stage == "preflight"
    assert d.error == "web_web: bind mount source /srv/missing does not exist on mgr-1."


SECRET_COMPOSE = """services:
  web:
    image: nginx:alpine
    secrets: [db_password]
secrets:
  db_password:
    environment: DB_PASSWORD
"""


@pytest.mark.sops
async def test_sops_secrets_become_hashed_swarm_secrets(env: Env) -> None:
    sops = Sops(SOPS_BINARY or "sops", generate_age_key())
    encrypted = await sops.encrypt({"DB_PASSWORD": "hunter2"}, [str(sops.recipient)])
    env.origin.write("stacks/web/compose.yaml", SECRET_COMPOSE)
    env.origin.write("stacks/web/secrets.env", encrypted.decode())
    env.origin.commit("Use a secret")
    env.pipeline.sops = lambda: sops
    env.reader.secret_list = [
        {"Spec": {"Name": "web_db_password_0badc0de", "Labels": {STACK_LABEL: "web"}}},
        {"Spec": {"Name": "other_db_password_0badc0de", "Labels": {STACK_LABEL: "other"}}},
    ]
    deployment_id = await env.setup()

    assert await env.pipeline.run(deployment_id) == DeploymentStatus.SUCCEEDED
    secret = env.cli.rendered["secrets"]["db_password"]
    assert secret["name"] == secret_name("web", "db_password", "hunter2")
    assert secret["file"] == "./.novaport-secrets/DB_PASSWORD"
    assert env.cli.secret_files == {"DB_PASSWORD": "hunter2"}
    assert env.cli.calls[0] == "config swarm web .novaport-source.yaml"
    assert env.cli.removed_secrets == ["web_db_password_0badc0de"]


async def test_secrets_without_age_key_fail_clearly(env: Env) -> None:
    env.origin.write("stacks/web/secrets.env", "DB_PASSWORD=ENC[AES256_GCM,data:x]\n")
    env.origin.commit("Secret")
    deployment_id = await env.setup()
    assert await env.pipeline.run(deployment_id) == DeploymentStatus.FAILED
    d = await env.deployment(deployment_id)
    assert d.error == "secrets.env is present, but NOVAPORT_AGE_KEY is not set."


async def test_missing_secret_variable(env: Env) -> None:
    env.origin.write("stacks/web/compose.yaml", SECRET_COMPOSE)
    env.origin.commit("Secret without value")
    deployment_id = await env.setup()
    assert await env.pipeline.run(deployment_id) == DeploymentStatus.FAILED
    d = await env.deployment(deployment_id)
    assert d.error == "Secret 'db_password' reads DB_PASSWORD, which is not in secrets.env or .env."


async def test_missing_mirror_is_fetched_again(env: Env) -> None:
    deployment_id = await env.setup()
    async with env.maker() as db:
        stack = await db.scalar(select(Stack).options(selectinload(Stack.repository)))
        assert stack is not None
    # A fresh data directory, as after a restart without a persistent volume.
    shutil.rmtree(mirror_path(env.settings, stack.repository_id))
    env.pipeline.ensure_commit = lambda s, commit: env.syncer.ensure_commit(s.repository, commit)
    assert await env.pipeline.run(deployment_id) == DeploymentStatus.SUCCEEDED
