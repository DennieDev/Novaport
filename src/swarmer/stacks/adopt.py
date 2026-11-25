# SPDX-License-Identifier: AGPL-3.0-or-later
"""Compose files for stacks that were deployed without Swarmer, rebuilt from what Docker runs.

Docker doesn't keep the compose file a stack came from, so this works backwards from service
and container specs. The result is a starting point to review, not an exact copy.
"""

import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from typing import Any

import yaml

from swarmer.docker.views import STACK_LABEL, Obj

COMPOSE_PROJECT = "com.docker.compose.project"
COMPOSE_SERVICE = "com.docker.compose.service"
COMPOSE_DEPENDS_ON = "com.docker.compose.depends_on"
# Labels Docker or Swarmer set themselves; copying them would only add noise.
_OWN_LABEL_PREFIXES = ("com.docker.stack.", "com.docker.compose.", "swarmer.")


@dataclass
class Generated:
    compose: str
    notes: list[str] = field(default_factory=list)
    # Environment values moved out of the compose file, which refers to them as ${NAME}.
    secrets: dict[str, str] = field(default_factory=dict)


_SECRET_NAME = re.compile(r"PASSWORD|PASSWD|SECRET|TOKEN|PRIVATE|CREDENTIAL|(^|_)(API_?)?KEY$")
_URL_WITH_PASSWORD = re.compile(r"^[a-z][a-z0-9+.-]*://[^/@:\s]+:[^/@\s]+@", re.IGNORECASE)


def looks_secret(name: str, value: str) -> bool:
    """Whether an environment variable probably holds a password, key, or token."""
    if not value or name.upper().endswith("_FILE"):
        return False
    return bool(_SECRET_NAME.search(name.upper()) or _URL_WITH_PASSWORD.match(value))


@dataclass(frozen=True)
class _Reference:
    """An environment value replaced by a variable from secrets.env."""

    name: str


def extract_secrets(services: dict[str, Any]) -> tuple[dict[str, str], list[str]]:
    """Replace secret-looking environment values with references, in place.

    Returns the values by name, and the names left in place because services set them to
    different values.
    """
    found: dict[str, set[str]] = {}
    for svc in services.values():
        for name, value in (svc.get("environment") or {}).items():
            if isinstance(value, str) and looks_secret(name, value):
                found.setdefault(name, set()).add(value)
    values = {name: next(iter(v)) for name, v in found.items() if len(v) == 1}
    for svc in services.values():
        env = svc.get("environment") or {}
        for name in env:
            if name in values:
                env[name] = _Reference(name)
    return dict(sorted(values.items())), sorted(set(found) - set(values))


def _secrets_note(moved: Iterable[str], kept: Iterable[str]) -> list[str]:
    notes = []
    if moved:
        notes.append(
            "These values look like passwords, keys, or tokens, so they go to secrets.env, "
            "encrypted with SOPS, and the compose file refers to them as ${NAME}: "
            + ", ".join(moved)
            + ". To keep one in the compose file instead, replace the reference with the value."
        )
    if kept:
        notes.append(
            "Services set these to different values, so they stay in the compose file. Rename "
            "them per service and move them to secrets.env before committing: " + ", ".join(kept)
        )
    return notes


def _own(label: str) -> bool:
    return label.startswith(_OWN_LABEL_PREFIXES)


def _labels(labels: Mapping[str, str] | None) -> dict[str, str]:
    return {k: v for k, v in sorted((labels or {}).items()) if not _own(k)}


def duration(nanoseconds: int | None) -> str | None:
    """Docker's nanoseconds as a compose duration: 90_000_000_000 gives `1m30s`."""
    if not nanoseconds:
        return None
    ms, rest = divmod(int(nanoseconds), 1_000_000)
    if rest:
        return f"{int(nanoseconds) // 1000}us"
    parts = []
    for unit, size in (("h", 3_600_000), ("m", 60_000), ("s", 1000)):
        count, ms = divmod(ms, size)
        if count:
            parts.append(f"{count}{unit}")
    if ms:
        parts.append(f"{ms}ms")
    return "".join(parts)


