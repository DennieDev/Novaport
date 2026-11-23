# SPDX-License-Identifier: AGPL-3.0-or-later
import re
import secrets
import shutil
import uuid
from datetime import datetime
from typing import Annotated, Self

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import func, select

from novaport.crypto import Cipher, MissingSecretKey, generate_ssh_keypair
from novaport.forges import Forge
from novaport.models import DeployMode, EditMode, GitAuth, Repository, Stack
from novaport.server.deps import DB, get_settings, require_admin
from novaport.stacks.sync import Syncer, mirror_path

router = APIRouter(
    prefix="/api/repositories", tags=["repositories"], dependencies=[Depends(require_admin)]
)

REMOTE_URL = re.compile(r"^(https?://|ssh://|git://)[^\s]+$|^[\w.-]+@[\w.-]+:[^\s]+$")
LOCAL_URL = re.compile(r"^(file://)?/[^\s]*$")


def check_url(url: str, allow_local: bool) -> None:
    if url.startswith("-") or not (REMOTE_URL.match(url) or (allow_local and LOCAL_URL.match(url))):
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT,
            "Use an https://, ssh://, or user@host:path repository URL.",
        )


def get_cipher(request: Request) -> Cipher:
    cipher: Cipher = request.app.state.cipher
    return cipher


def get_syncer(request: Request) -> Syncer:
    syncer: Syncer = request.app.state.syncer
    return syncer


CipherDep = Annotated[Cipher, Depends(get_cipher)]
SyncerDep = Annotated[Syncer, Depends(get_syncer)]

PollInterval = Annotated[int, Field(ge=0, le=86_400)]


class RepositoryIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=500)
    branch: str = Field(default="main", min_length=1, max_length=255, pattern=r"^[^\s~^:?*\[\\]+$")
    stacks_root: str = Field(default="stacks", max_length=255, pattern=r"^[^\s:]*$")
    auth: GitAuth = GitAuth.NONE
    token: str | None = Field(default=None, max_length=1000)
    poll_interval: PollInterval = 300
    default_deploy_mode: DeployMode = DeployMode.MANUAL
    default_edit_mode: EditMode = EditMode.COMMIT
    forge: Forge = Forge.NONE
    forge_api_url: str | None = Field(default=None, max_length=500, pattern=r"^https?://")
    forge_token: str | None = Field(default=None, max_length=1000)

    @model_validator(mode="after")
    def _token_matches_auth(self) -> Self:
        if self.auth == GitAuth.TOKEN and not self.token:
            raise ValueError("A token is required for token authentication.")
        return self


class RepositoryPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    url: str | None = Field(default=None, min_length=1, max_length=500)
    branch: str | None = Field(default=None, min_length=1, max_length=255)
    stacks_root: str | None = Field(default=None, max_length=255, pattern=r"^[^\s:]*$")
    auth: GitAuth | None = None
    token: str | None = Field(default=None, max_length=1000)
    poll_interval: PollInterval | None = None
    default_deploy_mode: DeployMode | None = None
    default_edit_mode: EditMode | None = None
    forge: Forge | None = None
    forge_api_url: str | None = Field(default=None, max_length=500)
    forge_token: str | None = Field(default=None, max_length=1000)


class RepositoryOut(BaseModel):
    id: uuid.UUID
    name: str
    url: str
    branch: str
    stacks_root: str
    auth: GitAuth
    ssh_public_key: str | None
    has_token: bool
    webhook_path: str
    webhook_secret: str
    poll_interval: int
    default_deploy_mode: DeployMode
    default_edit_mode: EditMode
    forge: Forge
    forge_api_url: str | None
    has_forge_token: bool
    created_at: datetime
    last_synced_at: datetime | None
    last_commit: str | None
    last_error: str | None
    stack_count: int


class SyncOut(BaseModel):
    commit: str | None
    new_revisions: int
    queued_deployments: int
    skipped: bool
    error: str | None


def to_out(repo: Repository, stack_count: int) -> RepositoryOut:
    return RepositoryOut(
        id=repo.id,
        name=repo.name,
        url=repo.url,
        branch=repo.branch,
        stacks_root=repo.stacks_root,
        auth=repo.auth,
        ssh_public_key=repo.ssh_public_key,
        has_token=repo.token is not None,
        webhook_path=f"/api/webhooks/git/{repo.id}",
        webhook_secret=repo.webhook_secret,
        poll_interval=repo.poll_interval,
        default_deploy_mode=repo.default_deploy_mode,
        default_edit_mode=repo.default_edit_mode,
        forge=Forge(repo.forge),
        forge_api_url=repo.forge_api_url,
        has_forge_token=repo.forge_token is not None,
        created_at=repo.created_at,
        last_synced_at=repo.last_synced_at,
        last_commit=repo.last_commit,
        last_error=repo.last_error,
        stack_count=stack_count,
    )


async def _stack_counts(db: DB) -> dict[uuid.UUID, int]:
    rows = await db.execute(
        select(Stack.repository_id, func.count())
        .where(Stack.removed_at.is_(None))
        .group_by(Stack.repository_id)
    )
    return {repo_id: count for repo_id, count in rows}


async def _get(db: DB, repository_id: uuid.UUID) -> Repository:
    repo = await db.get(Repository, repository_id)
    if repo is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Repository not found.")
    return repo


