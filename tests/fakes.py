# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from typing import Any

from swarmer.agents.pool import Agent, AgentUnavailable
from swarmer.docker.client import NotFound

Obj = Mapping[str, Any]

NODE_MANAGER: Obj = {
    "ID": "node1",
    "Spec": {"Role": "manager", "Availability": "active", "Labels": {"zone": "a"}},
    "Description": {
        "Hostname": "mgr-1",
        "Platform": {"OS": "linux", "Architecture": "x86_64"},
        "Resources": {"NanoCPUs": 4_000_000_000, "MemoryBytes": 8_000_000_000},
        "Engine": {"EngineVersion": "29.10.0", "Labels": {}},
    },
    "Status": {"State": "ready", "Addr": "10.0.0.1"},
    "ManagerStatus": {"Leader": True, "Reachability": "reachable"},
}

NODE_WORKER: Obj = {
    "ID": "node2",
    "Spec": {"Role": "worker", "Availability": "drain"},
    "Description": {"Hostname": "wrk-1"},
    "Status": {"State": "down", "Message": "heartbeat failure"},
}

SERVICE_WEB: Obj = {
    "ID": "svc1",
    "CreatedAt": "2026-11-20T10:00:00.000000000Z",
    "UpdatedAt": "2026-11-22T10:00:00.000000000Z",
    "Spec": {
        "Name": "shop_web",
        "Labels": {"com.docker.stack.namespace": "shop"},
        "TaskTemplate": {
            "ContainerSpec": {
                "Image": "nginx:1.29@sha256:abc",
                "Env": ["MODE=prod"],
                "Mounts": [
                    {"Type": "bind", "Source": "/srv/shop/html", "Target": "/usr/share/nginx/html"},
                    {
                        "Type": "volume",
                        "Source": "shop_cache",
                        "Target": "/cache",
                        "ReadOnly": True,
                    },
                ],
                "Secrets": [{"SecretID": "sec1", "SecretName": "shop_db_password"}],
            },
            "Placement": {"Constraints": ["node.role==worker"]},
            "RestartPolicy": {"Condition": "on-failure"},
            "Networks": [{"Target": "net1", "Aliases": ["web"]}],
        },
        "Mode": {"Replicated": {"Replicas": 2}},
    },
    "Endpoint": {"Ports": [{"Protocol": "tcp", "TargetPort": 80, "PublishedPort": 8080}]},
    "ServiceStatus": {"RunningTasks": 1, "DesiredTasks": 2},
    "UpdateStatus": {"State": "paused", "Message": "update paused due to failure"},
}

SERVICE_AGENT: Obj = {
    "ID": "svc2",
    "CreatedAt": "2026-11-20T10:00:00Z",
    "UpdatedAt": "2026-11-20T10:00:00Z",
    "Spec": {
        "Name": "agent",
        "TaskTemplate": {"ContainerSpec": {"Image": "swarmer:dev", "TTY": True}},
        "Mode": {"Global": {}},
    },
}

TASKS: list[Obj] = [
    {
        "ID": "task-old",
        "ServiceID": "svc1",
        "NodeID": "node1",
        "Slot": 1,
        "Spec": {"ContainerSpec": {"Image": "nginx:1.29@sha256:abc"}},
        "DesiredState": "shutdown",
        "UpdatedAt": "2026-11-22T09:00:00Z",
        "Status": {
            "Timestamp": "2026-11-22T09:00:00Z",
            "State": "rejected",
            "Message": "preparing",
            "Err": "invalid mount config for type bind: bind source path does not exist",
        },
    },
    {
        "ID": "task-new",
        "ServiceID": "svc1",
        "NodeID": "node1",
        "Slot": 1,
        "Spec": {"ContainerSpec": {"Image": "nginx:1.29@sha256:abc"}},
        "DesiredState": "running",
        "UpdatedAt": "2026-11-22T10:00:00Z",
        "Status": {
            "Timestamp": "2026-11-22T10:00:00Z",
            "State": "running",
            "Message": "started",
            "ContainerStatus": {"ContainerID": "c1", "ExitCode": 0},
        },
    },
    {
        "ID": "task-other",
        "ServiceID": "svc1-similar",
        "Spec": {"ContainerSpec": {"Image": "x"}},
        "DesiredState": "running",
        "Status": {"Timestamp": "2026-11-22T10:00:00Z", "State": "pending"},
    },
]

NETWORKS: list[Obj] = [
    {
        "Id": "net1",
        "Name": "shop_default",
        "Driver": "overlay",
        "Scope": "swarm",
        "Attachable": False,
        "Labels": {"com.docker.stack.namespace": "shop"},
        "IPAM": {"Config": [{"Subnet": "10.0.1.0/24", "Gateway": "10.0.1.1"}]},
        "Created": "2026-11-20T10:00:00Z",
    },
    {"Id": "net0", "Name": "ingress", "Driver": "overlay", "Scope": "swarm", "Ingress": True},
]

SECRETS: list[Obj] = [
    {
        "ID": "sec1",
        "CreatedAt": "2026-11-20T10:00:00Z",
        "UpdatedAt": "2026-11-20T10:00:00Z",
        "Spec": {"Name": "shop_db_password", "Labels": {"com.docker.stack.namespace": "shop"}},
    }
]

