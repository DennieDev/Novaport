# SPDX-License-Identifier: AGPL-3.0-or-later
from alembic import command
from alembic.config import Config

SCRIPT_LOCATION = "novaport:migrations"


def alembic_config(database_url: str) -> Config:
    config = Config()
    config.set_main_option("script_location", SCRIPT_LOCATION)
    # Passed as an attribute rather than an ini option, which would choke on `%` in passwords.
    config.attributes["database_url"] = database_url
    return config


def upgrade(database_url: str, revision: str = "head") -> None:
    command.upgrade(alembic_config(database_url), revision)
