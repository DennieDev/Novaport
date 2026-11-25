# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from swarmer.crypto import Cipher
from swarmer.models import (
    Deployment,
    DeploymentStatus,
    DeployMode,
    Repository,
    Revision,
    Stack,
    StackType,
)
from swarmer.sops import age_recipient, generate_age_key
from swarmer.stacks.sync import Syncer
from tests.conftest import TEST_SECRET_KEY, make_settings
from tests.gitrepo import COMPOSE, Repo

pytestmark = pytest.mark.postgres


class Env:
    def __init__(self, url: str, origin: Repo) -> None:
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.settings = make_settings(database_url=SecretStr(url))
        self.syncer = Syncer(self.settings, self.engine, self.maker, Cipher(TEST_SECRET_KEY))
        self.origin = origin
        self.repo_id = uuid.uuid4()

    async def add_repository(self, **fields: object) -> None:
        async with self.maker() as db:
            db.add(
                Repository(
                    id=self.repo_id,
                    name="infra",
                    url=self.origin.url,
                    webhook_secret="x",
                    **({"branch": "main", "stacks_root": "stacks"} | fields),
                )
            )
            await db.commit()

    async def db(self) -> AsyncSession:
        return self.maker()

    async def stacks(self) -> dict[str, Stack]:
        async with self.maker() as db:
            return {s.name: s for s in await db.scalars(select(Stack))}

    async def revisions(self, stack: str) -> list[Revision]:
        async with self.maker() as db:
            return list(
                await db.scalars(
                    select(Revision)
                    .join(Stack, Revision.stack_id == Stack.id)
                    .where(Stack.name == stack)
                    .order_by(Revision.created_at)
                )
            )

    async def deployments(self) -> list[Deployment]:
        async with self.maker() as db:
            return list(await db.scalars(select(Deployment).order_by(Deployment.created_at)))

    async def repository(self) -> Repository:
        async with self.maker() as db:
            repo = await db.get(Repository, self.repo_id)
            assert repo is not None
            return repo


@pytest.fixture
async def env(clean_db: str, tmp_path: Path) -> AsyncIterator[Env]:
    origin = Repo(tmp_path / "origin")
    origin.write("stacks/web/compose.yaml", COMPOSE)
    origin.write("stacks/api/compose.yaml", COMPOSE)
    origin.write("stacks/api/swarmer.yaml", "deploy: auto\ntype: compose\n")
    origin.write("stacks/notes/README.md", "not a stack")
    origin.write("README.md", "infra")
    origin.commit("Initial stacks")
    e = Env(clean_db, origin)
    yield e
    await e.engine.dispose()


async def test_first_sync_creates_stacks_and_revisions(env: Env) -> None:
    await env.add_repository(default_deploy_mode=DeployMode.APPROVE)
    result = await env.syncer.sync(env.repo_id)

    assert result.error is None
    assert len(result.new_revisions) == 2
    stacks = await env.stacks()
    assert set(stacks) == {"web", "api"}
    assert stacks["api"].type == StackType.COMPOSE
    assert stacks["api"].deploy_mode == DeployMode.AUTO
    assert stacks["web"].deploy_mode == DeployMode.APPROVE

    deployments = await env.deployments()
    statuses = sorted(d.status for d in deployments)
    assert statuses == [DeploymentStatus.AWAITING_APPROVAL, DeploymentStatus.PENDING]

    repo = await env.repository()
    assert repo.last_commit == env.origin.git("rev-parse", "HEAD").strip()
    assert repo.last_error is None


async def test_unchanged_repository_creates_nothing(env: Env) -> None:
    await env.add_repository()
    await env.syncer.sync(env.repo_id)
    env.origin.write("README.md", "changed outside any stack")
    env.origin.commit("Docs")
    result = await env.syncer.sync(env.repo_id)
    assert result.new_revisions == []


async def test_only_changed_stack_gets_a_revision(env: Env) -> None:
    await env.add_repository()
    await env.syncer.sync(env.repo_id)
    env.origin.write("stacks/web/compose.yaml", COMPOSE.replace("alpine", "1.29"))
    sha = env.origin.commit("Pin nginx")

    result = await env.syncer.sync(env.repo_id)
    assert len(result.new_revisions) == 1
    web = await env.revisions("web")
    assert len(web) == 2
    assert web[-1].commit == sha
    assert web[-1].message == "Pin nginx"
    assert len(await env.revisions("api")) == 1


