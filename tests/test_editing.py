# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest
from pydantic import SecretStr
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.orm import selectinload

from novaport.crypto import Cipher
from novaport.git import GitMirror, PushRejected
from novaport.models import Deployment, DeployMode, Repository, Revision, Stack, User
from novaport.process import Result
from novaport.sops import Sops, generate_age_key
from novaport.stacks import editing
from novaport.stacks.editing import EditConflict, EditError, StackEditor, check_path
from novaport.stacks.sync import Syncer
from tests.conftest import SOPS_BINARY, TEST_SECRET_KEY, make_settings
from tests.gitrepo import COMPOSE, Repo
from tests.test_pipeline import FakeCli

pytestmark = pytest.mark.postgres

USER = User(id=uuid.uuid4(), username="dana", email="dana@example.com", is_admin=True)


class Env:
    def __init__(self, url: str, origin: Repo) -> None:
        self.origin = origin
        self.engine = create_async_engine(url)
        self.maker = async_sessionmaker(self.engine, expire_on_commit=False)
        self.age_key = generate_age_key()
        self.settings = make_settings(
            database_url=SecretStr(url), commit_name="Novaport", commit_email="np@example.com"
        )
        cipher = Cipher(TEST_SECRET_KEY)
        self.syncer = Syncer(self.settings, self.engine, self.maker, cipher)
        self.cli = FakeCli()
        self.sops = Sops(SOPS_BINARY or "sops", self.age_key)
        self.editor = StackEditor(
            self.settings, self.engine, self.maker, self.syncer, cipher, self.sops, self.cli
        )

    async def setup(self, **repo_fields: Any) -> None:
        async with self.maker() as db:
            repo = Repository(
                name="infra",
                url=self.origin.url,
                webhook_secret="x",
                default_deploy_mode=DeployMode.AUTO,
                **repo_fields,
            )
            db.add(repo)
            await db.commit()
            self.repo_id = repo.id
        await self.syncer.sync(self.repo_id)

    async def stack(self, name: str = "web") -> Stack:
        async with self.maker() as db:
            stack = await db.scalar(
                select(Stack).options(selectinload(Stack.repository)).where(Stack.name == name)
            )
            assert stack is not None
            return stack

    async def revisions(self) -> list[Revision]:
        async with self.maker() as db:
            return list(await db.scalars(select(Revision).order_by(Revision.created_at)))


@pytest.fixture
async def env(clean_db: str, tmp_path: Path) -> AsyncIterator[Env]:
    origin = Repo(tmp_path / "origin")
    origin.write("stacks/web/compose.yaml", COMPOSE)
    origin.write("stacks/web/novaport.yaml", "deploy: auto\n")
    origin.write("stacks/web/conf/nginx.conf", "server {}\n")
    origin.write("stacks/api/compose.yaml", COMPOSE)
    origin.commit("Initial")
    e = Env(clean_db, origin)
    yield e
    await e.engine.dispose()


async def test_files_lists_the_stack_folder(env: Env) -> None:
    await env.setup()
    files = await env.editor.files(await env.stack())
    assert files.commit == env.origin.head()
    assert [f.path for f in files.files] == ["compose.yaml", "conf/nginx.conf", "novaport.yaml"]
    assert files.files[0].content == COMPOSE


async def test_save_commits_pushes_and_deploys(env: Env) -> None:
    await env.setup()
    stack = await env.stack()
    base = env.origin.head()
    new = COMPOSE.replace("alpine", "1.29")

    result = await env.editor.save(stack, USER, base, {"compose.yaml": new}, "Pin nginx")

    assert env.origin.head() == result.commit
    assert env.origin.show("stacks/web/compose.yaml") == new
    assert env.origin.log("%an <%ae>|%cn <%ce>|%s") == (
        "dana <dana@example.com>|Novaport <np@example.com>|Pin nginx"
    )
    assert env.cli.calls == ["config swarm web compose.yaml"]
    assert result.sync is not None
    assert result.sync.queued_deployments == 1
    assert (await env.revisions())[-1].commit == result.commit
    async with env.maker() as db:
        # Two from the first sync (web and api), one for the new web revision.
        assert len(list(await db.scalars(select(Deployment)))) == 3


