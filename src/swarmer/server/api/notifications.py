# SPDX-License-Identifier: AGPL-3.0-or-later
import uuid
from datetime import datetime
from typing import Annotated, Self

from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import BaseModel, Field, model_validator
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import selectinload

from swarmer.crypto import Cipher, MissingSecretKey
from swarmer.models import (
    ChannelKind,
    EventType,
    NotificationChannel,
    NotificationSubscription,
    Role,
    Stack,
)
from swarmer.notifications import DeliveryError, Event, Notifier
from swarmer.server.deps import DB, AdminAccess, require_role

router = APIRouter(
    prefix="/api/notification-channels",
    tags=["notifications"],
    dependencies=[Depends(require_role(Role.ADMIN))],
)

ALL_EVENTS = "*"


class SubscriptionIn(BaseModel):
    event: str = Field(min_length=1, max_length=40)
    stack_id: uuid.UUID | None = None

    @model_validator(mode="after")
    def _known(self) -> Self:
        if self.event != ALL_EVENTS and self.event not in {e.value for e in EventType}:
            raise ValueError(f"Unknown event {self.event!r}.")
        return self


class SubscriptionOut(SubscriptionIn):
    stack_name: str | None


class ChannelIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    kind: ChannelKind
    enabled: bool = True
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://\S+$")
    signing_secret: str | None = Field(default=None, max_length=500)
    recipients: str | None = Field(default=None, max_length=1000)
    subscriptions: list[SubscriptionIn] = Field(default_factory=list, max_length=200)

    @model_validator(mode="after")
    def _target(self) -> Self:
        if self.kind == ChannelKind.EMAIL and not (self.recipients or "").strip():
            raise ValueError("Email channels need recipients.")
        if self.kind != ChannelKind.EMAIL and not self.url:
            raise ValueError("Webhook and Slack channels need a URL.")
        return self


class ChannelPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=100)
    enabled: bool | None = None
    url: str | None = Field(default=None, max_length=2000, pattern=r"^https?://\S+$")
    # Empty string removes the secret.
    signing_secret: str | None = Field(default=None, max_length=500)
    recipients: str | None = Field(default=None, max_length=1000)
    subscriptions: list[SubscriptionIn] | None = Field(default=None, max_length=200)


class ChannelOut(BaseModel):
    id: uuid.UUID
    name: str
    kind: ChannelKind
    enabled: bool
    has_url: bool
    has_signing_secret: bool
    recipients: str | None
    subscriptions: list[SubscriptionOut]
    last_sent_at: datetime | None
    last_error: str | None


class TestResult(BaseModel):
    ok: bool
    error: str | None


def get_cipher(request: Request) -> Cipher:
    cipher: Cipher = request.app.state.cipher
    return cipher


CipherDep = Annotated[Cipher, Depends(get_cipher)]


async def _out(db: DB, channel: NotificationChannel) -> ChannelOut:
    stacks = {i: n for i, n in await db.execute(select(Stack.id, Stack.name))}
    return ChannelOut(
        id=channel.id,
        name=channel.name,
        kind=channel.kind,
        enabled=channel.enabled,
        has_url=channel.url is not None,
        has_signing_secret=channel.signing_secret is not None,
        recipients=channel.recipients,
        subscriptions=[
            SubscriptionOut(
                event=s.event,
                stack_id=s.stack_id,
                stack_name=stacks.get(s.stack_id) if s.stack_id else None,
            )
            for s in sorted(channel.subscriptions, key=lambda s: (s.event, str(s.stack_id)))
        ],
        last_sent_at=channel.last_sent_at,
        last_error=channel.last_error,
    )


async def _get(db: DB, channel_id: uuid.UUID) -> NotificationChannel:
    channel = await db.scalar(
        select(NotificationChannel)
        .options(selectinload(NotificationChannel.subscriptions))
        .where(NotificationChannel.id == channel_id)
    )
    if channel is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "Channel not found.")
    return channel


