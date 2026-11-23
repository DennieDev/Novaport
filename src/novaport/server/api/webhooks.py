# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import json
import logging
import uuid

from fastapi import APIRouter, HTTPException, Request, status
from pydantic import BaseModel

from novaport.models import Repository
from novaport.server.deps import DB
from novaport.stacks.webhooks import verify

log = logging.getLogger(__name__)

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

MAX_BODY = 5 * 1024 * 1024


class WebhookResult(BaseModel):
    status: str


def _pushed_branch(body: bytes) -> str | None:
    try:
        ref = json.loads(body).get("ref")
    except (ValueError, AttributeError):
        return None
    if isinstance(ref, str) and ref.startswith("refs/heads/"):
        return ref.removeprefix("refs/heads/")
    return None


@router.post("/git/{repository_id}", status_code=status.HTTP_202_ACCEPTED)
async def git_push(repository_id: uuid.UUID, request: Request, db: DB) -> WebhookResult:
    body = await request.body()
    if len(body) > MAX_BODY:
        raise HTTPException(status.HTTP_413_CONTENT_TOO_LARGE, "Payload too large.")
    repo = await db.get(Repository, repository_id)
    # Same response for unknown repositories and bad signatures, so IDs can't be probed.
    if repo is None or not verify(request.headers, body, repo.webhook_secret):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "Invalid signature.")

    if request.headers.get("x-github-event") == "ping":
        return WebhookResult(status="pong")
    branch = _pushed_branch(body)
    if branch is not None and branch != repo.branch:
        return WebhookResult(status="ignored: other branch")

    jobs = request.app.state.jobs
    if jobs is not None:
        jobs.request_sync(repo.id)
    else:
        task = asyncio.create_task(request.app.state.syncer.sync(repo.id))
        request.app.state.webhook_tasks.add(task)
        task.add_done_callback(request.app.state.webhook_tasks.discard)
    return WebhookResult(status="sync started")
