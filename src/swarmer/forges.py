# SPDX-License-Identifier: AGPL-3.0-or-later
"""Opening pull (merge) requests on GitHub, GitLab, Gitea, and Forgejo."""

import re
from datetime import UTC, datetime
from enum import StrEnum
from urllib.parse import quote, urlsplit

import httpx

TIMEOUT = 20.0


class Forge(StrEnum):
    NONE = "none"
    GITHUB = "github"
    GITLAB = "gitlab"
    GITEA = "gitea"  # also Forgejo, which keeps Gitea's API


class ForgeError(Exception):
    pass


SCP_URL = re.compile(r"^[\w.-]+@(?P<host>[\w.-]+):(?P<path>.+)$")


def repo_location(url: str) -> tuple[str, str]:
    """(host, owner/name path) from a clone URL."""
    if match := SCP_URL.match(url):
        host, path = match["host"], match["path"]
    else:
        parts = urlsplit(url)
        host, path = parts.hostname or "", parts.path
    path = path.strip("/").removesuffix(".git")
    if not host or "/" not in path:
        raise ForgeError(f"Can't tell owner and repository from {url!r}.")
    return host, path


def default_api_url(forge: Forge, host: str) -> str:
    if forge == Forge.GITHUB:
        return "https://api.github.com" if host == "github.com" else f"https://{host}/api/v3"
    return f"https://{host}"


async def open_pull_request(
    forge: Forge,
    api_url: str | None,
    token: str,
    repo_url: str,
    *,
    head: str,
    base: str,
    title: str,
    body: str,
    client: httpx.AsyncClient | None = None,
) -> str:
    """Open a pull request from branch `head` into `base` and return its web URL."""
    if forge == Forge.NONE:
        raise ForgeError("No forge is configured for this repository.")
    host, path = repo_location(repo_url)
    api = (api_url or default_api_url(forge, host)).rstrip("/")

    if forge == Forge.GITHUB:
        request = (
            f"{api}/repos/{path}/pulls",
            {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"},
            {"title": title, "head": head, "base": base, "body": body},
            "html_url",
        )
    elif forge == Forge.GITEA:
        request = (
            f"{api}/api/v1/repos/{path}/pulls",
            {"Authorization": f"token {token}"},
            {"title": title, "head": head, "base": base, "body": body},
            "html_url",
        )
    else:
        request = (
            f"{api}/api/v4/projects/{quote(path, safe='')}/merge_requests",
            {"PRIVATE-TOKEN": token},
            {"title": title, "source_branch": head, "target_branch": base, "description": body},
            "web_url",
        )

    url, headers, payload, link_field = request
    own_client = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await http.post(url, headers=headers, json=payload)
    except httpx.HTTPError as exc:
        raise ForgeError(f"Could not reach {forge} at {api}: {exc}") from exc
    finally:
        if own_client:
            await http.aclose()
    if response.is_error:
        raise ForgeError(
            f"{forge} refused the pull request ({response.status_code}): {response.text[:500]}"
        )
    link = response.json().get(link_field)
    if not isinstance(link, str):
        raise ForgeError(f"{forge} did not return a link to the pull request.")
    return link


def _github_expiry(value: str) -> datetime | None:
    """GitHub sends `2027-03-12 10:00:00 UTC` (or with a numeric offset) for tokens that expire."""
    text = value.strip().replace(" UTC", " +0000")
    try:
        return datetime.strptime(text, "%Y-%m-%d %H:%M:%S %z")
    except ValueError:
        return None


async def token_expiry(
    forge: Forge,
    api_url: str | None,
    token: str,
    repo_url: str,
    *,
    client: httpx.AsyncClient | None = None,
) -> datetime | None:
    """When the token expires, or None if it doesn't (or the forge doesn't say).

    Raises ForgeError when the forge refuses the token, as it does once it has expired.
    """
    if forge == Forge.NONE:
        raise ForgeError("No forge is configured for this repository.")
    host, path = repo_location(repo_url)
    api = (api_url or default_api_url(forge, host)).rstrip("/")
    if forge == Forge.GITHUB:
        url = f"{api}/repos/{path}"
        headers = {"Authorization": f"Bearer {token}", "Accept": "application/vnd.github+json"}
    elif forge == Forge.GITLAB:
        url = f"{api}/api/v4/personal_access_tokens/self"
        headers = {"PRIVATE-TOKEN": token}
    else:
        url = f"{api}/api/v1/repos/{path}"
        headers = {"Authorization": f"token {token}"}

    own_client = client is None
    http = client or httpx.AsyncClient(timeout=TIMEOUT)
    try:
        response = await http.get(url, headers=headers)
    except httpx.HTTPError as exc:
        raise ForgeError(f"Could not reach {forge} at {api}: {exc}") from exc
    finally:
        if own_client:
            await http.aclose()
    if response.status_code in (401, 403):
        raise ForgeError(
            f"{forge} refused the token ({response.status_code}); it may have expired."
        )
    if response.is_error:
        raise ForgeError(f"{forge} answered {response.status_code}: {response.text[:300]}")

    if forge == Forge.GITHUB:
        header = response.headers.get("github-authentication-token-expiration")
        return _github_expiry(header) if header else None
    if forge == Forge.GITLAB:
        expires = response.json().get("expires_at")
        if isinstance(expires, str) and expires:
            return datetime.fromisoformat(expires).replace(tzinfo=UTC)
    return None
