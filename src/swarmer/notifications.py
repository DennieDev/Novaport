# SPDX-License-Identifier: AGPL-3.0-or-later
"""Sends events to webhook, Slack, and email channels."""

import asyncio
import hashlib
import hmac
import json
import logging
import smtplib
import ssl
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime
from email.message import EmailMessage
from typing import Any

import httpx
from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker
from sqlalchemy.orm import selectinload

from swarmer.config import Settings
from swarmer.crypto import Cipher
from swarmer.models import (
    ChannelKind,
    EventType,
    NotificationChannel,
    NotificationSubscription,
)

log = logging.getLogger(__name__)


class DeliveryError(Exception):
    pass


@dataclass
class Event:
    type: EventType
    title: str
    text: str = ""
    stack_id: uuid.UUID | None = None
    stack_name: str | None = None
    # Path in the Swarmer UI, such as /deployments/<id>.
    link: str | None = None
    data: dict[str, Any] = field(default_factory=dict)
    at: datetime = field(default_factory=lambda: datetime.now(UTC))


def _sign(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


class Notifier:
    def __init__(
        self,
        settings: Settings,
        sessionmaker: async_sessionmaker[AsyncSession],
        cipher: Cipher,
        http: httpx.AsyncClient,
    ) -> None:
        self.settings = settings
        self.sessionmaker = sessionmaker
        self.cipher = cipher
        self.http = http
        self._pending: set[asyncio.Task[None]] = set()

    def emit(self, event: Event) -> None:
        """Deliver in the background, so a slow channel never holds up a deploy."""
        task = asyncio.create_task(self.deliver(event), name=f"notify-{event.type}")
        self._pending.add(task)
        task.add_done_callback(self._pending.discard)

    async def drain(self) -> None:
        while self._pending:
            await asyncio.gather(*list(self._pending), return_exceptions=True)

    def _link(self, event: Event) -> str | None:
        if not event.link:
            return None
        base = (self.settings.public_url or "").rstrip("/")
        return f"{base}{event.link}" if base else event.link

    async def deliver(self, event: Event) -> None:
        try:
            async with self.sessionmaker() as db:
                matching = or_(
                    NotificationSubscription.stack_id.is_(None),
                    NotificationSubscription.stack_id == event.stack_id,
                )
                channels = await db.scalars(
                    select(NotificationChannel)
                    .options(selectinload(NotificationChannel.subscriptions))
                    .where(
                        NotificationChannel.enabled,
                        NotificationChannel.id.in_(
                            select(NotificationSubscription.channel_id).where(
                                NotificationSubscription.event.in_([event.type.value, "*"]),
                                matching,
                            )
                        ),
                    )
                )
                for channel in channels:
                    try:
                        await self.send(channel, event)
                    except DeliveryError as exc:
                        log.warning("Notification to %s failed: %s", channel.name, exc)
                        channel.last_error = str(exc)[:2000]
                    else:
                        channel.last_sent_at = datetime.now(UTC)
                        channel.last_error = None
                await db.commit()
        except Exception:
            log.exception("Delivering %s failed", event.type)

    async def send(self, channel: NotificationChannel, event: Event) -> None:
        try:
            url = self.cipher.decrypt(channel.url) if channel.url else None
            secret = self.cipher.decrypt(channel.signing_secret) if channel.signing_secret else None
        except (ValueError, RuntimeError) as exc:
            raise DeliveryError(f"Stored channel settings can't be decrypted: {exc}") from exc
        if channel.kind == ChannelKind.EMAIL:
            await self._email(channel, event)
            return
        if not url:
            raise DeliveryError("The channel has no URL.")
        if channel.kind == ChannelKind.SLACK:
            await self._slack(url, event)
        else:
            await self._webhook(url, secret, event)

    async def _post(self, url: str, body: bytes, headers: dict[str, str]) -> None:
        try:
            response = await self.http.post(
                url, content=body, headers={"Content-Type": "application/json", **headers}
            )
        except httpx.HTTPError as exc:
            raise DeliveryError(f"Can't reach the endpoint: {exc}") from exc
        if response.status_code >= 300:
            raise DeliveryError(f"The endpoint answered HTTP {response.status_code}.")

    async def _webhook(self, url: str, secret: str | None, event: Event) -> None:
        body = json.dumps(
            {
                "event": event.type.value,
                "title": event.title,
                "text": event.text,
                "stack": {"id": str(event.stack_id), "name": event.stack_name}
                if event.stack_id
                else None,
                "link": self._link(event),
                "data": event.data,
                "at": event.at.isoformat(),
            },
            default=str,
        ).encode()
        headers = {"X-Swarmer-Event": event.type.value}
        if secret:
            headers["X-Swarmer-Signature"] = _sign(secret, body)
        await self._post(url, body, headers)

    async def _slack(self, url: str, event: Event) -> None:
        link = self._link(event)
        title = f"<{link}|{event.title}>" if link and link.startswith("http") else event.title
        text = f"*{title}*" + (f"\n{event.text}" if event.text else "")
        await self._post(url, json.dumps({"text": text}).encode(), {})

    async def _email(self, channel: NotificationChannel, event: Event) -> None:
        settings = self.settings
        if not settings.smtp_host:
            raise DeliveryError("SWARMER_SMTP_HOST is not set.")
        recipients = [r.strip() for r in (channel.recipients or "").split(",") if r.strip()]
        if not recipients:
            raise DeliveryError("The channel has no recipients.")
        message = EmailMessage()
        message["Subject"] = f"[Swarmer] {event.title}"
        message["From"] = settings.smtp_from
        message["To"] = ", ".join(recipients)
        link = self._link(event)
        message.set_content("\n\n".join(p for p in (event.text, link) if p) or event.title)
        password = settings.smtp_password.get_secret_value() if settings.smtp_password else None

        def send() -> None:
            context = ssl.create_default_context()
            smtp_class = smtplib.SMTP_SSL if settings.smtp_security == "tls" else smtplib.SMTP
            kwargs: dict[str, Any] = {"timeout": 20}
            if settings.smtp_security == "tls":
                kwargs["context"] = context
            with smtp_class(settings.smtp_host or "", settings.smtp_port, **kwargs) as smtp:
                if settings.smtp_security == "starttls":
                    smtp.starttls(context=context)
                if settings.smtp_username:
                    smtp.login(settings.smtp_username, password or "")
                smtp.send_message(message)

        try:
            await asyncio.to_thread(send)
        except (OSError, smtplib.SMTPException) as exc:
            raise DeliveryError(f"Sending mail failed: {exc}") from exc