def size(value: int | None) -> str | None:
    if not value:
        return None
    for unit, factor in (("g", 1024**3), ("m", 1024**2), ("k", 1024)):
        if value % factor == 0:
            return f"{value // factor}{unit}"
    return f"{value}b"


def _image(ref: str) -> str:
    """Drops the digest `docker stack deploy` pins, unless the image has no tag to fall back on."""
    name, _, digest = ref.partition("@")
    has_tag = ":" in name.rsplit("/", 1)[-1]
    return name if digest and has_tag else ref


def _environment(env: Iterable[str] | None) -> dict[str, str]:
    result = {}
    for item in env or []:
        key, _, value = item.partition("=")
        result[key] = value
    return result


def _strip(name: str, prefix: str) -> str | None:
    return name[len(prefix) + 1 :] if name.startswith(f"{prefix}_") else None


# Mappings where an empty string is a real value (`EMPTY=`), not an unset field.
_KEEP_EMPTY = {"environment", "labels"}


def _prune(value: Any) -> Any:
    """Drops empty values, so the YAML only says what was actually set."""
    if isinstance(value, dict):
        pruned = {k: v if k in _KEEP_EMPTY else _prune(v) for k, v in value.items()}
        return {k: v for k, v in pruned.items() if v not in (None, {}, [], "")}
    if isinstance(value, list):
        return [_prune(v) for v in value]
    return value


class _Dumper(yaml.SafeDumper):
    pass


# `data:` rather than `data: null` for volumes and networks without options.
_Dumper.add_representer(
    type(None), lambda dumper, _: dumper.represent_scalar("tag:yaml.org,2002:null", "")
)
_Dumper.add_representer(
    _Reference,
    lambda dumper, ref: dumper.represent_scalar("tag:yaml.org,2002:str", f"${{{ref.name}}}"),
)


