---
title: API
description: Use the Swarmer API from scripts with API tokens.
---

Scripts use the same API as the UI, authenticated with a token instead of a login. Create one
under Account, API tokens, and send it as a bearer token:

```sh
curl -H "Authorization: Bearer sw_..." \
  https://swarmer.example.com/api/stacks
```

A token acts as the user who created it, with no role higher than the one picked for it. Every
action is in the audit log under that user, with the token's name.

Tokens can't open terminals, create other tokens, or change a password; those need a login. A
token stops working when it expires, is revoked, or its user is disabled. Admins see and revoke
everyone's tokens under Access, API tokens.

## Reference

The [API reference](/api/) lists every endpoint, generated from the server's OpenAPI document for
this release. A running server also serves that document at `/api/openapi.json` and an
interactive version at `/api/docs`.

## Live updates

The UI keeps one WebSocket open to `/api/events`. Messages name what changed (stack, revision,
deployment, repository, image check, or endpoint) but carry no data; reload the resource through
the normal API. Only changes to stacks the user may see are sent.
