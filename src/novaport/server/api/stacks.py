# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from novaport.auth.access import Access
from novaport.models import (
    Deployment,
    DeploymentEvent,
    DeploymentStatus,
    DeployMode,
    EditMode,
    Revision,
    Role,
    Stack,
    StackType,
)
from novaport.server.deps import DB, CurrentAccess, current_user
from novaport.stacks.deployments import WAITING, queue_deployment

router = APIRouter(prefix="/api", tags=["stacks"], dependencies=[Depends(current_user)])

Limit = Annotated[int, Query(ge=1, le=500)]


class RevisionOut(BaseModel):
    id: uuid.UUID
    commit: str
    tree: str
    message: str
    author: str
    committed_at: datetime
    created_at: datetime


class DeploymentOut(BaseModel):
    id: uuid.UUID
    stack_id: uuid.UUID
    stack_name: str
    revision: RevisionOut
    status: DeploymentStatus
    stage: str | None
    trigger: str
    error: str | None
    created_at: datetime
    started_at: datetime | None
    finished_at: datetime | None


class EventOut(BaseModel):
    id: int
    at: datetime
    stage: str
    level: str
    message: str


class DeploymentDetail(DeploymentOut):
    events: list[EventOut]


class RepositoryRef(BaseModel):
    id: uuid.UUID
    name: str


class StackOut(BaseModel):
    id: uuid.UUID
    name: str
    path: str
    repository: RepositoryRef
    type: StackType
    deploy_mode: DeployMode
    edit_mode: EditMode
    config_error: str | None
    removed_at: datetime | None
    latest_revision: RevisionOut | None
    last_good_revision: RevisionOut | None
    last_deployment: DeploymentOut | None


class DeployRequest(BaseModel):
    revision_id: uuid.UUID | None = None


def revision_out(r: Revision) -> RevisionOut:
    return RevisionOut.model_validate(r, from_attributes=True)


def deployment_out(d: Deployment) -> DeploymentOut:
    return DeploymentOut(
        id=d.id,
        stack_id=d.stack_id,
        stack_name=d.stack.name,
        revision=revision_out(d.revision),
        status=d.status,
        stage=d.stage,
        trigger=d.trigger,
        error=d.error,
        created_at=d.created_at,
        started_at=d.started_at,
        finished_at=d.finished_at,
    )


DEPLOYMENT_LOAD = (selectinload(Deployment.stack), selectinload(Deployment.revision))


async def _latest_revision(db: DB, stack_id: uuid.UUID) -> Revision | None:
    return await db.scalar(
        select(Revision)
        .where(Revision.stack_id == stack_id)
        .order_by(Revision.created_at.desc())
        .limit(1)
    )


async def _last_deployment(db: DB, stack_id: uuid.UUID) -> Deployment | None:
    return await db.scalar(
        select(Deployment)
        .options(*DEPLOYMENT_LOAD)
        .where(Deployment.stack_id == stack_id)
        .order_by(Deployment.created_at.desc())
        .limit(1)
    )


async def stack_out(db: DB, stack: Stack) -> StackOut:
    latest = await _latest_revision(db, stack.id)
    last = await _last_deployment(db, stack.id)
    return StackOut(
        id=stack.id,
        name=stack.name,
        path=stack.path,
        repository=RepositoryRef(id=stack.repository.id, name=stack.repository.name),
        type=stack.type,
        deploy_mode=stack.deploy_mode,
        edit_mode=stack.edit_mode,
        config_error=stack.config_error,
        removed_at=stack.removed_at,
        latest_revision=revision_out(latest) if latest else None,
        last_good_revision=(
            revision_out(stack.last_good_revision) if stack.last_good_revision else None
        ),
        last_deployment=deployment_out(last) if last else None,
    )


STACK_LOAD = (selectinload(Stack.repository), selectinload(Stack.last_good_revision))


async def _get_stack(db: DB, stack_id: uuid.UUID) -> Stack:
    stack = await db.scalar(select(Stack).options(*STACK_LOAD).where(Stack.id == stack_id))
    if stack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stack not found.")
    return stack


async def _get_deployment(db: DB, deployment_id: uuid.UUID) -> Deployment:
    deployment = await db.scalar(
        select(Deployment).options(*DEPLOYMENT_LOAD).where(Deployment.id == deployment_id)
    )
    if deployment is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Deployment not found.")
    return deployment


def _audit_deployment(access: Access, db: DB, action: str, deployment: Deployment) -> None:
    access.audit(
        db,
        action,
        target=deployment.stack.name,
        stack_id=deployment.stack_id,
        details={"deployment": str(deployment.id), "commit": deployment.revision.commit},
    )


def _wake(request: Request) -> None:
    if request.app.state.jobs is not None:
        request.app.state.jobs.wake()


@router.get("/stacks")
async def list_stacks(db: DB, access: CurrentAccess) -> list[StackOut]:
    stacks = await db.scalars(select(Stack).options(*STACK_LOAD).order_by(Stack.name))
    return [await stack_out(db, s) for s in access.stacks_with(Role.VIEWER, stacks)]