INFO: Obj = {
    "Name": "mgr-1",
    "ServerVersion": "29.10.0",
    "OperatingSystem": "Debian GNU/Linux 13",
    "Architecture": "x86_64",
    "NCPU": 4,
    "MemTotal": 8_000_000_000,
    "Containers": 5,
    "ContainersRunning": 3,
    "Images": 10,
    "Swarm": {
        "LocalNodeState": "active",
        "NodeID": "node1",
        "ControlAvailable": True,
        "Cluster": {"ID": "cluster1"},
        "Managers": 1,
        "Nodes": 2,
    },
}


class FakeReader:
    def __init__(self) -> None:
        self.service_list: list[Obj] = [SERVICE_WEB, SERVICE_AGENT]
        self.container_list: list[Obj] = []
        # Inspect output by container ID; falls back to the list entry.
        self.container_inspect: dict[str, Obj] = {}
        self.task_list: list[Obj] = TASKS
        self.network_list: list[Obj] = NETWORKS
        self.secret_list: list[Obj] = SECRETS
        self.node_list: list[Obj] = [NODE_WORKER, NODE_MANAGER]
        self.log_chunks = ["line one\nline two\n", "line three\n"]
        self.error: Exception | None = None

    def _check(self) -> None:
        if self.error is not None:
            raise self.error

    async def info(self) -> Obj:
        self._check()
        return INFO

    async def nodes(self) -> list[Obj]:
        self._check()
        return self.node_list

    async def services(self, label: str | None = None) -> list[Obj]:
        self._check()
        if label is None:
            return self.service_list
        key, _, value = label.partition("=")
        return [s for s in self.service_list if s["Spec"].get("Labels", {}).get(key) == value]

    async def containers(self, label: str) -> list[Obj]:
        self._check()
        key, has_value, value = label.partition("=")
        if not has_value:
            return [c for c in self.container_list if key in c.get("Labels", {})]
        return [c for c in self.container_list if c.get("Labels", {}).get(key) == value]

    async def container(self, container_id: str) -> Obj:
        self._check()
        if container_id in self.container_inspect:
            return self.container_inspect[container_id]
        for c in self.container_list:
            if c.get("Id") == container_id:
                return c
        raise NotFound(container_id)

    async def service(self, service_id: str) -> Obj:
        self._check()
        for s in self.service_list:
            if service_id in (s["ID"], s["Spec"]["Name"]):
                return s
        raise NotFound(f"service {service_id} not found")

    async def tasks(self, service_id: str | None = None) -> list[Obj]:
        self._check()
        return self.task_list

    async def task(self, task_id: str) -> Obj:
        self._check()
        for t in self.task_list:
            if t["ID"] == task_id:
                return t
        raise NotFound(f"task {task_id} not found")

    async def networks(self) -> list[Obj]:
        self._check()
        return self.network_list

    async def secrets(self) -> list[Obj]:
        self._check()
        return self.secret_list

    async def configs(self) -> list[Obj]:
        self._check()
        return []

    async def service_logs(
        self, service_id: str, *, tail: int, timestamps: bool, tty: bool
    ) -> list[str]:
        await self.service(service_id)
        return self.log_chunks

    async def follow_service_logs(
        self, service_id: str, *, tail: int, timestamps: bool, tty: bool
    ) -> AsyncIterator[str]:
        await self.service(service_id)
        for chunk in self.log_chunks:
            yield chunk


class FakeAgentSocket:
    """Stands in for a websockets client connection to an agent."""

    def __init__(self, messages: list[str | bytes]) -> None:
        self.messages = messages
        self.sent: list[str | bytes] = []
        self.close_code: int | None = 1000
        self.close_reason = ""

    def __aiter__(self) -> AsyncIterator[str | bytes]:
        return self._iterate()

    async def _iterate(self) -> AsyncIterator[str | bytes]:
        for message in self.messages:
            yield message

    async def send(self, message: str | bytes) -> None:
        self.sent.append(message)


class FakeAgents:
    """Agents keyed by node ID; `paths` lists which host paths exist on each node."""

    def __init__(self, paths: dict[str, set[str]] | None = None, enabled: bool = True) -> None:
        self.paths = paths or {}
        self._enabled = enabled
        self.unreachable: set[str] = set()
        self.requests: list[tuple[str, str, object]] = []
        self.socket = FakeAgentSocket(["hello\n"])
        self.socket_paths: list[tuple[str, str]] = []

    @property
    def enabled(self) -> bool:
        return self._enabled

    async def list(self, refresh: bool = False) -> list[Agent]:
        return [
            Agent(
                f"http://{n}:9001",
                n,
                f"host-{n}",
                "test",
                None if n not in self.unreachable else "down",
            )
            for n in self.paths
        ]

    def _reachable(self, node_id: str) -> None:
        if node_id not in self.paths or node_id in self.unreachable:
            raise AgentUnavailable(f"No agent is reachable on node {node_id}.")

    async def get(self, node_id: str, path: str, params: dict[str, Any] | None = None) -> Any:
        self._reachable(node_id)
        self.requests.append((node_id, path, params))
        return {"node": node_id, "path": path, "params": params}

    async def post(self, node_id: str, path: str, body: Any) -> Any:
        self._reachable(node_id)
        self.requests.append((node_id, path, body))
        return [{"path": p, "exists": p in self.paths[node_id]} for p in body["paths"]]

    @asynccontextmanager
    async def websocket(self, node_id: str, path: str) -> AsyncIterator[FakeAgentSocket]:
        self._reachable(node_id)
        self.socket_paths.append((node_id, path))
        yield self.socket
