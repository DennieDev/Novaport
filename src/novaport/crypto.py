# SPDX-License-Identifier: AGPL-3.0-or-later
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey


class MissingSecretKey(RuntimeError):
    def __init__(self) -> None:
        super().__init__(
            "NOVAPORT_SECRET_KEY is not set, so credentials can't be stored. "
            "Generate one with `novaport generate-key`."
        )


class Cipher:
    """Symmetric encryption for credentials stored in the database."""

    def __init__(self, key: str | None) -> None:
        self._fernet = Fernet(key.encode()) if key else None

    @property
    def available(self) -> bool:
        return self._fernet is not None

    def encrypt(self, plaintext: str) -> str:
        if self._fernet is None:
            raise MissingSecretKey
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, token: str) -> str:
        if self._fernet is None:
            raise MissingSecretKey
        try:
            return self._fernet.decrypt(token.encode()).decode()
        except InvalidToken as exc:
            raise ValueError("Stored credential can't be decrypted with this secret key.") from exc


def generate_key() -> str:
    return Fernet.generate_key().decode()


def generate_ssh_keypair(comment: str) -> tuple[str, str]:
    """Return (private key in OpenSSH format, public key line)."""
    key = Ed25519PrivateKey.generate()
    private = key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.OpenSSH,
        serialization.NoEncryption(),
    ).decode()
    public = (
        key.public_key()
        .public_bytes(serialization.Encoding.OpenSSH, serialization.PublicFormat.OpenSSH)
        .decode()
    )
    return private, f"{public} {comment}"
