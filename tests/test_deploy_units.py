# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import hmac
from datetime import UTC, datetime

import pytest
import yaml
from fastapi import HTTPException
from starlette.datastructures import Headers

from novaport.crypto import Cipher, MissingSecretKey, generate_key, generate_ssh_keypair
from novaport.deploy.compose import ComposeError, add_labels
from novaport.deploy.converge import _parse_time, service_state
from novaport.models import DeployMode, StackType
from novaport.server.api.repositories import check_url
from novaport.stacks.settings import InvalidSettings, parse_settings, stack_name
from novaport.stacks.webhooks import verify


def test_cipher_round_trip() -> None:
    cipher = Cipher(generate_key())
    token = cipher.encrypt("ghp_secret")
    assert token != "ghp_secret"
    assert cipher.decrypt(token) == "ghp_secret"


def test_cipher_without_key() -> None:
    with pytest.raises(MissingSecretKey):
        Cipher(None).encrypt("x")


def test_cipher_with_wrong_key() -> None:
    token = Cipher(generate_key()).encrypt("x")
    with pytest.raises(ValueError, match="decrypted"):
        Cipher(generate_key()).decrypt(token)


def test_ssh_keypair() -> None:
    private, public = generate_ssh_keypair("novaport-infra")
    assert private.startswith("-----BEGIN OPENSSH PRIVATE KEY-----")
    assert public.startswith("ssh-ed25519 ")
    assert public.endswith(" novaport-infra")


def test_add_labels_swarm_merges_existing() -> None:
    normalized = yaml.safe_dump(
        {
            "services": {
                "web": {"image": "nginx", "deploy": {"labels": ["traefik.enable=true"]}},
                "db": {"image": "postgres"},
            }
        }
    )
    result = yaml.safe_load(add_labels(normalized, StackType.SWARM, {"novaport.stack": "shop"}))
    assert result["services"]["web"]["deploy"]["labels"] == {
        "traefik.enable": "true",
        "novaport.stack": "shop",
    }
    assert result["services"]["db"]["deploy"]["labels"] == {"novaport.stack": "shop"}


def test_add_labels_compose_uses_container_labels() -> None:
    normalized = "services:\n  web:\n    image: nginx\n    labels:\n      a: '1'\n"
    result = yaml.safe_load(add_labels(normalized, StackType.COMPOSE, {"novaport.stack": "shop"}))
    assert result["services"]["web"]["labels"] == {"a": "1", "novaport.stack": "shop"}


def test_add_labels_rejects_empty_file() -> None:
    with pytest.raises(ComposeError):
        add_labels("version: '3'\n", StackType.SWARM, {})


def test_settings_parsing() -> None:
    assert parse_settings(None).deploy is None
    settings = parse_settings("type: compose\ndeploy: approve\n")
    assert settings.type == StackType.COMPOSE
    assert settings.deploy == DeployMode.APPROVE


@pytest.mark.parametrize("text", ["deploy: sometimes", "- a\n- b", "unknown: 1", "type: [unclosed"])
def test_invalid_settings(text: str) -> None:
    with pytest.raises(InvalidSettings):
        parse_settings(text)


@pytest.mark.parametrize(
    ("folder", "expected"),
    [
        ("stacks/web", "web"),
        ("stacks/My-App", "my-app"),
        ("stacks/bad name", None),
        ("x/.git", None),
    ],
)
def test_stack_name(folder: str, expected: str | None) -> None:
    assert stack_name(folder) == expected


def _sign(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def test_webhook_signatures() -> None:
    body = b'{"ref": "refs/heads/main"}'
    secret = "s3cret"
    github = Headers({"x-hub-signature-256": "sha256=" + _sign(secret, body)})
    gitea = Headers({"x-gitea-signature": _sign(secret, body)})
    gitlab = Headers({"x-gitlab-token": secret})
    for headers in (github, gitea, gitlab):
        assert verify(headers, body, secret)
        assert not verify(headers, body, "other")
    assert not verify(github, body + b" ", secret)
    assert not verify(Headers({}), body, secret)


@pytest.mark.parametrize(
    "url",
    [
        "https://github.com/acme/infra.git",
        "ssh://git@git.example.com:2222/acme/infra.git",
        "git@github.com:acme/infra.git",
    ],
)
def test_remote_urls_allowed(url: str) -> None:
    check_url(url, allow_local=False)


@pytest.mark.parametrize(
    "url",
    ["file:///srv/repo", "/srv/repo", "ext::sh -c touch% /tmp/pwned", "--upload-pack=x", "ftp://x"],
)
def test_unsafe_urls_rejected(url: str) -> None:
    with pytest.raises(HTTPException):
        check_url(url, allow_local=False)


def test_local_urls_when_allowed() -> None:
    check_url("file:///srv/repo", allow_local=True)


def test_docker_timestamps() -> None:
    parsed = _parse_time("2026-11-23T10:07:51.756615123Z")
    assert parsed == datetime(2026, 11, 23, 10, 7, 51, 756615, tzinfo=UTC)
    assert _parse_time("2026-11-23T10:07:51Z") == datetime(2026, 11, 23, 10, 7, 51, tzinfo=UTC)


def test_service_states() -> None:
    def svc(**extra: object) -> dict[str, object]:
        return {"ID": "1", "Spec": {"Name": "web", "Mode": {"Replicated": {}}}, **extra}

    status = {"ServiceStatus": {"RunningTasks": 2, "DesiredTasks": 2}}
    assert service_state(svc(**status)).converged
    assert not service_state(svc(ServiceStatus={"RunningTasks": 1, "DesiredTasks": 2})).converged
    updating = service_state(svc(UpdateStatus={"State": "updating"}, **status))
    assert not updating.converged
    paused = service_state(svc(UpdateStatus={"State": "paused", "Message": "task failed"}))
    assert paused.detail == "update paused: task failed"
    job = {"ID": "2", "Spec": {"Name": "migrate", "Mode": {"ReplicatedJob": {}}}}
    assert service_state(job).converged
