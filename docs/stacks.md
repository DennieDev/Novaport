# Writing stacks

Swarmer deploys what is in git. This page describes what it reads from a repository.

## Repository layout

One repository holds any number of stacks, one folder each, under a stacks folder (`stacks` by
default, set per repository):

```
.sops.yaml                 # age public keys for encrypting secrets.env files
stacks/
  traefik/
    compose.yaml
    .env                   # optional, plain variables
    secrets.env            # optional, SOPS-encrypted variables
    swarmer.yaml          # optional, per-stack settings
  monitoring/
    compose.yaml
```

The folder name is the stack name. It must be lowercase letters, digits, `-` or `_`, start with
a letter or digit, and be at most 63 characters. Stack names are unique across all repositories.
Folders that break either rule are skipped, with a warning on the repository's page.

The compose file is the first of `compose.yaml`, `compose.yml`, `docker-compose.yaml`, and
`docker-compose.yml` that exists. Other files in the folder (configs, scripts) are available to
the compose file through relative paths, as with `docker stack deploy`.

A new, empty repository works too. On the first sync Swarmer pushes a commit with the stacks
folder and, when `SWARMER_AGE_KEY` is set, a `.sops.yaml` that lists Swarmer's public key. That
needs write access, the same as editing stacks. A repository that has commits but not the
configured branch is left alone and shows an error.

Removing a folder marks the stack as removed in Swarmer. Its services keep running until you
remove them.

## `swarmer.yaml`

Settings for one stack, reviewed like any other change. All keys are optional:

```yaml
type: swarm      # swarm or compose
endpoint: edge-01
deploy: approve  # auto, approve, or manual
edits: pr        # commit or pr
```

| Key | Values | Default | Meaning |
| --- | --- | --- | --- |
| `type` | `swarm`, `compose` | `swarm` | Deploy with `docker stack deploy` or `docker compose up`. |
| `endpoint` | an endpoint name | the Swarm Swarmer runs on | Where to deploy. Endpoints are added under Endpoints. |
| `deploy` | `auto`, `approve`, `manual` | the repository's default | What happens when a new revision arrives: deploy it, wait for approval, or nothing. |
| `edits` | `commit`, `pr` | the repository's default | How edits made in Swarmer reach git: straight to the branch, or as a pull request. |

Unknown keys and invalid values are refused. The stack page shows the error, and until the file
is fixed the stack keeps its last valid settings and gets no new revisions.

## Variables

For Compose stacks, `.env` works as usual. `docker stack deploy` ignores `.env`, so for Swarm
stacks Swarmer reads it and passes the variables to the deploy itself. Either way,
`${VARIABLE}` in the compose file is filled in from `.env` and then from `secrets.env`.

## Secrets

`secrets.env` holds variables encrypted with [SOPS](https://getsops.io) and age. Swarmer
decrypts it with its own age key (`SWARMER_AGE_KEY`). Contributors only need the public key,
listed in `.sops.yaml` at the root of the repository:

```yaml
creation_rules:
  - path_regex: secrets\.env$
    age: >-
      age1swarmer...,
      age1yourownkey...
```

Encrypt with `sops --encrypt --in-place stacks/app/secrets.env`, or use the Secrets page of a
stack, which encrypts for the same recipients.

Values from `secrets.env` can fill `${VARIABLE}`, but for anything sensitive use Swarm secrets.
Name the variable in the top-level `secrets` section:

```yaml
services:
  api:
    image: ghcr.io/acme/api:2.4.1
    secrets: [db_password]

secrets:
  db_password:
    environment: DB_PASSWORD
```

Swarmer creates a Swarm secret called `<stack>_db_password_<hash>`, where the hash comes from
the value. Changing the value creates a new secret and rolls the service over to it; secrets
that earlier deploys of the stack created and nothing uses any more are removed.

## Image updates

An `x-updates` block on a service asks Swarmer to look for newer images:

```yaml
services:
  api:
    image: ghcr.io/acme/api:2.4.1
    x-updates:
      mode: version
      allow: minor
      action: pr
```

| Key | Values | Default | Meaning |
| --- | --- | --- | --- |
| `mode` | `version`, `digest`, `none` | required | `version` looks for newer tags; `digest` follows a moving tag such as `latest` and reports when it points somewhere new. |
| `allow` | `patch`, `minor`, `major` | `minor` | For `version`: the largest step to take. `minor` allows `2.4.1` to `2.5.0` but not `3.0.0`. |
| `match` | a regular expression | inferred from the tag | Which tags count. `2.4.1-alpine` gives `^\d+\.\d+\.\d+-alpine$`, so `-alpine` stays `-alpine`. |
| `action` | `commit`, `pr`, `notify`, `none` | `notify` | Commit the new tag, open a pull request, only send a notification, or only show it in Swarmer. |

Checks run every six hours (`SWARMER_UPDATE_INTERVAL`) and within a minute of any change to the
stack in git. Commits and pull requests go through the stack's deploy mode like any other change.
Pull requests need a forge set on the repository (GitHub, GitLab, or Gitea/Forgejo).

## Stacks deployed without Swarmer

Stacks → "Add them to git" lists Swarm stacks and Compose projects on your hosts that no
repository describes. Swarmer writes a compose file from what is running, which you review and
commit to a repository. It starts in `deploy: manual`, so nothing changes until you deploy it,
and a faithful file updates the stack in place without restarting its tasks.

Check the generated file before committing: environment variables are copied as they are (move
passwords to `secrets.env`), secrets and configs become external references, and for Compose
projects the image's own environment variables and labels show up too.

## What Swarmer adds

Before deploying, Swarmer labels every service with `swarmer.stack`, `swarmer.revision`, and
`swarmer.commit`. It also checks that:

- the compose file is valid (`docker stack config` or `docker compose config`),
- every image exists in its registry,
- external networks, secrets, and configs exist,
- bind mount sources exist on every node a service can run on (needs the agents),
- published ports aren't taken by another stack's Swarm services or, for Compose stacks, by
  another Compose project on the host.

A failed check stops the deploy before anything changes. Git is never changed or reverted by a
failed deploy; to go back, deploy an older revision from the stack page.
