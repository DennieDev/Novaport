# SPDX-License-Identifier: AGPL-3.0-or-later
import asyncio

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from swarmer.migrate import alembic_config, upgrade
from swarmer.models import Base
from tests.conftest import TEST_DATABASE_URL

pytestmark = pytest.mark.postgres


def _diff(connection: Connection) -> list[object]:
    return list(compare_metadata(MigrationContext.configure(connection), Base.metadata))


async def _schema_diff(url: str) -> list[object]:
    engine = create_async_engine(url)
    try:
        async with engine.connect() as conn:
            return await conn.run_sync(_diff)
    finally:
        await engine.dispose()


def test_upgrade_downgrade_upgrade() -> None:
    assert TEST_DATABASE_URL
    upgrade(TEST_DATABASE_URL)
    command.downgrade(alembic_config(TEST_DATABASE_URL), "base")
    upgrade(TEST_DATABASE_URL)


def test_models_match_migrations() -> None:
    assert TEST_DATABASE_URL
    upgrade(TEST_DATABASE_URL)
    assert asyncio.run(_schema_diff(TEST_DATABASE_URL)) == []
