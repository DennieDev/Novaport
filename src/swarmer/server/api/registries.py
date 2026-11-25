# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from swarmer.crypto import Cipher, MissingSecretKey
from swarmer.models import Registry, Role
from swarmer.registries.client import RegistryClient, RegistryError
from swarmer.registries.reference import normalize_host
from swarmer.server.deps import DB, AdminAccess, require_role

router = APIRouter(
    prefix="/api/registries", tags=["registries"], dependencies=[Depends(require_role(Role.ADMIN))]
)

Host = Annotated[str, Field(min_length=1, max_length=255, pattern=r"^[\w.:/-]+$")]


class RegistryIn(BaseModel):
    host: Host
    username: str = Field(min_length=1, max_length=255)
    password: str = Field(min_length=1, max_length=5000)


class RegistryPatch(BaseModel):
    username: str | None = Field(default=None, min_length=1, max_length=255)
    password: str | None = Field(default=None, min_length=1, max_length=5000)


class RegistryOut(BaseModel):
    id: uuid.UUID
    host: str
    username: str
    created_at: datetime


class RegistryCheck(BaseModel):
    ok: bool
    error: str | None


def get_cipher(request: Request) -> Cipher:
    cipher: Cipher = request.app.state.cipher
    return cipher


def get_registry_client(request: Request) -> RegistryClient:
    client: RegistryClient = request.app.state.registry
    return client


CipherDep = Annotated[Cipher, Depends(get_cipher)]
ClientDep = Annotated[RegistryClient, Depends(get_registry_client)]


def _out(r: Registry) -> RegistryOut:
    return RegistryOut(id=r.id, host=r.host, username=r.username, created_at=r.created_at)


def _encrypt(cipher: Cipher, password: str) -> str:
    try:
        return cipher.encrypt(password)
    except MissingSecretKey as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


async def _get(db: DB, registry_id: uuid.UUID) -> Registry:
    registry = await db.get(Registry, registry_id)
    if registry is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Registry not found.")
    return registry


@router.get("")
async def list_registries(db: DB) -> list[RegistryOut]:
    return [_out(r) for r in await db.scalars(select(Registry).order_by(Registry.host))]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_registry(
    body: RegistryIn, db: DB, cipher: CipherDep, client: ClientDep, access: AdminAccess
) -> RegistryOut:
    registry = Registry(
        id=uuid.uuid4(),
        host=normalize_host(body.host),
        username=body.username,
        password=_encrypt(cipher, body.password),
    )
    db.add(registry)
    access.audit(db, "registry.create", target=registry.host)
    try:
        await db.commit()
    except IntegrityError as exc:
        raise HTTPException(
            status.HTTP_409_CONFLICT, "Credentials for this registry exist already."
        ) from exc
    client.clear_cache()
    await db.refresh(registry)
    return _out(registry)


@router.patch("/{registry_id}")
async def update_registry(
    registry_id: uuid.UUID,
    body: RegistryPatch,
    db: DB,
    cipher: CipherDep,
    client: ClientDep,
    access: AdminAccess,
) -> RegistryOut:
    registry = await _get(db, registry_id)
    if body.username is not None:
        registry.username = body.username
    if body.password is not None:
        registry.password = _encrypt(cipher, body.password)
    access.audit(
        db,
        "registry.update",
        target=registry.host,
        details={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    await db.commit()
    client.clear_cache()
    return _out(registry)


@router.delete("/{registry_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_registry(
    registry_id: uuid.UUID, db: DB, client: ClientDep, access: AdminAccess
) -> None:
    registry = await _get(db, registry_id)
    await db.delete(registry)
    access.audit(db, "registry.delete", target=registry.host)
    await db.commit()
    client.clear_cache()


@router.post("/{registry_id}/check")
async def check_registry(registry_id: uuid.UUID, db: DB, client: ClientDep) -> RegistryCheck:
    """Log in to the registry with the stored credentials."""
    registry = await _get(db, registry_id)
    try:
        await client.check_login(registry.host)
    except RegistryError as exc:
        return RegistryCheck(ok=False, error=str(exc))
    return RegistryCheck(ok=True, error=None)
