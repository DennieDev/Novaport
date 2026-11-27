---
title: Login, roles, and audit
description: Local login, OpenID Connect, group-based roles, and the audit log.
---

## Logging in

The first admin logs in with a password, created with `SWARMER_INITIAL_ADMIN_PASSWORD` or
`swarmer create-admin`. Forgotten passwords of local users can be reset with
`swarmer set-password <username>`.

Sessions end after `SWARMER_SESSION_IDLE_TIMEOUT` (8 hours) without requests, and after
`SWARMER_SESSION_MAX_AGE` (7 days) regardless.

## OpenID Connect

Add a provider under Access → Login providers. The page shows the redirect URI to register with
the provider. Behind a proxy, set `SWARMER_PUBLIC_URL` (for example
`https://swarmer.example.com`) so the redirect URI uses the external address.

## Roles

Users from a provider start without roles. Map values of the provider's groups claim to Swarmer
groups, then give the groups a role, either on everything or on single stacks:

| Role | Can |
| --- | --- |
| viewer | See stacks, services, logs, and history. |
| operator | Also deploy, approve, roll back, restart, and open terminals. |
| editor | Also edit files and secrets. |
| admin | Everything, including repositories and access. |

Roles are checked on every request, including WebSocket connections for logs and terminals.

Set `SWARMER_SEPARATE_APPROVER=true` to stop people from approving their own changes on stacks in
`approve` mode. See [Approvals](/stacks/deploys/#approvals).

## API tokens

Scripts authenticate with API tokens. See [API](/reference/api/).

## Audit log

Logins, changes, secret reveals, terminal sessions, and refused attempts end up in the audit
log, with the client's IP address. Behind a proxy, set `SWARMER_TRUSTED_PROXIES` so that address
comes from `X-Forwarded-For`.

Audit entries are kept for a year (`SWARMER_AUDIT_RETENTION`, `0d` keeps everything).
Finished deployments and unused revisions are deleted after 90 days, keeping at least the
newest 20 per stack (`SWARMER_DEPLOYMENT_RETENTION`, `SWARMER_KEEP_DEPLOYMENTS`).
