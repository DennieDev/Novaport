# SPDX-License-Identifier: AGPL-3.0-or-later
"""Watches a freshly deployed Swarm stack until every service is running, or something fails."""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from datetime import datetime

from novaport.docker.client import ClusterReader
from novaport.docker.views import STACK_LABEL, Obj

FAILED_UPDATE_STATES = {"paused", "rollback_started", "rollback_paused", "rollback_completed"}
JOB_MODES = {"ReplicatedJob", "GlobalJob"}


@dataclass
class ServiceState:
    name: str
    converged: bool
    detail: str


@dataclass
class Outcome:
    ok: bool
    services: list[ServiceState] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


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


async def converge_swarm(
    reader: ClusterReader,
    stack: str,
    *,
    started: datetime,
    timeout: float,
    interval: float = 3.0,
    report: Callable[[str], Awaitable[None]] | None = None,
) -> Outcome:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    last_summary = ""
    while True:
        services = await reader.services(label=f"{STACK_LABEL}={stack}")
        states = [service_state(s) for s in services]
        names = {s["ID"]: s.get("Spec", {}).get("Name", s["ID"]) for s in services}

        tasks: list[Obj] = []
        for raw in services:
            tasks.extend(await reader.tasks(raw["ID"]))
        errors, rejected = _task_errors(tasks, started, names)

        summary = ", ".join(f"{s.name} {s.detail}" for s in states)
        if report and summary != last_summary:
            await report(summary)
            last_summary = summary

        failed_update = [s for s in states if s.detail.startswith("update ")]
        if failed_update or rejected:
            problems = [f"{s.name}: {s.detail}" for s in failed_update]
            return Outcome(False, states, problems + errors)
        if all(s.converged for s in states):
            return Outcome(True, states)
        if loop.time() >= deadline:
            waiting = [f"{s.name}: {s.detail}" for s in states if not s.converged]
            return Outcome(False, states, [f"Timed out waiting for {', '.join(waiting)}", *errors])
        await asyncio.sleep(interval)
