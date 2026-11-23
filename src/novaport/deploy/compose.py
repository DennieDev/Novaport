# SPDX-License-Identifier: AGPL-3.0-or-later
import hashlib
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import yaml

from novaport.models import StackType

LABEL_STACK = "novaport.stack"
LABEL_REVISION = "novaport.revision"
LABEL_COMMIT = "novaport.commit"


class ComposeError(ValueError):
    pass


def _as_dict(labels: Any) -> dict[str, str]:
    if labels is None:
        return {}
    if isinstance(labels, dict):
        return {str(k): "" if v is None else str(v) for k, v in labels.items()}
    if isinstance(labels, list):
        result = {}
        for item in labels:
            key, _, value = str(item).partition("=")
            result[key] = value
        return result
    raise ComposeError(f"Unexpected labels value: {labels!r}")


def _escape_dollars(value: Any) -> Any:
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, dict):
        return {_escape_dollars(k): _escape_dollars(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_escape_dollars(v) for v in value]
    return value


def add_labels(normalized: str, stack_type: StackType, labels: Mapping[str, str]) -> str:
    """Add labels to every service in a normalised compose file.

    Swarm stacks get them as service labels (`deploy.labels`), Compose projects as container
    labels, which is where each tool looks for "what is this and where did it come from".
    """
    try:
        data = yaml.safe_load(normalized)
    except yaml.YAMLError as exc:
        raise ComposeError(f"Normalised compose file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict) or not isinstance(data.get("services"), dict):
        raise ComposeError("Compose file has no services.")

    if stack_type == StackType.SWARM:
        # `docker stack config` resolves `$$` to `$`, but `docker stack deploy` interpolates
        # the rendered file again, so literal dollars have to be escaped once more.
        data = _escape_dollars(data)
    for service in data["services"].values():
        if stack_type == StackType.SWARM:
            deploy = service.setdefault("deploy", {}) or {}
            service["deploy"] = deploy
            deploy["labels"] = _as_dict(deploy.get("labels")) | dict(labels)
        else:
            service["labels"] = _as_dict(service.get("labels")) | dict(labels)
    return yaml.safe_dump(data, sort_keys=False)


SECRETS_DIR = ".novaport-secrets"
VARIABLE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def secret_name(stack: str, secret: str, value: str) -> str:
    """Swarm secrets can't change, so the name carries a hash of the value: a new value makes a
    new secret and a rolling update."""
    digest = hashlib.sha256(value.encode()).hexdigest()[:8]
    return f"{stack}_{secret}_{digest}"


def secret_variables(source: str) -> list[str]:
    """Variables that `secrets: x: environment: VAR` entries read, in a raw compose file."""
    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError:
        return []
    if not isinstance(data, dict) or not isinstance(data.get("secrets"), dict):
        return []
    return [
        str(spec["environment"])
        for spec in data["secrets"].values()
        if isinstance(spec, dict) and "environment" in spec
    ]


def materialize_secrets(
    source: str, folder: Path, stack: str, values: Mapping[str, str]
) -> tuple[str, dict[str, str]]:
    """Turn `secrets: x: environment: KEY` entries into files that `docker stack` understands.

    Returns the rewritten compose file and a map of compose secret key to Swarm secret name.
    """
    try:
        data = yaml.safe_load(source)
    except yaml.YAMLError as exc:
        raise ComposeError(f"Compose file is not valid YAML: {exc}") from exc
    if not isinstance(data, dict):
        raise ComposeError("Compose file is not a mapping.")
    names: dict[str, str] = {}
    secrets = data.get("secrets") or {}
    for key, spec in secrets.items():
        if not isinstance(spec, dict) or "environment" not in spec:
            continue
        variable = str(spec["environment"])
        if not VARIABLE.match(variable):
            raise ComposeError(f"Secret {key!r}: {variable!r} is not a valid variable name.")
        if variable not in values:
            raise ComposeError(
                f"Secret {key!r} reads {variable}, which is not in secrets.env or .env."
            )
        directory = folder / SECRETS_DIR
        directory.mkdir(mode=0o700, exist_ok=True)
        target = directory / variable
        target.write_text(values[variable])
        target.chmod(0o600)
        names[key] = secret_name(stack, key, values[variable])
        secrets[key] = {"file": f"./{SECRETS_DIR}/{variable}", "name": names[key]}
    return yaml.safe_dump(data, sort_keys=False), names


def apply_secret_names(normalized: str, names: Mapping[str, str]) -> str:
    """Make sure the hashed names survive normalisation."""
    if not names:
        return normalized
    data = yaml.safe_load(normalized)
    for key, name in names.items():
        spec = (data.get("secrets") or {}).get(key)
        if isinstance(spec, dict):
            spec["name"] = name
    return yaml.safe_dump(data, sort_keys=False)
