---
title: Your first stack
description: Connect a git repository and deploy a stack from it.
---

This walks through connecting a repository and deploying one stack. It assumes Swarmer is
[installed](/install/) and you are logged in as an admin.

## Put a stack in git

Create a repository (an empty one works too) with a folder per stack under `stacks/`:

```
stacks/
  whoami/
    compose.yaml
```

```yaml title="stacks/whoami/compose.yaml"
services:
  whoami:
    image: traefik/whoami:v1.10
    deploy:
      replicas: 2
```

If the repository is empty, Swarmer pushes a first commit with the stacks folder and, when
`SWARMER_AGE_KEY` is set, a `.sops.yaml` that lists its public key.

## Add the repository

Under Repositories, choose Add repository and fill in:

- **URL** and **Branch**. SSH (`git@github.com:acme/infra.git`) and HTTPS URLs both work.
- **Stacks folder**, if it isn't `stacks`.
- **Authentication**: none for a public repository, an SSH deploy key that Swarmer generates, or
  an HTTPS token. With a deploy key, the repository's page shows the public key. Add it on the
  forge with write access, which edits and image updates need.
- **Default deploy mode**: manual, approve each revision, or deploy automatically. Stacks can
  override this in their [`swarmer.yaml`](/stacks/settings/).
- **Edits from Swarmer**: commit to the branch, or open a pull request. Pull requests also need
  a **Forge** and a forge token.

Swarmer polls the repository every 5 minutes by default. Add the [webhook](/operations/webhooks/)
shown on the repository's page to have it sync right after every push.

## Deploy

After the first sync the stack shows up under Stacks with a revision. With the manual deploy
mode, open the stack and deploy that revision. The deployment page shows each stage as it runs:
resolving the revision, validating the compose file, pre-flight checks, the diff against what's
running, applying it, and waiting for the tasks to converge.

If a check fails, nothing on the cluster has changed. Fix the cause (in git, or on the node for a
missing bind mount, for example) and deploy again.

## Next steps

- [Variables and secrets](/stacks/variables-and-secrets/) for passwords and keys.
- [Deploys](/stacks/deploys/) for deploy modes, approvals, rollback, and drift.
- [Access](/operations/access/) to add an OpenID Connect provider and give people roles.
