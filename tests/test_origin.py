# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest
from starlette.datastructures import Headers

from swarmer.auth.ratelimit import LoginThrottle
from swarmer.server.origin import is_cross_site


@pytest.mark.parametrize(
    ("headers", "expected"),
    [
        ({}, False),
        ({"sec-fetch-site": "same-origin"}, False),
        ({"sec-fetch-site": "none"}, False),
        ({"sec-fetch-site": "same-site"}, True),
        ({"sec-fetch-site": "cross-site"}, True),
        ({"origin": "https://swarmer.example", "host": "swarmer.example"}, False),
        ({"origin": "https://evil.example", "host": "swarmer.example"}, True),
    ],
)
def test_is_cross_site(headers: dict[str, str], expected: bool) -> None:
    assert is_cross_site(Headers(headers)) is expected


def test_throttle_blocks_after_limit_and_resets_on_success() -> None:
    throttle = LoginThrottle(max_failures=2, window_seconds=60)
    throttle.failed("ip:1", "user:a")
    assert not throttle.blocked("ip:1")
    throttle.failed("ip:1", "user:a")
    assert throttle.blocked("ip:1")
    assert throttle.blocked("user:a")
    assert not throttle.blocked("ip:2")
    throttle.succeeded("ip:1", "user:a")
    assert not throttle.blocked("ip:1", "user:a")
