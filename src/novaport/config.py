# SPDX-License-Identifier: AGPL-3.0-or-later
from datetime import timedelta
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
    # Comma-separated IPs or CIDRs whose X-Forwarded-For/-Proto headers are trusted.
    trusted_proxies: str = "127.0.0.1"

    # Unset means the usual resolution: DOCKER_HOST, then the local socket.
    docker_host: str | None = None

    initial_admin_username: str = "admin"
    initial_admin_password: SecretStr | None = None
    session_idle_timeout: timedelta = timedelta(hours=8)
    session_max_age: timedelta = timedelta(days=7)
    session_cookie: str = "novaport_session"

    agent_port: int = 9001
    agent_token: SecretStr | None = None


@lru_cache
def get_settings() -> Settings:
    return Settings()
