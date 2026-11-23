# Novaport

Git-driven management UI for Docker and Docker Swarm stacks.

Novaport deploys stacks from a git repository, shows exactly what is running and why something
isn't, and writes changes made in the UI back to git. A failed deploy never touches your stack
definition.

> Early development. Nothing here is ready for use yet.

See [docs/design.md](docs/design.md) for the design.

## Running on Docker Swarm

[deploy/stack.yml](deploy/stack.yml) runs the server, an agent on every node, and Postgres. The
comments at the top list the secrets to create first.

The agent mounts the Docker socket and the host filesystem read-only. It answers only to the
server (shared token, encrypted overlay network) and gives Novaport what a manager can't see on
its own: container logs and terminals on every node, bind mount checks before a deploy, and
per-node disk, image, and volume details.

## Secrets

Novaport reads SOPS-encrypted `secrets.env` files with an age key. Generate one with
`novaport generate-age-key`, give it to Novaport as `NOVAPORT_AGE_KEY` (or the
`novaport_age_key` Swarm secret), and list its public key in the repository's `.sops.yaml` next
to your own:

```yaml
creation_rules:
  - path_regex: secrets\.env$
    age: >-
      age1novaport...,
      age1yourownkey...
```

Reference a value from a compose file with `secrets: name: environment: KEY`. Novaport turns it
into a Swarm secret whose name includes a hash of the value, so rotating a secret is a normal
rolling update.

## Development

Requirements: [uv](https://docs.astral.sh/uv/), Docker, and Node.js 22+ for the frontend.

```sh
docker compose -f compose.dev.yaml up -d   # Postgres on localhost:5432
cp .env.example .env
uv sync
uv run novaport migrate
uv run novaport create-admin
uv run novaport server --reload
```

The API is then available on http://localhost:8000 (`/api/docs` for the OpenAPI UI). Novaport
talks to the Docker daemon from `NOVAPORT_DOCKER_HOST`, `DOCKER_HOST`, or the local socket, and
needs a Swarm manager for the Swarm views.

Copy `.env.example` to `.env` first. It sets a local data directory (git mirrors live there) and
has a slot for `NOVAPORT_SECRET_KEY`, which encrypts repository credentials; generate one with
`uv run novaport generate-key`.

Instead of `create-admin`, set `NOVAPORT_INITIAL_ADMIN_PASSWORD` (at least 12 characters) to have
the server create an `admin` user on first start, when no users exist yet.

Frontend, with the dev server proxying `/api` to the backend:

```sh
cd frontend
npm install
npm run dev
```

Checks:

```sh
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```

Tests marked `postgres` run when `NOVAPORT_TEST_DATABASE_URL` is set, for example
`postgresql+asyncpg://novaport:novaport@localhost:5432/novaport_test`.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
