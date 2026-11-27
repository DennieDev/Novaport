---
title: Image updates
description: Have Swarmer look for newer images with an x-updates block.
---

An `x-updates` block on a service asks Swarmer to look for newer images:

```yaml title="stacks/app/compose.yaml"
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
| `mode` | `version`, `digest`, `none` | required | `version` looks for newer tags. `digest` follows a moving tag such as `latest` and reports when it points somewhere new. |
| `allow` | `patch`, `minor`, `major` | `minor` | For `version`: the largest step to take. `minor` allows `2.4.1` to `2.5.0` but not `3.0.0`. |
| `match` | a regular expression | inferred from the tag | Which tags count. `2.4.1-alpine` gives `^\d+\.\d+\.\d+-alpine$`, so `-alpine` stays `-alpine`. |
| `action` | `commit`, `pr`, `notify`, `none` | `notify` | Commit the new tag, open a pull request, only send a notification, or only show it in Swarmer. |

## When checks run

Every six hours (`SWARMER_UPDATE_INTERVAL`), and within a minute of any change to the stack in
git.

## What happens next

Updates go through git like every other change. A commit or merged pull request creates a
revision, and the stack's [deploy mode](/stacks/deploys/#deploy-modes) decides whether it deploys.
Pull requests need a forge set on the repository (GitHub, GitLab, or Gitea/Forgejo).

## Private registries

Add credentials under Registries. Swarmer uses them to look for updates, to check that images
exist before a deploy, and passes them to Swarm with `--with-registry-auth`.
