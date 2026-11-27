---
title: Repository layout
description: How Swarmer finds stacks in a git repository.
---

Swarmer deploys what is in git. One repository holds any number of stacks, one folder each,
under a stacks folder (`stacks` by default, set per repository):

```
.sops.yaml                 # age public keys for encrypting secrets.env files
stacks/
  traefik/
    compose.yaml
    .env                   # optional, plain variables
    secrets.env            # optional, SOPS-encrypted variables
    swarmer.yaml           # optional, per-stack settings
  monitoring/
    compose.yaml
```

## Stack names

The folder name is the stack name. It must be lowercase letters, digits, `-` or `_`, start with
a letter or digit, and be at most 63 characters. Stack names are unique across all repositories.
Folders that break either rule are skipped, with a warning on the repository's page.

## The compose file

The compose file is the first of `compose.yaml`, `compose.yml`, `docker-compose.yaml`, and
`docker-compose.yml` that exists. Other files in the folder (configs, scripts) are available to
the compose file through relative paths, as with `docker stack deploy`.

## New and empty repositories

A new, empty repository works. On the first sync Swarmer pushes a commit with the stacks folder
and, when `SWARMER_AGE_KEY` is set, a `.sops.yaml` that lists Swarmer's public key. That needs
write access, the same as editing stacks. A repository that has commits but not the configured
branch is left alone and shows an error.

## Removing a stack

Removing a folder marks the stack as removed in Swarmer. Its services keep running until you
remove them.

## Syncing

On every new commit Swarmer fetches, finds the stack folders that changed, and creates a revision
for each. It polls each repository (every 5 minutes by default, set per repository) and syncs
right away when a [webhook](/operations/webhooks/) arrives.

Repositories authenticate with an SSH deploy key that Swarmer generates, or an HTTPS token. Pull
requests, from edits or image updates, also need a forge token for GitHub, GitLab, or
Gitea/Forgejo.
