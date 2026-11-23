# Novaport design

Status: draft

## Overview

Novaport is a self-hosted web UI for running Docker Compose and Docker Swarm stacks from a git
repository. Git holds the desired state; Novaport validates it, deploys it, shows exactly what is
running, and writes changes made in the UI back to git.

It exists because the existing tools fall short in one of three ways:

- Deploys that silently revert the stack definition when something goes wrong.
- No Swarm support.
- Problems such as missing bind mounts that are hidden instead of surfaced.

## Goals

- Manage Docker Swarm clusters and standalone Docker hosts.
- Git as the single source of truth, with two-way sync: commits deploy, UI edits commit.
- Predictable deploys. A failure never changes the desired state.
- Show everything that matters: failed tasks, missing mounts, missing networks, secrets and
  configs, image pull errors.
- Logs and an interactive terminal for any container on any node.
- OIDC login with role-based access control.
- Image auto-update with configurable policies, also going through git.

## Non-goals

- Kubernetes or any other orchestrator.
- "Projects", applications, or other groupings above the stack.
- App templates or a catalog.
- Building images. Novaport deploys images; CI builds them.

## Concepts

| Term | Meaning |
|---|---|
| Endpoint | A Swarm cluster or a standalone Docker host. |
| Repository | A git repository containing stacks, one folder per stack. |
| Stack | A folder in a repository containing a compose file. Deployed as a Swarm stack or a Compose project. |
| Revision | An immutable snapshot of a stack folder at a specific commit. |
| Deployment | One attempt to make an endpoint match a revision. |
| Agent | A Novaport process on every node for node-local operations. |

## Architecture

```
            browser
               |  HTTPS + WebSocket
               v
   +-----------------------+         +------------+
   |   novaport server     |-------->|  Postgres  |
   |  (on a Swarm manager) |         +------------+
   +-----------------------+
     |  docker.sock    |   overlay network (token auth)
     v                 v
  Docker API     novaport agent (global service, one per node)
  (cluster-          |
   level ops)        v
                docker.sock + host paths (node-local ops)
```

### Server

A single FastAPI process. Responsibilities:

- HTTP API and the static frontend.
- Authentication (OIDC) and authorization (RBAC).
- Git sync: webhooks, polling, commits, pushes.
- The deploy pipeline.
- Background jobs: git polling, image update checks, drift detection, cleanup.
- Proxying logs and terminal sessions between the browser and agents.

The server talks to the local Docker socket for everything cluster-wide: services, tasks, nodes,
networks, secrets, configs, service logs, and events. It shells out to the docker CLI for
`docker stack deploy`, `docker stack config`, `docker compose up`, and `docker compose config`.

Background jobs run in-process. Jobs that must not run twice take a Postgres advisory lock, so
running more than one server replica stays safe.

### Agent

The same image and codebase, started as `novaport agent`. Deployed as a global service in
Novaport's own stack, with the Docker socket and `/` (read-only) mounted.

Responsibilities, all scoped to its own node:

- Container exec (interactive terminal) and container logs.
- Bind mount checks: does a path exist, is it a file or a directory, who owns it.
- Volume listing and browsing.
- Disk usage, image list, and image pruning.

The server finds agents through Swarm DNS (`tasks.<agent service>`), which returns one address per
task. Each agent reports its node ID on connect. Agents listen only on an encrypted overlay network
and require a shared token. The token is a Swarm secret, generated on first start.

### Database

Postgres only. Besides normal storage it provides:

- Advisory locks: one deploy per stack at a time, and single-runner background jobs.
- `LISTEN/NOTIFY`: deploy progress, events, and pending revisions are pushed to every connected
  browser without a separate message broker.

### Frontend

SvelteKit with TypeScript, built to static files and served by the server. CodeMirror 6 for the
compose editor and diff views (much lighter than Monaco and simpler to bundle), xterm.js for
terminals. Pages poll the API for now; a single live WebSocket per tab can replace that later.

### Image

One image, `ghcr.io/denniedev/novaport`, containing the application, the docker CLI, the compose
plugin, `git`, and `sops`.

## Repository layout

One repository holds many stacks, one folder each:

```
.sops.yaml                 # age public key(s) for encrypting secrets
stacks/
  traefik/
    compose.yaml
    .env                   # optional, plain variables
    secrets.env            # optional, SOPS-encrypted
    novaport.yaml          # optional, per-stack settings
  monitoring/
    compose.yaml
    novaport.yaml
```

