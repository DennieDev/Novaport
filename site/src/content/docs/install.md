---
title: Installing
description: Run Swarmer on Docker Swarm behind a reverse proxy.
---

Swarmer runs as a stack on a Docker Swarm: the server on a manager, an agent on every node, and
Postgres. The example stack file puts it behind Traefik.

## Requirements

- A Docker Swarm. A single-node Swarm (`docker swarm init`) is fine.
- A reverse proxy that terminates TLS and passes WebSocket upgrades. Logs, terminals, and live
  updates use WebSockets.
- A directory on a manager for the database and git checkouts.

## Prepare the files

Download [`docker-compose.yml`](https://github.com/DennieDev/swarmer/blob/main/deploy/docker-compose.yml)
and [`.env.example`](https://github.com/DennieDev/swarmer/blob/main/deploy/.env.example) into one
directory, and copy `.env.example` to `.env`. Keep `.env` readable by root only (`chmod 600 .env`).

Fill in the values. The comments in the file say how to generate each one:

```sh
# SWARMER_AGENT_TOKEN
openssl rand -hex 32
# SWARMER_SECRET_KEY
docker run --rm ghcr.io/denniedev/swarmer generate-key
# SWARMER_AGE_KEY
docker run --rm ghcr.io/denniedev/swarmer generate-age-key
# POSTGRES_PASSWORD, also the password in SWARMER_DATABASE_URL
openssl rand -hex 24
```

`generate-age-key` prints the private key (the `AGE-SECRET-KEY-...` line) and its public key. The
private key goes in `.env`; the public key goes in your repositories' `.sops.yaml` (see
[Variables and secrets](/stacks/variables-and-secrets/)). Keep copies of both keys outside the
cluster: see [Keys and backups](/operations/keys-and-backups/).

`SWARMER_INITIAL_ADMIN_PASSWORD` (12 characters or more) creates the `admin` user on first start.
It is only read while there are no users, so you can remove it afterwards.

Values in `.env` are taken literally, without quotes. Anyone who can run `docker service inspect`
can read them; to keep them out of the service spec, use Swarm secrets instead (see
[Configuration](/reference/configuration/)).

## Deploy

Create the data directories and deploy from the directory with the files:

```sh
mkdir -p /data/swarmer/data /data/swarmer/postgres
export SWARMER_IMAGE=ghcr.io/denniedev/swarmer:0.1.0
docker stack deploy -c docker-compose.yml swarmer
```

Pin a version rather than `latest`, so a redeploy never upgrades by accident.

Then log in as `admin` with the password from `SWARMER_INITIAL_ADMIN_PASSWORD`, and continue with
[Your first stack](/first-stack/).

## Reverse proxy

### Traefik

The stack file has labels for Traefik's swarm provider (Traefik v3). Change the host in the
router rule to your own. Traefik reaches Swarmer over the `traefik` network, which has to be an
attachable overlay:

```sh
docker network create -d overlay --attachable traefik
```

Compose containers can join that network too.

### Other proxies

Drop the Traefik labels and the `traefik` network from the stack file. Swarmer listens on port
8000 without TLS; point the proxy at that, with WebSocket upgrades enabled.

### Settings for any proxy

- `SWARMER_PUBLIC_URL`: the external address, such as `https://swarmer.example.com`. Used for
  OIDC redirect URIs and edge agent keys.
- `SWARMER_TRUSTED_PROXIES`: the proxy's address or network, so client IPs in the audit log come
  from `X-Forwarded-For`. Swarm overlay networks use `10.0.0.0/8` unless the Swarm was set up with
  another address pool.

## What runs where

| Service | Where | Mounts |
| --- | --- | --- |
| `server` | one replica on a manager | Docker socket, `/data/swarmer/data` |
| `agent` | global, one per node | Docker socket, `/` read-only at `/host` |
| `postgres` | one replica on a manager | `/data/swarmer/postgres` |

The agent answers only to the server, over an encrypted overlay network, with the shared
`SWARMER_AGENT_TOKEN`. With more than one manager, pin Postgres to one node with a node label
instead of `node.role == manager`.
