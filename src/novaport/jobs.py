# SPDX-License-Identifier: AGPL-3.0-or-later
"""Background work inside the server process: polling repositories and running deployments."""

import asyncio
import contextlib
import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from functools import partial

from sqlalchemy import func, or_, select, update
from sqlalchemy.ext.asyncio import AsyncEngine, AsyncSession, async_sessionmaker

from novaport.auth.sessions import delete_expired_sessions
from novaport.deploy.pipeline import Pipeline
from novaport.locks import try_lock
from novaport.models import Deployment, DeploymentStatus, Repository, Revision, Stack
from novaport.monitor import Monitor
from novaport.retention import Retention, prune
from novaport.stacks.sync import Syncer
from novaport.updates.checker import UpdateChecker

log = logging.getLogger(__name__)

POLL_TICK = 15.0
DEPLOY_TICK = 5.0
UPDATE_TICK = 60.0
HEALTH_TICK = 60.0
HOUSEKEEPING_INTERVAL = timedelta(hours=1)


class Jobs:
    def __init__(
        self,
        engine: AsyncEngine,
        sessionmaker: async_sessionmaker[AsyncSession],
        syncer: Syncer,
        pipeline: Pipeline,
        updates: UpdateChecker | None = None,
        update_interval: timedelta = timedelta(hours=6),
        monitor: Monitor | None = None,
        drift_interval: timedelta = timedelta(minutes=5),
        retention: Retention | None = None,
    ) -> None:
        self.retention = retention
        self.monitor = monitor
        self.drift_interval = drift_interval
        self.updates = updates
        self.update_interval = update_interval
        self._checked: dict[uuid.UUID, datetime] = {}
        self.engine = engine
        self.sessionmaker = sessionmaker
        self.syncer = syncer
        self.pipeline = pipeline
        self._wake = asyncio.Event()
        self._tasks: list[asyncio.Task[None]] = []
        self._syncing: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._deploying: dict[uuid.UUID, asyncio.Task[None]] = {}
        self._last_housekeeping = datetime.min.replace(tzinfo=UTC)

    def start(self) -> None:
        self._tasks = [
            asyncio.create_task(
                self._loop(self.poll_repositories, POLL_TICK, asyncio.Event()), name="git-poller"
            ),
            asyncio.create_task(
                self._loop(self.run_deployments, DEPLOY_TICK, self._wake), name="deployer"
            ),
        ]
        if self.monitor is not None:
            self._tasks += [
                asyncio.create_task(
                    self._loop(
                        self.monitor.check_drift,
                        self.drift_interval.total_seconds(),
                        asyncio.Event(),
                    ),
                    name="drift",
                ),
                asyncio.create_task(
                    self._loop(self.monitor.check_health, HEALTH_TICK, asyncio.Event()),
                    name="health",
                ),
            ]
        if self.updates is not None:
            self._tasks.append(
                asyncio.create_task(
                    self._loop(self.check_updates, UPDATE_TICK, asyncio.Event()),
                    name="update-checker",
                )
            )

    async def stop(self) -> None:
        tasks = [*self._tasks, *self._syncing.values(), *self._deploying.values()]
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task

    def wake(self) -> None:
        """Look for pending deployments now instead of at the next tick."""
        self._wake.set()

    def request_sync(self, repository_id: uuid.UUID) -> None:
        if repository_id in self._syncing:
            return
        task = asyncio.create_task(self._sync(repository_id), name=f"sync-{repository_id}")
        self._syncing[repository_id] = task
        task.add_done_callback(lambda _: self._syncing.pop(repository_id, None))

    async def _sync(self, repository_id: uuid.UUID) -> None:
        try:
            result = await self.syncer.sync(repository_id)
        except Exception:
            log.exception("Sync of repository %s crashed", repository_id)
            return
        if result.queued_deployments:
            self.wake()

    async def _loop(
        self, step: Callable[[], Awaitable[None]], tick: float, wake: asyncio.Event
    ) -> None:
        while True:
            try:
                await step()
            except asyncio.CancelledError:
                raise
            except Exception:
                log.exception("Background job %s failed", step.__name__)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(wake.wait(), tick)
            wake.clear()

    async def poll_repositories(self) -> None:
        now = datetime.now(UTC)
        async with self.sessionmaker() as db:
            repos = await db.scalars(select(Repository).where(Repository.poll_interval > 0))
            due = [
                r.id
                for r in repos
                if r.last_synced_at is None
                or now - r.last_synced_at >= timedelta(seconds=r.poll_interval)
            ]
            if now - self._last_housekeeping >= HOUSEKEEPING_INTERVAL:
                self._last_housekeeping = now
                await delete_expired_sessions(db)
                if self.retention is not None:
                    pruned = await prune(db, self.retention)
                    if pruned.deployments or pruned.revisions or pruned.audit_entries:
                        log.info(
                            "Deleted %d deployments, %d revisions, and %d audit entries "
                            "past retention",
                            pruned.deployments,
                            pruned.revisions,
                            pruned.audit_entries,
                        )
        for repo_id in due:
            self.request_sync(repo_id)

    async def check_updates(self) -> None:
        """Check stacks one at a time: those whose last check is older than the interval, and
        those with a revision newer than their last check."""
        if self.updates is None:
            return
        async with try_lock(self.engine, "update-checks") as acquired:
            if not acquired:
                return
            async with self.sessionmaker() as db:
                stacks = list(
                    await db.execute(
                        select(Stack.id, func.max(Revision.created_at))
                        .outerjoin(Revision, Revision.stack_id == Stack.id)
                        .where(Stack.removed_at.is_(None))
                        .group_by(Stack.id)
                    )
                )
            now = datetime.now(UTC)
            for stack_id, changed in stacks:
                last = self._checked.get(stack_id)
                if (
                    last is not None
                    and now - last < self.update_interval
                    and (changed is None or changed <= last)
                ):
                    continue
                self._checked[stack_id] = now
                await self.updates.check(stack_id)
                self.wake()

    async def run_deployments(self) -> None:
        if self.monitor is not None:
            await self.monitor.announce_approvals()
        async with self.sessionmaker() as db:
            rows = (
                await db.execute(
                    select(Deployment.id, Deployment.stack_id, Deployment.status)
                    .where(
                        or_(
                            Deployment.status == DeploymentStatus.PENDING,
                            Deployment.status == DeploymentStatus.RUNNING,
                        )
                    )
                    .order_by(Deployment.created_at)
                )
            ).all()

        for deployment_id, stack_id, status in rows:
            if stack_id in self._deploying:
                continue
            task = asyncio.create_task(
                self._deploy(deployment_id, stack_id, DeploymentStatus(status)),
                name=f"deploy-{deployment_id}",
            )
            self._deploying[stack_id] = task
            task.add_done_callback(partial(self._deploy_done, stack_id))

    def _deploy_done(self, stack_id: uuid.UUID, _: asyncio.Task[None]) -> None:
        self._deploying.pop(stack_id, None)

    async def _deploy(
        self, deployment_id: uuid.UUID, stack_id: uuid.UUID, status: DeploymentStatus
    ) -> None:
        async with try_lock(self.engine, f"deploy:{stack_id}") as acquired:
            if not acquired:
                return
            if status == DeploymentStatus.RUNNING:
                # Nobody holds the stack lock, so whoever ran this is gone.
                await self._mark_orphaned(deployment_id)
                return
            await self.pipeline.run(deployment_id)
        self.wake()

    async def _mark_orphaned(self, deployment_id: uuid.UUID) -> None:
        async with self.sessionmaker() as db:
            await db.execute(
                update(Deployment)
                .where(
                    Deployment.id == deployment_id,
                    Deployment.status == DeploymentStatus.RUNNING,
                )
                .values(
                    status=DeploymentStatus.FAILED,
                    error="Novaport stopped while this deployment was running. "
                    "Check the stack and deploy again.",
                    finished_at=datetime.now(UTC),
                )
            )
            await db.commit()
