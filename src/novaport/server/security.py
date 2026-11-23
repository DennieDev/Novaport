# SPDX-License-Identifier: AGPL-3.0-or-later
import base64
import hashlib
import re
from pathlib import Path

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

INLINE_SCRIPT = re.compile(r"<script>(.*?)</script>", re.DOTALL)
# Swagger UI loads its scripts from a CDN and runs inline code, so it keeps the browser default.
UNRESTRICTED = ("/api/docs",)


def inline_script_hashes(index: Path) -> list[str]:
    """CSP hashes of the inline scripts SvelteKit writes into the built index.html."""
    try:
        html = index.read_text()
    except OSError:
        return []
    return [
        "'sha256-" + base64.b64encode(hashlib.sha256(s.encode()).digest()).decode() + "'"
        for s in INLINE_SCRIPT.findall(html)
    ]


def content_security_policy(script_hashes: list[str]) -> str:
    return "; ".join(
        [
            "default-src 'self'",
            f"script-src {' '.join(["'self'", *script_hashes])}",
            # CodeMirror and xterm.js set styles at runtime.
            "style-src 'self' 'unsafe-inline'",
            "img-src 'self' data:",
            "font-src 'self' data:",
            "connect-src 'self'",
            "object-src 'none'",
            "base-uri 'self'",
            "form-action 'self'",
            "frame-ancestors 'none'",
        ]
    )


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp, policy: str) -> None:
        self.app = app
        self.headers = {
            "Content-Security-Policy": policy,
            "X-Content-Type-Options": "nosniff",
            "Referrer-Policy": "same-origin",
            "X-Frame-Options": "DENY",
        }

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"].startswith(UNRESTRICTED):
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                for name, value in self.headers.items():
                    headers.setdefault(name, value)
            await send(message)

        await self.app(scope, receive, send_with_headers)
