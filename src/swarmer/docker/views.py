# SPDX-License-Identifier: AGPL-3.0-or-later
"""Turns raw Docker API objects into the shapes the API returns.

Only fields the UI uses are mapped, but nothing that explains a failure is dropped: task errors,
update status, mounts, and placement constraints are always included.
"""

from collections.abc import Iterable, Mapping
from datetime import datetime
from typing import Any

from pydantic import BaseModel

STACK_LABEL = "com.docker.stack.namespace"

Obj = Mapping[str, Any]


def _get(obj: Obj | None, *path: str, default: Any = None) -> Any:
    current: Any = obj
    for key in path:
        if not isinstance(current, Mapping):
            return default
        current = current.get(key)
        if current is None:
            return default
    return current


def _labels(obj: Obj, *path: str) -> dict[str, str]:
    return dict(_get(obj, *path, default={}) or {})


class SwarmSummary(BaseModel):
    state: str
    node_id: str | None
    is_manager: bool
    cluster_id: str | None
    managers: int | None
    nodes: int | None
    error: str | None


class SystemInfo(BaseModel):
    name: str
    docker_version: str
    os: str
    architecture: str
    cpus: int
    memory_bytes: int
    containers: int
    containers_running: int
    images: int
    swarm: SwarmSummary


def system_info(info: Obj) -> SystemInfo:
    swarm = _get(info, "Swarm", default={})
    return SystemInfo(
        name=info.get("Name", ""),
        docker_version=info.get("ServerVersion", ""),
        os=info.get("OperatingSystem", ""),
        architecture=info.get("Architecture", ""),
        cpus=info.get("NCPU", 0),
        memory_bytes=info.get("MemTotal", 0),
        containers=info.get("Containers", 0),
        containers_running=info.get("ContainersRunning", 0),
        images=info.get("Images", 0),
        swarm=SwarmSummary(
            state=swarm.get("LocalNodeState", "inactive"),
            node_id=swarm.get("NodeID") or None,
            is_manager=bool(swarm.get("ControlAvailable")),
            cluster_id=_get(swarm, "Cluster", "ID"),
            managers=swarm.get("Managers"),
            nodes=swarm.get("Nodes"),
            error=swarm.get("Error") or None,
        ),
    )


class Node(BaseModel):
    id: str
    hostname: str
    role: str
    availability: str
    state: str
    message: str | None
    address: str | None
    leader: bool
    manager_reachability: str | None
    engine_version: str | None
    os: str | None
    architecture: str | None
    cpus: int | None
    memory_bytes: int | None
    labels: dict[str, str]
    engine_labels: dict[str, str]


def node(raw: Obj) -> Node:
    nano_cpus = _get(raw, "Description", "Resources", "NanoCPUs")
    return Node(
        id=raw["ID"],
        hostname=_get(raw, "Description", "Hostname", default=""),
        role=_get(raw, "Spec", "Role", default=""),
        availability=_get(raw, "Spec", "Availability", default=""),
        state=_get(raw, "Status", "State", default="unknown"),
        message=_get(raw, "Status", "Message") or None,
        address=_get(raw, "Status", "Addr"),
        leader=bool(_get(raw, "ManagerStatus", "Leader", default=False)),
        manager_reachability=_get(raw, "ManagerStatus", "Reachability"),
        engine_version=_get(raw, "Description", "Engine", "EngineVersion"),
        os=_get(raw, "Description", "Platform", "OS"),
        architecture=_get(raw, "Description", "Platform", "Architecture"),
        cpus=nano_cpus // 1_000_000_000 if nano_cpus else None,
        memory_bytes=_get(raw, "Description", "Resources", "MemoryBytes"),
        labels=_labels(raw, "Spec", "Labels"),
        engine_labels=_labels(raw, "Description", "Engine", "Labels"),
    )


class Port(BaseModel):
    published: int | None
    target: int
    protocol: str
    mode: str


class Mount(BaseModel):
    type: str
    source: str | None
    target: str
    read_only: bool


class UpdateStatus(BaseModel):
    state: str | None
    message: str | None
    started_at: datetime | None
    completed_at: datetime | None


class ServiceSummary(BaseModel):
    id: str
    name: str
    stack: str | None
    image: str
    mode: str
    replicas_desired: int | None
    replicas_running: int | None
    ports: list[Port]
    update_status: UpdateStatus | None
    created_at: datetime
    updated_at: datetime


class NetworkRef(BaseModel):
    id: str
    name: str | None
    aliases: list[str]


class Service(ServiceSummary):
    labels: dict[str, str]
    container_labels: dict[str, str]
    command: list[str] | None
    args: list[str] | None
    env: list[str]
    mounts: list[Mount]
    networks: list[NetworkRef]
    secrets: list[str]
    configs: list[str]
    constraints: list[str]
    tty: bool
    restart_condition: str | None


def _mode(spec: Obj) -> tuple[str, int | None]:
    mode = spec.get("Mode", {})
    if "Replicated" in mode:
        return "replicated", _get(mode, "Replicated", "Replicas", default=1)
    if "Global" in mode:
        return "global", None
    if "ReplicatedJob" in mode:
        return "replicated-job", _get(mode, "ReplicatedJob", "TotalCompletions")
    if "GlobalJob" in mode:
        return "global-job", None
    return "unknown", None


def _image(container_spec: Obj) -> str:
    image: str = container_spec.get("Image", "")
    # Swarm pins `name:tag@sha256:...`; the digest is noise in lists.
    return image.split("@", 1)[0]