def _escape(value: Any) -> Any:
    """Doubles `$`, which compose would otherwise read as a variable: Docker stores the value
    after interpolation."""
    if isinstance(value, str):
        return value.replace("$", "$$")
    if isinstance(value, dict):
        return {k: _escape(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_escape(v) for v in value]
    return value


def _dump(services: dict[str, Any], **sections: dict[str, Any]) -> str:
    document: dict[str, Any] = {"services": _escape(_prune(services))}
    for key, entries in sections.items():
        if entries:
            document[key] = {name: _prune(value) or None for name, value in entries.items()}
    return yaml.dump(document, Dumper=_Dumper, sort_keys=False, default_flow_style=False, width=100)


def _healthcheck(check: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not check or not check.get("Test"):
        return None
    test = list(check["Test"])
    if test == ["NONE"]:
        return {"disable": True}
    return {
        "test": test,
        "interval": duration(check.get("Interval")),
        "timeout": duration(check.get("Timeout")),
        "retries": check.get("Retries"),
        "start_period": duration(check.get("StartPeriod")),
    }


def _update_config(config: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Update or rollback settings, without the values Swarm uses anyway."""
    if not config:
        return None
    return {
        "parallelism": p if (p := config.get("Parallelism")) not in (None, 1) else None,
        "delay": duration(config.get("Delay")),
        "failure_action": a if (a := config.get("FailureAction")) != "pause" else None,
        "monitor": duration(config.get("Monitor")),
        "max_failure_ratio": config.get("MaxFailureRatio") or None,
        "order": o if (o := config.get("Order")) != "stop-first" else None,
    }


def _resources(spec: Mapping[str, Any] | None) -> dict[str, Any] | None:
    if not spec:
        return None
    return {
        "cpus": f"{spec['NanoCPUs'] / 1e9:g}" if spec.get("NanoCPUs") else None,
        "memory": size(spec.get("MemoryBytes")),
        "pids": spec.get("Pids") or None,
    }


def swarm_compose(
    stack: str,
    services: Iterable[Obj],
    networks: Iterable[Obj],
    *,
    move_secrets: bool = False,
    start_first: Iterable[str] = (),
) -> Generated:
    """A compose file for a Swarm stack from its services' specs.

    With `move_secrets`, secret-looking environment values are taken out (see
    `extract_secrets`). Services named in `start_first` (full names) get
    `update_config.order: start-first`.
    """
    start_first = set(start_first)
    notes: list[str] = []
    network_by_id = {n.get("Id") or n.get("ID"): n for n in networks}
    top_networks: dict[str, Any] = {}
    top_volumes: dict[str, Any] = {}
    top_secrets: dict[str, Any] = {}
    top_configs: dict[str, Any] = {}
    out: dict[str, Any] = {}

    for raw in sorted(services, key=lambda s: s.get("Spec", {}).get("Name", "")):
        spec = raw.get("Spec", {})
        name = _strip(spec.get("Name", ""), stack) or spec.get("Name", "service")
        task = spec.get("TaskTemplate", {})
        container = task.get("ContainerSpec", {})
        svc: dict[str, Any] = {"image": _image(container.get("Image", ""))}
        svc["entrypoint"] = container.get("Command")
        svc["command"] = container.get("Args")
        svc["environment"] = _environment(container.get("Env"))
        svc["working_dir"] = container.get("Dir")
        svc["user"] = container.get("User")
        svc["hostname"] = container.get("Hostname")
        svc["labels"] = _labels(container.get("Labels"))
        svc["init"] = container.get("Init")
        svc["tty"] = container.get("TTY") or None
        svc["stdin_open"] = container.get("OpenStdin") or None
        svc["stop_signal"] = container.get("StopSignal")
        svc["stop_grace_period"] = duration(container.get("StopGracePeriod"))
        svc["cap_add"] = container.get("CapabilityAdd")
        svc["cap_drop"] = container.get("CapabilityDrop")
        svc["sysctls"] = container.get("Sysctls")
        svc["healthcheck"] = _healthcheck(container.get("Healthcheck"))

        volumes = []
        for mount in container.get("Mounts") or []:
            kind = mount.get("Type", "volume")
            source = mount.get("Source") or ""
            entry: dict[str, Any] = {"type": kind, "target": mount.get("Target")}
            if kind == "volume" and source:
                local = _strip(source, stack)
                if local is not None:
                    top_volumes[local] = {}
                    entry["source"] = local
                else:
                    top_volumes[source] = {"external": True}
                    entry["source"] = source
            elif source:
                entry["source"] = source
            entry["read_only"] = mount.get("ReadOnly") or None
            volumes.append(entry)
        svc["volumes"] = volumes

        for key, top, field_name in (
            ("Secrets", top_secrets, "SecretName"),
            ("Configs", top_configs, "ConfigName"),
        ):
            refs = []
            for ref in container.get(key) or []:
                real = ref.get(field_name, "")
                local = _strip(real, stack) or real
                top[local] = {"external": True, "name": real}
                file = ref.get("File") or {}
                target = file.get("Name")
                refs.append(
                    local
                    if target in (None, local)
                    else {
                        "source": local,
                        "target": target,
                        "uid": file.get("UID") if file.get("UID") not in (None, "0") else None,
                        "gid": file.get("GID") if file.get("GID") not in (None, "0") else None,
                    }
                )
            svc[key.lower()] = refs

        attached = []
        for net in task.get("Networks") or []:
            info = network_by_id.get(net.get("Target"), {})
            real = info.get("Name") or net.get("Target", "")
            if real == "ingress":
                continue
            local = _strip(real, stack)
            if local is not None:
                if local != "default" or info.get("Attachable"):
                    top_networks[local] = {
                        "driver": info.get("Driver") if info.get("Driver") != "overlay" else None,
                        "attachable": info.get("Attachable") or None,
                        "driver_opts": {
                            k: v
                            for k, v in (info.get("Options") or {}).items()
                            if not k.startswith("com.docker.network.driver.overlay.")
                        },
                        "labels": _labels(info.get("Labels")),
                    }
            else:
                local = real
                top_networks[local] = {"external": True}
            attached.append(
                {"name": local, "aliases": [a for a in net.get("Aliases") or [] if a != name]}
            )
        if attached and [a["name"] for a in attached] != ["default"]:
            if all(not a["aliases"] for a in attached):
                svc["networks"] = [a["name"] for a in attached]
            else:
                svc["networks"] = {a["name"]: {"aliases": a["aliases"] or None} for a in attached}

        endpoint = spec.get("EndpointSpec") or {}
        svc["ports"] = [
            {
                "target": p.get("TargetPort"),
                "published": p.get("PublishedPort"),
                "protocol": p.get("Protocol") if p.get("Protocol") != "tcp" else None,
                "mode": p.get("PublishMode") if p.get("PublishMode") != "ingress" else None,
            }
            for p in endpoint.get("Ports") or []
        ]

        mode = spec.get("Mode") or {}
        deploy: dict[str, Any] = {}
        if "Global" in mode:
            deploy["mode"] = "global"
        elif "ReplicatedJob" in mode:
            deploy["mode"] = "replicated-job"
        elif "GlobalJob" in mode:
            deploy["mode"] = "global-job"
        else:
            replicas = (mode.get("Replicated") or {}).get("Replicas")
            deploy["replicas"] = replicas if replicas != 1 else None
        deploy["endpoint_mode"] = endpoint.get("Mode") if endpoint.get("Mode") != "vip" else None
        deploy["labels"] = _labels(spec.get("Labels"))
        placement = task.get("Placement") or {}
        deploy["placement"] = {
            "constraints": placement.get("Constraints"),
            "preferences": [
                {"spread": p["Spread"]["SpreadDescriptor"]}
                for p in placement.get("Preferences") or []
                if "Spread" in p
            ],
            "max_replicas_per_node": placement.get("MaxReplicas"),
        }
        resources = task.get("Resources") or {}
        deploy["resources"] = {
            "limits": _resources(resources.get("Limits")),
            "reservations": _resources(resources.get("Reservations")),
        }
        restart = task.get("RestartPolicy") or {}
        if restart.get("Condition", "any") != "any" or restart.get("MaxAttempts"):
            deploy["restart_policy"] = {
                "condition": restart.get("Condition"),
                "delay": duration(restart.get("Delay")),
                "max_attempts": restart.get("MaxAttempts") or None,
                "window": duration(restart.get("Window")),
            }
        deploy["update_config"] = _update_config(spec.get("UpdateConfig"))
        if spec.get("Name") in start_first:
            deploy["update_config"] = (deploy["update_config"] or {}) | {"order": "start-first"}
        deploy["rollback_config"] = _update_config(spec.get("RollbackConfig"))
        svc["deploy"] = deploy

        log = task.get("LogDriver") or {}
        if log.get("Name"):
            svc["logging"] = {"driver": log["Name"], "options": log.get("Options")}

        out[name] = svc

    if top_secrets or top_configs:
        notes.append(
            "Secrets and configs are referenced as external, since their values can't be read "
            "back. To manage them in git, put the values in secrets.env and use "
            "`environment:` instead of `external:`."
        )
    secrets: dict[str, str] = {}
    if move_secrets:
        secrets, kept = extract_secrets(out)
        notes += _secrets_note(secrets, kept)
    elif any(s.get("environment") for s in out.values()):
        notes.append(
            "Environment variables are copied as they are. Move passwords and tokens to "
            "secrets.env before committing."
        )
    compose = _dump(
        out,
        networks=top_networks,
        volumes=top_volumes,
        secrets=top_secrets,
        configs=top_configs,
    )
    return Generated(compose, notes, secrets)


def compose_project(
    project: str, containers: Iterable[Obj], *, move_secrets: bool = False
) -> Generated:
    """A compose file for a Compose project from its containers' inspect output."""
    notes: list[str] = []
    by_service: dict[str, list[Obj]] = {}
    for c in containers:
        labels = (c.get("Config") or {}).get("Labels") or {}
        by_service.setdefault(
            labels.get(COMPOSE_SERVICE) or c.get("Name", "?").lstrip("/"), []
        ).append(c)

    top_networks: dict[str, Any] = {}
    top_volumes: dict[str, Any] = {}
    out: dict[str, Any] = {}
    for name, group in sorted(by_service.items()):
        c = group[0]
        config = c.get("Config") or {}
        host = c.get("HostConfig") or {}
        labels = config.get("Labels") or {}
        svc: dict[str, Any] = {"image": config.get("Image")}
        svc["entrypoint"] = config.get("Entrypoint")
        svc["command"] = config.get("Cmd")
        svc["environment"] = {
            k: v for k, v in _environment(config.get("Env")).items() if k != "PATH"
        }
        svc["working_dir"] = config.get("WorkingDir")
        svc["user"] = config.get("User")
        svc["labels"] = _labels(labels)
        svc["healthcheck"] = _healthcheck(config.get("Healthcheck"))
        restart = (host.get("RestartPolicy") or {}).get("Name")
        svc["restart"] = restart if restart not in (None, "", "no") else None
        svc["privileged"] = host.get("Privileged") or None
        svc["cap_add"] = host.get("CapAdd")
        svc["cap_drop"] = host.get("CapDrop")
        svc["extra_hosts"] = host.get("ExtraHosts")
        if len(group) > 1:
            svc["deploy"] = {"replicas": len(group)}

        if depends := labels.get(COMPOSE_DEPENDS_ON):
            deps: dict[str, Any] = {}
            for item in depends.split(","):
                dep, _, rest = item.partition(":")
                condition = rest.split(":")[0] or "service_started"
                if dep:
                    deps[dep] = {"condition": condition}
            svc["depends_on"] = deps

        ports = []
        for target, bindings in sorted((host.get("PortBindings") or {}).items()):
            port, _, protocol = target.partition("/")
            for b in bindings or []:
                ports.append(
                    {
                        "target": int(port),
                        "published": b.get("HostPort") or None,
                        "host_ip": b.get("HostIp") or None,
                        "protocol": protocol if protocol != "tcp" else None,
                    }
                )
        svc["ports"] = ports

        volumes = []
        for mount in c.get("Mounts") or []:
            kind = mount.get("Type", "volume")
            entry: dict[str, Any] = {"type": kind, "target": mount.get("Destination")}
            if kind == "volume":
                real = mount.get("Name") or ""
                local = _strip(real, project)
                if local is not None:
                    top_volumes[local] = {}
                    entry["source"] = local
                elif real and len(real) != 64:
                    top_volumes[real] = {"external": True}
                    entry["source"] = real
            else:
                entry["source"] = mount.get("Source")
            entry["read_only"] = (mount.get("RW") is False) or None
            volumes.append(entry)
        svc["volumes"] = volumes

        attached = []
        for real in sorted(((c.get("NetworkSettings") or {}).get("Networks") or {}).keys()):
            local = _strip(real, project)
            if local is None:
                if real in ("bridge", "host", "none"):
                    svc["network_mode"] = real if real != "bridge" else None
                    continue
                local = real
                top_networks[local] = {"external": True}
            elif local != "default":
                top_networks[local] = {}
            attached.append(local)
        if attached and attached != ["default"]:
            svc["networks"] = attached

        log = host.get("LogConfig") or {}
        if log.get("Type") and log["Type"] != "json-file":
            svc["logging"] = {"driver": log["Type"], "options": log.get("Config")}
        out[name] = svc

    notes.append(
        "Environment variables and labels include those the image sets; remove the ones you "
        "didn't set yourself."
    )
    secrets: dict[str, str] = {}
    if move_secrets:
        secrets, kept = extract_secrets(out)
        notes += _secrets_note(secrets, kept)
    elif any(s.get("environment") for s in out.values()):
        notes.append(
            "Move passwords and tokens from `environment` to secrets.env before committing."
        )
    notes.append("Build sections and env files can't be recovered from running containers.")
    return Generated(_dump(out, networks=top_networks, volumes=top_volumes), notes, secrets)


def stack_names(services: Iterable[Obj]) -> dict[str, int]:
    """Swarm stack names and how many services each has."""
    counts: dict[str, int] = {}
    for s in services:
        stack = (s.get("Spec", {}).get("Labels") or {}).get(STACK_LABEL)
        if stack:
            counts[stack] = counts.get(stack, 0) + 1
    return counts


def project_names(containers: Iterable[Obj]) -> dict[str, int]:
    """Compose project names and how many services each has."""
    services: dict[str, set[str]] = {}
    for c in containers:
        labels = c.get("Labels") or {}
        if project := labels.get(COMPOSE_PROJECT):
            services.setdefault(project, set()).add(labels.get(COMPOSE_SERVICE, ""))
    return {p: len(s) for p, s in services.items()}
