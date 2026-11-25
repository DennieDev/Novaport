# Swarmer

Git-driven management UI for Docker and Docker Swarm stacks.

Swarmer deploys stacks from a git repository, shows exactly what is running and why something
isn't, and writes changes made in the UI back to git. A failed deploy never changes what's in
git: it is marked failed, and rolling back is a deploy of an older revision you ask for.

> Early development. Nothing here is ready for use yet.

- [Writing stacks](docs/stacks.md): repository layout, `swarmer.yaml`, secrets, image updates.
- [Running Swarmer](docs/operations.md): installing, configuration, webhooks, backups, upgrades.
- [Design](docs/design.md).

## Running on Docker Swarm

[deploy/docker-compose.yml](deploy/docker-compose.yml) runs the server, an agent on every node,
and Postgres, behind Traefik. Settings and keys go in a `.env` file next to it; start from
[deploy/.env.example](deploy/.env.example).

The agent mounts the Docker socket and the host filesystem read-only. It answers only to the
server (shared token, encrypted overlay network) and gives Swarmer what a manager can't see on
its own: container logs and terminals on every node, bind mount checks before a deploy, and
per-node disk, image, and volume details.

## Other hosts and clusters

Swarmer deploys to the Swarm it runs on, and to any other Docker host or Swarm cluster with an
agent that connects back to it. Add an endpoint under Endpoints; Swarmer shows a key and the
command that starts the agent:

```sh
docker run -d --name swarmer-agent --restart always \
  -v /var/run/docker.sock:/var/run/docker.sock -v /:/host:ro \
  -e SWARMER_EDGE_KEY=... ghcr.io/dennielabs/swarmer:latest agent
```

The host needs no open ports, only a way to reach Swarmer. Point a stack at the endpoint in its
`swarmer.yaml`:

```yaml
endpoint: edge-01
type: compose
```

The key gives full Docker access to that host, so keep it secret. Set `SWARMER_PUBLIC_URL` when
Swarmer is behind a proxy, so the key carries the external address.

## Secrets

Swarmer reads SOPS-encrypted `secrets.env` files with an age key. Generate one with
`swarmer generate-age-key`, give it to Swarmer as `SWARMER_AGE_KEY` (or the
`swarmer_age_key` Swarm secret), and list its public key in the repository's `.sops.yaml` next
to your own:

```yaml
creation_rules:
  - path_regex: secrets\.env$
    age: >-
      age1swarmer...,
      age1yourownkey...
```

Reference a value from a compose file with `secrets: name: environment: KEY`. Swarmer turns it
into a Swarm secret whose name includes a hash of the value, so rotating a secret is a normal
rolling update.

## Image updates

Add an `x-updates` block to a service to have Swarmer look for newer images:

```yaml
services:
  api:
    image: ghcr.io/acme/api:2.4.1
    x-updates:
      mode: version   # or digest, to follow a moving tag
      allow: minor    # patch, minor, or major
      action: pr      # commit, pr, notify, or none
```

Updates are commits or pull requests like any other change, so the stack's deploy mode decides
what happens next. Credentials for private registries go under Registries; Swarmer also uses
them to check images before a deploy and passes them to Swarm.

## Notifications and drift

Under Notifications, send deploy results, approval requests, image updates, drift, and node,
agent, or endpoint outages to a webhook, Slack, or email. Email needs an SMTP server:

```sh
SWARMER_SMTP_HOST=smtp.example.com
SWARMER_SMTP_PORT=587
SWARMER_SMTP_USERNAME=swarmer
SWARMER_SMTP_PASSWORD=...        # or the swarmer_smtp_password Swarm secret
SWARMER_SMTP_FROM=swarmer@example.com
```

After each deploy Swarmer remembers how the Swarm services looked and flags a stack as drifted
when someone changes them by hand. Deploying again puts them back.

## Login and roles

The first admin logs in with a password. Add an OpenID Connect provider under Access → Login
providers; the page shows the redirect URI to register with the provider. Behind a proxy, set
`SWARMER_PUBLIC_URL` (for example `https://swarmer.example.com`) so the redirect URI uses the
external address.

Users from a provider start without roles. Map values of the provider's groups claim to Swarmer
groups, then give the groups a role, either on everything or on single stacks:

- viewer: stacks, services, logs, history
- operator: also deploy, approve, roll back, restart, and open terminals
- editor: also edit files and secrets
- admin: everything, including repositories and access

Logins, changes, secret reveals, terminal sessions, and refused attempts end up in the audit log.

Set `SWARMER_SEPARATE_APPROVER=true` to stop people from approving their own changes on stacks
in `approve` mode.

## History

Finished deployments and unused revisions are deleted after 90 days, keeping at least the newest 20
per stack. Audit entries are kept for a year. Change this with `SWARMER_DEPLOYMENT_RETENTION`,
`SWARMER_KEEP_DEPLOYMENTS`, and `SWARMER_AUDIT_RETENTION` (for example `30d`, or `0d` to keep
everything).

## Development

Requirements: [uv](https://docs.astral.sh/uv/), Docker, and Node.js 22+ for the frontend.

```sh
docker compose -f compose.dev.yaml up -d   # Postgres on localhost:5432
cp .env.example .env
uv sync
uv run swarmer migrate
uv run swarmer create-admin
uv run swarmer server --reload
```

The API is then available on http://localhost:8000 (`/api/docs` for the OpenAPI UI). Swarmer
talks to the Docker daemon from `SWARMER_DOCKER_HOST`, `DOCKER_HOST`, or the local socket, and
needs a Swarm manager for the Swarm views.

Copy `.env.example` to `.env` first. It sets a local data directory (git mirrors live there) and
has a slot for `SWARMER_SECRET_KEY`, which encrypts repository credentials; generate one with
`uv run swarmer generate-key`.

Instead of `create-admin`, set `SWARMER_INITIAL_ADMIN_PASSWORD` (at least 12 characters) to have
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

Tests marked `postgres` run when `SWARMER_TEST_DATABASE_URL` is set, for example
`postgresql+asyncpg://swarmer:swarmer@localhost:5432/swarmer_test`.

The integration tests in `tests/integration` deploy to a real three-node Swarm running in
Docker-in-Docker containers (Linux only):

```sh
docker build -t swarmer:dev .
SWARMER_SWARM_AGENT_IMAGE=swarmer:dev tests/integration/swarm.sh up > .swarm.env
set -a; . ./.swarm.env; set +a
uv run pytest tests/integration
tests/integration/swarm.sh down
```

Without `SWARMER_SWARM_AGENT_IMAGE` the Swarm runs without agents and the tests that need them
are skipped.

Frontend tests: `npm test` in `frontend` runs the unit and component tests. With the test Swarm
and Postgres from above, `frontend/e2e/run.sh` builds the frontend, starts a server, and runs
the Playwright suite against it.

### Releasing

Set the version in `pyproject.toml` (for example `0.2.0`, or `0.2.0rc1` for a pre-release), run
`uv lock`, commit, and push a matching tag (`v0.2.0`, or `v0.2.0-rc.1`). The release workflow runs
CI, publishes `ghcr.io/dennielabs/swarmer` for amd64 and arm64, and creates the GitHub release.
Pre-releases don't move `latest`.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