def _apply_auth(repo: Repository, auth: GitAuth, token: str | None, cipher: Cipher) -> None:
    try:
        if auth == GitAuth.SSH and repo.ssh_private_key is None:
            private, public = generate_ssh_keypair(f"novaport-{repo.name}")
            repo.ssh_private_key = cipher.encrypt(private)
            repo.ssh_public_key = public
        if token:
            repo.token = cipher.encrypt(token)
    except MissingSecretKey as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    repo.auth = auth


def _apply_forge_token(repo: Repository, token: str | None, cipher: Cipher) -> None:
    if not token:
        return
    try:
        repo.forge_token = cipher.encrypt(token)
    except MissingSecretKey as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


async def _name_taken(db: DB, name: str, exclude: uuid.UUID | None = None) -> bool:
    query = select(Repository.id).where(Repository.name == name)
    if exclude:
        query = query.where(Repository.id != exclude)
    return await db.scalar(query) is not None


@router.get("")
async def list_repositories(db: DB) -> list[RepositoryOut]:
    counts = await _stack_counts(db)
    repos = await db.scalars(select(Repository).order_by(Repository.name))
    return [to_out(r, counts.get(r.id, 0)) for r in repos]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_repository(
    body: RepositoryIn, request: Request, db: DB, cipher: CipherDep
) -> RepositoryOut:
    check_url(body.url, get_settings(request).allow_local_repositories)
    if await _name_taken(db, body.name):
        raise HTTPException(status.HTTP_409_CONFLICT, "A repository with this name exists.")
    repo = Repository(
        id=uuid.uuid4(),
        name=body.name,
        url=body.url,
        branch=body.branch,
        stacks_root=body.stacks_root.strip("/"),
        poll_interval=body.poll_interval,
        default_deploy_mode=body.default_deploy_mode,
        default_edit_mode=body.default_edit_mode,
        forge=body.forge,
        forge_api_url=body.forge_api_url,
        webhook_secret=secrets.token_hex(32),
    )
    _apply_auth(repo, body.auth, body.token, cipher)
    _apply_forge_token(repo, body.forge_token, cipher)
    db.add(repo)
    await db.commit()
    await db.refresh(repo)
    return to_out(repo, 0)


@router.get("/{repository_id}")
async def get_repository(repository_id: uuid.UUID, db: DB) -> RepositoryOut:
    repo = await _get(db, repository_id)
    return to_out(repo, (await _stack_counts(db)).get(repo.id, 0))


@router.patch("/{repository_id}")
async def update_repository(
    repository_id: uuid.UUID, body: RepositoryPatch, request: Request, db: DB, cipher: CipherDep
) -> RepositoryOut:
    repo = await _get(db, repository_id)
    if body.url is not None:
        check_url(body.url, get_settings(request).allow_local_repositories)
        repo.url = body.url
    if body.name is not None:
        if await _name_taken(db, body.name, exclude=repo.id):
            raise HTTPException(status.HTTP_409_CONFLICT, "A repository with this name exists.")
        repo.name = body.name
    for field in ("branch", "poll_interval", "default_deploy_mode", "default_edit_mode", "forge"):
        value = getattr(body, field)
        if value is not None:
            setattr(repo, field, value)
    if body.stacks_root is not None:
        repo.stacks_root = body.stacks_root.strip("/")
    if body.forge_api_url is not None:
        repo.forge_api_url = body.forge_api_url or None
    _apply_forge_token(repo, body.forge_token, cipher)
    if body.auth is not None or body.token:
        auth = body.auth or repo.auth
        if auth == GitAuth.TOKEN and not (body.token or repo.token):
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_CONTENT,
                "A token is required for token authentication.",
            )
        _apply_auth(repo, auth, body.token, cipher)
    await db.commit()
    return to_out(repo, (await _stack_counts(db)).get(repo.id, 0))


@router.post("/{repository_id}/ssh-key")
async def regenerate_ssh_key(repository_id: uuid.UUID, db: DB, cipher: CipherDep) -> RepositoryOut:
    repo = await _get(db, repository_id)
    repo.ssh_private_key = None
    _apply_auth(repo, GitAuth.SSH, None, cipher)
    await db.commit()
    return to_out(repo, (await _stack_counts(db)).get(repo.id, 0))


@router.post("/{repository_id}/webhook-secret")
async def regenerate_webhook_secret(repository_id: uuid.UUID, db: DB) -> RepositoryOut:
    repo = await _get(db, repository_id)
    repo.webhook_secret = secrets.token_hex(32)
    await db.commit()
    return to_out(repo, (await _stack_counts(db)).get(repo.id, 0))


@router.delete("/{repository_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_repository(repository_id: uuid.UUID, request: Request, db: DB) -> None:
    """Forget a repository and its stacks. Running services are left alone."""
    repo = await _get(db, repository_id)
    await db.delete(repo)
    await db.commit()
    shutil.rmtree(mirror_path(get_settings(request), repository_id), ignore_errors=True)


@router.post("/{repository_id}/sync")
async def sync_repository(
    repository_id: uuid.UUID, request: Request, db: DB, syncer: SyncerDep
) -> SyncOut:
    await _get(db, repository_id)
    result = await syncer.sync(repository_id)
    if result.queued_deployments and request.app.state.jobs is not None:
        request.app.state.jobs.wake()
    return SyncOut(
        commit=result.commit,
        new_revisions=len(result.new_revisions),
        queued_deployments=result.queued_deployments,
        skipped=result.skipped,
        error=result.error,
    )
