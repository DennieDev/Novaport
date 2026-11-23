# SPDX-License-Identifier: AGPL-3.0-or-later
import re
from dataclasses import dataclass, replace

DOCKER_HUB = "docker.io"
DOCKER_HUB_ALIASES = frozenset({"docker.io", "index.docker.io", "registry-1.docker.io"})
DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
TAG = re.compile(r"^[\w][\w.-]{0,127}$")


class InvalidReference(ValueError):
    pass


@dataclass(frozen=True)
class Reference:
    """A parsed image reference such as `ghcr.io/acme/api:2.4.1@sha256:...`."""

    registry: str
    repository: str
    tag: str | None
    digest: str | None
    # The registry and repository as written, so a rewritten reference keeps its spelling.
    name: str

    @property
    def api_host(self) -> str:
        return "registry-1.docker.io" if self.registry == DOCKER_HUB else self.registry

    def with_tag(self, tag: str, digest: str | None = None) -> "Reference":
        return replace(self, tag=tag, digest=digest)

    def with_digest(self, digest: str | None) -> "Reference":
        return replace(self, digest=digest)

    def __str__(self) -> str:
        text = self.name
        if self.tag:
            text += f":{self.tag}"
        if self.digest:
            text += f"@{self.digest}"
        return text


def normalize_host(host: str) -> str:
    host = host.strip().lower().removeprefix("https://").removeprefix("http://").rstrip("/")
    return DOCKER_HUB if host in DOCKER_HUB_ALIASES else host


def parse_reference(text: str) -> Reference:
    text = text.strip()
    if not text or any(c in text for c in " ${}"):
        raise InvalidReference(f"{text!r} is not a plain image reference.")
    name, _, digest = text.partition("@")
    if digest and not DIGEST.match(digest):
        raise InvalidReference(f"{text!r} has an invalid digest.")
    tag = None
    last = name.rsplit("/", 1)[-1]
    if ":" in last:
        name, tag = name.rsplit(":", 1)
        if not TAG.match(tag):
            raise InvalidReference(f"{text!r} has an invalid tag.")

    first, _, rest = name.partition("/")
    if rest and ("." in first or ":" in first or first == "localhost"):
        registry, repository = normalize_host(first), rest
    else:
        registry, repository = DOCKER_HUB, name
    if registry == DOCKER_HUB and "/" not in repository:
        repository = f"library/{repository}"
    if not repository or repository != repository.lower():
        raise InvalidReference(f"{text!r} has an invalid repository name.")
    if tag is None and not digest:
        tag = "latest"
    return Reference(registry, repository, tag, digest or None, name)
