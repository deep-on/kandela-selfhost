"""MCP protocol authentication via API keys.

Provides:
- ``SingleUserAuthMiddleware`` — ASGI middleware that enforces Bearer token
  auth in single-user mode when ``KANDELA_REQUIRE_AUTH=true``
  (legacy: ``MEMORY_MCP_REQUIRE_AUTH``).
- Helper functions for API key generation and hashing (stdlib only).
"""

from __future__ import annotations

import hashlib
import logging
import os
import secrets
from typing import Any

from starlette.responses import JSONResponse

logger = logging.getLogger(__name__)


def is_require_auth() -> bool:
    """Check if authentication is required even in single-user mode.

    Returns True when KANDELA_REQUIRE_AUTH env var is set to a truthy value
    (legacy: MEMORY_MCP_REQUIRE_AUTH).
    """
    val = os.environ.get("KANDELA_REQUIRE_AUTH", os.environ.get("MEMORY_MCP_REQUIRE_AUTH", ""))
    return val.lower() in ("1", "true", "yes")


def get_single_user_api_key() -> str | None:
    """Get the single-user API key from environment.

    Returns None if not configured.
    """
    return os.environ.get("KANDELA_API_KEY", os.environ.get("MEMORY_MCP_API_KEY")) or None


def verify_single_user_key(provided_key: str) -> bool:
    """Verify a provided key against the single-user API key.

    Uses constant-time comparison to prevent timing attacks.
    """
    expected = get_single_user_api_key()
    if expected is None:
        return False
    return secrets.compare_digest(provided_key, expected)


# ---------------------------------------------------------------------------
# API key helpers (stdlib only — no bcrypt dependency)
# ---------------------------------------------------------------------------

API_KEY_PREFIX = "mcp_"


def generate_api_key() -> str:
    """Generate a new API key: ``mcp_`` + 48 chars of url-safe random."""
    return API_KEY_PREFIX + secrets.token_urlsafe(36)


def hash_api_key(key: str) -> str:
    """SHA-256 hash of the raw API key for storage.

    API keys are high-entropy random tokens (not user-chosen passwords),
    so brute-forcing is infeasible and a fast hash suffices.
    """
    return hashlib.sha256(key.encode()).hexdigest()


# ---------------------------------------------------------------------------
# ASGI Middleware
# ---------------------------------------------------------------------------


class SingleUserAuthMiddleware:
    """ASGI middleware enforcing Bearer token auth in single-user mode.

    Active only when ``KANDELA_REQUIRE_AUTH=true`` (legacy: ``MEMORY_MCP_REQUIRE_AUTH``)
    and ``KANDELA_API_KEY`` (legacy: ``MEMORY_MCP_API_KEY``) env var is set.

    Paths that are always public (no auth required):
        - ``/api/health`` — health check endpoint
        - ``/api/install/*`` — client installer
    """

    # Paths that skip auth even when REQUIRE_AUTH is enabled
    _PUBLIC_PREFIXES = ("/api/health", "/api/install")

    def __init__(self, app: Any) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict,
        receive: Any,
        send: Any,
    ) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        path: str = scope.get("path", "")

        # Allow public endpoints without auth
        if any(path.startswith(prefix) for prefix in self._PUBLIC_PREFIXES):
            await self.app(scope, receive, send)
            return

        # Extract and verify Bearer token
        headers = dict(scope.get("headers", []))
        auth_header = headers.get(b"authorization", b"").decode()

        if not auth_header.startswith("Bearer "):
            client_ip = scope.get("client", ("unknown",))[0]
            logger.warning(
                "AUTH_FAIL no_bearer ip=%s path=%s (single-user require_auth)",
                client_ip, path,
            )
            response = JSONResponse(
                {"error": "Authorization header required: Bearer <api_key>"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        api_key = auth_header[7:]
        if not verify_single_user_key(api_key):
            client_ip = scope.get("client", ("unknown",))[0]
            logger.warning(
                "AUTH_FAIL invalid_key ip=%s path=%s (single-user require_auth)",
                client_ip, path,
            )
            response = JSONResponse(
                {"error": "Invalid API key"},
                status_code=401,
            )
            await response(scope, receive, send)
            return

        await self.app(scope, receive, send)


__all__ = [
    "API_KEY_PREFIX",
    "SingleUserAuthMiddleware",
    "generate_api_key",
    "get_single_user_api_key",
    "hash_api_key",
    "is_require_auth",
    "verify_single_user_key",
]
