# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watches a freshly deployed Swarm stack until every service is running, or something fails."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from swarmer.docker.client import ClusterReader
from swarmer.docker.views import STACK_LABEL, Obj

FAILED_UPDATE_STATES = {"paused", "rollback_started", "rollback_paused", "rollback_completed"}
JOB_MODES = {"ReplicatedJob", "GlobalJob"}


@dataclass
class ServiceState:
    name: str
    converged: bool
    detail: str


@dataclass
class FailedTask:
    name: str
    node_id: str | None
    container_id: str | None


@dataclass
class Outcome:
    ok: bool
    services: list[ServiceState] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    # The latest failed container of each failing service, so its output can be shown.
    failed: list[FailedTask] = field(default_factory=list)


def _parse_time(value: str | None) -> datetime | None:
    if not value:
        return None
    # Docker uses nanosecond precision; Python handles up to microseconds.
    head, _, tail = value.partition(".")
    if tail:
        digits = tail.rstrip("Z")[:6]
        value = f"{head}.{digits}+00:00"
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


def service_state(raw: Obj) -> ServiceState:
    spec = raw.get("Spec", {})
    name = spec.get("Name", raw.get("ID", "?"))
    update = (raw.get("UpdateStatus") or {}).get("State")
    if update in FAILED_UPDATE_STATES:
        message = (raw.get("UpdateStatus") or {}).get("Message", "")
        return ServiceState(name, False, f"update {update}: {message}".strip())
    if update == "updating":
        return ServiceState(name, False, "updating")
    if JOB_MODES & set(spec.get("Mode", {})):
        return ServiceState(name, True, "job")
    status = raw.get("ServiceStatus") or {}
    running, desired = status.get("RunningTasks", 0), status.get("DesiredTasks", 0)
    return ServiceState(name, running >= desired, f"{running}/{desired} running")


def _task_errors(
    tasks: list[Obj], since: datetime, names: dict[str, str]
) -> tuple[list[str], bool]:
    """Errors of tasks created since the deploy started, and whether any was rejected.

    A rejected task can't be scheduled at all (a missing bind mount, an unknown network), so
    waiting longer won't help.
    """
    errors: list[str] = []
    rejected = False
    for task in tasks:
        created = _parse_time(task.get("CreatedAt"))
        if created is None or created < since:
            continue
        status = task.get("Status", {})
        if status.get("Err"):
            service = names.get(task.get("ServiceID", ""), task.get("ServiceID", "?"))
            message = f"{service}: {status.get('State')}: {status['Err']}"
            if message not in errors:
                errors.append(message)
            rejected = rejected or status.get("State") == "rejected"
    return errors, rejected


def _latest_failures(
    tasks: list[Obj], since: datetime, names: dict[str, str], service_ids: set[str]
) -> list[FailedTask]:
    latest: dict[str, Obj] = {}
    for task in tasks:
        created = _parse_time(task.get("CreatedAt"))
        sid = task.get("ServiceID", "")
        if created is None or created < since or sid not in service_ids:
            continue
        if task.get("Status", {}).get("State") != "failed":
            continue
        current = latest.get(sid)
        if current is None or task.get("Status", {}).get("Timestamp", "") > current.get(
            "Status", {}
        ).get("Timestamp", ""):
            latest[sid] = task
    return [
        FailedTask(
            name=f"{names.get(sid, sid)}.{t.get('Slot') or t['ID'][:12]}",
            node_id=t.get("NodeID") or None,
            container_id=(t.get("Status", {}).get("ContainerStatus") or {}).get("ContainerID"),
        )
        for sid, t in sorted(latest.items(), key=lambda item: names.get(item[0], item[0]))
    ]


def _failed_tasks(tasks: list[Obj], since: datetime) -> dict[str, set[str]]:
    """IDs of tasks that failed since the deploy started, by service ID."""
    failed: dict[str, set[str]] = {}
    for task in tasks:
        created = _parse_time(task.get("CreatedAt"))
        if created is None or created < since:
            continue
        if task.get("Status", {}).get("State") == "failed":
            failed.setdefault(task.get("ServiceID", ""), set()).add(task["ID"])
    return failed


async def converge_swarm(
    reader: ClusterReader,
    stack: str,
    *,
    started: datetime,
    timeout: float,
    stable_for: float = 0.0,
    crash_loop_after: int = 2,
    interval: float = 3.0,
    report: Callable[[str], Awaitable[None]] | None = None,
) -> Outcome:
    """Wait until every service runs and has kept running for `stable_for` seconds.

    Swarm counts a crash-looping task as running between restarts, so being up at one moment
    proves little. A service whose tasks failed `crash_loop_after` times since the deploy
    started fails the rollout at once.
    """
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_summary = ""
    stable_since: float | None = None
    seen_failures: set[str] = set()
    while True:
        services = await reader.services(label=f"{STACK_LABEL}={stack}")
        states = [service_state(s) for s in services]
        names = {s["ID"]: s.get("Spec", {}).get("Name", s["ID"]) for s in services}

        tasks: list[Obj] = []
        for raw in services:
            tasks.extend(await reader.tasks(raw["ID"]))
        errors, rejected = _task_errors(tasks, started, names)
        failed = _failed_tasks(tasks, started)

        summary = ", ".join(f"{s.name} {s.detail}" for s in states)
        if report and summary != last_summary:
            await report(summary)
            last_summary = summary

        failed_update = [s for s in states if s.detail.startswith("update ")]
        if failed_update or rejected:
            problems = [f"{s.name}: {s.detail}" for s in failed_update]
            return Outcome(False, states, problems + errors)

        looping = {sid for sid, ids in failed.items() if len(ids) >= crash_loop_after}
        if looping:
            return Outcome(False, states, errors, _latest_failures(tasks, started, names, looping))

        new_failures = set().union(*failed.values()) - seen_failures if failed else set()
        seen_failures |= new_failures
        now = loop.time()
        if all(s.converged for s in states) and not new_failures:
            stable_since = now if stable_since is None else stable_since
            if now - stable_since >= stable_for:
                return Outcome(True, states)
        else:
            stable_since = None
        if now >= deadline:
            waiting = [f"{s.name}: {s.detail}" for s in states if not s.converged]
            reason = (
                f"Timed out waiting for {', '.join(waiting)}"
                if waiting
                else f"Services didn't stay running for {stable_for:g}s"
            )
            return Outcome(
                False,
                states,
                [reason, *errors],
                _latest_failures(tasks, started, names, set(failed)),
            )
        await asyncio.sleep(interval)
