# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from swarmer.auth.access import Access
from swarmer.config import Settings
from swarmer.locks import LockTimeout
from swarmer.models import DeployMode, Outcome, Revision, Role, Stack
from swarmer.server.api.stacks import DeployOptions, deploy_now
from swarmer.server.deps import DB, CurrentAccess, current_user
from swarmer.sops import KEY_PATTERN, Sops
from swarmer.stacks.editing import (
    EditConflict,
    EditError,
    EditResult,
    StackEditor,
    default_message,
)

router = APIRouter(prefix="/api", tags=["editing"], dependencies=[Depends(current_user)])


class FileOut(BaseModel):
    path: str
    content: str | None
    binary: bool
    size: int


class FilesOut(BaseModel):
    commit: str
    files: list[FileOut]
    has_secrets: bool


class FileChange(BaseModel):
    path: str = Field(min_length=1, max_length=500)
    content: str | None = Field(default=None, max_length=512 * 1024)


class SaveFiles(BaseModel):
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    changes: list[FileChange] = Field(min_length=1, max_length=100)
    message: str | None = Field(default=None, max_length=5000)
    validate_compose: bool = True
    # Deploy the new revision right away, whatever the stack's deploy mode.
    deploy: DeployOptions | None = None


class SaveSecrets(BaseModel):
    base_commit: str = Field(pattern=r"^[0-9a-f]{40,64}$")
    set: dict[str, str] = Field(default_factory=dict)
    remove: list[str] = Field(default_factory=list)
    message: str | None = Field(default=None, max_length=5000)


class SecretsOut(BaseModel):
    commit: str
    keys: list[str]
    values: dict[str, str] | None


class SaveResult(BaseModel):
    commit: str
    branch: str
    pull_request_url: str | None
    new_revisions: int
    queued_deployments: int
    # Set when saving also deployed the new revision.
    deployment_id: uuid.UUID | None = None


class SopsInfo(BaseModel):
    available: bool
    recipient: str | None
    example: str | None


def get_editor(request: Request) -> StackEditor:
    editor: StackEditor = request.app.state.editor
    return editor


Editor = Annotated[StackEditor, Depends(get_editor)]


async def _stack(db: DB, stack_id: uuid.UUID) -> Stack:
    stack = await db.scalar(
        select(Stack).options(selectinload(Stack.repository)).where(Stack.id == stack_id)
    )
    if stack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stack not found.")
    if stack.removed_at is not None:
        raise HTTPException(status.HTTP_409_CONFLICT, "This stack was removed from git.")
    return stack


def save_result(request: Request, result: EditResult) -> SaveResult:
    queued = result.sync.queued_deployments if result.sync else 0
    if queued and request.app.state.jobs is not None:
        request.app.state.jobs.wake()
    return SaveResult(
        commit=result.commit,
        branch=result.branch,
        pull_request_url=result.pull_request_url,
        new_revisions=len(result.sync.new_revisions) if result.sync else 0,
        queued_deployments=queued,
    )


def conflict_response(exc: EditConflict) -> JSONResponse:
    return JSONResponse(
        {
            "detail": str(exc),
            "tip": exc.tip,
            "conflicts": [c.__dict__ for c in exc.conflicts],
        },
        status_code=status.HTTP_409_CONFLICT,
    )


@router.get("/stacks/{stack_id}/files")
async def get_files(stack_id: uuid.UUID, db: DB, access: CurrentAccess, editor: Editor) -> FilesOut:
    access.require(Role.VIEWER, stack_id)
    stack = await _stack(db, stack_id)
    try:
        result = await editor.files(stack)
    except EditError as exc:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, str(exc)) from exc
    entries = [FileOut(**f.__dict__) for f in result.files]
    has_secrets = await editor.has_secrets(stack, result.commit)
    return FilesOut(commit=result.commit, files=entries, has_secrets=has_secrets)


async def _audit_save(
    db: DB,
    access: Access,
    action: str,
    stack: Stack,
    details: dict[str, object],
    result: EditResult | None = None,
    error: Exception | None = None,
) -> None:
    if result is not None:
        details = {**details, "commit": result.commit, "branch": result.branch}
        if result.pull_request_url:
            details["pull_request"] = result.pull_request_url
    if error is not None:
        details = {**details, "error": str(error)[:500]}
    access.audit(
        db,
        action,
        outcome=Outcome.SUCCESS if error is None else Outcome.FAILED,
        target=stack.name,
        stack_id=stack.id,
        details=details,
    )
    await db.commit()


