---
title: Configuration
description: Every SWARMER_* environment variable.
---

Swarmer is configured with `SWARMER_*` environment variables. The tables leave out the prefix:
`DATABASE_URL` means `SWARMER_DATABASE_URL`.

Any variable can also come from a Swarm secret named after it in lower case:
`swarmer_database_url` for `SWARMER_DATABASE_URL`. Use that for the database URL and the keys,
since environment variables show up in `docker service inspect`.

Durations take values such as `30s`, `5m`, `6h`, or `90d`.

## Server

| Variable | Default | |
| --- | --- | --- |
| `DATABASE_URL` | local Postgres | `postgresql+asyncpg://user:password@host:5432/db` |
| `MIGRATE_ON_START` | `true` | Apply database migrations when the server starts. |
| `HOST`, `PORT` | `0.0.0.0`, `8000` | Address the server listens on. |
| `PUBLIC_URL` | from the request | External address, for OIDC redirect URIs, edge agent keys, and links in notifications. |
| `TRUSTED_PROXIES` | `127.0.0.1` | Comma-separated IPs or CIDRs whose forwarded headers are trusted. |
| `LOG_LEVEL` | `info` | `debug`, `info`, `warning`, or `error`. |
| `DATA_DIR` | `/var/lib/swarmer` | Git checkouts and SSH known hosts. Swarmer recreates the checkouts if lost. |
| `DOCKER_HOST` | `DOCKER_HOST`, then the local socket | Docker daemon to use for the local endpoint. |

## Keys and login

| Variable | Default | |
| --- | --- | --- |
| `SECRET_KEY` | none | Encrypts stored credentials. Generate with `swarmer generate-key`. Without it, repositories can only use public URLs. |
| `AGE_KEY` | none | Decrypts `secrets.env` files. Generate with `swarmer generate-age-key`. |
| `INITIAL_ADMIN_USERNAME` | `admin` | Name of the user created on first start. |
| `INITIAL_ADMIN_PASSWORD` | none | Creates that user on first start, while there are no users (12 characters or more). |
| `SESSION_IDLE_TIMEOUT` | `8h` | Logs out after this long without requests. |
| `SESSION_MAX_AGE` | `7d` | Logs out after this long regardless. |
| `SEPARATE_APPROVER` | `false` | Authors can't approve their own changes on `approve` stacks. |

## Git

| Variable | Default | |
| --- | --- | --- |
| `COMMIT_NAME`, `COMMIT_EMAIL` | `Swarmer`, `swarmer@localhost` | Committer of Swarmer's commits. The author is the logged-in user. |
| `ALLOW_LOCAL_REPOSITORIES` | `false` | Allow `file://` and plain-path repository URLs, which read the server's own filesystem. |

## Deploys and background jobs

| Variable | Default | |
| --- | --- | --- |
| `DEPLOY_TIMEOUT` | `5m` | How long a rollout may take to converge. |
| `DEPLOY_STABLE_FOR` | `15s` | How long Swarm services must run without a failing task before a deploy succeeds. |
| `COMMAND_TIMEOUT` | `2m` | Limit for each docker and git command. |
| `UPDATE_INTERVAL` | `6h` | How often `x-updates` policies are checked. |
| `DRIFT_INTERVAL` | `5m` | How often running services are compared with the last deploy. |
| `BACKGROUND_JOBS` | `true` | Set to `false` to run the API without the git poller and deploy worker. |

## History

| Variable | Default | |
| --- | --- | --- |
| `DEPLOYMENT_RETENTION` | `90d` | Finished deployments, their logs, and unused revisions older than this are deleted. `0d` keeps everything. |
| `KEEP_DEPLOYMENTS` | `20` | Deployments each stack keeps regardless of age. |
| `AUDIT_RETENTION` | `365d` | Audit log history. `0d` keeps everything. |

## Email

| Variable | Default | |
| --- | --- | --- |
| `SMTP_HOST` | none | SMTP server for email notifications. |
| `SMTP_PORT` | `587` | |
| `SMTP_SECURITY` | `starttls` | `starttls`, `tls` (implicit, usually port 465), or `none`. |
| `SMTP_USERNAME`, `SMTP_PASSWORD` | none | |
| `SMTP_FROM` | `swarmer@localhost` | Sender address. |

## Agents

| Variable | Default | |
| --- | --- | --- |
| `AGENT_TOKEN` | none | Shared by the server and its agents. Agents refuse to start without it. |
| `AGENT_PORT` | `9001` | Port agents listen on. |
| `AGENT_DNS` | none | Swarm DNS name that resolves to every agent task: `tasks.agent` in the example stack. |
| `AGENT_URLS` | none | A fixed comma-separated list of agent URLs, instead of `AGENT_DNS`. |
| `EDGE_KEY` | none | On an edge agent: the key from the endpoint's page. The agent then connects out to the server instead of listening. |
| `HOST_ROOT` | `/host` | Where the agent sees the host's root filesystem, mounted read-only. |
