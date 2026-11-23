# SPDX-License-Identifier: AGPL-3.0-or-later
"""Checks a normalised compose file against the cluster before anything is deployed."""

import asyncio
from collections import defaultdict
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from novaport.agents.pool import AgentError, Agents, AgentUnavailable
from novaport.deploy.constraints import InvalidConstraint, eligible_nodes
from novaport.docker.client import ClusterReader
from novaport.models import StackType


@dataclass
class Report:
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


def bind_sources(service: Mapping[str, Any]) -> list[str]:
    sources = []
    for volume in service.get("volumes") or []:
        if isinstance(volume, dict):
            if volume.get("type") == "bind" and volume.get("source"):
                sources.append(str(volume["source"]))
        elif isinstance(volume, str):
            source = volume.split(":", 1)[0]
            if source.startswith("/"):
                sources.append(source)
    return sources


def _externals(config: Mapping[str, Any], section: str) -> dict[str, str]:
    """Map of compose key to the real object name for external objects in a section."""
    result = {}
    for key, value in (config.get(section) or {}).items():
        if isinstance(value, dict) and value.get("external"):
            result[key] = str(value.get("name") or key)
    return result


def _hostname(node: Mapping[str, Any]) -> str:
    return str(node.get("Description", {}).get("Hostname") or node.get("ID", "?"))


async def _check_externals(
    config: Mapping[str, Any], stack_type: StackType, reader: ClusterReader, report: Report
) -> None:
    sections = ["networks"] + (["secrets", "configs"] if stack_type == StackType.SWARM else [])
    for section in sections:
        wanted = _externals(config, section)
        if not wanted:
            continue
        if section == "networks":
            existing = {n.get("Name") for n in await reader.networks()}
        elif section == "secrets":
            existing = {s.get("Spec", {}).get("Name") for s in await reader.secrets()}
        else:
            existing = {c.get("Spec", {}).get("Name") for c in await reader.configs()}
        noun = section.rstrip("s")
        for name in sorted(set(wanted.values()) - existing):
            report.errors.append(f"External {noun} {name!r} does not exist.")


async def _check_mounts(
    plan: dict[str, list[tuple[str, str]]],
    hostnames: dict[str, str],
    agents: Agents,
    report: Report,
) -> None:
    """`plan` maps node ID to (service, source) pairs that must exist on that node."""

    async def check(node_id: str, pairs: list[tuple[str, str]]) -> None:
        sources = sorted({source for _, source in pairs})
        try:
            results = await agents.post(node_id, "/paths", {"paths": sources})
        except (AgentUnavailable, AgentError) as exc:
            report.warnings.append(f"Could not check mounts on {hostnames[node_id]}: {exc}")
            return
        missing = {r["path"] for r in results if not r.get("exists")}
        for service, source in pairs:
            if source in missing:
                missing_on[(service, source)].append(hostnames[node_id])

    missing_on: dict[tuple[str, str], list[str]] = defaultdict(list)
    await asyncio.gather(*(check(node_id, pairs) for node_id, pairs in plan.items()))
    for (service, source), nodes in sorted(missing_on.items()):
        report.errors.append(
            f"{service}: bind mount source {source} does not exist on {', '.join(sorted(nodes))}."
        )


async def preflight(
    config: Mapping[str, Any],
    stack_type: StackType,
    stack: str,
    reader: ClusterReader,
    agents: Agents | None,
) -> Report:
    report = Report()
    await _check_externals(config, stack_type, reader, report)

    services: Mapping[str, Any] = config.get("services") or {}
    mounts = {name: bind_sources(svc) for name, svc in services.items()}
    if not any(mounts.values()):
        return report
    if agents is None or not agents.enabled:
        report.notes.append("Bind mounts were not checked: no agents configured.")
        return report

    if stack_type == StackType.SWARM:
        nodes = await reader.nodes()
    else:
        node_id = ((await reader.info()).get("Swarm") or {}).get("NodeID")
        nodes = [n for n in await reader.nodes() if n.get("ID") == node_id] if node_id else []
        if not nodes:
            report.notes.append("Bind mounts were not checked: this host is not a Swarm node.")
            return report

    plan: dict[str, list[tuple[str, str]]] = defaultdict(list)
    for name, sources in mounts.items():
        if not sources:
            continue
        service_name = f"{stack}_{name}" if stack_type == StackType.SWARM else name
        constraints = (
            (services[name].get("deploy") or {}).get("placement", {}).get("constraints") or []
            if stack_type == StackType.SWARM
            else []
        )
        try:
            targets = eligible_nodes(nodes, constraints)
        except InvalidConstraint as exc:
            report.errors.append(f"{service_name}: {exc}")
            continue
        if not targets:
            report.errors.append(f"{service_name}: no ready node satisfies its placement rules.")
            continue
        for node in targets:
            plan[node["ID"]].extend((service_name, source) for source in sources)

    hostnames = {n["ID"]: _hostname(n) for n in nodes}
    await _check_mounts(plan, hostnames, agents, report)
    return report
