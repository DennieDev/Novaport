# SPDX-License-Identifier: AGPL-3.0-or-later
"""Read-only access to OCI / Docker registries through the v2 HTTP API."""

import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

import httpx

from novaport.registries.reference import Reference

MANIFEST_TYPES = ", ".join(
    [
        "application/vnd.oci.image.index.v1+json",
        "application/vnd.docker.distribution.manifest.list.v2+json",
        "application/vnd.oci.image.manifest.v1+json",
        "application/vnd.docker.distribution.manifest.v2+json",
    ]
)
CACHE_SECONDS = 900
MAX_TAG_PAGES = 20
CHALLENGE_PARAM = re.compile(r'(\w+)="([^"]*)"')
NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="?next"?')


class RegistryError(Exception):
    pass


@dataclass(frozen=True)
class Login:
    username: str
    password: str


# Looks up stored credentials for a registry host (as normalised by `normalize_host`).
LoginLookup = Callable[[str], Awaitable[Login | None]]


async def _no_login(_: str) -> Login | None:
    return None


class RegistryClient:
    def __init__(self, http: httpx.AsyncClient, logins: LoginLookup = _no_login) -> None:
        self.http = http
        self.logins = logins
        self._tokens: dict[tuple[str, str], tuple[float, str]] = {}
        self._cache: dict[tuple[str, ...], tuple[float, Any]] = {}

    def clear_cache(self) -> None:
        self._cache.clear()
        self._tokens.clear()

    def _cached(self, key: tuple[str, ...]) -> Any:
        hit = self._cache.get(key)
        if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
            return hit[1]
        return None

    async def _token(self, ref: Reference, challenge: str, login: Login | None) -> str | None:
        params = dict(CHALLENGE_PARAM.findall(challenge))
        realm = params.get("realm")
        if not realm:
            return None
        scope = params.get("scope") or f"repository:{ref.repository}:pull"
        key = (ref.registry, scope)
        cached = self._tokens.get(key)
        if cached and cached[0] > time.monotonic():
            return cached[1]
        query = {"scope": scope}
        if "service" in params:
            query["service"] = params["service"]
        auth = (login.username, login.password) if login else None
        try:
            response = await self.http.get(
                realm, params=query, auth=auth or httpx.USE_CLIENT_DEFAULT
            )
        except httpx.HTTPError as exc:
            raise RegistryError(f"Can't reach {realm}: {exc}") from exc
        if response.status_code in (401, 403):
            raise RegistryError(f"{ref.registry} refused the credentials.")
        if response.status_code != 200:
            raise RegistryError(f"{ref.registry} token endpoint returned {response.status_code}.")
        data = response.json()
        token = data.get("token") or data.get("access_token")
        if not isinstance(token, str):
            raise RegistryError(f"{ref.registry} returned no token.")
        lifetime = int(data.get("expires_in") or 60)
        self._tokens[key] = (time.monotonic() + max(lifetime - 10, 10), token)
        return token

    async def _request(
        self, method: str, ref: Reference, path: str, headers: dict[str, str] | None = None
    ) -> httpx.Response:
        url = path if path.startswith("http") else f"https://{ref.api_host}/v2/{path}"
        headers = dict(headers or {})
        cached = self._tokens.get((ref.registry, f"repository:{ref.repository}:pull"))
        if cached and cached[0] > time.monotonic():
            headers["Authorization"] = f"Bearer {cached[1]}"
        try:
            response = await self.http.request(method, url, headers=headers)
            if response.status_code != 401:
                return response
            challenge = response.headers.get("www-authenticate", "")
            login = await self.logins(ref.registry)
            if challenge.lower().startswith("bearer"):
                token = await self._token(ref, challenge, login)
                if token is None:
                    return response
                headers["Authorization"] = f"Bearer {token}"
                return await self.http.request(method, url, headers=headers)
            if challenge.lower().startswith("basic") and login:
                return await self.http.request(
                    method, url, headers=headers, auth=(login.username, login.password)
                )
            return response
        except httpx.HTTPError as exc:
            raise RegistryError(f"Can't reach {ref.registry}: {exc}") from exc

    async def digest(self, ref: Reference, tag: str | None = None) -> str | None:
        """The manifest digest a tag points to, or None if the tag doesn't exist."""
        tag = tag or ref.tag or "latest"
        key = ("digest", ref.registry, ref.repository, tag)
        if (hit := self._cached(key)) is not None:
            return str(hit) if hit else None
        response = await self._request(
            "HEAD", ref, f"{ref.repository}/manifests/{tag}", {"Accept": MANIFEST_TYPES}
        )
        if response.status_code == 404:
            self._cache[key] = (time.monotonic(), "")
            return None
        if response.status_code in (401, 403):
            raise RegistryError(
                f"{ref.registry} denied access to {ref.repository}. Add credentials under "
                "Registries if the image is private."
            )
        if response.status_code != 200:
            raise RegistryError(
                f"{ref.registry} returned {response.status_code} for {ref.repository}:{tag}."
            )
        found: str | None = response.headers.get("docker-content-digest")
        if not found:
            raise RegistryError(f"{ref.registry} didn't report a digest for {tag}.")
        self._cache[key] = (time.monotonic(), found)
        return found

    async def tags(self, ref: Reference) -> list[str]:
        key = ("tags", ref.registry, ref.repository)
        if (hit := self._cached(key)) is not None:
            return list(hit)
        found: list[str] = []
        path: str | None = f"{ref.repository}/tags/list?n=1000"
        for _ in range(MAX_TAG_PAGES):
            if path is None:
                break
            response = await self._request("GET", ref, path)
            if response.status_code in (401, 403):
                raise RegistryError(f"{ref.registry} denied access to {ref.repository}.")
            if response.status_code == 404:
                raise RegistryError(f"{ref.repository} doesn't exist on {ref.registry}.")
            if response.status_code != 200:
                raise RegistryError(f"{ref.registry} returned {response.status_code} listing tags.")
            found.extend(t for t in response.json().get("tags") or [] if isinstance(t, str))
            link = NEXT_LINK.search(response.headers.get("link", ""))
            if not link:
                path = None
            elif link.group(1).startswith("http"):
                path = link.group(1)
            else:
                path = link.group(1).removeprefix("/v2/")
        self._cache[key] = (time.monotonic(), found)
        return found

    async def check_login(self, host: str) -> None:
        """Authenticate against a registry, raising RegistryError if that fails."""
        ref = Reference(host, "library/hello-world", "latest", None, host)
        url = f"https://{ref.api_host}/v2/"
        try:
            response = await self.http.get(url)
        except httpx.HTTPError as exc:
            raise RegistryError(f"Can't reach {host}: {exc}") from exc
        if response.status_code == 200:
            return
        if response.status_code != 401:
            raise RegistryError(f"{host} returned {response.status_code} for /v2/.")
        login = await self.logins(host)
        challenge = response.headers.get("www-authenticate", "")
        if challenge.lower().startswith("basic"):
            if not login:
                raise RegistryError(f"{host} needs credentials.")
            try:
                retry = await self.http.get(url, auth=(login.username, login.password))
            except httpx.HTTPError as exc:
                raise RegistryError(f"Can't reach {host}: {exc}") from exc
            if retry.status_code != 200:
                raise RegistryError(f"{host} refused the credentials.")
            return
        params = dict(CHALLENGE_PARAM.findall(challenge))
        if "realm" not in params:
            raise RegistryError(f"{host} sent an authentication challenge Novaport can't use.")
        query = {k: v for k, v in params.items() if k == "service"}
        try:
            token = await self.http.get(
                params["realm"],
                params=query,
                auth=(login.username, login.password) if login else httpx.USE_CLIENT_DEFAULT,
            )
        except httpx.HTTPError as exc:
            raise RegistryError(f"Can't reach {params['realm']}: {exc}") from exc
        if token.status_code != 200:
            raise RegistryError(f"{host} refused the credentials.")