The stack name is the folder name. The `stacks/` root is configurable per repository.

### `novaport.yaml`

Per-stack settings live in git, next to the compose file, so they are reviewed like any other
change.

```yaml
endpoint: production        # name of the endpoint to deploy to
type: swarm                 # swarm | compose
deploy: approve             # auto | approve | manual
edits: pr                   # commit | pr  (how UI edits reach git)
```

All keys are optional. Defaults come from the repository settings in Novaport.

## Deploy pipeline

Each deployment runs through fixed stages. Every stage writes its result to the database and
streams it to the UI.

1. **Resolve.** Check out the revision into a working directory. Decrypt `secrets.env`.
2. **Validate.** Run `docker stack config` or `docker compose config` to merge and normalise the
   compose file. Syntax and schema errors stop here.
3. **Pre-flight.** Check the normalised config against the endpoint:
   - Every bind mount source exists on every node that satisfies the service's placement
     constraints (asked through the agents).
   - External networks, secrets, configs, and volumes exist.
   - Images resolve in their registries with the configured credentials.
   - Published ports don't collide with other stacks.
   Errors stop the deploy. Warnings are shown and don't block.
4. **Diff.** Compare the new config with what is running: services added, removed, or changed,
   and which fields changed.
5. **Apply.** Create hash-suffixed secrets and configs, then run `docker stack deploy
   --with-registry-auth --prune` or `docker compose up -d --remove-orphans`.
6. **Converge.** Watch tasks or containers until they are running and healthy, or until a timeout.
   Task errors are collected and shown per service.

Outcome:

- **Succeeded:** the revision becomes the stack's last good revision.
- **Failed:** the deployment is marked failed with all collected errors. The desired state does
  not change, nothing in git is reverted, and nothing is rolled back automatically.

Rolling back is an explicit action: deploy a previous revision. When edits go through git,
rolling back the desired state means reverting the commit, which Novaport can do on request.

Only one deployment per stack runs at a time (Postgres advisory lock on the stack ID). A new
revision arriving during a deploy is queued.

### Labels

Everything Novaport deploys gets labels so live state can be traced back:

- `novaport.stack`: stack name
- `novaport.revision`: revision ID
- `novaport.commit`: commit SHA

### Drift detection

A background job compares running services with the last deployed revision. A difference, for
example someone running `docker service update` by hand, shows the stack as drifted with a diff.
Novaport does not correct drift on its own. A redeploy does.

After a successful Swarm deploy Novaport stores each service's spec as the baseline. A job
(`NOVAPORT_DRIFT_INTERVAL`, five minutes by default) compares the running specs with it and lists
the differences by path: changed replicas, image, environment, removed services, and services
with the stack's label that the stack doesn't define. `docker service update --force` only
restarts tasks and is not drift, and the order of plain lists such as environment variables is
ignored. Compose projects are not checked yet.

## Git sync

### Inbound (git to Novaport)

- Webhook endpoint per repository, with HMAC verification for GitHub, GitLab, Gitea, and Forgejo.
- Polling as a fallback, interval configurable per repository.
- On a new commit, Novaport fetches, finds the stack folders that changed, and creates a revision
  for each.
- What happens next depends on the stack's deploy mode:
  - `auto`: deploy immediately.
  - `approve`: the revision waits until a user with permission approves it. With
    `NOVAPORT_SEPARATE_APPROVER=true`, the author of the change can't approve it, or deploy it by
    hand on that stack. Authors are matched on the commit's email address; edits made in Novaport
    carry the user's email, or `<username>@novaport.local` when they have none. The setting is
    server-wide rather than in `novaport.yaml`, so a change can't switch it off for itself.
  - `manual`: the revision is recorded and nothing else happens.
- Stack folders that appear or disappear create or (after confirmation) remove stacks.

### Outbound (Novaport to git)

Every change made in the UI is a git commit. There is no state that exists only in Novaport.

- The editor always works against a base revision (the commit the user started from).
- On save, Novaport fetches first. If the stack folder changed since the base, the save is
  refused and the user sees a 3-way diff: base, the current branch, and their edit. They resolve it
  in the editor and save again. Novaport never picks a winner silently.
- If only other folders changed, Novaport rebases the commit and pushes.
- With `edits: commit` the commit goes to the default branch. With `edits: pr` Novaport pushes a
  branch and opens a pull request through the forge API.
- Commit author is the logged-in user (name and email from OIDC). The committer is the Novaport
  service identity.

