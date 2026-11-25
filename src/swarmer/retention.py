# SPDX-License-Identifier: AGPL-3.0-or-later
"""Deletes old deployments, revisions, and audit entries."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, exists, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from swarmer.models import AuditEntry, Deployment, DeploymentStatus, Revision, Stack

FINISHED = [s.value for s in DeploymentStatus if s.finished]


@dataclass(frozen=True)
class Retention:
    deployments: timedelta
    keep: int
    audit: timedelta


@dataclass
class Pruned:
    deployments: int = 0
    revisions: int = 0
    audit_entries: int = 0


async def prune(db: AsyncSession, retention: Retention) -> Pruned:
    now = datetime.now(UTC)
    pruned = Pruned()
    if retention.deployments:
        cutoff = now - retention.deployments
        ranked = select(
            Deployment.id,
            func.row_number()
            .over(partition_by=Deployment.stack_id, order_by=Deployment.created_at.desc())
            .label("rank"),
        ).subquery()
        old_deployments = await db.execute(
            delete(Deployment).where(
                Deployment.created_at < cutoff,
                Deployment.status.in_(FINISHED),
                Deployment.id.in_(select(ranked.c.id).where(ranked.c.rank > retention.keep)),
            )
        )
        pruned.deployments = old_deployments.rowcount  # type: ignore[attr-defined]

        ranked_revisions = select(
            Revision.id,
            func.row_number()
            .over(partition_by=Revision.stack_id, order_by=Revision.created_at.desc())
            .label("rank"),
        ).subquery()
        old_revisions = await db.execute(
            delete(Revision).where(
                Revision.created_at < cutoff,
                Revision.id.in_(
                    select(ranked_revisions.c.id).where(ranked_revisions.c.rank > retention.keep)
                ),
                ~exists().where(Deployment.revision_id == Revision.id),
                ~exists().where(Stack.last_good_revision_id == Revision.id),
            )
        )
        pruned.revisions = old_revisions.rowcount  # type: ignore[attr-defined]
    if retention.audit:
        cutoff = now - retention.audit
        old_audit = await db.execute(delete(AuditEntry).where(AuditEntry.at < cutoff))
        pruned.audit_entries = old_audit.rowcount  # type: ignore[attr-defined]
    await db.commit()
    return pruned
