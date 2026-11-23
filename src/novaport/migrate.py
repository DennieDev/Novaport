# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio
import logging
import time

from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from novaport.db import create_engine

log = logging.getLogger(__name__)

SCRIPT_LOCATION = "novaport:migrations"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    # Passed as an attribute rather than an ini option, which would choke on `%` in passwords.
    config.attributes["database_url"] = database_url
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)


async def _ping(database_url: str) -> None:
    engine = create_engine(database_url)
    try:
        async with engine.connect() as conn:
            await conn.execute(text("SELECT 1"))
    finally:
        await engine.dispose()


def wait_for_database(database_url: str, timeout: float = 60) -> None:
    """Block until the database accepts connections; on Swarm it often starts after us."""
    deadline = time.monotonic() + timeout
    while True:
        try:
            asyncio.run(_ping(database_url))
            return
        except (SQLAlchemyError, OSError) as exc:
            if time.monotonic() >= deadline:
                raise
            log.warning("Database not reachable yet (%s), retrying", exc)
            time.sleep(2)
