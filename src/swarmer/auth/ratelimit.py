# SPDX-License-Identifier: AGPL-3.0-or-later
import time
from collections import defaultdict, deque


class LoginThrottle:
    """Limits failed logins per key (client IP and username) within a sliding window.

    In memory, so limits are per server process. Good enough against online guessing; a
    shared store can come later if multiple replicas need a common limit.
    """

    def __init__(self, max_failures: int = 10, window_seconds: float = 900) -> None:
        self.max_failures = max_failures
        self.window = window_seconds
        self._failures: dict[str, deque[float]] = defaultdict(deque)

    def _prune(self, key: str, now: float) -> deque[float]:
        attempts = self._failures[key]
        while attempts and now - attempts[0] > self.window:
            attempts.popleft()
        if not attempts:
            del self._failures[key]
        return attempts

    def blocked(self, *keys: str) -> bool:
        now = time.monotonic()
        return any(len(self._prune(k, now)) >= self.max_failures for k in keys)

    def failed(self, *keys: str) -> None:
        now = time.monotonic()
        for key in keys:
            self._failures[key].append(now)

    def succeeded(self, *keys: str) -> None:
        for key in keys:
            self._failures.pop(key, None)
