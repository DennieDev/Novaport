# SPDX-License-Identifier: AGPL-3.0-or-later
from pathlib import Path
from typing import Any

import pytest

from novaport.deploy import cli
from novaport.models import StackType
from novaport.process import Result


@pytest.fixture
def commands(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    seen: list[list[str]] = []

    async def run(args: list[str], **_: Any) -> Result:
        seen.append(args)
        return Result(tuple(args), 0, "", "")

    monkeypatch.setattr(cli, "run", run)
    return seen


async def test_swarm_deploy_options(commands: list[list[str]], tmp_path: Path) -> None:
    docker = cli.SubprocessDockerCli(None, 10)
    await docker.apply(StackType.SWARM, "web", tmp_path, "c.yaml", 60, {})
    await docker.apply(
        StackType.SWARM, "web", tmp_path, "c.yaml", 60, {}, prune=False, pull_images=False
    )
    assert "--prune" in commands[0]
    assert "--resolve-image=always" in commands[0]
    assert "--prune" not in commands[1]
    assert "--resolve-image=never" in commands[1]


async def test_compose_deploy_options(commands: list[list[str]], tmp_path: Path) -> None:
    docker = cli.SubprocessDockerCli(None, 10)
    await docker.apply(StackType.COMPOSE, "web", tmp_path, "c.yaml", 60, {})
    await docker.apply(
        StackType.COMPOSE, "web", tmp_path, "c.yaml", 60, {}, prune=False, pull_images=False
    )
    assert "--remove-orphans" in commands[0]
    assert "--pull=always" in commands[0]
    assert "--remove-orphans" not in commands[1]
    assert "--pull=missing" in commands[1]
