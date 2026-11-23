# SPDX-License-Identifier: AGPL-3.0-or-later
"""An in-process container registry speaking enough of the v2 API for update checks."""

import base64
import hashlib

import httpx

TOKEN_REALM = "https://auth.example.com/token"


def digest_of(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode()).hexdigest()


class FakeRegistry:
    def __init__(self, host: str = "registry.example.com") -> None:
        self.host = host
        # repository -> tag -> digest
        self.repos: dict[str, dict[str, str]] = {}
        # Repositories that need these credentials; others allow anonymous pulls.
        self.private: dict[str, tuple[str, str]] = {}
        self.page_size = 1000
        self.requests: list[str] = []

    def push(self, repository: str, *tags: str, content: str | None = None) -> None:
        for tag in tags:
            self.repos.setdefault(repository, {})[tag] = digest_of(content or f"{repository}:{tag}")

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def _token_for(self, scope: str) -> str:
        return "token-" + scope.replace(":", "-").replace("/", "_")

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append(f"{request.method} {url.host}{url.path}")
        if str(url).startswith(TOKEN_REALM):
            return self._token(request)
        if url.host != self.host or not url.path.startswith("/v2/"):
            return httpx.Response(404)
        path = url.path.removeprefix("/v2/")
        if path == "":
            return httpx.Response(401, headers=self._challenge("registry:catalog:*"))
        if "/manifests/" in path:
            repository, _, tag = path.partition("/manifests/")
            kind = "manifest"
        elif path.endswith("/tags/list"):
            repository, kind, tag = path.removesuffix("/tags/list"), "tags", ""
        else:
            return httpx.Response(404)
        scope = f"repository:{repository}:pull"
        if request.headers.get("authorization") != f"Bearer {self._token_for(scope)}":
            return httpx.Response(401, headers=self._challenge(scope))
        tags = self.repos.get(repository)
        if tags is None:
            return httpx.Response(404)
        if kind == "manifest":
            digest = tags.get(tag) or (tag if tag in tags.values() else None)
            if digest is None:
                return httpx.Response(404)
            return httpx.Response(200, headers={"Docker-Content-Digest": digest})
        names = sorted(tags)
        start = names.index(url.params["last"]) + 1 if "last" in url.params else 0
        page = names[start : start + self.page_size]
        headers = {}
        if start + self.page_size < len(names):
            headers["Link"] = (
                f'</v2/{repository}/tags/list?n={self.page_size}&last={page[-1]}>; rel="next"'
            )
        return httpx.Response(200, json={"name": repository, "tags": page}, headers=headers)

    def _challenge(self, scope: str) -> dict[str, str]:
        params = f'realm="{TOKEN_REALM}",service="{self.host}",scope="{scope}"'
        return {"WWW-Authenticate": f"Bearer {params}"}

    def _token(self, request: httpx.Request) -> httpx.Response:
        scope = request.url.params.get("scope", "")
        repository = scope.split(":")[1] if scope.count(":") == 2 else ""
        if repository in self.private:
            user, password = self.private[repository]
            expected = "Basic " + base64.b64encode(f"{user}:{password}".encode()).decode()
            if request.headers.get("authorization") != expected:
                return httpx.Response(401)
        return httpx.Response(200, json={"token": self._token_for(scope), "expires_in": 300})
