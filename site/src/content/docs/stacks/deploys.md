---
title: Deploys
description: Deploy modes, the stages of a deploy, failures, rollback, and drift.
---

## Deploy modes

Each stack has a deploy mode, from its [`swarmer.yaml`](/stacks/settings/) or the repository's
default. It decides what happens when a new revision arrives:

- `auto`: deploy it right away.
- `approve`: wait until someone with permission approves it.
- `manual`: record it and do nothing else. Someone deploys it from the stack page.

## Stages

Every deployment runs through the same stages. Each stage's result is stored and shown live on
the deployment page.

1. **Resolve.** Check out the revision and decrypt `secrets.env`.
2. **Validate.** Run `docker stack config` or `docker compose config` to merge and normalize the
   compose file. Syntax and schema errors stop here.
3. **Pre-flight.** Check the normalized file against the endpoint (see below). Errors stop the
   deploy; warnings are shown and don't block.
4. **Diff.** Compare the new configuration with what is running: services added, removed, or
   changed, and which fields changed.
5. **Apply.** Create the hash-named secrets and configs, then run
   `docker stack deploy --with-registry-auth --prune` or `docker compose up -d --remove-orphans`.
6. **Converge.** Watch the tasks or containers until they are running and healthy, or until
   `SWARMER_DEPLOY_TIMEOUT` (5 minutes) runs out. A Swarm deploy only succeeds once its services
   have run for `SWARMER_DEPLOY_STABLE_FOR` (15 seconds) without a task failing. Task errors are
   collected per service.

Only one deployment per stack runs at a time. A revision that arrives during a deploy waits for
it.

## Pre-flight checks

Before anything changes on the endpoint, Swarmer checks that:

- the compose file is valid,
- every image exists in its registry, using the credentials under Registries,
- external networks exist, and for Swarm stacks external secrets and configs,
- bind mount sources exist on every node a service can run on, given its placement constraints
  (this needs the agents),
- published ports aren't taken by another stack's Swarm services or, for Compose stacks, by
  another Compose project on the host.

## Labels

Swarmer labels every service it deploys, so running services can be traced back to git:

- `swarmer.stack`: the stack name
- `swarmer.revision`: the revision ID
- `swarmer.commit`: the commit SHA

## When a deploy fails

A failed deployment is marked failed, with every error it collected. That's all:

- Git is never changed or reverted.
- The stack's desired state stays the failed revision.
- Nothing is rolled back on its own.

A successful deployment makes its revision the stack's last good revision. To go back, deploy an
older revision from the stack page. To change what git says, revert the commit in git.

## Approvals

On a stack in `approve` mode, a revision waits until a user with the operator role or higher on
that stack approves it. Swarmer announces each waiting deployment once, through the
[notifications](/operations/notifications/) that subscribe to approval requests.

With `SWARMER_SEPARATE_APPROVER=true`, the author of a change can't approve it, or deploy it by
hand on that stack. Authors are matched on the commit's email address. Edits made in Swarmer carry
the user's email, or `<username>@swarmer.local` when they have none.

## Drift

After each successful Swarm deploy, Swarmer stores how each service looked. Every
`SWARMER_DRIFT_INTERVAL` (5 minutes) it compares the running services with that and flags the
stack as drifted when they differ, for example after someone ran `docker service update` by hand.
The stack page lists the differences: changed replicas, image, environment, removed services,
and services with the stack's label that the stack doesn't define.

`docker service update --force` only restarts tasks and doesn't count as drift, and the order of
plain lists such as environment variables is ignored.

Swarmer doesn't correct drift on its own. Deploying the stack again puts it back. Compose
projects aren't checked for drift yet.