@router.post("/stacks/{stack_id}/files", response_model=SaveResult)
async def save_files(
    stack_id: uuid.UUID,
    body: SaveFiles,
    request: Request,
    db: DB,
    access: CurrentAccess,
    editor: Editor,
) -> SaveResult | JSONResponse:
    access.require(Role.EDITOR, stack_id, action="stack.edit")
    stack = await _stack(db, stack_id)
    if body.deploy is not None:
        access.require(Role.OPERATOR, stack_id, action="stack.deploy")
        settings: Settings = request.app.state.settings
        if settings.separate_approver and stack.deploy_mode == DeployMode.APPROVE:
            raise HTTPException(
                status.HTTP_403_FORBIDDEN,
                "Someone other than the author has to approve this change, so it can't be "
                "deployed when saving.",
            )
    paths = [c.path for c in body.changes]
    details: dict[str, object] = {"files": paths}
    try:
        result = await editor.save(
            stack,
            access.user,
            body.base_commit,
            {c.path: c.content for c in body.changes},
            body.message or default_message(stack, paths),
            validate=body.validate_compose,
        )
    except EditConflict as exc:
        await _audit_save(db, access, "stack.edit", stack, details, error=exc)
        return conflict_response(exc)
    except (EditError, LockTimeout) as exc:
        await _audit_save(db, access, "stack.edit", stack, details, error=exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await _audit_save(db, access, "stack.edit", stack, details, result)
    saved = save_result(request, result)
    if body.deploy is not None and result.pull_request_url is None:
        revision = await db.scalar(
            select(Revision)
            .where(Revision.stack_id == stack.id, Revision.commit == result.commit)
            .order_by(Revision.created_at.desc())
            .limit(1)
        )
        if revision is not None:
            deployment = await deploy_now(db, access, stack, revision, body.deploy)
            await db.commit()
            if request.app.state.jobs is not None:
                request.app.state.jobs.wake()
            saved.deployment_id = deployment.id
    return saved


@router.get("/stacks/{stack_id}/secrets")
async def get_secrets(
    stack_id: uuid.UUID, db: DB, access: CurrentAccess, editor: Editor, reveal: bool = False
) -> SecretsOut:
    if reveal:
        access.require(Role.EDITOR, stack_id, action="secrets.reveal")
    else:
        access.require(Role.VIEWER, stack_id)
    stack = await _stack(db, stack_id)
    try:
        commit, values = await editor.read_secrets(stack)
    except EditError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    if reveal:
        access.audit(
            db,
            "secrets.reveal",
            target=stack.name,
            stack_id=stack.id,
            details={"commit": commit, "keys": sorted(values)},
        )
        await db.commit()
    return SecretsOut(commit=commit, keys=sorted(values), values=values if reveal else None)


@router.post("/stacks/{stack_id}/secrets", response_model=SaveResult)
async def save_secrets(
    stack_id: uuid.UUID,
    body: SaveSecrets,
    request: Request,
    db: DB,
    access: CurrentAccess,
    editor: Editor,
) -> SaveResult | JSONResponse:
    access.require(Role.EDITOR, stack_id, action="secrets.edit")
    stack = await _stack(db, stack_id)
    for key in [*body.set, *body.remove]:
        if not KEY_PATTERN.match(key):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT, f"{key!r} is not a valid variable name."
            )
    details: dict[str, object] = {"set": sorted(body.set), "removed": sorted(body.remove)}
    try:
        result = await editor.save_secrets(
            stack,
            access.user,
            body.base_commit,
            body.set,
            body.remove,
            body.message or f"Update secrets for {stack.name}",
        )
    except EditConflict as exc:
        await _audit_save(db, access, "secrets.edit", stack, details, error=exc)
        return conflict_response(exc)
    except (EditError, LockTimeout) as exc:
        await _audit_save(db, access, "secrets.edit", stack, details, error=exc)
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    await _audit_save(db, access, "secrets.edit", stack, details, result)
    return save_result(request, result)


@router.get("/settings/sops")
async def sops_info(request: Request) -> SopsInfo:
    sops: Sops = request.app.state.sops
    recipient = sops.recipient
    example = (
        f"creation_rules:\n  - path_regex: secrets\\.env$\n    age: >-\n      {recipient}\n"
        if recipient
        else None
    )
    return SopsInfo(available=sops.available, recipient=recipient, example=example)
