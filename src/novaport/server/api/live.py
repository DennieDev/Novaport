# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import contextlib
import json

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

from novaport.auth.access import Access
from novaport.events import Change, EventHub, stack_uuid
from novaport.models import Role
from novaport.server.deps import websocket_access

ws_router = APIRouter(prefix="/api", tags=["live"])

# Roles and sessions are checked again this often, so a logout or a removed role ends the stream.
RECHECK_INTERVAL = 60.0


def visible(access: Access, change: Change) -> bool:
    stack_id = stack_uuid(change)
    if stack_id is not None:
        return access.can(Role.VIEWER, stack_id)
    return access.global_role is not None or bool(access.stack_roles)


async def _forward(websocket: WebSocket, queue: asyncio.Queue[Change], access: Access) -> None:
    loop = asyncio.get_running_loop()
    deadline = loop.time() + RECHECK_INTERVAL
    while True:
        try:
            change = await asyncio.wait_for(queue.get(), max(deadline - loop.time(), 0))
        except TimeoutError:
            fresh = await websocket_access(websocket)
            if fresh is None:
                await websocket.close(code=1008, reason="Not logged in.")
                return
            access = fresh
            deadline = loop.time() + RECHECK_INTERVAL
            continue
        if visible(access, change):
            await websocket.send_text(json.dumps(change.as_dict()))


@ws_router.websocket("/events")
async def events(websocket: WebSocket) -> None:
    access = await websocket_access(websocket)
    if access is None:
        await websocket.close(code=1008, reason="Not logged in.")
        return
    hub: EventHub = websocket.app.state.events
    await websocket.accept()
    with hub.subscribe() as queue:
        forward = asyncio.create_task(_forward(websocket, queue, access))
        try:
            # The client never sends anything; this returns when it goes away.
            while True:
                message = await websocket.receive()
                if message["type"] == "websocket.disconnect":
                    break
        except WebSocketDisconnect:
            pass
        finally:
            forward.cancel()
            with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect, RuntimeError):
                await forward
