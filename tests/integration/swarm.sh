#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# A three-node Docker Swarm in Docker-in-Docker containers, for the integration tests.
#
#   tests/integration/swarm.sh up      start the nodes and print the variables pytest needs
#   tests/integration/swarm.sh env     print those variables again
#   tests/integration/swarm.sh down    remove everything
#
# Set SWARMER_SWARM_AGENT_IMAGE (for example swarmer:dev, built with `docker build -t
# swarmer:dev .`) to also run an agent on every node. Requires Linux: the tests reach the
# agents on the nodes' container IPs.
set -eu

NAME=${SWARMER_SWARM_NAME:-swarmer-swarm}
PORT=${SWARMER_SWARM_PORT:-23750}
DIND_IMAGE=${SWARMER_SWARM_DIND_IMAGE:-docker:29-dind}
AGENT_IMAGE=${SWARMER_SWARM_AGENT_IMAGE:-}
AGENT_TOKEN=integration-test-token
# Loaded into every node up front, so tests don't depend on Docker Hub rate limits.
IMAGES="busybox:1.37"
NODES="manager worker1 worker2"

node() { echo "$NAME-$1"; }
on() {
    n=$1
    shift
    docker exec "$(node "$n")" "$@"
}

wait_for_docker() {
    for _ in $(seq 60); do
        on "$1" docker info >/dev/null 2>&1 && return 0
        sleep 1
    done
    echo "Docker in $(node "$1") did not start" >&2
    exit 1
}

load_image() {
    docker image inspect "$1" >/dev/null 2>&1 || docker pull -q "$1" >/dev/null
    for n in $NODES; do
        docker save "$1" | docker exec -i "$(node "$n")" docker load -q >/dev/null
    done
}

print_env() {
    echo "SWARMER_TEST_DOCKER_HOST=tcp://127.0.0.1:$PORT"
    if on manager docker container inspect swarmer-agent >/dev/null 2>&1; then
        urls=""
        for n in $NODES; do
            ip=$(docker inspect -f "{{(index .NetworkSettings.Networks \"$NAME\").IPAddress}}" "$(node "$n")")
            urls="${urls:+$urls,}http://$ip:9001"
        done
        echo "SWARMER_TEST_AGENT_URLS=$urls"
        echo "SWARMER_TEST_AGENT_TOKEN=$AGENT_TOKEN"
    fi
}

up() {
    docker network inspect "$NAME" >/dev/null 2>&1 || docker network create "$NAME" >/dev/null
    for n in $NODES; do
        publish=""
        [ "$n" = manager ] && publish="--publish 127.0.0.1:$PORT:2375"
        # shellcheck disable=SC2086
        docker run --detach --privileged --name "$(node "$n")" --hostname "$n" \
            --network "$NAME" $publish "$DIND_IMAGE" \
            dockerd --host=unix:///var/run/docker.sock --host=tcp://0.0.0.0:2375 --tls=false \
            >/dev/null
    done
    for n in $NODES; do wait_for_docker "$n"; done

    on manager docker swarm init --advertise-addr eth0 >/dev/null
    token=$(on manager docker swarm join-token --quiet worker)
    for n in $NODES; do
        [ "$n" = manager ] && continue
        on "$n" docker swarm join --token "$token" "$(node manager):2377" >/dev/null
    done

    for image in $IMAGES; do load_image "$image"; done

    if [ -n "$AGENT_IMAGE" ]; then
        load_image "$AGENT_IMAGE"
        for n in $NODES; do
            on "$n" docker run --detach --name swarmer-agent --restart always \
                --publish 9001:9001 \
                --volume /var/run/docker.sock:/var/run/docker.sock --volume /:/host:ro \
                --env SWARMER_AGENT_TOKEN="$AGENT_TOKEN" "$AGENT_IMAGE" agent >/dev/null
        done
    fi
    print_env
}

down() {
    for n in $NODES; do
        docker rm --force --volumes "$(node "$n")" >/dev/null 2>&1 || true
    done
    docker network rm "$NAME" >/dev/null 2>&1 || true
}

case "${1:-}" in
up) up ;;
env) print_env ;;
down) down ;;
*)
    echo "usage: $0 up|env|down" >&2
    exit 2
    ;;
esac
