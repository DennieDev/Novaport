# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio

from alembic import context
from sqlalchemy import Connection, pool, text
from sqlalchemy.ext.asyncio import create_async_engine

from swarmer.config import get_settings
from swarmer.models import Base

# Arbitrary key for pg_advisory_xact_lock, so replicas starting together migrate one at a time.
MIGRATION_LOCK_KEY = 0x6E6F7661

config = context.config
target_metadata = Base.metadata


def database_url() -> str:
    url: str | None = config.attributes.get("database_url")
    return url or get_settings().database_url.get_secret_value()


def run_migrations_offline() -> None:
    context.configure(
        url=database_url(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=target_metadata, compare_type=True)
    with context.begin_transaction():
        connection.execute(text("SELECT pg_advisory_xact_lock(:key)"), {"key": MIGRATION_LOCK_KEY})
        context.run_migrations()


async def run_migrations_online() -> None:
    engine = create_async_engine(database_url(), poolclass=pool.NullPool)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(do_run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