async def test_add_and_delete_files(env: Env) -> None:
    await env.setup()
    base = env.origin.head()
    await env.editor.save(
        await env.stack(),
        USER,
        base,
        {"conf/nginx.conf": None, ".env": "MODE=prod\n"},
        "Swap config",
    )
    files = env.origin.git("ls-tree", "-r", "--name-only", "HEAD", "stacks/web").split()
    assert files == ["stacks/web/.env", "stacks/web/compose.yaml", "stacks/web/novaport.yaml"]


async def test_changes_elsewhere_in_the_repo_are_not_conflicts(env: Env) -> None:
    await env.setup()
    base = env.origin.head()
    env.origin.write("stacks/api/compose.yaml", COMPOSE.replace("alpine", "1.28"))
    other = env.origin.commit("Change api")

    result = await env.editor.save(await env.stack(), USER, base, {".env": "A=1\n"}, "Add env")
    assert env.origin.git("rev-parse", f"{result.commit}^").strip() == other
    assert "1.28" in env.origin.show("stacks/api/compose.yaml")


async def test_conflicting_change_is_refused_with_three_versions(env: Env) -> None:
    await env.setup()
    base = env.origin.head()
    env.origin.write("stacks/web/compose.yaml", COMPOSE.replace("alpine", "1.28"))
    theirs = env.origin.commit("Someone else")

    with pytest.raises(EditConflict) as exc:
        await env.editor.save(
            await env.stack(), USER, base, {"compose.yaml": COMPOSE.replace("alpine", "1.29")}, "x"
        )
    assert exc.value.tip == theirs
    [conflict] = exc.value.conflicts
    assert conflict.path == "compose.yaml"
    assert conflict.base == COMPOSE
    assert conflict.theirs is not None
    assert "1.28" in conflict.theirs
    assert conflict.yours is not None
    assert "1.29" in conflict.yours
    assert env.origin.head() == theirs


async def test_invalid_compose_is_not_committed(env: Env) -> None:
    await env.setup()
    env.cli.config_result = Result(("docker",), 1, "", "services.web.image must be a string")
    base = env.origin.head()
    with pytest.raises(EditError, match="image must be a string"):
        await env.editor.save(await env.stack(), USER, base, {"compose.yaml": "x"}, "Break")
    assert env.origin.head() == base


async def test_validation_can_be_skipped(env: Env) -> None:
    await env.setup()
    env.cli.config_result = Result(("docker",), 1, "", "nope")
    base = env.origin.head()
    await env.editor.save(
        await env.stack(), USER, base, {"notes.md": "hi"}, "Notes", validate=False
    )
    assert env.origin.head() != base


@pytest.mark.parametrize("path", ["../x", "/etc/passwd", ".git/config", "a//b", "secrets.env"])
def test_unsafe_paths(path: str) -> None:
    with pytest.raises(EditError):
        check_path(path)


async def test_push_race_is_retried_on_top_of_the_new_commit(
    env: Env, monkeypatch: pytest.MonkeyPatch
) -> None:
    await env.setup()
    base = env.origin.head()
    real_push = GitMirror.push
    calls = 0

    async def racing_push(self: GitMirror, commit: str, branch: str, credentials: Any) -> None:
        nonlocal calls
        calls += 1
        if calls == 1:
            env.origin.write("README.md", "someone was faster")
            env.origin.commit("Docs")
            raise PushRejected("[rejected] main -> main (fetch first)")
        await real_push(self, commit, branch, credentials)

    monkeypatch.setattr(GitMirror, "push", racing_push)
    result = await env.editor.save(await env.stack(), USER, base, {".env": "A=1\n"}, "Env")
    assert calls == 2
    assert env.origin.head() == result.commit
    assert env.origin.show("README.md") == "someone was faster"