### Credentials

Per repository: an SSH deploy key (generated by Novaport, public key shown for the user to add) or
an HTTPS token. A forge API token is required for `edits: pr` and for update PRs.

## Secrets

Secrets are stored in git, encrypted with SOPS and age.

- Each stack folder may contain `secrets.env`, encrypted by SOPS. Keys stay readable, values are
  encrypted, so diffs and reviews still show which secrets changed.
- `.sops.yaml` at the repository root lists the age public keys. Anyone can add or change a secret
  in a pull request. Nobody can read existing values from the repository.
- The server holds the age private key, provided as a Swarm secret. Admins can add their own age
  key as a second recipient for recovery.
- In the UI, users with permission can view and edit decrypted values. Novaport re-encrypts and
  commits.
- A compose file uses a value through the standard `environment` secret source:

  ```yaml
  services:
    api:
      secrets: [db_password]
  secrets:
    db_password:
      environment: DB_PASSWORD   # from secrets.env (or .env)
  ```

  Values can also be interpolated as `${DB_PASSWORD}`, which puts them in the service spec, so
  prefer secrets where the image supports `/run/secrets`.
- For Swarm stacks each referenced value becomes a Swarm secret named
  `<stack>_<secret>_<first 8 hex of sha256(value)>`. A changed value creates a new secret and a
  rolling update. Secrets from earlier deploys that nothing uses any more are removed afterwards.
- Compose projects get the values as environment variables of `docker compose`, which supports
  `environment` secrets natively.

Secret values are never logged, never stored in the database, and never sent to the browser
unless explicitly revealed by a user with permission. Reveals are audited.

## Image auto-update

Configured per service with an `x-updates` extension in the compose file, so the policy lives in
git:

```yaml
services:
  api:
    image: registry.example.com/api:2.4.1
    x-updates:
      mode: version            # digest | version | none
      allow: minor             # patch | minor | major
      match: '^\d+\.\d+\.\d+$' # optional
      action: pr               # commit | pr | notify | none
```

- `digest`: the same tag now points to a different digest.
- `version`: a newer tag exists that matches `match` and stays within `allow`. Without `match`,
  the pattern is derived from the current tag's shape: `2.4.1` gives `^\d+\.\d+\.\d+$`,
  `2.4.1-alpine` gives `^\d+\.\d+\.\d+-alpine$`.
- Actions:
  - `commit`: change the tag (or digest pin) in the compose file and commit to the default branch.
  - `pr`: same change, as a pull request.
  - `notify`: only send a notification.
  - `none`: record it in the UI only.

- In `digest` mode the "current" digest is the one pinned in the compose file
  (`image: nginx:1.27@sha256:...`), otherwise the digest the running Swarm service uses. The
  update pins the new digest. In `version` mode a pinned digest stays pinned, to the new tag's
  digest.
- `x-updates: none` (or `mode: none`) turns checks off for a service.

Updates that change git follow the normal path: new commit, new revision, then the stack's deploy
mode decides. There is no separate update path that could drift from git.

- `commit` updates of one stack found in the same check go into one commit, authored by the
  Novaport service identity. `pr` updates get one pull request per service, on a branch named
  `novaport/update-<stack>-<service>-<tag>`.
- Only the `image:` value is replaced, using the YAML parser's positions, so comments and
  formatting stay as they are.
- Each service's last result is stored with the candidate it acted on, so the same update is not
  committed, proposed, or announced twice. A failed action (no forge token, the stack changed in
  git meanwhile) is retried on the next check.

Checks run on a schedule (`NOVAPORT_UPDATE_INTERVAL`, six hours by default) and on demand from the
stack page, using the registry v2 API with the configured credentials. Manifest lookups use `HEAD`
requests, which Docker Hub doesn't count against its pull limit, and tag lists and digests are
cached for 15 minutes.

## Authentication and authorization

- OIDC (authorization code flow with PKCE). Any compliant provider. Providers are configured in
  the UI; client secrets are encrypted in the database like git credentials.
- The login state is single-use and bound to the browser that started it by a short-lived cookie,
  so a callback URL can't log someone else in. ID tokens are checked against the provider's JWKS
  (asymmetric algorithms only), with issuer, audience, expiry, and nonce.
- OIDC users are matched on issuer and subject, never on email or username. They are created on
  first login with no roles.
