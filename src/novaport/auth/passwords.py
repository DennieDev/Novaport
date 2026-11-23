# SPDX-License-Identifier: AGPL-3.0-or-later
from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError

MIN_LENGTH = 12

_hasher = PasswordHasher()

# Verified against when the user doesn't exist, so a login takes the same time either way.
_DUMMY_HASH = _hasher.hash("novaport-timing-equaliser")


class WeakPassword(ValueError):
    pass


def check_strength(password: str) -> None:
    if len(password) < MIN_LENGTH:
        raise WeakPassword(f"Password must be at least {MIN_LENGTH} characters.")


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(password_hash: str | None, password: str) -> bool:
    try:
        return _hasher.verify(password_hash or _DUMMY_HASH, password) and password_hash is not None
    except (VerificationError, InvalidHashError):
        return False


def needs_rehash(password_hash: str) -> bool:
    return _hasher.check_needs_rehash(password_hash)
