# SPDX-License-Identifier: AGPL-3.0-or-later
from typing import Any

import pytest

from novaport.deploy.constraints import InvalidConstraint, eligible_nodes, matches
from novaport.deploy.preflight import bind_sources, preflight
from novaport.models import StackType
from tests.fakes import FakeAgents, FakeReader


def node(node_id: str, role: str, **labels: str) -> dict[str, Any]:
    return {
        "ID": node_id,
        "Spec": {"Role": role, "Availability": "active", "Labels": labels},
        "Description": {
            "Hostname": f"host-{node_id}",
            "Platform": {"OS": "linux", "Architecture": "x86_64"},
            "Engine": {"Labels": {"storage": "ssd"}},
        },
        "Status": {"State": "ready"},
    }


MANAGER = node("m1", "manager", zone="a")
WORKER_A = node("w1", "worker", zone="a")
WORKER_B = node("w2", "worker", zone="b")
DRAINED = {**node("w3", "worker"), "Spec": {"Role": "worker", "Availability": "drain"}}


@pytest.mark.parametrize(
    ("constraints", "expected"),
    [
        ([], ["m1", "w1", "w2"]),
        (["node.role==worker"], ["w1", "w2"]),
        (["node.role != manager", "node.labels.zone==a"], ["w1"]),
        (["node.hostname==host-w2"], ["w2"]),
        (["node.labels.missing==x"], []),
        (["node.labels.missing!=x"], ["m1", "w1", "w2"]),
        (["engine.labels.storage==SSD"], ["m1", "w1", "w2"]),
        (["node.platform.arch==aarch64"], []),
    ],
)
def test_eligible_nodes(constraints: list[str], expected: list[str]) -> None:
    nodes = [MANAGER, WORKER_A, WORKER_B, DRAINED]
    assert [n["ID"] for n in eligible_nodes(nodes, constraints)] == expected


def test_bad_constraints() -> None:
    with pytest.raises(InvalidConstraint):
        matches(MANAGER, ["node.role"])
    with pytest.raises(InvalidConstraint):
        matches(MANAGER, ["node.colour==red"])


def test_bind_sources_short_and_long_syntax() -> None:
    service = {
        "volumes": [
            {"type": "bind", "source": "/srv/a", "target": "/a"},
            {"type": "volume", "source": "data", "target": "/data"},
            "/srv/b:/b:ro",
            "named:/c",
        ]
    }
    assert bind_sources(service) == ["/srv/a", "/srv/b"]


def config(**services: dict[str, Any]) -> dict[str, Any]:
    return {"services": services}


def reader_with(*nodes: dict[str, Any]) -> FakeReader:
    reader = FakeReader()
    reader.node_list = list(nodes)
    return reader


async def test_missing_mount_is_reported_per_node() -> None:
    agents = FakeAgents({"m1": {"/srv/www"}, "w1": set(), "w2": {"/srv/www"}})
    report = await preflight(
        config(web={"volumes": [{"type": "bind", "source": "/srv/www", "target": "/w"}]}),
        StackType.SWARM,
        "shop",
        reader_with(MANAGER, WORKER_A, WORKER_B),
        agents,
    )
    assert report.errors == ["shop_web: bind mount source /srv/www does not exist on host-w1."]


async def test_only_eligible_nodes_are_checked() -> None:
    agents = FakeAgents({"m1": set(), "w1": {"/srv/www"}, "w2": {"/srv/www"}})
    service = {
        "volumes": ["/srv/www:/w"],
        "deploy": {"placement": {"constraints": ["node.role==worker"]}},
    }
    report = await preflight(
        config(web=service),
        StackType.SWARM,
        "shop",
        reader_with(MANAGER, WORKER_A, WORKER_B),
        agents,
    )
    assert report.errors == []
    assert {node for node, _, _ in agents.requests} == {"w1", "w2"}


async def test_unplaceable_service() -> None:
    service = {
        "volumes": ["/srv/www:/w"],
        "deploy": {"placement": {"constraints": ["node.labels.zone==c"]}},
    }
    report = await preflight(
        config(web=service), StackType.SWARM, "shop", reader_with(MANAGER, WORKER_A), FakeAgents()
    )
    assert report.errors == ["shop_web: no ready node satisfies its placement rules."]


async def test_unreachable_agent_is_a_warning() -> None:
    agents = FakeAgents({"m1": {"/srv/www"}, "w1": {"/srv/www"}})
    agents.unreachable.add("w1")
    report = await preflight(
        config(web={"volumes": ["/srv/www:/w"]}),
        StackType.SWARM,
        "shop",
        reader_with(MANAGER, WORKER_A),
        agents,
    )
    assert report.errors == []
    assert len(report.warnings) == 1
    assert "host-w1" in report.warnings[0]


async def test_without_agents_mounts_are_noted_not_checked() -> None:
    report = await preflight(
        config(web={"volumes": ["/srv/www:/w"]}),
        StackType.SWARM,
        "shop",
        reader_with(MANAGER),
        None,
    )
    assert report.errors == []
    assert report.notes == ["Bind mounts were not checked: no agents configured."]


async def test_missing_external_objects() -> None:
    cfg = {
        "services": {"web": {}},
        "networks": {"proxy": {"external": True}, "default": {}},
        "secrets": {"db": {"external": True, "name": "shop_db_password"}, "x": {"external": True}},
    }
    report = await preflight(cfg, StackType.SWARM, "shop", FakeReader(), None)
    assert report.errors == [
        "External network 'proxy' does not exist.",
        "External secret 'x' does not exist.",
    ]


async def test_compose_checks_the_local_node() -> None:
    reader = reader_with(MANAGER, WORKER_A)
    agents = FakeAgents({"m1": set(), "w1": {"/srv/www"}})
    report = await preflight(
        config(web={"volumes": ["/srv/www:/w"]}), StackType.COMPOSE, "shop", reader, agents
    )
    # FakeReader.info() reports node1 as the local node, which isn't in the list.
    assert report.notes == ["Bind mounts were not checked: this host is not a Swarm node."]

    reader.node_list = [{**MANAGER, "ID": "node1"}]
    agents.paths = {"node1": set()}
    report = await preflight(
        config(web={"volumes": ["/srv/www:/w"]}), StackType.COMPOSE, "shop", reader, agents
    )
    assert report.errors == ["web: bind mount source /srv/www does not exist on host-m1."]
