# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import contextlib
import os
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

MAX_OUTPUT = 64 * 1024


@dataclass(frozen=True)
class Result:
    args: tuple[str, ...]
    returncode: int
    stdout: str
    stderr: str

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def summary(self) -> str:
        text = (self.stderr.strip() or self.stdout.strip())[-4000:]
        return text or f"exit code {self.returncode}"


class CommandFailed(Exception):
    def __init__(self, result: Result) -> None:
        super().__init__(result.summary())
        self.result = result


class CommandTimeout(Exception):
    pass


async def _exec(
    args: Sequence[str],
    cwd: Path | None,
    env: Mapping[str, str] | None,
    stdin: bytes | None,
    timeout: float,
) -> tuple[int, bytes, bytes]:
    full_env = {k: v for k, v in os.environ.items() if k in ("PATH", "HOME", "LANG", "TZ")}
    full_env.update(env or {})
    proc = await asyncio.create_subprocess_exec(
        *args,
        cwd=cwd,
        env=full_env,
        stdin=asyncio.subprocess.PIPE if stdin is not None else asyncio.subprocess.DEVNULL,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    try:
        out, err = await asyncio.wait_for(proc.communicate(stdin), timeout)
    except TimeoutError:
        with contextlib.suppress(ProcessLookupError):
            proc.kill()
        await proc.wait()
        raise CommandTimeout(f"{args[0]} did not finish within {timeout:.0f}s") from None
    return proc.returncode or 0, out, err


async def run(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    stdin: bytes | None = None,
    timeout: float = 120,
    check: bool = True,
) -> Result:
    """Run a command without a shell. `env` is added to a minimal inherited environment."""
    code, out, err = await _exec(args, cwd, env, stdin, timeout)
    result = Result(
        args=tuple(args),
        returncode=code,
        stdout=out[-MAX_OUTPUT:].decode(errors="replace"),
        stderr=err[-MAX_OUTPUT:].decode(errors="replace"),
    )
    if check and not result.ok:
        raise CommandFailed(result)
    return result


async def run_binary(
    args: Sequence[str],
    *,
    cwd: Path | None = None,
    env: Mapping[str, str] | None = None,
    timeout: float = 120,
) -> bytes:
    """Like `run`, but returns raw stdout and always raises on a non-zero exit."""
    code, out, err = await _exec(args, cwd, env, None, timeout)
    if code:
        raise CommandFailed(
            Result(tuple(args), code, "", err[-MAX_OUTPUT:].decode(errors="replace"))
        )
    return out