- A local admin account exists for bootstrap and break-glass. It can be disabled.
- Sessions are server-side, referenced by an HTTP-only, `SameSite=Lax` cookie.

### Roles

| Role | Can |
|---|---|
| Viewer | See stacks, services, logs, and deploy history. |
| Operator | Everything Viewer can, plus deploy, approve, roll back, restart, and open terminals. |
| Editor | Everything Operator can, plus edit stacks and reveal or edit secrets. |
| Admin | Everything, including endpoints, repositories, registries, users, and settings. |

Roles are bound globally or per stack, to users or to groups. The highest applicable role counts.

- A role on a stack covers that stack, the services of the Docker stack with the same name, their
  tasks, logs, and terminals. Users with only stack roles see only those services.
- Nodes, networks, Swarm secrets and configs, and agent node views need a global role.
- Group membership comes from a provider's groups claim (a dotted path such as
  `realm_access.roles` reaches nested claims) through per-provider mappings from claim values to
  Novaport groups, and is synced on every login. Members added by hand are left alone. When the
  ID token has no groups claim, Novaport asks the userinfo endpoint.
- Novaport refuses changes that would leave no enabled user with the global admin role.

### Audit log

Every state-changing action and every secret reveal or terminal session is recorded: who, what,
when, which stack, from which IP, and the outcome (`success`, `failed`, or `denied`). Denied
state-changing requests are recorded too. Secret values are never written to it; only key names.
Terminal sessions are recorded when the connection to the agent is established. The audit log is
append-only in the application.

### Retention

An hourly job deletes history past its retention:

- Finished deployments and their logs after 90 days (`NOVAPORT_DEPLOYMENT_RETENTION`), keeping the
  newest 20 per stack (`NOVAPORT_KEEP_DEPLOYMENTS`) however old they are.
- Revisions after the same 90 days, unless a remaining deployment uses them, they are a stack's
  last good revision, or they are among the newest 20 of their stack. Git still has every commit.
- Audit entries after a year (`NOVAPORT_AUDIT_RETENTION`).

A retention of `0d` keeps everything.

## Registries

Registry credentials are stored encrypted in the database, one login per registry host
(`docker.io` for Docker Hub). They are used for pre-flight image checks and update checks, and are
written to a temporary docker client config for each deploy, which `docker stack deploy
--with-registry-auth` passes to the nodes.

The pre-flight check resolves every image's tag or digest. A missing image fails a Swarm deploy;
for Compose projects it is only a warning, since the image may have been built on the host. An
unreachable registry is a warning either way.

## Notifications

Channels: generic webhook, email (SMTP), Slack.

Events:

- Deploy succeeded or failed.
- Revision waiting for approval.
- Drift detected.
- Image update available, committed, or PR opened.
- Agent or node unreachable.

Each channel can subscribe to events globally or per stack.

- Webhooks get a JSON body (`event`, `title`, `text`, `stack`, `link`, `data`, `at`) and, when the
  channel has a signing secret, an `X-Novaport-Signature: sha256=<HMAC of the body>` header. URLs
  and secrets are encrypted in the database, since webhook URLs often carry a token.
- Slack channels post to an incoming webhook. Email uses the SMTP server configured with
  `NOVAPORT_SMTP_*`; channels only hold the recipients.
- Delivery happens in the background, so a slow endpoint never holds up a deploy. The last
  delivery time and error are shown per channel, and a test message can be sent from the UI.
- Approval requests are announced once per deployment, whichever path created them. Drift is
  announced when the differences change, not on every check. Nodes and agents are announced when
  they go down; outages already in place when Novaport starts are not.

## Data model

Main tables (columns abbreviated):

- `users`: id, username, name, email, password_hash (local users), issuer, subject, disabled
- `groups`, `group_members` (provider id for memberships synced from a groups claim)
- `oidc_providers`, `oidc_group_mappings`: claim value to group, per provider
- `role_bindings`: user or group, role, stack id (NULL for global)
- `endpoints`: id, name, kind (swarm, standalone), connection
- `repositories`: id, url, branch, stacks_root, credentials, webhook secret, poll interval
- `stacks`: id, repository_id, path, name, endpoint_id, type, deploy_mode, edits_mode,
  last_good_revision_id
- `revisions`: id, stack_id, commit_sha, content_hash, created_at, source (webhook, poll, ui,
  update)
- `deployments`: id, stack_id, revision_id, status, stage, started_at, finished_at, triggered_by
- `deployment_events`: id, deployment_id, stage, level, message, data
- `image_checks`: id, stack_id, service, current_ref, candidate_ref, status, policy, error,
  checked_at, action_taken, acted_on_ref, action_ref
