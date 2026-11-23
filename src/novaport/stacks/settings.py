# SPDX-License-Identifier: AGPL-3.0-or-later
"""Per-stack settings from an optional `novaport.yaml` next to the compose file."""

import re

import yaml
from pydantic import BaseModel, ConfigDict, ValidationError

from novaport.models import DeployMode, EditMode, StackType

SETTINGS_FILE = "novaport.yaml"
COMPOSE_FILES = ("compose.yaml", "compose.yml", "docker-compose.yaml", "docker-compose.yml")

# Swarm stack and Compose project names: lowercase, digits, dash, underscore.
NAME_PATTERN = re.compile(r"^[a-z0-9][a-z0-9_-]{0,62}$")


class StackSettings(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: StackType | None = None
    deploy: DeployMode | None = None
    edits: EditMode | None = None


class InvalidSettings(ValueError):
    pass


def parse_settings(text: str | None) -> StackSettings:
    if text is None or not text.strip():
        return StackSettings()
    try:
        data = yaml.safe_load(text)
    except yaml.YAMLError as exc:
        raise InvalidSettings(f"{SETTINGS_FILE} is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise InvalidSettings(f"{SETTINGS_FILE} must be a mapping.")
    try:
        return StackSettings.model_validate(data)
    except ValidationError as exc:
        problems = "; ".join(
            f"{'.'.join(str(p) for p in e['loc']) or 'value'}: {e['msg']}" for e in exc.errors()
        )
        raise InvalidSettings(f"{SETTINGS_FILE}: {problems}") from exc


def stack_name(folder: str) -> str | None:
    """The stack name for a folder, or None if the folder name can't be used as one."""
    name = folder.rsplit("/", 1)[-1].lower()
    return name if NAME_PATTERN.match(name) else None
