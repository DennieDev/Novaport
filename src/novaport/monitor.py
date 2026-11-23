# SPDX-License-Identifier: AGPL-3.0-or-later
"""Periodic checks that raise notifications: drift, approvals waiting, nodes and agents."""

import logging
import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from novaport.agents.pool import Agents
from novaport.deploy.drift import compare
from novaport.docker.client import ClusterReader, DockerUnavailable, NotSwarmManager
from novaport.docker.views import STACK_LABEL
from novaport.edge.hub import EdgeHub, EndpointOffline, Target
from novaport.locks import try_lock
from novaport.models import (
    Deployment,
    DeploymentStatus,
    Endpoint,
    EventType,
    Stack,
    StackType,
)
from novaport.notifications import Event, Notifier
from novaport.updates.checker import UpdateEvent

log = logging.getLogger(__name__)

UPDATE_EVENTS = {
    "committed": (EventType.UPDATE_COMMITTED, "committed"),
    "pr_opened": (EventType.UPDATE_PR, "pull request opened"),
    "notified": (EventType.UPDATE_AVAILABLE, "available"),
}


def update_event(update: UpdateEvent) -> Event | None:
    """The notification for an update result; `recorded` (action: none) stays quiet."""
    found = UPDATE_EVENTS.get(update.action_taken)
    if found is None:
        return None
    kind, verb = found
    text = f"{update.current} -> {update.candidate}"
    if update.action_ref:
        text += f"\n{update.action_ref}"
    return Event(
        kind,
        f"Image update for {update.stack_name}/{update.service} {verb}",
        text,
        stack_id=update.stack_id,
        stack_name=update.stack_name,
        link=f"/stacks/{update.stack_id}",
        data={
            "service": update.service,
            "current": update.current,
            "candidate": update.candidate,
            "ref": update.action_ref,
        },
    )


class Monitor:
    def __init__(
        self,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        notifier: Notifier,
        reader: Callable[[], ClusterReader | None],
        agents: Callable[[], Agents | None],
        targets: Callable[[uuid.UUID | None], Target] | None = None,
        edge: EdgeHub | None = None,
    ) -> None:
        self.targets = targets
        self.edge = edge
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.notifier = notifier
        self.reader = reader
        self.agents = agents
        # Nodes and agents already reported as down, so each outage is announced once.
        self._down_nodes: set[str] | None = None
        self._down_agents: set[str] | None = None
        self._down_endpoints: set[str] | None = None

    def _reader_for(self, stack: Stack) -> ClusterReader | None:
        if self.targets is None:
            return self.reader()
        try:
            return self.targets(stack.endpoint_id).reader
        except EndpointOffline:
            return None

    async def check_drift(self) -> None:
        async with try_lock(self.engine, "drift") as acquired:
            if not acquired:
                return
            async with self.sessionmaker() as db:
                stacks = await db.scalars(
                    select(Stack).where(
                        Stack.type == StackType.SWARM,
                        Stack.removed_at.is_(None),
                        Stack.deployed_spec.is_not(None),
                    )
                )
                for stack in stacks:
                    reader = self._reader_for(stack)
                    if reader is None:
                        continue
                    try:
                        services = await reader.services(label=f"{STACK_LABEL}={stack.name}")
                    except (DockerUnavailable, NotSwarmManager) as exc:
                        log.warning("Drift check of %s skipped: %s", stack.name, exc)
                        continue
                    drift = compare(stack.deployed_spec or {}, services, stack.name)
                    if drift and drift != (stack.drift or []):
                        self.notifier.emit(
                            Event(
                                EventType.DRIFT,
                                f"{stack.name} drifted from its last deploy",
                                "\n".join(drift[:10]),
                                stack_id=stack.id,
                                stack_name=stack.name,
                                link=f"/stacks/{stack.id}",
                                data={"differences": drift},
                            )
                        )
                    stack.drift = drift
                    stack.drift_checked_at = datetime.now(UTC)
                await db.commit()

    async def announce_approvals(self) -> None:
        async with self.sessionmaker() as db:
            waiting = await db.scalars(
                select(Deployment)
                .options(selectinload(Deployment.stack), selectinload(Deployment.revision))
                .where(
                    Deployment.status == DeploymentStatus.AWAITING_APPROVAL,
                    Deployment.approval_notified.is_(False),
                )
            )
            for deployment in waiting:
                deployment.approval_notified = True
                revision = deployment.revision
                self.notifier.emit(
                    Event(
                        EventType.APPROVAL_NEEDED,
                        f"{deployment.stack.name} is waiting for approval",
                        f"{revision.commit[:10]} {revision.message}".strip(),
                        stack_id=deployment.stack_id,
                        stack_name=deployment.stack.name,
                        link=f"/deployments/{deployment.id}",
                        data={"deployment": str(deployment.id), "commit": revision.commit},
                    )
                )
            await db.commit()

    async def check_health(self) -> None:
        reader = self.reader()
        if reader is not None:
            try:
                nodes = await reader.nodes()
            except (DockerUnavailable, NotSwarmManager):
                nodes = None
            if nodes is not None:
                down = {
                    str(n.get("Description", {}).get("Hostname") or n.get("ID"))
                    for n in nodes
                    if n.get("Status", {}).get("State") not in ("ready", None)
                }
                self._down_nodes = self._report(
                    self._down_nodes, down, EventType.NODE_DOWN, "Node {} is down"
                )
        agents = self.agents()
        if agents is not None and agents.enabled:
            found = await agents.list(refresh=True)
            failing = {a.hostname or a.url for a in found if a.error}
            self._down_agents = self._report(
                self._down_agents,
                failing,
                EventType.AGENT_UNREACHABLE,
                "Agent on {} is unreachable",
            )
        await self._check_endpoints()

    async def _check_endpoints(self) -> None:
        if self.edge is None:
            return
        async with self.sessionmaker() as db:
            rows = await db.execute(
                select(Endpoint.id, Endpoint.name).where(Endpoint.connected_at.is_not(None))
            )
            down = {name for endpoint_id, name in rows if self.edge.get(endpoint_id) is None}
        self._down_endpoints = self._report(
            self._down_endpoints, down, EventType.ENDPOINT_DOWN, "Endpoint {} is disconnected"
        )

    def _report(
        self, known: set[str] | None, current: set[str], kind: EventType, title: str
    ) -> set[str]:
        # The first round only learns the state; outages from before startup aren't news.
        if known is not None:
            for name in sorted(current - known):
                self.notifier.emit(Event(kind, title.format(name), data={"name": name}))
        return current