@router.get("/stacks/{stack_id}")
async def get_stack(stack_id: uuid.UUID, db: DB, access: CurrentAccess) -> StackOut:
    access.require(Role.VIEWER, stack_id)
    return await stack_out(db, await _get_stack(db, stack_id))


@router.get("/stacks/{stack_id}/revisions")
async def stack_revisions(
    stack_id: uuid.UUID, db: DB, access: CurrentAccess, limit: Limit = 50
) -> list[RevisionOut]:
    access.require(Role.VIEWER, stack_id)
    await _get_stack(db, stack_id)
    revisions = await db.scalars(
        select(Revision)
        .where(Revision.stack_id == stack_id)
        .order_by(Revision.created_at.desc())
        .limit(limit)
    )
    return [revision_out(r) for r in revisions]


@router.get("/stacks/{stack_id}/deployments")
async def stack_deployments(
    stack_id: uuid.UUID, db: DB, access: CurrentAccess, limit: Limit = 50
) -> list[DeploymentOut]:
    access.require(Role.VIEWER, stack_id)
    await _get_stack(db, stack_id)
    deployments = await db.scalars(
        select(Deployment)
        .options(*DEPLOYMENT_LOAD)
        .where(Deployment.stack_id == stack_id)
        .order_by(Deployment.created_at.desc())
        .limit(limit)
    )
    return [deployment_out(d) for d in deployments]


@router.post("/stacks/{stack_id}/deploy", status_code=status.HTTP_202_ACCEPTED)
async def deploy_stack(
    stack_id: uuid.UUID, body: DeployRequest, request: Request, db: DB, access: CurrentAccess
) -> DeploymentOut:
    """Deploy a revision now: the latest one by default, or an older one to roll back."""
    access.require(Role.OPERATOR, stack_id, action="stack.deploy")
    stack = await _get_stack(db, stack_id)
    if stack.removed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This stack was removed from git.")
    if body.revision_id is None:
        revision = await _latest_revision(db, stack.id)
    else:
        revision = await db.get(Revision, body.revision_id)
        if revision is not None and revision.stack_id != stack.id:
            revision = None
    if revision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Revision not found.")

    deployment = await queue_deployment(
        db, stack, revision, trigger="manual", user_id=access.user.id
    )
    await db.flush()
    access.audit(
        db,
        "stack.deploy",
        target=stack.name,
        stack_id=stack.id,
        details={"commit": revision.commit, "deployment": str(deployment.id)},
    )
    await db.commit()
    _wake(request)
    return deployment_out(await _get_deployment(db, deployment.id))


@router.get("/deployments")
async def recent_deployments(
    db: DB, access: CurrentAccess, limit: Limit = 20
) -> list[DeploymentOut]:
    query = select(Deployment).options(*DEPLOYMENT_LOAD)
    if not access.can(Role.VIEWER):
        query = query.where(Deployment.stack_id.in_(list(access.stack_roles)))
    deployments = await db.scalars(query.order_by(Deployment.created_at.desc()).limit(limit))
    return [deployment_out(d) for d in deployments]


@router.get("/deployments/{deployment_id}")
async def get_deployment(
    deployment_id: uuid.UUID, db: DB, access: CurrentAccess
) -> DeploymentDetail:
    deployment = await _get_deployment(db, deployment_id)
    access.require(Role.VIEWER, deployment.stack_id)
    events = await db.scalars(
        select(DeploymentEvent)
        .where(DeploymentEvent.deployment_id == deployment_id)
        .order_by(DeploymentEvent.id)
    )
    return DeploymentDetail(
        **deployment_out(deployment).model_dump(),
        events=[EventOut.model_validate(e, from_attributes=True) for e in events],
    )


@router.post("/deployments/{deployment_id}/approve")
async def approve_deployment(
    deployment_id: uuid.UUID, request: Request, db: DB, access: CurrentAccess
) -> DeploymentOut:
    deployment = await _get_deployment(db, deployment_id)
    access.require(Role.OPERATOR, deployment.stack_id, action="deployment.approve")
    if deployment.status != DeploymentStatus.AWAITING_APPROVAL:
        raise HTTPException(status.HTTP_409_CONFLICT, "This deployment isn't waiting for approval.")
    deployment.status = DeploymentStatus.PENDING
    deployment.approved_by_id = access.user.id
    _audit_deployment(access, db, "deployment.approve", deployment)
    await db.commit()
    _wake(request)
    return deployment_out(deployment)


@router.post("/deployments/{deployment_id}/cancel")
async def cancel_deployment(
    deployment_id: uuid.UUID, db: DB, access: CurrentAccess
) -> DeploymentOut:
    deployment = await _get_deployment(db, deployment_id)
    access.require(Role.OPERATOR, deployment.stack_id, action="deployment.cancel")
    if deployment.status not in WAITING:
        raise HTTPException(status.HTTP_409_CONFLICT, "Only waiting deployments can be cancelled.")
    deployment.status = DeploymentStatus.CANCELLED
    deployment.error = f"Cancelled by {access.user.username}."
    deployment.finished_at = datetime.now(UTC)
    _audit_deployment(access, db, "deployment.cancel", deployment)
    await db.commit()
    return deployment_out(deployment)