- `registries`: id, host, username, encrypted password
- `notification_channels`: name, kind, encrypted url and signing secret, recipients, last delivery
- `notification_subscriptions`: channel, event (or `*`), stack id (NULL for all)
- `audit_log`: id, at, user_id, username, action, target, stack_id, ip, outcome, details
- `endpoints`: id, name, secret hash, connected and disconnected times, hostname, Docker and agent
  versions, whether it is a Swarm manager
- `settings`

Deployment status values: `pending`, `awaiting_approval`, `running`, `succeeded`, `failed`,
`cancelled`.

## Endpoints

The Swarm or host Novaport runs on is the endpoint `local`. Other Docker hosts and Swarm clusters
are endpoints too, each with an agent that connects out to the server, so they need no open ports
and can sit behind NAT. One Novaport manages any number of them.

- An admin adds an endpoint under Endpoints and gets an edge key: the server URL, the endpoint
  ID, and a secret, in one string. The server stores only a hash of the secret. A new key
  disconnects the agent using the old one.
- The agent runs with `NOVAPORT_EDGE_KEY` set (`docker run` on a single host, a service on a
  manager for a Swarm cluster). It opens a WebSocket to `/api/edge/connect` and reconnects with
  backoff when the connection drops. Use an `https://` server URL; the agent verifies the
  certificate.
- Over that WebSocket the server opens byte streams to the agent: to the host's Docker socket and
  to the agent's own API. On the server, each connected endpoint gets a local Docker socket and
  agent socket that turn every connection into such a stream. The deploy pipeline, `docker
  compose`, `docker stack deploy`, drift checks, and restarts run against the endpoint's socket
  exactly as they do locally.
- A stack picks its endpoint with `endpoint: <name>` in `novaport.yaml`. Unknown names are shown
  as a configuration error on the stack. Moving a stack to another endpoint does not remove it
  from the old one.
- Endpoints that stacks still use can't be deleted. A disconnected endpoint fails deploys at the
  resolve stage and raises an `endpoint.disconnected` notification.

The edge agent has Docker socket access, which is root on that host, and the server can use all
of it. The key is what protects that: treat it like a root password for the host.

Not yet done: browsing nodes, services, logs, and terminals of remote endpoints (the pages show
`local` only), and per-node agents on remote Swarm clusters. Bind mount checks on a remote Swarm
cover the manager the agent runs on.

## Security notes

- Access to the Docker socket is root on the host. Novaport's own access control and audit log are
  the main defense, so RBAC is enforced on every endpoint, including WebSocket upgrades.
- The agent mounts the host filesystem read-only and only exposes the operations listed above.
  Path checks resolve symlinks and reject paths outside `/`.
- CSRF protection on all state-changing requests. Strict CSP on the frontend: scripts only from
  Novaport itself plus hashes of the inline bootstrap script in the built `index.html`, no
  framing. Styles allow inline, which CodeMirror and xterm.js need.
- Webhook payloads are verified before any work happens.
- Git credentials, registry credentials, and the age key are never returned by the API.

## Testing

- Unit tests for parsing, diffing, policy evaluation, and conflict handling.
- Integration tests against a throwaway Swarm (Docker-in-Docker, multiple nodes) and a local git
  server: full deploy pipeline, pre-flight failures, conflicts, update PRs.
- Frontend: component tests, and a small end-to-end suite against the integration environment.

## Roadmap

1. **Foundation.** Project layout, FastAPI app, Postgres and migrations, local admin login,
   read-only views of nodes, services, tasks, networks, secrets, configs, and service logs.
2. **Deploys from git.** Repositories, inbound sync, revisions, the deploy pipeline without
   pre-flight, deploy modes, history, labels.
3. **Agent.** Agent service, terminal and container logs on any node, mount and volume pre-flight
   checks, disk and image usage.
4. **Editing.** Compose editor, outbound commits, conflict handling with 3-way diff, PR mode,
   SOPS secrets.
5. **Access.** OIDC, roles and bindings, audit log.
6. **Updates and notifications.** Registries, update policies, update commits and PRs, notification
   channels, drift detection.
7. **Endpoints.** Standalone hosts and other Swarm clusters through outbound edge agents.

## Open questions

- Does Novaport need to read stacks that were deployed outside it, to help with migration?
