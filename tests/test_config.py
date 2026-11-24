# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import timedelta

import pytest
from pydantic import ValidationError

from novaport.config import Settings


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("90", timedelta(seconds=90)),
        ("30s", timedelta(seconds=30)),
        ("5m", timedelta(minutes=5)),
        ("6h", timedelta(hours=6)),
        ("90d", timedelta(days=90)),
        ("2w", timedelta(weeks=2)),
        ("1h30m", timedelta(hours=1, minutes=30)),
        ("0d", timedelta()),
        ("PT6H", timedelta(hours=6)),
        ("01:00:00", timedelta(hours=1)),
    ],
)
def test_durations(monkeypatch: pytest.MonkeyPatch, value: str, expected: timedelta) -> None:
    monkeypatch.setenv("NOVAPORT_UPDATE_INTERVAL", value)
    assert Settings(_env_file=None).update_interval == expected  # type: ignore[call-arg]


@pytest.mark.parametrize("value", ["soon", "5 minutes", "h5", "5x"])
def test_bad_durations_are_refused(monkeypatch: pytest.MonkeyPatch, value: str) -> None:
    monkeypatch.setenv("NOVAPORT_DRIFT_INTERVAL", value)
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]
