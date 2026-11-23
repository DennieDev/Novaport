# SPDX-License-Identifier: AGPL-3.0-or-later
import zlib
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine


def lock_key(name: str) -> int:
    # Advisory lock keys are bigint; a stable 32-bit hash of the name is plenty.
    return zlib.crc32(name.encode())


@asynccontextmanager
async def try_lock(engine: AsyncEngine, name: str) -> AsyncIterator[bool]:
    """Hold a Postgres session-level advisory lock for the duration of the block.

    Yields False without waiting if another process holds it. Keeps one connection checked out
    while held, so the lock lives exactly as long as the block.
    """
    key = lock_key(name)
    async with engine.connect() as conn:
        acquired = bool(await conn.scalar(text("SELECT pg_try_advisory_lock(:k)"), {"k": key}))
        await conn.commit()
        try:
            yield acquired
        finally:
            if acquired:
                await conn.execute(text("SELECT pg_advisory_unlock(:k)"), {"k": key})
                await conn.commit()
