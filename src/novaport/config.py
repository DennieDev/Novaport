# SPDX-License-Identifier: AGPL-3.0-or-later
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

SWARM_SECRETS_DIR = Path("/run/secrets")

LogLevel = Literal["debug", "info", "warning", "error"]


class Settings(BaseSettings):
    """Configuration from `NOVAPORT_*` environment variables, `.env`, and Swarm secrets.

    A Swarm secret named after the variable in lower case (`novaport_database_url`) works too.
    """

    model_config = SettingsConfigDict(
        env_prefix="NOVAPORT_",
        env_file=".env",
        secrets_dir=SWARM_SECRETS_DIR if SWARM_SECRETS_DIR.is_dir() else None,
        extra="ignore",
    )

    database_url: SecretStr = SecretStr(
        "postgresql+asyncpg://novaport:novaport@localhost:5432/novaport"
    )
    migrate_on_start: bool = True

    host: str = "0.0.0.0"
    port: int = 8000
    log_level: LogLevel = "info"
    static_dir: Path | None = None

    agent_port: int = 9001
    agent_token: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
