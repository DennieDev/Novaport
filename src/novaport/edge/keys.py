# SPDX-License-Identifier: AGPL-3.0-or-later
"""Edge keys: everything an agent needs to find and log in to its server, in one string."""

import base64
import binascii
import hashlib
import json
import secrets
import uuid
from dataclasses import dataclass

CONNECT_PATH = "/api/edge/connect"


@dataclass(frozen=True)
class EdgeKey:
    url: str
    endpoint_id: uuid.UUID
    secret: str

    def encode(self) -> str:
        data = json.dumps(
            {"url": self.url, "id": str(self.endpoint_id), "secret": self.secret},
            separators=(",", ":"),
        )
        return base64.urlsafe_b64encode(data.encode()).decode().rstrip("=")

    @property
    def token(self) -> str:
        return f"{self.endpoint_id}.{self.secret}"

    @property
    def connect_url(self) -> str:
        base = self.url.rstrip("/")
        scheme, _, rest = base.partition("://")
        ws = {"https": "wss", "http": "ws"}.get(scheme.lower())
        if ws is None:
            raise ValueError(f"Unsupported server URL: {self.url}")
        return f"{ws}://{rest}{CONNECT_PATH}"

    @classmethod
    def decode(cls, value: str) -> "EdgeKey":
        try:
            padded = value.strip() + "=" * (-len(value.strip()) % 4)
            data = json.loads(base64.urlsafe_b64decode(padded))
            return cls(str(data["url"]), uuid.UUID(str(data["id"])), str(data["secret"]))
        except (binascii.Error, ValueError, KeyError, TypeError) as exc:
            raise ValueError("Not a valid edge key.") from exc


def new_secret() -> str:
    return secrets.token_urlsafe(32)


def hash_secret(secret: str) -> str:
    return hashlib.sha256(secret.encode()).hexdigest()


def parse_token(header: str) -> tuple[uuid.UUID, str] | None:
    """The endpoint ID and secret from an `Authorization: Bearer <id>.<secret>` header."""
    scheme, _, token = header.partition(" ")
    endpoint_id, _, secret = token.partition(".")
    if scheme.lower() != "bearer" or not secret:
        return None
    try:
        return uuid.UUID(endpoint_id), secret
    except ValueError:
        return None
