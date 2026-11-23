# SPDX-License-Identifier: AGPL-3.0-or-later
import json
from pathlib import Path

import httpx
import pytest

from novaport.registries.client import Login, RegistryClient, RegistryError
from novaport.registries.reference import InvalidReference, parse_reference
from novaport.registries.store import docker_config
from novaport.updates.policy import Allow, Mode, Policy, infer_pattern, newer_tag, read_policies
from novaport.updates.rewrite import RewriteError, replace_image
from tests.registry_fake import FakeRegistry, digest_of


@pytest.mark.parametrize(
    ("text", "registry", "repository", "tag"),
    [
        ("nginx", "docker.io", "library/nginx", "latest"),
        ("nginx:1.27-alpine", "docker.io", "library/nginx", "1.27-alpine"),
        ("acme/api:2", "docker.io", "acme/api", "2"),
        ("ghcr.io/acme/api:2.4.1", "ghcr.io", "acme/api", "2.4.1"),
        ("localhost:5000/api:dev", "localhost:5000", "api", "dev"),
        ("index.docker.io/library/redis:7", "docker.io", "library/redis", "7"),
    ],
)
def test_parse_reference(text: str, registry: str, repository: str, tag: str) -> None:
    ref = parse_reference(text)
    assert (ref.registry, ref.repository, ref.tag) == (registry, repository, tag)


def test_reference_keeps_its_spelling() -> None:
    digest = digest_of("x")
    ref = parse_reference(f"nginx:1.27@{digest}")
    assert ref.digest == digest
    assert str(ref.with_tag("1.28")) == "nginx:1.28"
    assert str(ref.with_tag("1.28", digest)) == f"nginx:1.28@{digest}"


@pytest.mark.parametrize("text", ["${IMAGE}", "api:$TAG", "Upper/case:1", "nginx@sha256:short"])
def test_invalid_references(text: str) -> None:
    with pytest.raises(InvalidReference):
        parse_reference(text)


def test_infer_pattern() -> None:
    pattern = infer_pattern("2.4.1-alpine")
    assert pattern is not None
    assert pattern.match("2.10.0-alpine")
    assert not pattern.match("2.10.0")
    assert not pattern.match("2.10-alpine")
    assert infer_pattern("latest") is None


TAGS = [
    "1.2.3",
    "1.2.4",
    "1.2.10",
    "1.3.0",
    "1.10.0",
    "2.0.0",
    "2.0.0-rc1",
    "latest",
    "1.2.4-alpine",
]


@pytest.mark.parametrize(
    ("allow", "expected"),
    [(Allow.PATCH, "1.2.10"), (Allow.MINOR, "1.10.0"), (Allow.MAJOR, "2.0.0")],
)
def test_newer_tag_respects_allow(allow: Allow, expected: str) -> None:
    assert newer_tag("1.2.3", TAGS, Policy(mode=Mode.VERSION, allow=allow)) == expected


def test_newer_tag_keeps_suffix_and_uses_match() -> None:
    policy = Policy(mode=Mode.VERSION, allow=Allow.MAJOR)
    assert newer_tag("1.2.3-alpine", TAGS, policy) == "1.2.4-alpine"
    assert newer_tag("2.0.0", TAGS, policy) is None
    assert newer_tag("latest", TAGS, policy) is None
    explicit = Policy(mode=Mode.VERSION, allow=Allow.MAJOR, match=r"^2\.0\.0-rc\d+$")
    assert newer_tag("1.0.0", TAGS, explicit) == "2.0.0-rc1"


def test_read_policies() -> None:
    compose = """
services:
  web:
    image: nginx:1.27
    x-updates: {mode: version, allow: patch, action: commit}
  db:
    image: postgres:16
    x-updates: {mode: sometimes}
  cache:
    image: redis:7
"""
    web, db = read_policies(compose)
    assert web.policy == Policy(mode=Mode.VERSION, allow=Allow.PATCH, action="commit")
    assert db.policy is None
    assert db.error is not None
    assert "mode" in db.error


def test_replace_image_keeps_the_rest() -> None:
    compose = """# the shop
services:
  web:
    image: "nginx:1.27"   # pinned for now
    x-updates: {mode: version}
  worker:
    image: nginx:1.27
"""
    updated = replace_image(compose, "web", "nginx:1.28")
    assert updated == compose.replace('"nginx:1.27"   #', '"nginx:1.28"   #')
    assert replace_image(compose, "worker", "nginx:1.29").endswith("    image: nginx:1.29\n")
    with pytest.raises(RewriteError):
        replace_image(compose, "nope", "x")


def test_docker_config() -> None:
    with docker_config({"docker.io": Login("u", "p"), "ghcr.io": Login("a", "b")}) as env:
        path = Path(env["DOCKER_CONFIG"]) / "config.json"
        auths = json.loads(path.read_text())["auths"]
        assert set(auths) == {"https://index.docker.io/v1/", "ghcr.io"}
        assert path.stat().st_mode & 0o077 == 0
    assert not path.exists()
    with docker_config({}) as env:
        assert env == {}


@pytest.fixture
def registry() -> FakeRegistry:
    registry = FakeRegistry()
    registry.push("acme/api", "1.0.0", "1.0.1", "1.1.0")
    return registry


async def test_client_gets_token_and_caches(registry: FakeRegistry) -> None:
    async with httpx.AsyncClient(transport=registry.transport()) as http:
        client = RegistryClient(http)
        ref = parse_reference("registry.example.com/acme/api:1.0.0")
        assert await client.tags(ref) == ["1.0.0", "1.0.1", "1.1.0"]
        assert await client.digest(ref) == digest_of("acme/api:1.0.0")
        assert await client.digest(ref, "9.9.9") is None
        before = len(registry.requests)
        await client.tags(ref)
        await client.digest(ref)
        assert len(registry.requests) == before
    assert sum("auth.example.com" in r for r in registry.requests) == 1


async def test_client_follows_pages(registry: FakeRegistry) -> None:
    registry.page_size = 2
    async with httpx.AsyncClient(transport=registry.transport()) as http:
        tags = await RegistryClient(http).tags(parse_reference("registry.example.com/acme/api"))
    assert tags == ["1.0.0", "1.0.1", "1.1.0"]


async def test_private_repository_needs_login(registry: FakeRegistry) -> None:
    registry.private["acme/api"] = ("bot", "secret")
    ref = parse_reference("registry.example.com/acme/api:1.0.0")

    async def login(host: str) -> Login | None:
        return Login("bot", "secret") if host == "registry.example.com" else None

    async with httpx.AsyncClient(transport=registry.transport()) as http:
        with pytest.raises(RegistryError, match="refused"):
            await RegistryClient(http).digest(ref)
        assert await RegistryClient(http, login).digest(ref) == digest_of("acme/api:1.0.0")
