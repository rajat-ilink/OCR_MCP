"""Optional static bearer-token gate in front of the MCP endpoint (pure ASGI)."""

from __future__ import annotations

import hmac
import json
from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

PUBLIC_PATHS = frozenset({"/healthz"})


class BearerAuthMiddleware:
    """Require ``Authorization: Bearer <token>``.

    For clients that cannot set headers (``allow_query_token``), the token may instead be
    passed as ``?token=`` on the SSE connect request. The follow-up ``POST /messages/``
    requests are then accepted with just a ``session_id`` query parameter: the SDK rejects
    any session id it did not issue, and ids are random UUIDs (unguessable), so they act
    as a per-connection capability granted only after the token was checked.
    """

    def __init__(
        self,
        app: ASGIApp,
        token: str,
        *,
        allow_query_token: bool = False,
        sse_path: str = "/sse",
        message_path: str = "/messages",
    ) -> None:
        self.app = app
        self._token = token.encode()
        self._allow_query_token = allow_query_token
        self._sse_path = sse_path
        self._message_path = message_path.rstrip("/")

    def _authorized(self, scope: Scope, header_token: bytes) -> bool:
        if hmac.compare_digest(header_token, self._token):
            return True
        if not self._allow_query_token:
            return False
        query = parse_qs(scope.get("query_string", b"").decode("latin-1"))
        path = scope["path"]
        if path == self._sse_path:
            supplied = query.get("token", [""])[0].encode()
            return hmac.compare_digest(supplied, self._token)
        if path.rstrip("/") == self._message_path:
            # Session ids are validated by the SDK (unknown id -> 404).
            return bool(query.get("session_id", [""])[0])
        return False

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http" or scope["path"] in PUBLIC_PATHS:
            await self.app(scope, receive, send)
            return
        supplied = b""
        for name, value in scope["headers"]:
            if name == b"authorization":
                scheme, _, credential = value.partition(b" ")
                if scheme.lower() == b"bearer":
                    supplied = credential.strip()
                break
        if self._authorized(scope, supplied):
            await self.app(scope, receive, send)
            return
        body = json.dumps({"error": "unauthorized"}).encode()
        await send(
            {
                "type": "http.response.start",
                "status": 401,
                "headers": [
                    (b"content-type", b"application/json"),
                    (b"content-length", str(len(body)).encode()),
                    (b"www-authenticate", b"Bearer"),
                ],
            }
        )
        await send({"type": "http.response.body", "body": body})
