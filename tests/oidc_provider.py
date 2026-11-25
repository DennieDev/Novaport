# SPDX-License-Identifier: AGPL-3.0-or-later
"""An in-process OpenID Connect provider for login tests."""

import base64
import hashlib
import json
import time
from typing import Any
from urllib.parse import parse_qs

import httpx
import jwt
from cryptography.hazmat.primitives.asymmetric import rsa

ISSUER = "https://idp.example.com"
CLIENT_ID = "swarmer"
CLIENT_SECRET = "s3cret"


def rsa_key() -> rsa.RSAPrivateKey:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeProvider:
    def __init__(self) -> None:
        self.key = rsa_key()
        self.kid = "key-1"
        self.claims: dict[str, Any] = {"sub": "user-1", "preferred_username": "alice"}
        self.userinfo: dict[str, Any] | None = None
        # code -> (nonce, code_challenge, redirect_uri)
        self.codes: dict[str, tuple[str, str, str]] = {}
        self.token_requests: list[dict[str, str]] = []
        self.sign_with: rsa.RSAPrivateKey | None = None
        self.nonce_override: str | None = None

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def authorize(self, query: dict[str, str], code: str = "code-1") -> str:
        """Act as the user approving the login; returns the code the browser brings back."""
        self.codes[code] = (query["nonce"], query["code_challenge"], query["redirect_uri"])
        return code

    def id_token(self, nonce: str) -> str:
        now = int(time.time())
        claims = {
            "iss": ISSUER,
            "aud": CLIENT_ID,
            "iat": now,
            "exp": now + 300,
            "nonce": self.nonce_override or nonce,
            **self.claims,
        }
        return jwt.encode(
            claims, self.sign_with or self.key, algorithm="RS256", headers={"kid": self.kid}
        )

    def handle(self, request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path == "/.well-known/openid-configuration":
            return httpx.Response(
                200,
                json={
                    "issuer": ISSUER,
                    "authorization_endpoint": f"{ISSUER}/authorize",
                    "token_endpoint": f"{ISSUER}/token",
                    "jwks_uri": f"{ISSUER}/jwks",
                    "userinfo_endpoint": f"{ISSUER}/userinfo",
                },
            )
        if path == "/jwks":
            jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(self.key.public_key()))
            return httpx.Response(200, json={"keys": [{**jwk, "kid": self.kid, "use": "sig"}]})
        if path == "/token":
            return self._token(request)
        if path == "/userinfo":
            if request.headers.get("authorization") != "Bearer access-1":
                return httpx.Response(401)
            return httpx.Response(200, json=self.userinfo or {})
        return httpx.Response(404)

    def _token(self, request: httpx.Request) -> httpx.Response:
        form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
        self.token_requests.append(form)
        expected = "Basic " + base64.b64encode(f"{CLIENT_ID}:{CLIENT_SECRET}".encode()).decode()
        if request.headers.get("authorization") != expected:
            return httpx.Response(401, json={"error": "invalid_client"})
        found = self.codes.pop(form.get("code", ""), None)
        if found is None:
            return httpx.Response(400, json={"error": "invalid_grant"})
        nonce, challenge, redirect_uri = found
        digest = hashlib.sha256(form["code_verifier"].encode()).digest()
        if base64.urlsafe_b64encode(digest).rstrip(b"=").decode() != challenge:
            return httpx.Response(400, json={"error": "invalid_grant", "error_description": "PKCE"})
        if form.get("redirect_uri") != redirect_uri:
            return httpx.Response(400, json={"error": "invalid_grant"})
        return httpx.Response(
            200,
            json={
                "access_token": "access-1",
                "token_type": "Bearer",
                "id_token": self.id_token(nonce),
            },
        )