async def test_pull_request_mode(env: Env, monkeypatch: pytest.MonkeyPatch) -> None:
    env.origin.write("stacks/web/novaport.yaml", "deploy: auto\nedits: pr\n")
    env.origin.commit("Use PRs")
    await env.setup(forge="github", forge_token=Cipher(TEST_SECRET_KEY).encrypt("ghp_x"))
    opened: dict[str, Any] = {}

    async def fake_open(forge: Any, api_url: Any, token: str, repo_url: str, **kw: Any) -> str:
        opened.update(kw, token=token, forge=str(forge))
        return "https://github.com/acme/infra/pull/7"

    monkeypatch.setattr(editing, "open_pull_request", fake_open)
    base = env.origin.head()
    result = await env.editor.save(await env.stack(), USER, base, {".env": "A=1\n"}, "Env\n\nBody")

    assert result.pull_request_url == "https://github.com/acme/infra/pull/7"
    assert env.origin.head() == base
    assert env.origin.head(f"refs/heads/{result.branch}") == result.commit
    assert opened["head"] == result.branch
    assert opened["base"] == "main"
    assert opened["title"] == "Env"
    assert opened["token"] == "ghp_x"
    assert result.sync is None


async def test_pull_request_mode_needs_a_token(env: Env) -> None:
    env.origin.write("stacks/web/novaport.yaml", "edits: pr\n")
    env.origin.commit("Use PRs")
    await env.setup(forge="github")
    with pytest.raises(EditError, match="forge token"):
        await env.editor.save(await env.stack(), USER, env.origin.head(), {".env": "A=1\n"}, "Env")


@pytest.mark.sops
async def test_secrets_round_trip(env: Env) -> None:
    await env.setup()
    stack = await env.stack()
    base = env.origin.head()
    result = await env.editor.save_secrets(
        stack, USER, base, {"DB_PASSWORD": "hunter2", "TOKEN": "a=b"}, [], "Add secrets"
    )
    stored = env.origin.show("stacks/web/secrets.env")
    assert "hunter2" not in stored
    assert "DB_PASSWORD=ENC[" in stored

    commit, values = await env.editor.read_secrets(stack)
    assert commit == result.commit
    assert values == {"DB_PASSWORD": "hunter2", "TOKEN": "a=b"}

    await env.editor.save_secrets(stack, USER, commit, {"TOKEN": "new"}, ["DB_PASSWORD"], "Rotate")
    _, values = await env.editor.read_secrets(stack)
    assert values == {"TOKEN": "new"}

    files = await env.editor.files(stack)
    assert "secrets.env" not in [f.path for f in files.files]


@pytest.mark.sops
async def test_sops_config_recipients_are_used(env: Env) -> None:
    other = Sops(SOPS_BINARY or "sops", generate_age_key())
    env.origin.write(
        ".sops.yaml",
        "creation_rules:\n"
        "  - path_regex: secrets\\.env$\n"
        f"    age: {env.sops.recipient},{other.recipient}\n",
    )
    env.origin.commit("SOPS config")
    await env.setup()
    await env.editor.save_secrets(
        await env.stack(), USER, env.origin.head(), {"A": "1"}, [], "Secret"
    )
    # The second recipient (a person's own key) can decrypt it too.
    stored = env.origin.show("stacks/web/secrets.env").encode()
    assert await other.decrypt(stored) == {"A": "1"}


async def test_validation_rewrites_environment_secrets(env: Env) -> None:
    await env.setup()
    compose = (
        "services:\n  web:\n    image: nginx\n    secrets: [db]\n"
        "secrets:\n  db:\n    environment: DB_PASSWORD\n"
    )
    await env.editor.save(
        await env.stack(), USER, env.origin.head(), {"compose.yaml": compose}, "x"
    )
    # `docker stack config` rejects `environment:` secrets, so validation sees the rewrite.
    assert env.cli.calls == ["config swarm web .novaport-source.yaml"]
