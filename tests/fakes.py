# SPDX-License-Identifier: AGPL-3.0-or-later
from collections.abc import AsyncIterator, Mapping
from typing import Any

from novaport.docker.client import NotFound

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
        "TaskTemplate": {"ContainerSpec": {"Image": "novaport:dev", "TTY": True}},
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
        self.service_list = [SERVICE_WEB, SERVICE_AGENT]
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
        return [NODE_WORKER, NODE_MANAGER]

    async def services(self) -> list[Obj]:
        self._check()
        return self.service_list

    async def service(self, service_id: str) -> Obj:
        self._check()
        for s in self.service_list:
            if service_id in (s["ID"], s["Spec"]["Name"]):
                return s
        raise NotFound(f"service {service_id} not found")

    async def tasks(self, service_id: str | None = None) -> list[Obj]:
        self._check()
        return TASKS

    async def networks(self) -> list[Obj]:
        self._check()
        return NETWORKS

    async def secrets(self) -> list[Obj]:
        self._check()
        return SECRETS

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
