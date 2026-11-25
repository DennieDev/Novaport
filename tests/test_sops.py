# SPDX-License-Identifier: AGPL-3.0-or-later
import pytest

from swarmer.sops import (
    SopsError,
    age_recipient,
    bech32_decode,
    bech32_encode,
    generate_age_key,
    parse,
    recipients_for,
    serialize,
)

# From the age specification's test vectors.
KNOWN_KEY = "AGE-SECRET-KEY-1GFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPYYSJZGFPQ4EGAEX"
KNOWN_RECIPIENT = "age1zvkyg2lqzraa2lnjvqej32nkuu0ues2s82hzrye869xeexvn73equnujwj"


def test_bech32_round_trip() -> None:
    data = bytes(range(32))
    assert bech32_decode(bech32_encode("age", data)) == ("age", data)
    with pytest.raises(ValueError, match="checksum"):
        bech32_decode(bech32_encode("age", data)[:-1] + "q")


def test_known_age_key() -> None:
    assert age_recipient(KNOWN_KEY) == KNOWN_RECIPIENT


def test_generated_keys() -> None:
    key = generate_age_key()
    assert key.startswith("AGE-SECRET-KEY-1")
    assert age_recipient(key).startswith("age1")
    assert generate_age_key() != key
    with pytest.raises(ValueError, match="age secret key"):
        age_recipient(KNOWN_RECIPIENT)


def test_recipients_from_sops_config() -> None:
    config = """
creation_rules:
  - path_regex: ^stacks/db/
    age: age1db
  - path_regex: secrets\\.env$
    age: >-
      age1one,
      age1two
"""
    assert recipients_for(config, "stacks/db/secrets.env", "age1me") == ["age1db"]
    assert recipients_for(config, "stacks/web/secrets.env", "age1me") == ["age1one", "age1two"]
    assert recipients_for(None, "stacks/web/secrets.env", "age1me") == ["age1me"]
    assert recipients_for("creation_rules: []", "x", "age1me") == ["age1me"]
    with pytest.raises(SopsError):
        recipients_for("creation_rules: [", "x", "age1me")


def test_dotenv_serialization() -> None:
    values = {"B": "x=y", "A": "with space"}
    assert serialize(values) == b"A=with space\nB=x=y\n"
    assert parse(serialize(values)) == values
    with pytest.raises(SopsError, match="line breaks"):
        serialize({"A": "two\nlines"})
    with pytest.raises(SopsError, match="variable name"):
        serialize({"bad-key": "x"})
