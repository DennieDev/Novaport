---
title: Keys and backups
description: What to back up, and the two keys you can't lose.
---

## Keys

Three things matter, and only the first is in the database:

- **The Postgres database** holds users, settings, history, and the encrypted credentials.
- **`SWARMER_SECRET_KEY`** encrypts git, registry, forge, notification, and OIDC credentials.
  Without it, those credentials can't be read and have to be entered again.
- **`SWARMER_AGE_KEY`** decrypts `secrets.env` files. Without it, stacks with secrets can't be
  deployed, and the secrets in git can only be recovered with another recipient's key. Keep a
  copy outside the cluster, or list a second age key (kept offline) in `.sops.yaml`.

Git checkouts don't need a backup. Swarmer recreates them from the remotes.

The agent token can be replaced at any time. Swarm secrets can't be changed in place, so to
replace one, create it under a new name, point both services at it in the stack file, and
redeploy.

## Backing up

Back up the database and keep copies of the two keys. With the example stack file, Postgres runs
on a manager. On that node:

```sh
docker exec "$(docker ps -q -f name=swarmer_postgres)" \
  pg_dump -U swarmer -Fc swarmer > swarmer-$(date +%F).dump
```

## Restoring

Stop the server, restore, and start it again:

```sh
docker service scale swarmer_server=0
docker exec -i "$(docker ps -q -f name=swarmer_postgres)" \
  pg_restore -U swarmer -d swarmer --clean --if-exists < swarmer-2026-11-24.dump
docker service scale swarmer_server=1
```

A restored database needs the `SWARMER_SECRET_KEY` it was created with.
