# SPDX-License-Identifier: AGPL-3.0-or-later
import base64
import json
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from swarmer.crypto import Cipher
from swarmer.models import Registry
from swarmer.registries.client import Login, LoginLookup
from swarmer.registries.reference import DOCKER_HUB

# The key the docker CLI uses for Docker Hub in config.json.
DOCKER_HUB_AUTH_KEY = "https://index.docker.io/v1/"


def login_lookup(maker: async_sessionmaker[AsyncSession], cipher: Cipher) -> LoginLookup:
    async def lookup(host: str) -> Login | None:
        async with maker() as db:
            registry = await db.scalar(select(Registry).where(Registry.host == host))
        if registry is None:
            return None
        try:
            return Login(registry.username, cipher.decrypt(registry.password))
        except ValueError:
            return None

    return lookup


async def all_logins(db: AsyncSession, cipher: Cipher) -> dict[str, Login]:
    logins = {}
    for registry in await db.scalars(select(Registry)):
        try:
            logins[registry.host] = Login(registry.username, cipher.decrypt(registry.password))
        except ValueError:
            continue
    return logins


@contextmanager
def docker_config(logins: dict[str, Login]) -> Iterator[dict[str, str]]:
    """A temporary docker client config with the given logins, as environment variables.

    `docker stack deploy --with-registry-auth` reads it and hands the credentials to the
    nodes, and `docker compose` uses it to pull.
    """
    if not logins:
        yield {}
        return
    auths = {
        (DOCKER_HUB_AUTH_KEY if host == DOCKER_HUB else host): {
            "auth": base64.b64encode(f"{login.username}:{login.password}".encode()).decode()
        }
        for host, login in logins.items()
    }
    with tempfile.TemporaryDirectory(prefix="swarmer-docker-") as tmp:
        path = Path(tmp) / "config.json"
        path.touch(mode=0o600)
        path.write_text(json.dumps({"auths": auths}))
        yield {"DOCKER_CONFIG": tmp}
