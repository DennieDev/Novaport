# Novaport

Git-driven management UI for Docker and Docker Swarm stacks.

Novaport deploys stacks from a git repository, shows exactly what is running and why something
isn't, and writes changes made in the UI back to git. A failed deploy never touches your stack
definition.

> Early development. Nothing here is ready for use yet.

See [docs/design.md](docs/design.md) for the design.

## Development

Requirements: [uv](https://docs.astral.sh/uv/), Docker, and Node.js 22+ for the frontend.

```sh
docker compose -f compose.dev.yaml up -d   # Postgres on localhost:5432
uv sync
uv run novaport migrate
uv run novaport server --reload
```

The API is then available on http://localhost:8000 (`/api/docs` for the OpenAPI UI).

Frontend, with the dev server proxying `/api` to the backend:

```sh
cd frontend
npm install
npm run dev
```

Checks:

```sh
uv run ruff check
uv run ruff format --check
uv run mypy
uv run pytest
```

Tests marked `postgres` run when `NOVAPORT_TEST_DATABASE_URL` is set, for example
`postgresql+asyncpg://novaport:novaport@localhost:5432/novaport_test`.

## License

AGPL-3.0-or-later. See [LICENSE](LICENSE).
