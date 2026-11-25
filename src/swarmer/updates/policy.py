# SPDX-License-Identifier: AGPL-3.0-or-later
"""The `x-updates` compose extension and choosing a newer tag."""

import re
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError, field_validator

EXTENSION = "x-updates"
NUMBER = re.compile(r"\d+")


class Mode(StrEnum):
    DIGEST = "digest"
    VERSION = "version"
    NONE = "none"


class Allow(StrEnum):
    PATCH = "patch"
    MINOR = "minor"
    MAJOR = "major"


class Action(StrEnum):
    COMMIT = "commit"
    PR = "pr"
    NOTIFY = "notify"
    NONE = "none"


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    mode: Mode
    allow: Allow = Allow.MINOR
    match: str | None = None
    action: Action = Action.NOTIFY

    @field_validator("match")
    @classmethod
    def _compiles(cls, value: str | None) -> str | None:
        if value is not None:
            try:
                re.compile(value)
            except re.error as exc:
                raise ValueError(f"not a valid regular expression: {exc}") from exc
        return value


@dataclass
class ServicePolicy:
    service: str
    image: str | None
    policy: Policy | None
    error: str | None = None


class InvalidCompose(ValueError):
    pass


def read_policies(compose: str) -> list[ServicePolicy]:
    """Services with an `x-updates` extension, in file order."""
    try:
        data = yaml.safe_load(compose)
    except yaml.YAMLError as exc:
        raise InvalidCompose(f"The compose file is not valid YAML: {exc}") from exc
    services = data.get("services") if isinstance(data, dict) else None
    if not isinstance(services, dict):
        return []
    found = []
    for name, service in services.items():
        if not isinstance(service, dict) or EXTENSION not in service:
            continue
        image = service.get("image")
        image = image if isinstance(image, str) else None
        raw: Any = service[EXTENSION]
        try:
            policy = Policy.model_validate(raw if isinstance(raw, dict) else {"mode": raw})
        except ValidationError as exc:
            problems = "; ".join(
                f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in exc.errors()
            )
            found.append(ServicePolicy(str(name), image, None, f"{EXTENSION}: {problems}"))
            continue
        found.append(ServicePolicy(str(name), image, policy))
    return found


def infer_pattern(tag: str) -> re.Pattern[str] | None:
    """The shape of a tag with its numbers generalised: `2.4.1-alpine` gives
    `^\\d+\\.\\d+\\.\\d+-alpine$`. None when the tag has no numbers, like `latest`."""
    parts = NUMBER.split(tag)
    if len(parts) == 1:
        return None
    return re.compile("^" + r"\d+".join(re.escape(p) for p in parts) + "$")


def version_key(tag: str) -> tuple[int, ...]:
    return tuple(int(n) for n in NUMBER.findall(tag))


LEVEL = {Allow.MAJOR: 0, Allow.MINOR: 1, Allow.PATCH: 2}


def newer_tag(current: str, tags: list[str], policy: Policy) -> str | None:
    """The highest tag that matches the policy and is newer than the current one."""
    pattern = re.compile(policy.match) if policy.match else infer_pattern(current)
    if pattern is None:
        return None
    current_key = version_key(current)
    if not current_key:
        return None
    keep = current_key[: LEVEL[policy.allow]]
    candidates = [
        tag
        for tag in tags
        if pattern.search(tag)
        and (key := version_key(tag))
        and key > current_key
        and key[: len(keep)] == keep
    ]
    return max(candidates, key=version_key, default=None)
