# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid

from fastapi import APIRouter, Depends, HTTPException, Request, status
from sqlalchemy import select

from novaport.auth.access import Access
from novaport.deploy.cli import DockerCli
from novaport.docker.views import STACK_LABEL
from novaport.locks import try_lock
from novaport.models import Outcome, Role, Stack, StackType
from novaport.process import CommandTimeout, Result
from novaport.server.deps import (
    DB,
    CurrentAccess,
    Reader,
    current_user,
    require_service_role,
    service_stack,
)

router = APIRouter(prefix="/api", tags=["restart"], dependencies=[Depends(current_user)])

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


def _cli(request: Request) -> DockerCli:
    cli: DockerCli = request.app.state.pipeline.cli
    return cli


async def _finish(
    db: DB,
    access: Access,
    action: str,
    target: str,
    stack_id: uuid.UUID | None,
    results: list[Result],
) -> None:
    failed = [r for r in results if not r.ok]
    access.audit(
        db,
        action,
        outcome=Outcome.FAILED if failed else Outcome.SUCCESS,
        target=target,
        stack_id=stack_id,
    )
    await db.commit()
    if failed:
        raise HTTPException(status.HTTP_502_BAD_GATEWAY, failed[0].summary())


@router.post("/services/{service_id}/restart", status_code=status.HTTP_204_NO_CONTENT)
async def restart_service(
    service_id: str, request: Request, reader: Reader, db: DB, access: CurrentAccess
) -> None:
    """Replace the service's tasks with fresh ones, keeping its spec."""
    raw = await reader.service(service_id)
    await require_service_role(db, access, Role.OPERATOR, raw, action="service.restart")
    name = raw["Spec"]["Name"]
    stack_name = service_stack(raw)
    stack_id = (
        await db.scalar(select(Stack.id).where(Stack.name == stack_name)) if stack_name else None
    )
    try:
        result = await _cli(request).restart_service(name)
    except CommandTimeout as exc:
        raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc)) from exc
    await _finish(db, access, "service.restart", name, stack_id, [result])


@router.post("/stacks/{stack_id}/restart", status_code=status.HTTP_204_NO_CONTENT)
async def restart_stack(
    stack_id: uuid.UUID, request: Request, reader: Reader, db: DB, access: CurrentAccess
) -> None:
    """Restart every service of a Swarm stack, or every container of a Compose project."""
    access.require(Role.OPERATOR, stack_id, action="stack.restart")
    stack = await db.get(Stack, stack_id)
    if stack is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Stack not found.")
    cli = _cli(request)
    async with try_lock(request.app.state.engine, f"deploy:{stack.id}") as acquired:
        if not acquired:
            raise HTTPException(
                status.HTTP_409_CONFLICT, "A deployment of this stack is running. Try again later."
            )
        try:
            if stack.type == StackType.SWARM:
                services = await reader.services(label=f"{STACK_LABEL}={stack.name}")
                names = sorted(s["Spec"]["Name"] for s in services)
                results = [await cli.restart_service(n) for n in names]
            else:
                containers = await reader.containers(f"{COMPOSE_PROJECT_LABEL}={stack.name}")
                ids = [c["Id"] for c in containers]
                results = [await cli.restart_containers(ids)] if ids else []
        except CommandTimeout as exc:
            raise HTTPException(status.HTTP_504_GATEWAY_TIMEOUT, str(exc)) from exc
    if not results:
        raise HTTPException(status.HTTP_409_CONFLICT, f"Nothing of {stack.name} is running.")
    await _finish(db, access, "stack.restart", stack.name, stack.id, results)
