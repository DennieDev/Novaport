# SPDX-License-Identifier: AGPL-3.0-or-later
import copy
from typing import Any

from novaport.deploy.drift import compare, snapshot


def service(name: str, replicas: int = 2, image: str = "nginx:1.27@sha256:aaa") -> dict[str, Any]:
    return {
        "ID": f"id-{name}",
        "Spec": {
            "Name": f"shop_{name}",
            "Labels": {"com.docker.stack.namespace": "shop"},
            "TaskTemplate": {
                "ContainerSpec": {"Image": image, "Env": ["A=1", "B=2"]},
                "ForceUpdate": 0,
            },
            "Mode": {"Replicated": {"Replicas": replicas}},
        },
    }


def test_snapshot_keys_by_compose_name() -> None:
    taken = snapshot([service("web"), service("db")], "shop")
    assert set(taken) == {"web", "db"}
    assert "ForceUpdate" not in taken["web"]["TaskTemplate"]


def test_no_drift_when_unchanged() -> None:
    services = [service("web")]
    assert compare(snapshot(services, "shop"), services, "shop") == []


def test_forced_restart_is_not_drift() -> None:
    deployed = snapshot([service("web")], "shop")
    restarted = service("web")
    restarted["Spec"]["TaskTemplate"]["ForceUpdate"] = 3
    assert compare(deployed, [restarted], "shop") == []


def test_env_order_is_not_drift() -> None:
    deployed = snapshot([service("web")], "shop")
    reordered = service("web")
    reordered["Spec"]["TaskTemplate"]["ContainerSpec"]["Env"] = ["B=2", "A=1"]
    assert compare(deployed, [reordered], "shop") == []


def test_manual_changes_are_reported() -> None:
    deployed = snapshot([service("web"), service("db")], "shop")
    web = service("web", replicas=5, image="nginx:1.28@sha256:bbb")
    web["Spec"]["TaskTemplate"]["ContainerSpec"]["Env"].append("DEBUG=1")
    extra = service("debug")

    drift = compare(deployed, [web, extra], "shop")

    assert drift == [
        "db: service was removed",
        "debug: service is not part of the deployed stack",
        "web: Mode.Replicated.Replicas changed from 2 to 5",
        'web: TaskTemplate.ContainerSpec.Env changed from ["A=1", "B=2"] to '
        '["A=1", "B=2", "DEBUG=1"]',
        "web: TaskTemplate.ContainerSpec.Image changed from nginx:1.27@sha256:aaa to "
        "nginx:1.28@sha256:bbb",
    ]


def test_long_diffs_are_cut() -> None:
    deployed = snapshot([service("web")], "shop")
    changed = copy.deepcopy(service("web"))
    changed["Spec"]["Labels"] = {f"l{i}": str(i) for i in range(40)}
    drift = compare(deployed, [changed], "shop")
    assert len(drift) == 21
    assert drift[-1].startswith("web: … and ")
