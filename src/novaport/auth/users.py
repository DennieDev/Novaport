# SPDX-License-Identifier: AGPL-3.0-or-later
import logging

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from novaport.auth.passwords import check_strength, hash_password
from novaport.models import User

log = logging.getLogger(__name__)


class UserExists(ValueError):
    pass


async def create_admin(db: AsyncSession, username: str, password: str) -> User:
    check_strength(password)
    if await db.scalar(select(User).where(User.username == username)):
        raise UserExists(f"User {username!r} already exists.")
    user = User(username=username, password_hash=hash_password(password), is_admin=True)
    db.add(user)
    await db.commit()
    return user


async def ensure_initial_admin(db: AsyncSession, username: str, password: str | None) -> None:
    """Create the first admin from configuration when the database has no users yet."""
    if await db.scalar(select(func.count()).select_from(User)):
        return
    if not password:
        log.warning(
            "No users exist. Set NOVAPORT_INITIAL_ADMIN_PASSWORD or run "
            "`novaport create-admin` to create the first admin."
        )
        return
    await create_admin(db, username, password)
    log.info("Created initial admin user %r", username)
