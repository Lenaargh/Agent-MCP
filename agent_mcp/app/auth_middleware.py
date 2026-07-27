"""Authentication middleware for the remote Agent-MCP service."""

import hmac
import os
from typing import Iterable, Tuple

from starlette.responses import JSONResponse


_HEADER = Tuple[bytes, bytes]


class BearerTokenAuthMiddleware:
    """Protect MCP transports and private APIs with one server-side token.

    The health endpoint remains public so hosting providers can monitor the
    service. CORS preflight requests are also allowed, but the corresponding
    request still requires authentication.
    """

    def __init__(self, app):
        self.app = app

    @staticmethod
    def _header_value(headers: Iterable[_HEADER], name: bytes) -> str:
        for header_name, value in headers:
            if header_name.lower() == name:
                return value.decode("latin-1").strip()
        return ""

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        path = scope.get("path", "")
        method = scope.get("method", "GET").upper()

        if method == "OPTIONS" or (path == "/api/status" and method in {"GET", "HEAD"}):
            await self.app(scope, receive, send)
            return

        expected_token = os.environ.get("HERMES_MCP_TOKEN", "").strip()
        if not expected_token:
            response = JSONResponse(
                {"error": "Agent-MCP authentication is not configured"},
                status_code=503,
            )
            await response(scope, receive, send)
            return

        headers = scope.get("headers", [])
        authorization = self._header_value(headers, b"authorization")
        presented_token = ""
        if authorization.lower().startswith("bearer "):
            presented_token = authorization[7:].strip()
        if not presented_token:
            presented_token = self._header_value(headers, b"x-hermes-mcp-token")

        if not presented_token or not hmac.compare_digest(presented_token, expected_token):
            response = JSONResponse(
                {"error": "Unauthorized"},
                status_code=401,
                headers={"WWW-Authenticate": 'Bearer realm="agent-mcp"'},
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)
