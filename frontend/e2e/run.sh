#!/bin/sh
# SPDX-License-Identifier: AGPL-3.0-or-later
#
# Runs the end-to-end tests: builds the frontend, starts a Novaport server against a fresh
# database and the test Swarm, and drives it with Playwright.
#
# Needs Postgres (NOVAPORT_TEST_DATABASE_URL, as for pytest), a test Swarm
# (NOVAPORT_TEST_DOCKER_HOST, from tests/integration/swarm.sh), uv, and git. Node.js and
# Playwright's browsers are used when installed; otherwise the steps run in containers.
set -eu

ROOT=$(cd "$(dirname "$0")/../.." && pwd)
PORT=${NOVAPORT_E2E_PORT:-8790}
PASSWORD=e2e-admin-password
STACK=e2e-web
PLAYWRIGHT_IMAGE=mcr.microsoft.com/playwright:v1.65.1-noble
: "${NOVAPORT_TEST_DATABASE_URL:?set it as for pytest}"
: "${NOVAPORT_TEST_DOCKER_HOST:?start tests/integration/swarm.sh first}"

WORK=$(mktemp -d)
SERVER_PID=""
cleanup() {
    [ -n "$SERVER_PID" ] && kill "$SERVER_PID" 2>/dev/null || true
    DOCKER_HOST=$NOVAPORT_TEST_DOCKER_HOST docker stack rm "$STACK" >/dev/null 2>&1 || true
    rm -rf "$WORK"
}
trap cleanup EXIT INT TERM

in_frontend() {
    if command -v npx >/dev/null 2>&1; then
        (cd "$ROOT/frontend" && "$@")
    else
        docker run --rm --network host -u "$(id -u):$(id -g)" -e HOME=/tmp -e CI \
            -e NOVAPORT_E2E_URL -e NOVAPORT_E2E_PASSWORD -e NOVAPORT_E2E_REPO -e NOVAPORT_E2E_STACK \
            -v "$ROOT/frontend:/app" -w /app "$PLAYWRIGHT_IMAGE" "$@"
    fi
}

echo "Building the frontend"
in_frontend npm run build >/dev/null

DATABASE_URL=$(cd "$ROOT" && uv run --quiet python - "$NOVAPORT_TEST_DATABASE_URL" <<'EOF'
import asyncio, sys
import asyncpg
from sqlalchemy.engine import make_url

url = make_url(sys.argv[1])
e2e = url.set(database="novaport_e2e")

async def main() -> None:
    conn = await asyncpg.connect(
        user=url.username, password=url.password, host=url.host, port=url.port, database="postgres"
    )
    await conn.execute("DROP DATABASE IF EXISTS novaport_e2e WITH (FORCE)")
    await conn.execute("CREATE DATABASE novaport_e2e")
    await conn.close()

asyncio.run(main())
print(e2e.render_as_string(hide_password=False))
EOF
)

REPO="$WORK/origin"
mkdir -p "$REPO/stacks/$STACK"
cat >"$REPO/stacks/$STACK/compose.yaml" <<'EOF'
services:
  app:
    image: busybox:1.37
    command: sleep 3600
    deploy:
      replicas: 2
EOF
git -C "$REPO" init --quiet --initial-branch=main
git -C "$REPO" add --all
git -C "$REPO" -c user.name=Test -c user.email=test@example.com commit --quiet -m "Add $STACK"

echo "Starting Novaport on port $PORT"
(
    cd "$ROOT"
    NOVAPORT_DATABASE_URL=$DATABASE_URL \
    NOVAPORT_DATA_DIR="$WORK/data" \
    NOVAPORT_STATIC_DIR="$ROOT/frontend/build" \
    NOVAPORT_INITIAL_ADMIN_PASSWORD=$PASSWORD \
    NOVAPORT_SECRET_KEY=$(uv run --quiet novaport generate-key) \
    NOVAPORT_DOCKER_HOST=$NOVAPORT_TEST_DOCKER_HOST \
    NOVAPORT_ALLOW_LOCAL_REPOSITORIES=true \
    NOVAPORT_AGENT_URLS=${NOVAPORT_TEST_AGENT_URLS:-} \
    NOVAPORT_AGENT_TOKEN=${NOVAPORT_TEST_AGENT_TOKEN:-} \
        exec uv run --quiet novaport server --port "$PORT"
) >"$WORK/server.log" 2>&1 &
SERVER_PID=$!

for _ in $(seq 60); do
    curl -sf "http://127.0.0.1:$PORT/api/healthz" >/dev/null 2>&1 && break
    sleep 1
done
if ! curl -sf "http://127.0.0.1:$PORT/api/healthz" >/dev/null; then
    cat "$WORK/server.log" >&2
    exit 1
fi

export NOVAPORT_E2E_URL="http://127.0.0.1:$PORT"
export NOVAPORT_E2E_PASSWORD=$PASSWORD
export NOVAPORT_E2E_REPO="file://$REPO"
export NOVAPORT_E2E_STACK=$STACK
if ! in_frontend npx playwright test "$@"; then
    echo "--- server log ---" >&2
    tail -50 "$WORK/server.log" >&2
    exit 1
fi
