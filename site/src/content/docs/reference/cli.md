---
title: Command line
description: The swarmer command and its subcommands.
---

The image's entry point is `swarmer`. Run a subcommand in a running server container with
`docker exec`, or in a throwaway one:

```sh
docker run --rm ghcr.io/denniedev/swarmer generate-key
```

| Command | |
| --- | --- |
| `swarmer server [--host HOST] [--port PORT] [--no-migrate]` | Run the web server. `--no-migrate` skips database migrations on start. |
| `swarmer agent [--port PORT]` | Run the node agent, or an edge agent when `SWARMER_EDGE_KEY` is set. |
| `swarmer migrate [REVISION]` | Apply database migrations, up to `head` by default. |
| `swarmer create-admin [--username NAME] [--password-stdin]` | Create an admin user. Asks for the password unless it comes from standard input. |
| `swarmer set-password USERNAME [--password-stdin]` | Set a local user's password. |
| `swarmer generate-key` | Print a new value for `SWARMER_SECRET_KEY`. |
| `swarmer generate-age-key` | Print a new value for `SWARMER_AGE_KEY`, with its public key in a comment line. |
| `swarmer version` | Print the version. |

To reset a password on a running install:

```sh
docker exec -it "$(docker ps -q -f name=swarmer_server)" \
  swarmer set-password admin
```
