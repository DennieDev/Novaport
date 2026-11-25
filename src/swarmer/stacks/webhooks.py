# SPDX-License-Identifier: AGPL-3.0-or-later
"""Signature checks for push webhooks from the common git forges."""

import hashlib
import hmac

from starlette.datastructures import Headers


def _hmac_sha256(secret: str, body: bytes) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def verify(headers: Headers, body: bytes, secret: str) -> bool:
    # GitHub (and Forgejo/Gitea, which also send this header).
    if signature := headers.get("x-hub-signature-256"):
        expected = "sha256=" + _hmac_sha256(secret, body)
        return hmac.compare_digest(signature, expected)
    # Gitea and Forgejo.
    for header in ("x-gitea-signature", "x-forgejo-signature"):
        if signature := headers.get(header):
            return hmac.compare_digest(signature, _hmac_sha256(secret, body))
    # GitLab sends the shared secret as-is.
    if token := headers.get("x-gitlab-token"):
        return hmac.compare_digest(token, secret)
    return False
