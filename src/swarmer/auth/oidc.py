# SPDX-License-Identifier: AGPL-3.0-or-later
import base64
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

import httpx
import jwt

CACHE_SECONDS = 3600
# Asymmetric algorithms only: HS* would let anyone holding the client secret mint tokens,
# and "none" isn't a signature at all.
ALGORITHMS = frozenset(
    {"RS256", "RS384", "RS512", "PS256", "PS384", "PS512", "ES256", "ES384", "ES512", "EdDSA"}
)


class OidcError(Exception):
    pass


@dataclass(frozen=True)
class Discovery:
    issuer: str
    authorization_endpoint: str
    token_endpoint: str
    jwks_uri: str
    userinfo_endpoint: str | None


@dataclass(frozen=True)
class Pkce:
    verifier: str
    challenge: str


def new_pkce() -> Pkce:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode()).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode()
    return Pkce(verifier, challenge)


def _same_issuer(a: str, b: str) -> bool:
    return a.rstrip("/") == b.rstrip("/")


class OidcClient:
    """Talks to OpenID Connect providers. Discovery documents and key sets are cached."""

    def __init__(self, http: httpx.AsyncClient) -> None:
        self.http = http
        self._discovery: dict[str, tuple[float, Discovery]] = {}
        self._jwks: dict[str, tuple[float, jwt.PyJWKSet]] = {}

    async def _get_json(self, url: str, **kwargs: Any) -> dict[str, Any]:
        try:
            response = await self.http.get(url, **kwargs)
        except httpx.HTTPError as exc:
            raise OidcError(f"Can't reach {url}: {exc}") from exc
        if response.status_code != 200:
            raise OidcError(f"{url} returned HTTP {response.status_code}.")
        try:
            data = response.json()
        except json.JSONDecodeError as exc:
            raise OidcError(f"{url} didn't return JSON.") from exc
        if not isinstance(data, dict):
            raise OidcError(f"{url} didn't return a JSON object.")
        return data

    async def discover(self, issuer: str, *, refresh: bool = False) -> Discovery:
        cached = self._discovery.get(issuer)
        if cached and not refresh and time.monotonic() - cached[0] < CACHE_SECONDS:
            return cached[1]
        data = await self._get_json(issuer.rstrip("/") + "/.well-known/openid-configuration")
        if not _same_issuer(str(data.get("issuer", "")), issuer):
            raise OidcError(
                f"The provider reports issuer {data.get('issuer')!r}, expected {issuer!r}."
            )
        try:
            found = Discovery(
                issuer=data["issuer"],
                authorization_endpoint=data["authorization_endpoint"],
                token_endpoint=data["token_endpoint"],
                jwks_uri=data["jwks_uri"],
                userinfo_endpoint=data.get("userinfo_endpoint"),
            )
        except KeyError as exc:
            raise OidcError(f"The discovery document is missing {exc.args[0]}.") from exc
        self._discovery[issuer] = (time.monotonic(), found)
        return found

    def authorization_url(
        self,
        discovery: Discovery,
        *,
        client_id: str,
        redirect_uri: str,
        scopes: str,
        state: str,
        nonce: str,
        challenge: str,
    ) -> str:
        params = {
            "response_type": "code",
            "client_id": client_id,
            "redirect_uri": redirect_uri,
            "scope": scopes,
            "state": state,
            "nonce": nonce,
            "code_challenge": challenge,
            "code_challenge_method": "S256",
        }
        separator = "&" if "?" in discovery.authorization_endpoint else "?"
        return discovery.authorization_endpoint + separator + urlencode(params)

    async def exchange_code(
        self,
        discovery: Discovery,
        *,
        client_id: str,
        client_secret: str | None,
        code: str,
        redirect_uri: str,
        verifier: str,
    ) -> dict[str, Any]:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "code_verifier": verifier,
            "client_id": client_id,
        }
        auth = (client_id, client_secret) if client_secret else httpx.USE_CLIENT_DEFAULT
        try:
            response = await self.http.post(discovery.token_endpoint, data=form, auth=auth)
        except httpx.HTTPError as exc:
            raise OidcError(f"Can't reach the token endpoint: {exc}") from exc
        try:
            data = response.json()
        except json.JSONDecodeError:
            data = {}
        if response.status_code != 200 or not isinstance(data, dict):
            reason = data.get("error_description") or data.get("error") if data else None
            raise OidcError(
                f"The token endpoint refused the code (HTTP {response.status_code}"
                + (f": {reason}" if reason else "")
                + ")."
            )
        if "id_token" not in data:
            raise OidcError("The provider didn't return an ID token. Is 'openid' in the scopes?")
        return data

    async def _signing_key(self, discovery: Discovery, kid: str | None) -> jwt.PyJWK:
        for refresh in (False, True):
            cached = self._jwks.get(discovery.jwks_uri)
            if refresh or not cached or time.monotonic() - cached[0] >= CACHE_SECONDS:
                data = await self._get_json(discovery.jwks_uri)
                try:
                    keys = jwt.PyJWKSet.from_dict(data)
                except jwt.PyJWTError as exc:
                    raise OidcError(f"The provider's key set is invalid: {exc}") from exc
                cached = (time.monotonic(), keys)
                self._jwks[discovery.jwks_uri] = cached
            signing = [k for k in cached[1].keys if k.public_key_use in (None, "sig")]
            if kid is None and len(signing) == 1:
                return signing[0]
            for key in signing:
                if key.key_id == kid:
                    return key
        raise OidcError("The ID token was signed with a key the provider doesn't publish.")

    async def verify_id_token(
        self, discovery: Discovery, token: str, *, client_id: str, nonce: str
    ) -> dict[str, Any]:
        try:
            header = jwt.get_unverified_header(token)
        except jwt.PyJWTError as exc:
            raise OidcError(f"The ID token is malformed: {exc}") from exc
        algorithm = header.get("alg")
        if algorithm not in ALGORITHMS:
            raise OidcError(f"The ID token uses an unsupported algorithm ({algorithm}).")
        key = await self._signing_key(discovery, header.get("kid"))
        try:
            claims: dict[str, Any] = jwt.decode(
                token,
                key=key.key,
                algorithms=[algorithm],
                audience=client_id,
                issuer=discovery.issuer,
                leeway=60,
                options={"require": ["exp", "iat", "sub", "iss", "aud"]},
            )
        except jwt.PyJWTError as exc:
            raise OidcError(f"The ID token is invalid: {exc}") from exc
        if not secrets.compare_digest(str(claims.get("nonce", "")), nonce):
            raise OidcError("The ID token's nonce doesn't match this login.")
        audiences = claims["aud"] if isinstance(claims["aud"], list) else [claims["aud"]]
        if len(audiences) > 1 and claims.get("azp") != client_id:
            raise OidcError("The ID token was issued to another client.")
        return claims

    async def userinfo(self, discovery: Discovery, access_token: str) -> dict[str, Any]:
        if not discovery.userinfo_endpoint:
            return {}
        return await self._get_json(
            discovery.userinfo_endpoint, headers={"Authorization": f"Bearer {access_token}"}
        )
