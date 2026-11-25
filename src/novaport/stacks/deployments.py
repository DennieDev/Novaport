# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import UTC, datetime

from sqlalchemy import update
from sqlalchemy.ext.asyncio import AsyncSession

from novaport.models import Deployment, DeploymentStatus, DeployMode, Revision, Stack

WAITING = (DeploymentStatus.PENDING, DeploymentStatus.AWAITING_APPROVAL)


async def queue_deployment(
    db: AsyncSession,
    stack: Stack,
    revision: Revision,
    *,
    trigger: str,
    needs_approval: bool = False,
    user_id: uuid.UUID | None = None,
    prune: bool = True,
    pull_images: bool = True,
) -> Deployment:
    """Create a deployment and cancel older ones of the same stack that haven't started."""
    await db.execute(
        update(Deployment)
        .where(Deployment.stack_id == stack.id, Deployment.status.in_(WAITING))
        .values(
            status=DeploymentStatus.CANCELLED,
            error="Superseded by a newer deployment.",
            finished_at=datetime.now(UTC),
        )
    )
    deployment = Deployment(
        stack_id=stack.id,
        revision_id=revision.id,
        status=DeploymentStatus.AWAITING_APPROVAL if needs_approval else DeploymentStatus.PENDING,
        trigger=trigger,
        triggered_by_id=user_id,
        prune=prune,
        pull_images=pull_images,
    )
    db.add(deployment)
    return deployment


async def deployment_for_new_revision(
    db: AsyncSession, stack: Stack, revision: Revision
) -> Deployment | None:
    if stack.deploy_mode == DeployMode.MANUAL:
        return None
    return await queue_deployment(
        db,
        stack,
        revision,
        trigger="git",
        needs_approval=stack.deploy_mode == DeployMode.APPROVE,
    )
