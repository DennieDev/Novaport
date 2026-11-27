---
title: swarmer.yaml
description: Per-stack settings, kept in git next to the compose file.
---

A `swarmer.yaml` in a stack folder holds settings for that stack. Since it is in git, a change to
it is reviewed like any other change. All keys are optional:

```yaml title="stacks/app/swarmer.yaml"
type: swarm      # swarm or compose
endpoint: edge-01
deploy: approve  # auto, approve, or manual
edits: pr        # commit or pr
```

| Key | Values | Default | Meaning |
| --- | --- | --- | --- |
| `type` | `swarm`, `compose` | `swarm` | Deploy with `docker stack deploy` or `docker compose up`. |
| `endpoint` | an endpoint name | the Swarm Swarmer runs on | Where to deploy. See [Endpoints](/operations/endpoints/). |
| `deploy` | `auto`, `approve`, `manual` | the repository's default | What happens when a new revision arrives: deploy it, wait for approval, or nothing. |
| `edits` | `commit`, `pr` | the repository's default | How edits made in Swarmer reach git: straight to the branch, or as a pull request. |

Unknown keys and invalid values are refused. The stack page shows the error, and until the file
is fixed the stack keeps its last valid settings and gets no new revisions.

Moving a stack to another endpoint doesn't remove it from the old one.

Whether authors may approve their own changes is a server setting
(`SWARMER_SEPARATE_APPROVER`), not a `swarmer.yaml` key, so a change can't switch it off for
itself. See [Deploys](/stacks/deploys/#approvals).
