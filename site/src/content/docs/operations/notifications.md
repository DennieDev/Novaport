---
title: Notifications
description: Send deploy results, approvals, updates, drift, and outages to webhooks, Slack, or email.
---

Under Notifications, add channels and pick the events each one gets, for all stacks or for single
stacks. A test message can be sent from the same page, which also shows each channel's last
delivery and error.

## Events

| Event | Sent when |
| --- | --- |
| `deploy.succeeded`, `deploy.failed` | A deployment finishes. |
| `deploy.awaiting_approval` | A revision waits for approval. Sent once per deployment. |
| `drift.detected` | A stack's services differ from the last deploy. Sent when the differences change, not on every check. |
| `update.available`, `update.committed`, `update.pr_opened` | An `x-updates` check finds a newer image and acts on it. |
| `node.down`, `agent.unreachable`, `endpoint.disconnected` | A node, agent, or edge endpoint goes away. Outages already there when Swarmer starts aren't sent. |
| `forge_token.expiring` | A repository's forge token is about to expire. |

Delivery runs in the background, so a slow receiver never holds up a deploy.

## Webhook

Swarmer posts a JSON body:

```json
{
  "event": "deploy.failed",
  "title": "...",
  "text": "...",
  "stack": { "id": "...", "name": "app" },
  "link": "https://swarmer.example.com/deployments/...",
  "data": {},
  "at": "2026-11-27T12:00:00Z"
}
```

`stack` is `null` for events that aren't about a stack, and `link` is only a full URL when
`SWARMER_PUBLIC_URL` is set. The `X-Swarmer-Event` header carries the event name. When the
channel has a signing secret, requests also carry an
`X-Swarmer-Signature: sha256=<HMAC-SHA256 of the body>` header. Webhook URLs and secrets are
stored encrypted, since URLs often carry a token.

## Slack

Create an incoming webhook in Slack and add its URL as a Slack channel.

## Email

Email channels only hold recipients. The SMTP server is set on the server:

```sh
SWARMER_SMTP_HOST=smtp.example.com
SWARMER_SMTP_PORT=587
SWARMER_SMTP_SECURITY=starttls   # or tls (port 465), or none
SWARMER_SMTP_USERNAME=swarmer
SWARMER_SMTP_PASSWORD=...        # or a swarmer_smtp_password secret
SWARMER_SMTP_FROM=swarmer@example.com
```
