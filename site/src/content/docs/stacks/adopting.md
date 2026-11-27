---
title: Adopting existing stacks
description: Bring stacks that were deployed without Swarmer under git.
---

Stacks → "Add them to git" lists Swarm stacks and Compose projects on your hosts that no
repository describes. Swarmer writes a compose file from what is running, which you review and
commit to a repository.

The new stack starts in `deploy: manual`, so nothing changes until you deploy it. A faithful file
updates the stack in place without restarting its tasks.

## Check the generated file

Read it before committing:

- Environment variables are copied as they are. Move passwords and tokens to
  [`secrets.env`](/stacks/variables-and-secrets/).
- Secrets and configs become external references.
- For Compose projects, the image's own environment variables and labels show up too. Remove the
  ones you didn't set yourself.

To adopt Swarmer's own stack, see [Managing Swarmer with Swarmer](/operations/self-managed/).
