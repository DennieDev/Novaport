# SPDX-License-Identifier: AGPL-3.0-or-later
"""Change notifications for the UI, sent with Postgres NOTIFY when a transaction commits.

Notifications only say what changed (table, row, stack), never the data. Clients reload through
the API, which applies access control as usual.
"""

import asyncio
import contextlib
import json
import logging
import uuid
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Any

from sqlalchemy import event, text
from sqlalchemy.ext.asyncio import AsyncEngine
from sqlalchemy.orm import ORMExecuteState, Session

from novaport.models import (
    Deployment,
    DeploymentEvent,
    Endpoint,
    ImageCheck,
    Repository,
    Revision,
    Stack,
)

log = logging.getLogger(__name__)

CHANNEL = "novaport"
# Postgres refuses payloads of 8000 bytes or more.
MAX_PAYLOAD = 7000
QUEUE_SIZE = 256
RECONNECT_DELAYS = (1.0, 2.0, 5.0, 10.0, 30.0)

_PENDING = "novaport_events"
_WATCHED: dict[type[Any], str] = {
    Deployment: "deployments",
    DeploymentEvent: "deployments",
    Endpoint: "endpoints",
    ImageCheck: "image_checks",
    Repository: "repositories",
    Revision: "revisions",
    Stack: "stacks",
}


@dataclass(frozen=True)
class Change:
    topic: str
    id: str | None = None
    stack: str | None = None

    def as_dict(self) -> dict[str, str]:
        fields = (("topic", self.topic), ("id", self.id), ("stack", self.stack))
        return {k: v for k, v in fields if v}


# Sent to every subscriber after the listener reconnects, since changes may have been missed.
RESYNC = Change("*")


def _str(value: object) -> str | None:
    return str(value) if value is not None else None


def _change(session: Session, obj: object) -> Change | None:
    topic = _WATCHED.get(type(obj))
    if topic is None:
        return None
    if isinstance(obj, DeploymentEvent):
        deployment = session.identity_map.get(session.identity_key(Deployment, obj.deployment_id))
        stack_id = deployment.stack_id if isinstance(deployment, Deployment) else None
        return Change(topic, _str(obj.deployment_id), _str(stack_id))
    if isinstance(obj, Stack):
        return Change(topic, _str(obj.id), _str(obj.id))
    return Change(topic, _str(getattr(obj, "id", None)), _str(getattr(obj, "stack_id", None)))


def _pending(session: Session) -> set[Change]:
    pending: set[Change] = session.info.setdefault(_PENDING, set())
    return pending


def _after_flush(session: Session, _: object) -> None:
    dirty = [o for o in session.dirty if session.is_modified(o, include_collections=False)]
    for obj in (*session.new, *dirty, *session.deleted):
        if (change := _change(session, obj)) is not None:
            _pending(session).add(change)


def _orm_execute(state: ORMExecuteState) -> None:
    if not (state.is_update or state.is_delete or state.is_insert):
        return
    mapper = state.bind_mapper
    if mapper is not None and (topic := _WATCHED.get(mapper.class_)) is not None:
        _pending(state.session).add(Change(topic))


def _before_commit(session: Session) -> None:
    # Flush now, or changes the commit itself would flush are missed.
    if session.new or session.dirty or session.deleted:
        session.flush()
    changes = session.info.pop(_PENDING, None)
    if not changes:
        return
    for payload in payloads(changes):
        session.execute(text("SELECT pg_notify(:c, :p)"), {"c": CHANNEL, "p": payload})


def _discard(session: Session, *_: object) -> None:
    session.info.pop(_PENDING, None)


def payloads(changes: Iterable[Change]) -> list[str]:
    """JSON arrays of changes, each small enough for one NOTIFY."""
    result: list[str] = []
    batch: list[dict[str, str]] = []
    size = 2
    for change in sorted(changes, key=lambda c: (c.topic, c.id or "", c.stack or "")):
        item = change.as_dict()
        length = len(json.dumps(item)) + 1
        if batch and size + length > MAX_PAYLOAD:
            result.append(json.dumps(batch))
            batch, size = [], 2
        batch.append(item)
        size += length
    if batch:
        result.append(json.dumps(batch))
    return result


def install() -> None:
    """Send notifications from every ORM session in this process."""
    if event.contains(Session, "after_flush", _after_flush):
        return
    event.listen(Session, "after_flush", _after_flush)
    event.listen(Session, "do_orm_execute", _orm_execute)
    event.listen(Session, "before_commit", _before_commit)
    event.listen(Session, "after_rollback", _discard)
    event.listen(Session, "after_soft_rollback", _discard)


def parse(payload: str) -> list[Change]:
    try:
        items = json.loads(payload)
    except ValueError:
        return []
    if not isinstance(items, list):
        return []
    return [
        Change(str(i["topic"]), _str(i.get("id")), _str(i.get("stack")))
        for i in items
        if isinstance(i, dict) and i.get("topic")
    ]


class EventHub:
    """Listens on one database connection and fans changes out to subscribers."""

    def __init__(self, engine: AsyncEngine, health_interval: float = 30.0) -> None:
        self.engine = engine
        self.health_interval = health_interval
        self._subscribers: set[asyncio.Queue[Change]] = set()
        self._task: asyncio.Task[None] | None = None
        self.listening = asyncio.Event()

    def start(self) -> None:
        self._task = asyncio.create_task(self._run(), name="event-hub")

    async def stop(self) -> None:
        if self._task is not None:
            self._task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await self._task

    @contextlib.contextmanager
    def subscribe(self) -> Iterator[asyncio.Queue[Change]]:
        queue: asyncio.Queue[Change] = asyncio.Queue(QUEUE_SIZE)
        self._subscribers.add(queue)
        try:
            yield queue
        finally:
            self._subscribers.discard(queue)

    def publish(self, changes: Iterable[Change]) -> None:
        changes = list(changes)
        for queue in self._subscribers:
            for change in changes:
                try:
                    queue.put_nowait(change)
                except asyncio.QueueFull:
                    # A slow client gets one resync instead of an ever-growing backlog.
                    while not queue.empty():
                        queue.get_nowait()
                    queue.put_nowait(RESYNC)
                    break

    def _on_notify(self, _conn: object, _pid: int, _channel: str, payload: str) -> None:
        self.publish(parse(payload))

    async def _run(self) -> None:
        attempt = 0
        connected_before = False
        while True:
            try:
                async with self.engine.connect() as conn:
                    raw = (await conn.get_raw_connection()).driver_connection
                    if raw is None:
                        raise RuntimeError("no driver connection")
                    await raw.add_listener(CHANNEL, self._on_notify)
                    self.listening.set()
                    attempt = 0
                    if connected_before:
                        self.publish([RESYNC])
                    connected_before = True
                    try:
                        while True:
                            await asyncio.sleep(self.health_interval)
                            await asyncio.wait_for(raw.fetchval("SELECT 1"), 10)
                    except asyncio.CancelledError:
                        with contextlib.suppress(Exception):
                            await raw.remove_listener(CHANNEL, self._on_notify)
                        raise
                    except Exception:
                        await conn.invalidate()
                        raise
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                log.warning("Live updates unavailable: %s", exc)
            self.listening.clear()
            await asyncio.sleep(RECONNECT_DELAYS[min(attempt, len(RECONNECT_DELAYS) - 1)])
            attempt += 1


def stack_uuid(change: Change) -> uuid.UUID | None:
    if change.stack is None:
        return None
    try:
        return uuid.UUID(change.stack)
    except ValueError:
        return None
