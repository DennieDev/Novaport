# Running Novaport

## Installing

[deploy/docker-compose.yml](../deploy/docker-compose.yml) runs the server on a manager, an
agent on every node, and Postgres, behind Traefik. Copy
[deploy/.env.example](../deploy/.env.example) to `.env` next to it, fill it in, create the data
directories, and deploy from that directory:

```sh
mkdir -p /data/novaport/data /data/novaport/postgres
NOVAPORT_IMAGE=ghcr.io/denniedev/novaport:0.1.0 docker stack deploy -c docker-compose.yml novaport
```

Pin a version rather than `latest`, so a redeploy never upgrades by accident.

Log in as `admin` with the password from `NOVAPORT_INITIAL_ADMIN_PASSWORD`. It is only read
while there are no users, so you can remove it afterwards.

Traefik reads the labels on the server service through its swarm provider (Traefik v3) and
reaches it over the `traefik` network, which has to be an attachable overlay:

```sh
docker network create -d overlay --attachable traefik
```

Compose containers can join that network too. With another proxy, drop the labels and the
`traefik` network. Novaport listens on port 8000 without TLS; the proxy has to terminate TLS and
pass WebSocket upgrades (logs, terminals, and live updates use them). Set:

- `NOVAPORT_PUBLIC_URL` to the external address, for OIDC redirect URIs and edge agent keys.
- `NOVAPORT_TRUSTED_PROXIES` to the proxy's address or network, so client IPs in the audit log
  come from `X-Forwarded-For`. Swarm overlay networks use `10.0.0.0/8` unless the Swarm was set
  up with another address pool.

Anyone who can run `docker service inspect` can read the values in `.env`. To keep them out of
the service spec, use Swarm secrets instead (see below).

## Configuration

Everything is set through `NOVAPORT_*` environment variables. Any of them can also be a Swarm
secret named after the variable in lower case: `novaport_database_url` for
`NOVAPORT_DATABASE_URL`. Durations take values such as `30s`, `5m`, `6h`, or `90d`.

| Variable | Default | |
| --- | --- | --- |
| `DATABASE_URL` | local Postgres | `postgresql+asyncpg://user:password@host:5432/db` |
| `SECRET_KEY` | none | Encrypts stored credentials. See [Keys](#keys). |
| `AGE_KEY` | none | Decrypts `secrets.env` files. See [Keys](#keys). |
| `AGENT_TOKEN` | none | Shared by the server and its agents. |
| `AGENT_DNS` | none | How the server finds its agents; `tasks.agent` in the stack file. |
| `INITIAL_ADMIN_PASSWORD` | none | Creates `admin` on first start (12 characters or more). |
| `PUBLIC_URL` | from the request | External address. |
| `TRUSTED_PROXIES` | `127.0.0.1` | IPs or CIDRs whose forwarded headers are trusted. |
| `SESSION_IDLE_TIMEOUT` | `8h` | Logs out after this long without requests. |
| `SESSION_MAX_AGE` | `7d` | Logs out after this long regardless. |
| `SEPARATE_APPROVER` | `false` | Authors can't approve their own changes. |
| `COMMIT_NAME`, `COMMIT_EMAIL` | `Novaport`, `novaport@localhost` | Committer of Novaport's commits; the author is the logged-in user. |
| `UPDATE_INTERVAL` | `6h` | How often `x-updates` policies are checked. |
| `DRIFT_INTERVAL` | `5m` | How often running services are compared with the last deploy. |
| `DEPLOY_TIMEOUT` | `5m` | How long a rollout may take to converge. |
| `COMMAND_TIMEOUT` | `2m` | Limit for each docker and git command. |
| `DEPLOYMENT_RETENTION`, `KEEP_DEPLOYMENTS` | `90d`, `20` | Deployment history to keep per stack. |
| `AUDIT_RETENTION` | `365d` | Audit log history. `0d` keeps everything. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_SECURITY`, `SMTP_FROM` | port `587`, `starttls` | Outgoing mail for email notifications. |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning`, or `error`. |
| `DATA_DIR` | `/var/lib/novaport` | Git checkouts. Novaport recreates them from the remotes if lost. |
| `MIGRATE_ON_START` | `true` | Apply database migrations when the server starts. |

## Webhooks

Novaport polls each repository (every 5 minutes by default), and a webhook makes it sync right
after a push. The repository's page shows the webhook URL and secret. Add a push webhook in the
forge:

- **GitHub:** content type `application/json`, the secret in "Secret".
- **GitLab:** the secret in "Secret token".
- **Gitea and Forgejo:** content type `application/json`, the secret in "Secret".

Requests with a wrong signature get a 401 and are ignored.

## API tokens

Scripts use the same API as the UI, authenticated with a token instead of a login. Create one
under Account, API tokens, and send it as a bearer token:

```sh
curl -H "Authorization: Bearer np_..." https://novaport.example.com/api/stacks
```

A token acts as the user who created it, with no role higher than the one picked for it. Every
action is in the audit log under that user, with the token's name. Tokens can't open terminals,
create other tokens, or change a password; those need a login. A token stops working when it
expires, is revoked, or its user is disabled. Admins see and revoke everyone's tokens under
Access, API tokens.

The API is described at `/api/docs`.

## Keys

Three keys matter, and only the first is in the database:

- **The Postgres database** holds users, settings, history, and the encrypted credentials.
- **`NOVAPORT_SECRET_KEY`** encrypts git, registry, forge, notification, and OIDC credentials.
  Without it, those credentials can't be read and have to be entered again.
- **`NOVAPORT_AGE_KEY`** decrypts `secrets.env` files. Without it, stacks with secrets can't be
  deployed, and the secrets in git can only be recovered with another recipient's key. Keep a
  copy outside the cluster, or list a second age key (kept offline) in `.sops.yaml`.

The agent token can be replaced at any time. Swarm secrets can't be changed in place, so create
one under a new name, point both services at it in the stack file, and redeploy.

## Backups

Back up the database and keep copies of the two keys. Git checkouts don't need a backup.

With the stack file, Postgres runs on a manager. On that node:

```sh
docker exec "$(docker ps -q -f name=novaport_postgres)" \
  pg_dump -U novaport -Fc novaport > novaport-$(date +%F).dump
```

To restore, stop the server, restore, and start it again:

```sh
docker service scale novaport_server=0
docker exec -i "$(docker ps -q -f name=novaport_postgres)" \
  pg_restore -U novaport -d novaport --clean --if-exists < novaport-2026-11-24.dump
docker service scale novaport_server=1
```

A restored database needs the `NOVAPORT_SECRET_KEY` it was created with.

## Upgrading

Read the release notes, back up the database, and deploy the new version:

```sh
NOVAPORT_IMAGE=ghcr.io/denniedev/novaport:0.2.0 docker stack deploy -c docker-compose.yml novaport
```

This updates the server and every agent together; keep them on the same version. The server
applies database migrations when it starts, so expect a few seconds of downtime. Edge agents on
other hosts run their own container: start it again with the new image tag.

Migrations only go forward. To go back to an older version, restore the backup taken before the
upgrade.

## Health

- `GET /api/healthz` answers as long as the server process runs.
- `GET /api/readyz` also checks the database and returns 503 when it can't reach it.

The Nodes and Endpoints pages show agents and edge agents that stopped answering, and the
notification channels can announce node, agent, and endpoint outages.
