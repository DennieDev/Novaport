# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from typing import Literal

import yaml
from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select

from novaport.docker.client import DockerUnavailable, NotSwarmManager
from novaport.docker.views import STACK_LABEL
from novaport.edge.hub import EndpointOffline, Target, Targets
from novaport.locks import LockTimeout
from novaport.models import DeployMode, Endpoint, Outcome, Repository, Stack, StackType
from novaport.server.api.editing import Editor, SaveResult, conflict_response, save_result
from novaport.server.deps import DB, AdminAccess, ViewerAccess, current_user
from novaport.stacks.adopt import (
    COMPOSE_PROJECT,
    Generated,
    compose_project,
    project_names,
    stack_names,
    swarm_compose,
)
from novaport.stacks.editing import EditConflict, EditError
from novaport.stacks.settings import NAME_PATTERN, SETTINGS_FILE

router = APIRouter(prefix="/api", tags=["adopt"], dependencies=[Depends(current_user)])

Kind = Literal["swarm", "compose"]


class Unmanaged(BaseModel):
    name: str
    type: StackType
    endpoint: str | None
    services: int
    # Another Novaport stack already uses the name, so this one can't be added under it.
    name_taken: bool


class GeneratedOut(BaseModel):
    compose: str
    notes: list[str]


class AdoptIn(BaseModel):
    repository_id: uuid.UUID
    compose: str = Field(min_length=1, max_length=512 * 1024)
    deploy: DeployMode = DeployMode.MANUAL
    message: str | None = Field(default=None, max_length=5000)


async def _targets(db: DB, request: Request) -> list[tuple[Endpoint | None, Target]]:
    targets: Targets = request.app.state.targets
    result: list[tuple[Endpoint | None, Target]] = [(None, targets(None))]
    for endpoint in await db.scalars(select(Endpoint).order_by(Endpoint.name)):
        try:
            result.append((endpoint, targets(endpoint.id)))
        except EndpointOffline:
            continue
    return result


async def _target(db: DB, request: Request, endpoint: str | None) -> Target:
    targets: Targets = request.app.state.targets
    if endpoint is None:
        return targets(None)
    found = await db.scalar(select(Endpoint).where(Endpoint.name == endpoint))
    if found is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Endpoint not found.")
    try:
        return targets(found.id)
    except EndpointOffline as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


async def _scan(target: Target) -> list[tuple[StackType, str, int]]:
    reader = target.reader
    if reader is None:
        return []
    found: list[tuple[StackType, str, int]] = []
    try:
        services = await reader.services()
    except NotSwarmManager:
        services = []
    found += [(StackType.SWARM, n, c) for n, c in stack_names(services).items()]
    containers = await reader.containers(COMPOSE_PROJECT)
    found += [(StackType.COMPOSE, n, c) for n, c in project_names(containers).items()]
    return found


@router.get("/unmanaged")
async def unmanaged(request: Request, db: DB, _: ViewerAccess) -> list[Unmanaged]:
    """Swarm stacks and Compose projects that no Novaport stack deploys."""
    stacks = list(await db.scalars(select(Stack).where(Stack.removed_at.is_(None))))
    managed = {(s.endpoint_id, s.name) for s in stacks}
    taken = {s.name for s in stacks}
    result = []
    for endpoint, target in await _targets(db, request):
        endpoint_id = endpoint.id if endpoint else None
        try:
            found = await _scan(target)
        except DockerUnavailable:
            continue
        for kind, name, count in found:
            if (endpoint_id, name) in managed:
                continue
            result.append(
                Unmanaged(
                    name=name,
                    type=kind,
                    endpoint=endpoint.name if endpoint else None,
                    services=count,
                    name_taken=name in taken or not NAME_PATTERN.match(name),
                )
            )
    return sorted(result, key=lambda u: (u.endpoint or "", u.name, u.type))


async def _generate(target: Target, kind: Kind, name: str) -> Generated:
    reader = target.reader
    if reader is None:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "No Docker connection.")
    if kind == "swarm":
        services = await reader.services(label=f"{STACK_LABEL}={name}")
        if not services:
            raise HTTPException(status.HTTP_404_NOT_FOUND, "No such stack.")
        return swarm_compose(name, services, await reader.networks())
    listed = await reader.containers(f"{COMPOSE_PROJECT}={name}")
    if not listed:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "No such Compose project.")
    return compose_project(name, [await reader.container(c["Id"]) for c in listed])


@router.get("/unmanaged/{kind}/{name}")
async def generate(
    kind: Kind,
    name: str,
    request: Request,
    db: DB,
    _: AdminAccess,
    endpoint: str | None = Query(default=None),
) -> GeneratedOut:
    """A compose file rebuilt from what runs, as a starting point for adding the stack."""
    generated = await _generate(await _target(db, request, endpoint), kind, name)
    return GeneratedOut(compose=generated.compose, notes=generated.notes)


@router.post("/unmanaged/{kind}/{name}/adopt", response_model=SaveResult)
async def adopt(
    kind: Kind,
    name: str,
    body: AdoptIn,
    request: Request,
    db: DB,
    access: AdminAccess,
    editor: Editor,
    endpoint: str | None = Query(default=None),
) -> SaveResult | JSONResponse:
    """Commit a compose file for a stack deployed outside Novaport to a repository."""
    if not NAME_PATTERN.match(name):
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, "Not a valid stack name.")
    if await db.scalar(select(Stack).where(Stack.name == name, Stack.removed_at.is_(None))):
        raise HTTPException(status.HTTP_409_CONFLICT, f"A stack named {name} already exists.")
    repo = await db.get(Repository, body.repository_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found.")
    if endpoint is not None:
        await _target(db, request, endpoint)

    stack_type = StackType(kind)
    settings: dict[str, str] = {"deploy": body.deploy.value}
    if stack_type == StackType.COMPOSE:
        settings["type"] = stack_type.value
    if endpoint is not None:
        settings["endpoint"] = endpoint
    files = {
        "compose.yaml": body.compose,
        SETTINGS_FILE: yaml.safe_dump(settings, sort_keys=False),
    }
    details: dict[str, object] = {"repository": repo.name, "type": kind, "endpoint": endpoint}
    try:
        result = await editor.add_stack(
            repo.id,
            name,
            stack_type,
            files,
            access.user,
            body.message or f"Add {name}",
        )
    except EditConflict as exc:
        return conflict_response(exc)
    except (EditError, LockTimeout) as exc:
        access.audit(db, "stack.adopt", target=name, outcome=Outcome.FAILED, details=details)
        await db.commit()
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc
    access.audit(
        db,
        "stack.adopt",
        target=name,
        details=details | {"commit": result.commit, "pull_request": result.pull_request_url},
    )
    await db.commit()
    return save_result(request, result)
