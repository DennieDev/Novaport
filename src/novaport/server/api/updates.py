# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from novaport.models import ImageCheck, Role, Stack
from novaport.server.deps import DB, CurrentAccess, current_user
from novaport.updates.checker import UpdateChecker

router = APIRouter(prefix="/api", tags=["updates"], dependencies=[Depends(current_user)])


class ImageCheckOut(BaseModel):
    stack_id: uuid.UUID
    stack_name: str
    service: str
    current_ref: str | None
    candidate_ref: str | None
    status: str
    policy: dict[str, Any]
    error: str | None
    checked_at: datetime
    action_taken: str | None
    action_ref: str | None


def get_checker(request: Request) -> UpdateChecker:
    checker: UpdateChecker = request.app.state.updates
    return checker


Checker = Annotated[UpdateChecker, Depends(get_checker)]


def _out(c: ImageCheck) -> ImageCheckOut:
    return ImageCheckOut(
        stack_id=c.stack_id,
        stack_name=c.stack.name,
        service=c.service,
        current_ref=c.current_ref,
        candidate_ref=c.candidate_ref,
        status=c.status,
        policy=c.policy,
        error=c.error,
        checked_at=c.checked_at,
        action_taken=c.action_taken,
        action_ref=c.action_ref,
    )


@router.get("/updates")
async def list_updates(db: DB, access: CurrentAccess) -> list[ImageCheckOut]:
    """Update checks for every stack the user can see."""
    query = select(ImageCheck).options(selectinload(ImageCheck.stack))
    if not access.can(Role.VIEWER):
        query = query.where(ImageCheck.stack_id.in_(list(access.stack_roles)))
    checks = await db.scalars(query.order_by(ImageCheck.stack_id, ImageCheck.service))
    return sorted((_out(c) for c in checks), key=lambda c: (c.stack_name, c.service))


@router.get("/stacks/{stack_id}/updates")
async def stack_updates(stack_id: uuid.UUID, db: DB, access: CurrentAccess) -> list[ImageCheckOut]:
    access.require(Role.VIEWER, stack_id)
    checks = await db.scalars(
        select(ImageCheck)
        .options(selectinload(ImageCheck.stack))
        .where(ImageCheck.stack_id == stack_id)
        .order_by(ImageCheck.service)
    )
    return [_out(c) for c in checks]


@router.post("/stacks/{stack_id}/updates/check")
async def check_stack_updates(
    stack_id: uuid.UUID, request: Request, db: DB, access: CurrentAccess, checker: Checker
) -> list[ImageCheckOut]:
    """Check for newer images now, and act on what is found as the policies say."""
    access.require(Role.OPERATOR, stack_id, action="update.check")
    stack = await db.get(Stack, stack_id)
    if stack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stack not found.")
    access.audit(db, "update.check", target=stack.name, stack_id=stack.id)
    await db.commit()
    await checker.check(stack_id)
    db.expire_all()
    if request.app.state.jobs is not None:
        request.app.state.jobs.wake()
    return await stack_updates(stack_id, db, access)