def _encrypt(cipher: Cipher, value: str) -> str:
    try:
        return cipher.encrypt(value)
    except MissingSecretKey as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_CONTENT, str(exc)) from exc


async def _set_subscriptions(
    db: DB, channel: NotificationChannel, wanted: list[SubscriptionIn]
) -> None:
    stack_ids = {s.stack_id for s in wanted if s.stack_id}
    found = set(await db.scalars(select(Stack.id).where(Stack.id.in_(stack_ids))))
    if missing := stack_ids - found:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"Unknown stack: {next(iter(missing))}"
        )
    channel.subscriptions.clear()
    await db.flush()
    unique = {(s.event, s.stack_id) for s in wanted}
    channel.subscriptions.extend(
        NotificationSubscription(event=event, stack_id=stack_id)
        for event, stack_id in sorted(unique, key=str)
    )


@router.get("")
async def list_channels(db: DB) -> list[ChannelOut]:
    channels = await db.scalars(
        select(NotificationChannel)
        .options(selectinload(NotificationChannel.subscriptions))
        .order_by(NotificationChannel.name)
    )
    return [await _out(db, c) for c in channels]


@router.get("/events")
async def event_types() -> list[str]:
    return [e.value for e in EventType]


@router.post("", status_code=status.HTTP_201_CREATED)
async def create_channel(
    body: ChannelIn, db: DB, cipher: CipherDep, access: AdminAccess
) -> ChannelOut:
    channel = NotificationChannel(
        id=uuid.uuid4(),
        name=body.name,
        kind=body.kind,
        enabled=body.enabled,
        url=_encrypt(cipher, body.url) if body.url else None,
        signing_secret=_encrypt(cipher, body.signing_secret) if body.signing_secret else None,
        recipients=body.recipients,
        subscriptions=[],
    )
    db.add(channel)
    await _set_subscriptions(db, channel, body.subscriptions)
    access.audit(db, "notification_channel.create", target=channel.name)
    try:
        await db.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "A channel with this name exists.") from exc
    return await _out(db, await _get(db, channel.id))


@router.patch("/{channel_id}")
async def update_channel(
    channel_id: uuid.UUID, body: ChannelPatch, db: DB, cipher: CipherDep, access: AdminAccess
) -> ChannelOut:
    channel = await _get(db, channel_id)
    if body.name is not None:
        channel.name = body.name
    if body.enabled is not None:
        channel.enabled = body.enabled
    if body.url is not None:
        channel.url = _encrypt(cipher, body.url)
    if body.signing_secret is not None:
        channel.signing_secret = (
            _encrypt(cipher, body.signing_secret) if body.signing_secret else None
        )
    if body.recipients is not None:
        channel.recipients = body.recipients
    if body.subscriptions is not None:
        await _set_subscriptions(db, channel, body.subscriptions)
    access.audit(
        db,
        "notification_channel.update",
        target=channel.name,
        details={"fields": sorted(body.model_dump(exclude_unset=True))},
    )
    try:
        await db.commit()
    except IntegrityError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, "A channel with this name exists.") from exc
    db.expire_all()
    return await _out(db, await _get(db, channel_id))


@router.delete("/{channel_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_channel(channel_id: uuid.UUID, db: DB, access: AdminAccess) -> None:
    channel = await _get(db, channel_id)
    await db.delete(channel)
    access.audit(db, "notification_channel.delete", target=channel.name)
    await db.commit()


@router.post("/{channel_id}/test")
async def test_channel(channel_id: uuid.UUID, request: Request, db: DB) -> TestResult:
    """Send a test message right away, whatever the channel subscribes to."""
    channel = await _get(db, channel_id)
    notifier: Notifier = request.app.state.notifier
    try:
        await notifier.send(
            channel,
            Event(
                EventType.DEPLOY_SUCCEEDED,
                "Test message from Swarmer",
                "If you can read this, the channel works.",
                data={"test": True},
            ),
        )
    except DeliveryError as exc:
        return TestResult(ok=False, error=str(exc))
    return TestResult(ok=True, error=None)
