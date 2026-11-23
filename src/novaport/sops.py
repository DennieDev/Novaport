# SPDX-License-Identifier: AGPL-3.0-or-later
"""SOPS-encrypted `secrets.env` files, using age keys.

Encryption and decryption go through the `sops` binary so files stay compatible with the
tool people use on their own machines.
"""

import re
import tempfile
from pathlib import Path

import yaml
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

from novaport.process import CommandTimeout, run

SECRETS_FILE = "secrets.env"
KEY_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")

_CHARSET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"


class SopsError(Exception):
    pass


def _polymod(values: list[int]) -> int:
    generator = [0x3B6A57B2, 0x26508E6D, 0x1EA119FA, 0x3D4233DD, 0x2A1462B3]
    checksum = 1
    for value in values:
        top = checksum >> 25
        checksum = (checksum & 0x1FFFFFF) << 5 ^ value
        for i in range(5):
            checksum ^= generator[i] if (top >> i) & 1 else 0
    return checksum


def _hrp_expand(hrp: str) -> list[int]:
    return [ord(c) >> 5 for c in hrp] + [0] + [ord(c) & 31 for c in hrp]


def _to_5bit(data: bytes) -> list[int]:
    acc, bits, result = 0, 0, []
    for byte in data:
        acc = (acc << 8) | byte
        bits += 8
        while bits >= 5:
            bits -= 5
            result.append((acc >> bits) & 31)
    if bits:
        result.append((acc << (5 - bits)) & 31)
    return result


def _from_5bit(data: list[int]) -> bytes:
    acc, bits, result = 0, 0, bytearray()
    for value in data:
        acc = (acc << 5) | value
        bits += 5
        if bits >= 8:
            bits -= 8
            result.append((acc >> bits) & 0xFF)
    return bytes(result)


def bech32_encode(hrp: str, data: bytes) -> str:
    values = _to_5bit(data)
    checksum = _polymod([*_hrp_expand(hrp), *values, 0, 0, 0, 0, 0, 0]) ^ 1
    values += [(checksum >> 5 * (5 - i)) & 31 for i in range(6)]
    return hrp + "1" + "".join(_CHARSET[v] for v in values)


def bech32_decode(text: str) -> tuple[str, bytes]:
    text = text.lower()
    hrp, _, rest = text.rpartition("1")
    if not hrp or len(rest) < 6 or any(c not in _CHARSET for c in rest):
        raise ValueError("Not a bech32 string.")
    values = [_CHARSET.index(c) for c in rest]
    if _polymod([*_hrp_expand(hrp), *values]) != 1:
        raise ValueError("Bad bech32 checksum.")
    return hrp, _from_5bit(values[:-6])


def generate_age_key() -> str:
    raw = X25519PrivateKey.generate().private_bytes(
        serialization.Encoding.Raw, serialization.PrivateFormat.Raw, serialization.NoEncryption()
    )
    return bech32_encode("age-secret-key-", raw).upper()


def age_recipient(secret_key: str) -> str:
    hrp, raw = bech32_decode(secret_key.strip())
    if hrp != "age-secret-key-" or len(raw) != 32:
        raise ValueError("Not an age secret key.")
    public = X25519PrivateKey.from_private_bytes(raw).public_key()
    return bech32_encode(
        "age", public.public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)
    )


def recipients_for(sops_config: str | None, path: str, fallback: str) -> list[str]:
    """age recipients for `path` from a `.sops.yaml`, or `fallback` if it has no rule for it."""
    if sops_config:
        try:
            data = yaml.safe_load(sops_config) or {}
        except yaml.YAMLError as exc:
            raise SopsError(f".sops.yaml is not valid YAML: {exc}") from exc
        for rule in data.get("creation_rules") or []:
            pattern = rule.get("path_regex")
            if pattern and not re.search(pattern, path):
                continue
            age = rule.get("age")
            if isinstance(age, list):
                age = ",".join(age)
            if age:
                return [r.strip() for r in str(age).split(",") if r.strip()]
    return [fallback]


def serialize(values: dict[str, str]) -> bytes:
    lines = []
    for key in sorted(values):
        value = values[key]
        if not KEY_PATTERN.match(key):
            raise SopsError(f"{key!r} is not a valid variable name.")
        if "\n" in value or "\r" in value:
            raise SopsError(f"{key}: values can't contain line breaks.")
        lines.append(f"{key}={value}")
    return ("\n".join(lines) + "\n").encode() if lines else b""


def parse(data: bytes) -> dict[str, str]:
    values = {}
    for line in data.decode().splitlines():
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value
    return values


class Sops:
    def __init__(self, binary: str, age_key: str | None, timeout: float = 30) -> None:
        self.binary = binary
        self.age_key = age_key
        self.timeout = timeout

    @property
    def available(self) -> bool:
        return bool(self.age_key)

    @property
    def recipient(self) -> str | None:
        return age_recipient(self.age_key) if self.age_key else None

    async def _run(self, args: list[str], content: bytes) -> bytes:
        if not self.age_key:
            raise SopsError("NOVAPORT_AGE_KEY is not set, so secrets can't be read or written.")
        with tempfile.TemporaryDirectory(prefix="novaport-sops-") as tmp:
            source = Path(tmp) / SECRETS_FILE
            source.write_bytes(content)
            try:
                result = await run(
                    [
                        self.binary,
                        *args,
                        "--input-type",
                        "dotenv",
                        "--output-type",
                        "dotenv",
                        str(source),
                    ],
                    env={"SOPS_AGE_KEY": self.age_key},
                    timeout=self.timeout,
                    check=False,
                )
            except FileNotFoundError as exc:
                raise SopsError(f"The sops binary ({self.binary}) is not installed.") from exc
            except CommandTimeout as exc:
                raise SopsError(str(exc)) from exc
        if not result.ok:
            raise SopsError(result.summary().replace(self.age_key, "***"))
        return result.stdout.encode()

    async def decrypt(self, encrypted: bytes) -> dict[str, str]:
        return parse(await self._run(["--decrypt"], encrypted))

    async def encrypt(self, values: dict[str, str], recipients: list[str]) -> bytes:
        if not values:
            raise SopsError("Refusing to write an empty secrets file; delete it instead.")
        return await self._run(["--encrypt", "--age", ",".join(recipients)], serialize(values))
