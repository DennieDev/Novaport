# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import Mapping
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

    for service in data["services"].values():
        if stack_type == StackType.SWARM:
            deploy = service.setdefault("deploy", {}) or {}
            service["deploy"] = deploy
            deploy["labels"] = _as_dict(deploy.get("labels")) | dict(labels)
        else:
            service["labels"] = _as_dict(service.get("labels")) | dict(labels)
    return yaml.safe_dump(data, sort_keys=False)
