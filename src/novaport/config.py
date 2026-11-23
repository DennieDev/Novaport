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
    # External URL, such as https://novaport.example.com. Used for OIDC redirect URIs; without
    # it they are built from the request.
    public_url: str | None = None
    # Comma-separated IPs or CIDRs whose X-Forwarded-For/-Proto headers are trusted.
    trusted_proxies: str = "127.0.0.1"

    # Unset means the usual resolution: DOCKER_HOST, then the local socket.
    docker_host: str | None = None

    initial_admin_username: str = "admin"
    initial_admin_password: SecretStr | None = None
    session_idle_timeout: timedelta = timedelta(hours=8)
    session_max_age: timedelta = timedelta(days=7)
    session_cookie: str = "novaport_session"

    # Encrypts git and registry credentials in the database. Generate one with
    # `novaport generate-key`. Without it, repositories can only use public URLs.
    secret_key: SecretStr | None = None
    # age secret key (AGE-SECRET-KEY-...) for SOPS-encrypted secrets.env files. Generate one
    # with `novaport generate-age-key` and add its public key to each repository's .sops.yaml.
    age_key: SecretStr | None = None
    sops_binary: str = "sops"
    # Identity for commits Novaport makes; the author is the logged-in user.
    commit_name: str = "Novaport"
    commit_email: str = "novaport@localhost"
    # Git checkouts and SSH known_hosts live here.
    data_dir: Path = Path("/var/lib/novaport")
    # Set to false to run the API without the git poller and deploy worker.
    background_jobs: bool = True
    # Allow file:// and plain-path repository URLs. Off by default, since they read the
    # server's own filesystem.
    allow_local_repositories: bool = False
    # How often services with an x-updates policy are checked for newer images.
    update_interval: timedelta = timedelta(hours=6)
    # How often running Swarm services are compared with what was last deployed.
    drift_interval: timedelta = timedelta(minutes=5)
    # Outgoing mail for email notification channels.
    smtp_host: str | None = None
    smtp_port: int = 587
    smtp_username: str | None = None
    smtp_password: SecretStr | None = None
    # starttls, tls (implicit, usually port 465), or none
    smtp_security: Literal["starttls", "tls", "none"] = "starttls"
    smtp_from: str = "novaport@localhost"
    deploy_timeout: timedelta = timedelta(minutes=5)
    command_timeout: timedelta = timedelta(minutes=2)
    # Refuse to let someone approve, or deploy on an approve-mode stack, a revision they
    # authored. Authors are matched on the commit's email address.
    separate_approver: bool = False
    # Finished deployments and their logs older than this are deleted, but each stack keeps at
    # least `keep_deployments` of them. Revisions without deployments age out the same way.
    # 0d keeps everything.
    deployment_retention: timedelta = timedelta(days=90)
    keep_deployments: int = 20
    audit_retention: timedelta = timedelta(days=365)

    # Shared between server and agents; agents refuse to start without it.
    agent_token: SecretStr | None = None
    agent_port: int = 9001
    # How the server finds agents: a Swarm DNS name that resolves to every agent task
    # (`tasks.agent` inside the Novaport stack), or a fixed comma-separated list of URLs.
    agent_dns: str | None = None
    agent_urls: str | None = None
    # Where the agent sees the host's root filesystem (mounted read-only).
    host_root: Path = Path("/host")


@lru_cache
def get_settings() -> Settings:
    return Settings()
