---
title: Architecture
description: How the server, agents, and database fit together.
---

```
            browser
               |  HTTPS + WebSocket
               v
   +-----------------------+         +------------+
   |    swarmer server     |-------->|  Postgres  |
   |  (on a Swarm manager) |         +------------+
   +-----------------------+
     |  docker.sock    |   overlay network (token auth)
     v                 v
  Docker API     swarmer agent (global service, one per node)
  (cluster-          |
   level ops)        v
                docker.sock + host paths (node-local ops)
```

## Server

A single FastAPI process. It serves the API and the frontend, handles login and roles, syncs git
(webhooks, polling, commits, pushes), runs the deploy pipeline, and runs background jobs: git
polling, image update checks, drift detection, and cleanup. It also proxies logs and terminal
sessions between the browser and the agents.

The server uses the local Docker socket for everything cluster-wide: services, tasks, nodes,
networks, secrets, configs, service logs, and events. Deploys and validation shell out to the
docker CLI (`docker stack deploy`, `docker stack config`, `docker compose up`,
`docker compose config`).

Background jobs that must not run twice take a Postgres advisory lock, so more than one server
replica stays safe.

## Agent

The same image, started as `swarmer agent`, as a global service in Swarmer's own stack with the
Docker socket and `/` (read-only) mounted. Everything it does is scoped to its own node:

- container exec (terminals) and container logs,
- bind mount checks: whether a path exists, whether it's a file or a directory, who owns it,
- volume listing and browsing,
- disk usage, image list, and image pruning.

The server finds agents through Swarm DNS (`tasks.agent`), which returns one address per task.
Agents listen only on an encrypted overlay network and require the shared token. Path checks
resolve symlinks and reject paths outside the host root.

Edge agents on other hosts connect out to the server over a WebSocket instead. See
[Endpoints](/operations/endpoints/).

## Database

Postgres only. Besides normal storage it provides:

- Advisory locks: one deploy per stack at a time, and single-runner background jobs.
- `LISTEN/NOTIFY`: deploy progress, events, and new revisions reach every open browser without a
  separate message broker.

## Frontend

SvelteKit with TypeScript, built to static files and served by the server. CodeMirror 6 for the
compose editor and diffs, xterm.js for terminals.

## Security

- Access to the Docker socket is root on the host. Swarmer's access control and audit log are the
  main defense, so roles are checked on every endpoint, including WebSocket upgrades.
- CSRF protection on all state-changing requests. A strict Content Security Policy: scripts only
  from Swarmer itself, no framing.
- Webhook payloads are verified before any work happens.
- Git credentials, registry credentials, and the age key are never returned by the API.

## The image

One image, `ghcr.io/denniedev/swarmer`, with the application, the docker CLI, the compose plugin,
`git`, and `sops`.
