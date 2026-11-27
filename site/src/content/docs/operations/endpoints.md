---
title: Endpoints
description: Deploy to other Docker hosts and Swarm clusters through an edge agent.
---

Swarmer deploys to the Swarm it runs on, called `local`, and to any other Docker host or Swarm
cluster with an edge agent that connects back to it. The agent opens the connection, so the host
needs no open ports and can sit behind NAT.

## Adding an endpoint

Under Endpoints, add one. Swarmer shows a key and the command that starts the agent:

```sh
docker run -d --name swarmer-agent --restart always \
  -v /var/run/docker.sock:/var/run/docker.sock -v /:/host:ro \
  -e SWARMER_EDGE_KEY=... ghcr.io/denniedev/swarmer:latest agent
```

On a Swarm cluster, run it as a service on a manager instead.

The key holds the server URL, the endpoint's ID, and a secret. Swarmer only stores a hash of the
secret. Generating a new key disconnects the agent that uses the old one.

Use an `https://` server URL; the agent verifies the certificate. Set `SWARMER_PUBLIC_URL` when
Swarmer is behind a proxy, so the key carries the external address. The agent reconnects with
backoff when the connection drops.

:::danger
The edge agent has full access to the host's Docker socket, which is root on that host, and the
server can use all of it. Treat the key like a root password for the host.
:::

## Using it

Point a stack at the endpoint in its `swarmer.yaml`:

```yaml title="stacks/app/swarmer.yaml"
endpoint: edge-01
type: compose
```

Deploys, validation, drift checks, and restarts run against the endpoint exactly as they do
locally. An unknown endpoint name shows as a configuration error on the stack. Moving a stack to
another endpoint doesn't remove it from the old one.

Endpoints that stacks still use can't be deleted. Deploys to a disconnected endpoint fail at the
resolve stage, and Swarmer sends an `endpoint.disconnected` notification.

## Limits

- The Nodes, Services, logs, and terminal pages only show `local` so far.
- Remote Swarm clusters have one agent, on the manager it runs on. Bind mount checks there only
  cover that node.
- Keep edge agents on the same version as the server. On upgrade, start them again with the new
  image tag.
