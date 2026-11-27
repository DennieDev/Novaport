---
title: Upgrading and health
description: Upgrade Swarmer, and the health endpoints to monitor it with.
---

## Upgrading

Read the [release notes](https://github.com/DennieDev/swarmer/releases), back up the database,
and deploy the new version:

```sh
SWARMER_IMAGE=ghcr.io/denniedev/swarmer:0.2.0 docker stack deploy -c docker-compose.yml swarmer
```

This updates the server and every agent together; keep them on the same version. The server
applies database migrations when it starts, so expect a few seconds of downtime. Edge agents on
other hosts run their own container: start it again with the new image tag.

Migrations only go forward. To go back to an older version, restore the backup taken before the
upgrade.

To run migrations yourself instead, set `SWARMER_MIGRATE_ON_START=false` and run
`swarmer migrate` before starting the new server.

## Image tags

Releases are published as `ghcr.io/denniedev/swarmer:<version>` for amd64 and arm64, with
build provenance and an SBOM attached. Pre-releases don't move `latest`.

## Health

- `GET /api/healthz` answers as long as the server process runs.
- `GET /api/readyz` also checks the database and returns 503 when it can't reach it.

The Nodes and Endpoints pages show agents and edge agents that stopped answering, and
[notifications](/operations/notifications/) can announce node, agent, and endpoint outages.
