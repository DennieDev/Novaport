---
title: Repository webhooks
description: Have Swarmer sync right after a push instead of waiting for the next poll.
---

Swarmer polls each repository, every 5 minutes by default. A push webhook makes it sync right
after a push. The repository's page in Swarmer shows the webhook URL and secret. Add a push
webhook in the forge:

- **GitHub:** content type `application/json`, the secret in "Secret".
- **GitLab:** the secret in "Secret token".
- **Gitea and Forgejo:** content type `application/json`, the secret in "Secret".

Swarmer verifies the signature before doing any work. Requests with a wrong signature get a 401
and are ignored.

With a webhook in place you can set the repository's poll interval to 0 to rely on the webhook
alone, or keep polling as a fallback for missed deliveries.
