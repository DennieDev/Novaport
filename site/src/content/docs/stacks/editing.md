---
title: Editing in Swarmer
description: How edits made in the UI become git commits or pull requests.
---

Every change made in Swarmer is a git commit. There is no state that exists only in Swarmer, so
the repository always describes what should run.

## Saving

The editor always works against a base: the commit you started editing from. When you save,
Swarmer fetches first.

- If the stack folder changed since your base, the save is refused and you see a 3-way diff: the
  base, the branch as it is now, and your edit. Resolve it in the editor and save again. Swarmer
  never picks a winner silently.
- If only other folders changed, Swarmer rebases your commit onto the branch and pushes it.

## Commit or pull request

The stack's `edits` setting decides where the commit goes:

- `commit`: straight to the repository's branch.
- `pr`: to a new branch, with a pull request opened through the forge's API. This needs a forge
  and a forge token on the repository.

Either way the commit then goes through the stack's [deploy mode](/stacks/deploys/#deploy-modes)
like any other change.

## Authorship

The author of each commit is the logged-in user, with the name and email from their account or
OpenID Connect provider. The committer is Swarmer's own identity, `SWARMER_COMMIT_NAME` and
`SWARMER_COMMIT_EMAIL`.

## Who can edit

Editing compose files and secrets needs the editor role on the stack. See
[Access](/operations/access/).
