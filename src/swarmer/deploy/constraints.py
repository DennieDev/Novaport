# SPDX-License-Identifier: AGPL-3.0-or-later
"""Evaluates Swarm placement constraints against nodes, to know where a task may land."""

import re
from collections.abc import Iterable, Mapping
from typing import Any

CONSTRAINT = re.compile(r"^\s*([\w.\-/]+)\s*(==|!=)\s*(.*?)\s*$")


class InvalidConstraint(ValueError):
    pass


def _attribute(node: Mapping[str, Any], key: str) -> str | None:
    description = node.get("Description", {})
    spec = node.get("Spec", {})
    simple = {
        "node.id": node.get("ID"),
        "node.hostname": description.get("Hostname"),
        "node.role": spec.get("Role"),
        "node.platform.os": description.get("Platform", {}).get("OS"),
        "node.platform.arch": description.get("Platform", {}).get("Architecture"),
    }
    if key in simple:
        value = simple[key]
        return None if value is None else str(value)
    if key.startswith("node.labels."):
        label = (spec.get("Labels") or {}).get(key.removeprefix("node.labels."))
        return None if label is None else str(label)
    if key.startswith("engine.labels."):
        labels = description.get("Engine", {}).get("Labels") or {}
        engine_label = labels.get(key.removeprefix("engine.labels."))
        return None if engine_label is None else str(engine_label)
    raise InvalidConstraint(f"Unknown constraint attribute {key!r}.")


def matches(node: Mapping[str, Any], constraints: Iterable[str]) -> bool:
    for constraint in constraints:
        parsed = CONSTRAINT.match(constraint)
        if parsed is None:
            raise InvalidConstraint(f"Can't parse constraint {constraint!r}.")
        key, op, expected = parsed.groups()
        actual = _attribute(node, key)
        # Swarm compares case-insensitively for these, and a missing label never equals.
        equal = actual is not None and actual.lower() == expected.lower()
        if (op == "==") != equal:
            return False
    return True


def schedulable(node: Mapping[str, Any]) -> bool:
    spec, node_status = node.get("Spec", {}), node.get("Status", {})
    return bool(spec.get("Availability") == "active" and node_status.get("State") == "ready")


def eligible_nodes(
    nodes: Iterable[Mapping[str, Any]], constraints: Iterable[str]
) -> list[Mapping[str, Any]]:
    constraints = list(constraints)
    return [n for n in nodes if schedulable(n) and matches(n, constraints)]