def _update_status(raw: Obj) -> UpdateStatus | None:
    status = raw.get("UpdateStatus")
    if not status:
        return None
    return UpdateStatus(
        state=status.get("State"),
        message=status.get("Message"),
        started_at=status.get("StartedAt"),
        completed_at=status.get("CompletedAt"),
    )


def service_summary(raw: Obj) -> ServiceSummary:
    spec = raw.get("Spec", {})
    mode, desired = _mode(spec)
    service_status = raw.get("ServiceStatus") or {}
    ports = [
        Port(
            published=p.get("PublishedPort"),
            target=p["TargetPort"],
            protocol=p.get("Protocol", "tcp"),
            mode=p.get("PublishMode", "ingress"),
        )
        for p in _get(raw, "Endpoint", "Ports", default=[])
    ]
    return ServiceSummary(
        id=raw["ID"],
        name=spec.get("Name", ""),
        stack=_get(spec, "Labels", STACK_LABEL),
        image=_image(_get(spec, "TaskTemplate", "ContainerSpec", default={})),
        mode=mode,
        replicas_desired=service_status.get("DesiredTasks", desired),
        replicas_running=service_status.get("RunningTasks"),
        ports=ports,
        update_status=_update_status(raw),
        created_at=raw["CreatedAt"],
        updated_at=raw["UpdatedAt"],
    )


def service(raw: Obj, network_names: Mapping[str, str]) -> Service:
    summary = service_summary(raw)
    task = _get(raw, "Spec", "TaskTemplate", default={})
    container = task.get("ContainerSpec", {})
    networks = task.get("Networks") or _get(raw, "Spec", "Networks", default=[])
    return Service(
        **summary.model_dump(),
        labels=_labels(raw, "Spec", "Labels"),
        container_labels=dict(container.get("Labels") or {}),
        command=container.get("Command"),
        args=container.get("Args"),
        env=list(container.get("Env") or []),
        mounts=[
            Mount(
                type=m.get("Type", "volume"),
                source=m.get("Source"),
                target=m["Target"],
                read_only=bool(m.get("ReadOnly")),
            )
            for m in container.get("Mounts") or []
        ],
        networks=[
            NetworkRef(
                id=n["Target"],
                name=network_names.get(n["Target"]),
                aliases=list(n.get("Aliases") or []),
            )
            for n in networks
        ],
        secrets=[
            s.get("SecretName", s.get("SecretID", "")) for s in container.get("Secrets") or []
        ],
        configs=[
            c.get("ConfigName", c.get("ConfigID", "")) for c in container.get("Configs") or []
        ],
        constraints=list(_get(task, "Placement", "Constraints", default=[])),
        tty=bool(container.get("TTY")),
        restart_condition=_get(task, "RestartPolicy", "Condition"),
    )


class Task(BaseModel):
    id: str
    service_id: str
    node_id: str | None
    node: str | None
    slot: int | None
    image: str
    desired_state: str
    state: str
    message: str | None
    error: str | None
    exit_code: int | None
    container_id: str | None
    updated_at: datetime


def task(raw: Obj, node_names: Mapping[str, str]) -> Task:
    status = raw.get("Status", {})
    node_id = raw.get("NodeID") or None
    return Task(
        id=raw["ID"],
        service_id=raw["ServiceID"],
        node_id=node_id,
        node=node_names.get(node_id) if node_id else None,
        slot=raw.get("Slot"),
        image=_image(_get(raw, "Spec", "ContainerSpec", default={})),
        desired_state=raw.get("DesiredState", ""),
        state=status.get("State", ""),
        message=status.get("Message") or None,
        error=status.get("Err") or None,
        exit_code=_get(status, "ContainerStatus", "ExitCode"),
        container_id=_get(status, "ContainerStatus", "ContainerID"),
        updated_at=raw.get("UpdatedAt") or status["Timestamp"],
    )


def tasks(raw: Iterable[Obj], node_names: Mapping[str, str]) -> list[Task]:
    """Newest first, so a task that keeps failing shows its latest error on top."""
    result = [task(t, node_names) for t in raw]
    result.sort(key=lambda t: t.updated_at, reverse=True)
    return result


class Subnet(BaseModel):
    subnet: str | None
    gateway: str | None


class Network(BaseModel):
    id: str
    name: str
    stack: str | None
    driver: str
    scope: str
    attachable: bool
    internal: bool
    ingress: bool
    subnets: list[Subnet]
    labels: dict[str, str]
    created_at: datetime | None


def network(raw: Obj) -> Network:
    labels = dict(raw.get("Labels") or {})
    return Network(
        id=raw["Id"],
        name=raw.get("Name", ""),
        stack=labels.get(STACK_LABEL),
        driver=raw.get("Driver", ""),
        scope=raw.get("Scope", ""),
        attachable=bool(raw.get("Attachable")),
        internal=bool(raw.get("Internal")),
        ingress=bool(raw.get("Ingress")),
        subnets=[
            Subnet(subnet=c.get("Subnet"), gateway=c.get("Gateway"))
            for c in _get(raw, "IPAM", "Config", default=[]) or []
        ],
        labels=labels,
        created_at=raw.get("Created"),
    )


class SwarmObject(BaseModel):
    """A secret or config. Values are never read or returned."""

    id: str
    name: str
    stack: str | None
    labels: dict[str, str]
    created_at: datetime
    updated_at: datetime


def swarm_object(raw: Obj) -> SwarmObject:
    labels = _labels(raw, "Spec", "Labels")
    return SwarmObject(
        id=raw["ID"],
        name=_get(raw, "Spec", "Name", default=""),
        stack=labels.get(STACK_LABEL),
        labels=labels,
        created_at=raw["CreatedAt"],
        updated_at=raw["UpdatedAt"],
    )
