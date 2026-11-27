---
title: Introduction
description: What Swarmer is, why it exists, and the terms used in these docs.
---

Swarmer is a self-hosted web UI for running Docker Swarm stacks and Docker Compose projects from a
git repository. Git holds the desired state. Swarmer validates it, deploys it, shows what is
running, and writes changes made in the UI back to git.

It exists because other tools fall short in one of three ways:

- Deploys that silently revert the stack definition when something goes wrong.
- No Swarm support.
- Problems such as missing bind mounts that are hidden instead of shown.

## How it works

1. You keep stacks in a git repository, one folder per stack, each with a compose file.
2. Swarmer notices a new commit, through a webhook or by polling, and creates a revision for every
   stack folder that changed.
3. Depending on the stack's deploy mode, it deploys the revision right away, waits for someone to
   approve it, or does nothing until asked.
4. A deploy runs pre-flight checks, applies the stack with `docker stack deploy` or
   `docker compose up`, and watches the tasks until they are running and healthy.
5. If it fails, the deployment is marked failed with its errors. Git isn't touched, and nothing is
   rolled back on its own.

Edits made in Swarmer, such as changing a compose file or a secret, are commits too. If someone
else changed the same stack in the meantime, Swarmer refuses the save and shows a 3-way diff
instead of picking a winner.

## Concepts

| Term | Meaning |
| --- | --- |
| Endpoint | A Swarm cluster or a standalone Docker host. The one Swarmer runs on is `local`. |
| Repository | A git repository containing stacks, one folder per stack. |
| Stack | A folder in a repository with a compose file. Deployed as a Swarm stack or a Compose project. |
| Revision | A snapshot of a stack folder at a specific commit. Revisions don't change. |
| Deployment | One attempt to make an endpoint match a revision. |
| Agent | A Swarmer process on every node for things only that node can do: logs, terminals, mount checks. |

## What it doesn't do

- Kubernetes or any other orchestrator.
- Projects, applications, or other groupings above the stack.
- App templates or a catalog.
- Building images. Swarmer deploys images; your CI builds them.