async def test_newer_revision_supersedes_waiting_deployment(env: Env) -> None:
    await env.add_repository(default_deploy_mode=DeployMode.APPROVE)
    await env.syncer.sync(env.repo_id)
    env.origin.write("stacks/web/compose.yaml", COMPOSE.replace("alpine", "1.29"))
    env.origin.commit("Pin nginx")
    await env.syncer.sync(env.repo_id)

    stacks = await env.stacks()
    web = [d for d in await env.deployments() if d.stack_id == stacks["web"].id]
    assert [d.status for d in web] == [
        DeploymentStatus.CANCELLED,
        DeploymentStatus.AWAITING_APPROVAL,
    ]
    assert web[0].error == "Superseded by a newer deployment."


async def test_manual_mode_queues_nothing(env: Env) -> None:
    env.origin.write("stacks/api/swarmer.yaml", "deploy: manual\n")
    env.origin.commit("Manual api")
    await env.add_repository(default_deploy_mode=DeployMode.MANUAL)
    result = await env.syncer.sync(env.repo_id)
    assert result.queued_deployments == 0
    assert await env.deployments() == []


async def test_invalid_settings_block_new_revisions(env: Env) -> None:
    await env.add_repository()
    await env.syncer.sync(env.repo_id)
    env.origin.write("stacks/web/swarmer.yaml", "deploy: sometimes\n")
    env.origin.write("stacks/web/compose.yaml", COMPOSE.replace("alpine", "1.29"))
    env.origin.commit("Typo")

    result = await env.syncer.sync(env.repo_id)
    assert result.new_revisions == []
    web = (await env.stacks())["web"]
    assert web.config_error is not None
    assert "deploy" in web.config_error


async def test_removed_folder_marks_stack_removed(env: Env) -> None:
    await env.add_repository()
    await env.syncer.sync(env.repo_id)
    env.origin.remove("stacks/web")
    env.origin.commit("Drop web")
    await env.syncer.sync(env.repo_id)

    stacks = await env.stacks()
    assert stacks["web"].removed_at is not None
    assert stacks["api"].removed_at is None


async def test_invalid_folder_name_is_reported(env: Env) -> None:
    env.origin.write("stacks/Bad Name/compose.yaml", COMPOSE)
    env.origin.commit("Bad folder")
    await env.add_repository()
    await env.syncer.sync(env.repo_id)
    repo = await env.repository()
    assert repo.last_error is not None
    assert "Bad Name" in repo.last_error


async def test_unreachable_remote_records_error(env: Env, tmp_path: Path) -> None:
    env.origin = Repo(tmp_path / "empty")
    env.origin.path = tmp_path / "missing"
    await env.add_repository()
    result = await env.syncer.sync(env.repo_id)
    assert result.error is not None
    assert (await env.repository()).last_error == result.error


async def test_empty_repository_gets_a_first_commit(env: Env, tmp_path: Path) -> None:
    age_key = generate_age_key()
    env.settings = make_settings(database_url=env.settings.database_url, age_key=SecretStr(age_key))
    env.syncer = Syncer(env.settings, env.engine, env.maker, Cipher(TEST_SECRET_KEY))
    env.origin = Repo(tmp_path / "empty")
    await env.add_repository()

    result = await env.syncer.sync(env.repo_id)

    assert result.error is None
    assert result.commit == env.origin.head("main")
    assert env.origin.git("ls-tree", "-r", "--name-only", "main").split() == [
        ".sops.yaml",
        "stacks/.gitkeep",
    ]
    assert age_recipient(age_key) in env.origin.show(".sops.yaml", "main")
    assert env.origin.log("%an <%ae>", "main") == "Swarmer <swarmer@localhost>"
    assert (await env.repository()).last_error is None

    again = await env.syncer.sync(env.repo_id)
    assert again.commit == result.commit


async def test_empty_repository_without_age_key_gets_only_the_stacks_folder(
    env: Env, tmp_path: Path
) -> None:
    env.origin = Repo(tmp_path / "empty")
    await env.add_repository()
    await env.syncer.sync(env.repo_id)
    assert env.origin.git("ls-tree", "-r", "--name-only", "main").split() == ["stacks/.gitkeep"]


async def test_missing_branch_in_non_empty_repository_is_left_alone(env: Env) -> None:
    await env.add_repository(branch="production")
    result = await env.syncer.sync(env.repo_id)
    assert result.error is not None
    assert "production" in result.error
    assert "production" not in env.origin.git("branch", "--list")
