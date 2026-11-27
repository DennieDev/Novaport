---
title: Managing Swarmer with Swarmer
description: Keep Swarmer's own stack in git and deploy it from Swarmer.
---

Once a repository is added, Swarmer can manage its own stack like any other. Under Stacks,
Deployed without Swarmer, add `swarmer` to the repository:

- Values that look like passwords, keys, or tokens (the secret key, the age key, the database
  password and URL) go to the stack's `secrets.env`, encrypted with the age keys in the
  repository's `.sops.yaml`. The compose file refers to them as `${NAME}`, so the running
  services keep exactly the same environment and the first deploy changes nothing.
- The server is set to `update_config.order: start-first`: Swarm starts the new server and only
  stops the old one when the new one is healthy. A deployment that was converging when the old
  server stopped is finished by the new one.
- The stack starts with `deploy: manual`, which is also the safer choice to keep. Every deploy
  of this stack restarts Swarmer.

Keep the `docker-compose.yml` and `.env` you installed with. If a change ever keeps Swarmer from
starting, revert it in git and deploy those files by hand, as when installing.

Change the database service with care: a deploy that restarts Postgres interrupts the server
until it is back.
