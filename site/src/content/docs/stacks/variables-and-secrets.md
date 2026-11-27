---
title: Variables and secrets
description: Plain variables in .env, encrypted ones in secrets.env, and Swarm secrets.
---

## Variables

For Compose stacks, `.env` works as usual. `docker stack deploy` ignores `.env`, so for Swarm
stacks Swarmer reads it and passes the variables to the deploy itself. Either way,
`${VARIABLE}` in the compose file is filled in from `.env` and then from `secrets.env`.

## Encrypted variables

`secrets.env` holds variables encrypted with [SOPS](https://getsops.io) and age. Swarmer
decrypts it with its own age key (`SWARMER_AGE_KEY`). Contributors only need the public key,
listed in `.sops.yaml` at the root of the repository, next to their own:

```yaml title=".sops.yaml"
creation_rules:
  - path_regex: secrets\.env$
    age: >-
      age1swarmer...,
      age1yourownkey...
```

`swarmer generate-age-key` prints both halves of a new key; the public key is the `age1...`
value in its first line.

Encrypt with:

```sh
sops --encrypt --in-place stacks/app/secrets.env
```

or use the Secrets page of a stack, which encrypts for the same recipients. Since `.sops.yaml`
only holds public keys, anyone can add or change a secret through a pull request without being
able to read the others.

## Swarm secrets

Values from `secrets.env` can fill `${VARIABLE}`, but for anything sensitive use Swarm secrets.
Name the variable in the top-level `secrets` section:

```yaml title="stacks/app/compose.yaml"
services:
  api:
    image: ghcr.io/acme/api:2.4.1
    secrets: [db_password]

secrets:
  db_password:
    environment: DB_PASSWORD
```

Swarmer creates a Swarm secret called `<stack>_db_password_<hash>`, where the hash comes from the
value. Changing the value creates a new secret and rolls the service over to it. Secrets that
earlier deploys of the stack created and nothing uses any more are removed.

The service sees the secret at `/run/secrets/db_password` as usual.
