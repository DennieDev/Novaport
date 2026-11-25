# SPDX-License-Identifier: AGPL-3.0-or-later
from urllib.parse import urlsplit

from starlette.datastructures import Headers
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})


def is_cross_site(headers: Headers) -> bool:
    """True when a request was made by another site's page.

    Browsers send Sec-Fetch-Site on every request; Origin is the fallback for older ones.
    Requests without either come from non-browser clients, which don't carry our cookie
    implicitly and are allowed.
    """
    site = headers.get("sec-fetch-site")
    if site is not None:
        return site not in ("same-origin", "none")
    origin = headers.get("origin")
    if origin is None:
        return False
    return urlsplit(origin).netloc != headers.get("host")


class SameOriginMiddleware:
    """Rejects cross-site state-changing requests and WebSocket handshakes.

    Session cookies are SameSite=Lax, which already blocks most cross-site POSTs, but WebSocket
    handshakes carry cookies regardless, so this check is what stops cross-site WebSockets.
    """

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] not in SAFE_METHODS:
            if is_cross_site(Headers(scope=scope)):
                response = JSONResponse({"detail": "Cross-site request refused."}, status_code=403)
                await response(scope, receive, send)
                return
        elif scope["type"] == "websocket" and is_cross_site(Headers(scope=scope)):
            await send({"type": "websocket.close", "code": 1008})
            return
        await self.app(scope, receive, send)
