# SPDX-License-Identifier: AGPL-3.0-or-later
import argparse
from collections.abc import Sequence

import uvicorn

from novaport import __version__
from novaport.config import get_settings
from novaport.migrate import upgrade


def _server(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.migrate_on_start and not args.no_migrate:
        upgrade(settings.database_url.get_secret_value())
    uvicorn.run(
        "novaport.server.app:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level,
        reload=args.reload,
        proxy_headers=True,
    )
    return 0


def _agent(args: argparse.Namespace) -> int:
    settings = get_settings()
    uvicorn.run(
        "novaport.agent.app:create_app",
        factory=True,
        host=settings.host,
        port=args.port or settings.agent_port,
        log_level=settings.log_level,
    )
    return 0


def _migrate(args: argparse.Namespace) -> int:
    upgrade(get_settings().database_url.get_secret_value(), args.revision)
    return 0


def _version(_: argparse.Namespace) -> int:
    print(__version__)
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="novaport")
    sub = parser.add_subparsers(dest="command", required=True)

    server = sub.add_parser("server", help="run the web server")
    server.add_argument("--host")
    server.add_argument("--port", type=int)
    server.add_argument("--reload", action="store_true", help="reload on code changes")
    server.add_argument("--no-migrate", action="store_true", help="skip database migrations")
    server.set_defaults(func=_server)

    agent = sub.add_parser("agent", help="run the node agent")
    agent.add_argument("--port", type=int)
    agent.set_defaults(func=_agent)

    migrate = sub.add_parser("migrate", help="apply database migrations")
    migrate.add_argument("revision", nargs="?", default="head")
    migrate.set_defaults(func=_migrate)

    version = sub.add_parser("version", help="print the version")
    version.set_defaults(func=_version)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result
