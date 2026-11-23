# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compares running Swarm services with the specs the last deploy left behind."""

import copy
import json
from collections.abc import Mapping
from typing import Any

MAX_LINES_PER_SERVICE = 20
MAX_VALUE = 80


def _clean(spec: Mapping[str, Any]) -> dict[str, Any]:
    cleaned = copy.deepcopy(dict(spec))
    # `docker service update --force` bumps this to restart tasks; it changes nothing else.
    cleaned.get("TaskTemplate", {}).pop("ForceUpdate", None)
    return cleaned


def snapshot(services: list[Mapping[str, Any]], stack: str) -> dict[str, dict[str, Any]]:
    """Specs of a stack's services, keyed by the name in the compose file."""
    prefix = f"{stack}_"
    result = {}
    for service in services:
        spec = service.get("Spec", {})
        name = str(spec.get("Name", ""))
        if name.startswith(prefix):
            result[name.removeprefix(prefix)] = _clean(spec)
    return result


def _flatten(value: Any, path: str, out: dict[str, Any]) -> None:
    if isinstance(value, dict):
        if not value and path:
            out[path] = {}
        for key, item in value.items():
            _flatten(item, f"{path}.{key}" if path else str(key), out)
    elif isinstance(value, list) and all(not isinstance(i, dict | list) for i in value):
        # Order of plain lists (env, args, constraints) rarely matters to anyone reading a diff.
        out[path] = sorted(str(i) for i in value)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            _flatten(item, f"{path}[{index}]", out)
    else:
        out[path] = value


def _show(value: Any) -> str:
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    return text if len(text) <= MAX_VALUE else text[: MAX_VALUE - 1] + "…"


def _differences(name: str, before: dict[str, Any], after: dict[str, Any]) -> list[str]:
    old: dict[str, Any] = {}
    new: dict[str, Any] = {}
    _flatten(before, "", old)
    _flatten(after, "", new)
    lines = []
    for path in sorted(set(old) | set(new)):
        if path in old and path in new:
            if old[path] != new[path]:
                lines.append(
                    f"{name}: {path} changed from {_show(old[path])} to {_show(new[path])}"
                )
        elif path in new:
            lines.append(f"{name}: {path} added ({_show(new[path])})")
        else:
            lines.append(f"{name}: {path} removed (was {_show(old[path])})")
    if len(lines) > MAX_LINES_PER_SERVICE:
        hidden = len(lines) - MAX_LINES_PER_SERVICE
        lines = [*lines[:MAX_LINES_PER_SERVICE], f"{name}: … and {hidden} more"]
    return lines


def compare(
    deployed: Mapping[str, dict[str, Any]], services: list[Mapping[str, Any]], stack: str
) -> list[str]:
    """Human-readable differences; empty when the running services match the deploy."""
    running = snapshot(services, stack)
    lines = []
    for name in sorted(set(deployed) | set(running)):
        if name not in running:
            lines.append(f"{name}: service was removed")
        elif name not in deployed:
            lines.append(f"{name}: service is not part of the deployed stack")
        else:
            lines.extend(_differences(name, deployed[name], running[name]))
    return lines
