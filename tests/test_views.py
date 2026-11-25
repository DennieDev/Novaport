# SPDX-License-Identifier: AGPL-3.0-or-later
import aiodocker

from swarmer.docker import views
from swarmer.docker.client import DockerUnavailable, NotFound, _translate
from tests.fakes import (
    INFO,
    NETWORKS,
    NODE_MANAGER,
    NODE_WORKER,
    SECRETS,
    SERVICE_AGENT,
    SERVICE_WEB,
    TASKS,
)


def test_system_info() -> None:
    info = views.system_info(INFO)
    assert info.docker_version == "29.10.0"
    assert info.swarm.state == "active"
    assert info.swarm.is_manager
    assert info.swarm.cluster_id == "cluster1"


def test_system_info_without_swarm() -> None:
    info = views.system_info({"ServerVersion": "29.0.0", "Swarm": {"LocalNodeState": "inactive"}})
    assert info.swarm.state == "inactive"
    assert not info.swarm.is_manager
    assert info.swarm.node_id is None


def test_node() -> None:
    node = views.node(NODE_MANAGER)
    assert node.hostname == "mgr-1"
    assert node.leader
    assert node.cpus == 4
    assert node.labels == {"zone": "a"}


def test_node_shows_why_it_is_down() -> None:
    node = views.node(NODE_WORKER)
    assert node.state == "down"
    assert node.message == "heartbeat failure"
    assert node.cpus is None


def test_service_summary() -> None:
    summary = views.service_summary(SERVICE_WEB)
    assert summary.name == "shop_web"
    assert summary.stack == "shop"
    assert summary.image == "nginx:1.29"
    assert summary.mode == "replicated"
    assert (summary.replicas_running, summary.replicas_desired) == (1, 2)
    assert summary.ports[0].published == 8080
    assert summary.update_status is not None
    assert summary.update_status.message == "update paused due to failure"


def test_global_service_without_status() -> None:
    summary = views.service_summary(SERVICE_AGENT)
    assert summary.mode == "global"
    assert summary.replicas_desired is None
    assert summary.stack is None


def test_service_detail() -> None:
    service = views.service(SERVICE_WEB, {"net1": "shop_default"})
    assert [m.source for m in service.mounts] == ["/srv/shop/html", "shop_cache"]
    assert service.mounts[1].read_only
    assert service.networks[0].name == "shop_default"
    assert service.networks[0].aliases == ["web"]
    assert service.secrets == ["shop_db_password"]
    assert service.constraints == ["node.role==worker"]
    assert service.restart_condition == "on-failure"
    assert not service.tty


def test_tasks_newest_first_with_errors() -> None:
    tasks = views.tasks(TASKS[:2], {"node1": "mgr-1"})
    assert [t.id for t in tasks] == ["task-new", "task-old"]
    failed = tasks[1]
    assert failed.state == "rejected"
    assert failed.error is not None
    assert "bind source path does not exist" in failed.error
    assert failed.node == "mgr-1"
    assert tasks[0].container_id == "c1"


def test_unassigned_task() -> None:
    task = views.task(TASKS[2], {})
    assert task.node_id is None
    assert task.node is None


def test_network() -> None:
    net = views.network(NETWORKS[0])
    assert net.stack == "shop"
    assert net.subnets[0].subnet == "10.0.1.0/24"
    assert views.network(NETWORKS[1]).ingress


def test_secret_has_no_value_field() -> None:
    secret = views.swarm_object(SECRETS[0])
    assert secret.name == "shop_db_password"
    assert secret.stack == "shop"
    assert "data" not in secret.model_dump()


def test_connection_failure_means_docker_unavailable() -> None:
    error = aiodocker.DockerError(900, {"message": "Cannot connect to Docker Engine"})
    assert isinstance(_translate(error), DockerUnavailable)
    assert isinstance(_translate(aiodocker.DockerError(404, {"message": "nope"})), NotFound)
