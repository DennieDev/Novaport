# SPDX-License-Identifier: AGPL-3.0-or-later
import argparse
import asyncio
import contextlib
import getpass
import logging
import sys
from collections.abc import Sequence

import uvicorn
from sqlalchemy.ext.asyncio import async_sessionmaker

from novaport import __version__
from novaport.auth.passwords import WeakPassword
from novaport.auth.users import UserExists, create_admin
from novaport.config import get_settings
from novaport.crypto import generate_key
from novaport.db import create_engine
from novaport.edge.agent import run_edge_agent
from novaport.migrate import upgrade, wait_for_database
from novaport.sops import age_recipient, generate_age_key


def _server(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.migrate_on_start and not args.no_migrate:
        wait_for_database(settings.database_url.get_secret_value())
        upgrade(settings.database_url.get_secret_value())
    uvicorn.run(
        "novaport.server.app:create_app",
        factory=True,
        host=args.host or settings.host,
        port=args.port or settings.port,
        log_level=settings.log_level,
        reload=args.reload,
        proxy_headers=True,
        forwarded_allow_ips=settings.trusted_proxies,
    )
    return 0


def _agent(args: argparse.Namespace) -> int:
    settings = get_settings()
    if settings.edge_key is not None:
        logging.basicConfig(level=settings.log_level.upper(), format="%(levelname)s: %(message)s")
        with contextlib.suppress(KeyboardInterrupt):
            asyncio.run(run_edge_agent(settings))
        return 0
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


def _read_password(from_stdin: bool) -> str:
    if from_stdin:
        return sys.stdin.readline().rstrip("\n")
    password = getpass.getpass("Password: ")
    if getpass.getpass("Repeat password: ") != password:
        raise SystemExit("Passwords don't match.")
    return password


async def _create_admin(username: str, password: str) -> None:
    engine = create_engine(get_settings().database_url.get_secret_value())
    try:
        async with async_sessionmaker(engine)() as db:
            await create_admin(db, username, password)
    finally:
        await engine.dispose()


def _create_admin_command(args: argparse.Namespace) -> int:
    password = _read_password(args.password_stdin)
    try:
        asyncio.run(_create_admin(args.username, password))
    except (WeakPassword, UserExists) as exc:
        print(exc, file=sys.stderr)
        return 1
    print(f"Created admin {args.username!r}.")
    return 0


def _generate_key(_: argparse.Namespace) -> int:
    print(generate_key())
    return 0


def _generate_age_key(_: argparse.Namespace) -> int:
    key = generate_age_key()
    print(f"# public key: {age_recipient(key)}")
    print(key)
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

    admin = sub.add_parser("create-admin", help="create an admin user")
    admin.add_argument("--username", default="admin")
    admin.add_argument(
        "--password-stdin", action="store_true", help="read the password from standard input"
    )
    admin.set_defaults(func=_create_admin_command)

    key = sub.add_parser("generate-key", help="print a new value for NOVAPORT_SECRET_KEY")
    key.set_defaults(func=_generate_key)

    age = sub.add_parser("generate-age-key", help="print a new value for NOVAPORT_AGE_KEY")
    age.set_defaults(func=_generate_age_key)

    version = sub.add_parser("version", help="print the version")
    version.set_defaults(func=_version)

    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    result: int = args.func(args)
    return result
