# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from swarmer import __version__
from swarmer.cli import main


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["version"]) == 0
    assert capsys.readouterr().out.strip() == __version__


def test_requires_command() -> None:
    with pytest.raises(SystemExit):
        main([])
