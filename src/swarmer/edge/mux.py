# SPDX-License-Identifier: AGPL-3.0-or-later
"""Byte streams multiplexed over one WebSocket.

Each binary message is one frame: a type byte, a 4-byte stream ID, and the payload. Only the
server opens streams; the payload of OPEN names what to connect to on the agent's side. EOF ends
one direction, like a TCP half-close, and RESET aborts the whole stream.
"""

import asyncio
import contextlib
import logging
import struct
from collections.abc import Awaitable, Callable

log = logging.getLogger(__name__)

OPEN, DATA, EOF, RESET = 1, 2, 3, 4
HEADER = struct.Struct("!BI")
CHUNK = 64 * 1024
# Frames queued per stream before the reader waits for the consumer to catch up.
QUEUE_FRAMES = 64

Send = Callable[[bytes], Awaitable[None]]
Receive = Callable[[], Awaitable[bytes | None]]
OpenHandler = Callable[["Stream", str], Awaitable[None]]


class StreamReset(ConnectionError):
    pass


_EOF = object()
_RESET = object()


class Stream:
    def __init__(self, mux: "Mux", stream_id: int) -> None:
        self.mux = mux
        self.id = stream_id
        self._incoming: asyncio.Queue[bytes | object] = asyncio.Queue(QUEUE_FRAMES)
        self._read_done = False
        self._write_done = False
        self._reset = False

    async def read(self) -> bytes:
        """The next chunk, or b"" once the other side sent EOF."""
        if self._read_done:
            return b""
        item = await self._incoming.get()
        if item is _RESET:
            self._read_done = True
            raise StreamReset(f"stream {self.id} was reset")
        if item is _EOF:
            self._read_done = True
            self._maybe_forget()
            return b""
        return item if isinstance(item, bytes) else b""

    async def write(self, data: bytes) -> None:
        if self._reset:
            raise StreamReset(f"stream {self.id} was reset")
        for start in range(0, len(data), CHUNK):
            await self.mux.send_frame(DATA, self.id, data[start : start + CHUNK])

    async def write_eof(self) -> None:
        if self._write_done or self._reset:
            return
        self._write_done = True
        await self.mux.send_frame(EOF, self.id)
        self._maybe_forget()

    async def reset(self) -> None:
        if self._reset:
            return
        self._abort()
        with contextlib.suppress(ConnectionError):
            await self.mux.send_frame(RESET, self.id)

    def _abort(self) -> None:
        self._reset = True
        self._write_done = True
        self.mux.streams.pop(self.id, None)
        with contextlib.suppress(asyncio.QueueFull):
            self._incoming.put_nowait(_RESET)

    def _maybe_forget(self) -> None:
        if self._read_done and self._write_done:
            self.mux.streams.pop(self.id, None)


class Mux:
    def __init__(self, send: Send, on_open: OpenHandler | None = None) -> None:
        self._send = send
        self._send_lock = asyncio.Lock()
        self.on_open = on_open
        self.streams: dict[int, Stream] = {}
        self._next_id = 1
        self._handlers: set[asyncio.Task[None]] = set()
        self.closed = False

    async def send_frame(self, kind: int, stream_id: int, payload: bytes = b"") -> None:
        if self.closed:
            raise ConnectionError("the tunnel is closed")
        async with self._send_lock:
            await self._send(HEADER.pack(kind, stream_id) + payload)

    async def open(self, target: str) -> Stream:
        stream_id = self._next_id
        self._next_id += 1
        stream = Stream(self, stream_id)
        self.streams[stream_id] = stream
        await self.send_frame(OPEN, stream_id, target.encode())
        return stream

    async def run(self, receive: Receive) -> None:
        """Dispatch incoming frames until the connection ends."""
        try:
            while (message := await receive()) is not None:
                if len(message) < HEADER.size:
                    continue
                kind, stream_id = HEADER.unpack_from(message)
                payload = message[HEADER.size :]
                if kind == OPEN:
                    self._accept(stream_id, payload.decode(errors="replace"))
                    continue
                stream = self.streams.get(stream_id)
                if stream is None:
                    continue
                if kind == DATA:
                    await stream._incoming.put(payload)
                elif kind == EOF:
                    await stream._incoming.put(_EOF)
                elif kind == RESET:
                    stream._abort()
        finally:
            self.close()

    def _accept(self, stream_id: int, target: str) -> None:
        if self.on_open is None or stream_id in self.streams:
            return
        stream = Stream(self, stream_id)
        self.streams[stream_id] = stream
        task = asyncio.create_task(self._handle(stream, target))
        self._handlers.add(task)
        task.add_done_callback(self._handlers.discard)

    async def _handle(self, stream: Stream, target: str) -> None:
        if self.on_open is None:
            return
        try:
            await self.on_open(stream, target)
        except Exception:
            log.exception("Tunnel stream to %s failed", target)
            await stream.reset()

    def close(self) -> None:
        self.closed = True
        for stream in list(self.streams.values()):
            stream._abort()
        for task in self._handlers:
            task.cancel()


async def pipe(stream: Stream, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
    """Copy between a stream and a socket in both directions until both sides are done."""

    async def outgoing() -> None:
        while chunk := await reader.read(CHUNK):
            await stream.write(chunk)
        await stream.write_eof()

    async def incoming() -> None:
        while chunk := await stream.read():
            writer.write(chunk)
            await writer.drain()
        if writer.can_write_eof():
            writer.write_eof()

    tasks = [asyncio.create_task(outgoing()), asyncio.create_task(incoming())]
    try:
        for done in asyncio.as_completed(tasks):
            try:
                await done
            except (ConnectionError, OSError):
                # Either side went away; the other direction can't finish meaningfully.
                for task in tasks:
                    task.cancel()
                await stream.reset()
                break
    finally:
        for task in tasks:
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError, ConnectionError, OSError):
            await asyncio.gather(*tasks, return_exceptions=True)
        writer.close()
        with contextlib.suppress(ConnectionError, OSError):
            await writer.wait_closed()
