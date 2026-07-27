"""Validate Auth0 access tokens presented to the public MCP endpoint."""

import os
from typing import Any

import anyio
import jwt
from jwt import PyJWKClient
from mcp.server.auth.provider import AccessToken, TokenVerifier

from ..core.config import logger


DEFAULT_AUTH0_ISSUER = "https://dev-5zlaufe8j61g2uf8.us.auth0.com/"
DEFAULT_AUTH0_AUDIENCE = (
    "https://agent-mcp-ghg5h.ondigitalocean.app/mcp"
)
DEFAULT_AUTH0_SCOPE = "hermes:access"


def auth0_issuer() -> str:
    return os.environ.get("AUTH0_ISSUER", DEFAULT_AUTH0_ISSUER).rstrip("/") + "/"


def auth0_audience() -> str:
    return os.environ.get("AUTH0_AUDIENCE", DEFAULT_AUTH0_AUDIENCE).rstrip("/")


def auth0_required_scope() -> str:
    return os.environ.get("AUTH0_REQUIRED_SCOPE", DEFAULT_AUTH0_SCOPE).strip()


class Auth0TokenVerifier(TokenVerifier):
    """Verify signature, issuer, audience, expiry and scopes for Auth0 JWTs."""

    def __init__(self) -> None:
        self.issuer = auth0_issuer()
        self.audience = auth0_audience()
        self.jwks_client = PyJWKClient(
            f"{self.issuer}.well-known/jwks.json",
            cache_jwk_set=True,
            lifespan=3600,
        )

    def _decode(self, token: str) -> dict[str, Any]:
        signing_key = self.jwks_client.get_signing_key_from_jwt(token)
        return jwt.decode(
            token,
            signing_key.key,
            algorithms=["RS256"],
            audience=self.audience,
            issuer=self.issuer,
            options={
                "require": ["exp", "iat", "iss", "aud", "sub"],
            },
        )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await anyio.to_thread.run_sync(self._decode, token)
        except jwt.PyJWTError as exc:
            logger.warning("Rejected Auth0 access token: %s", exc)
            return None
        except Exception as exc:
            logger.error("Unable to validate Auth0 access token: %s", exc)
            return None

        raw_scope = claims.get("scope", "")
        if isinstance(raw_scope, str):
            scopes = [scope for scope in raw_scope.split() if scope]
        elif isinstance(raw_scope, list):
            scopes = [str(scope) for scope in raw_scope if scope]
        else:
            scopes = []

        client_id = (
            claims.get("azp")
            or claims.get("client_id")
            or claims.get("sub")
            or "unknown"
        )
        return AccessToken(
            token=token,
            client_id=str(client_id),
            scopes=scopes,
            expires_at=int(claims["exp"]),
            resource=self.audience,
            subject=str(claims["sub"]),
            claims=claims,
        )
