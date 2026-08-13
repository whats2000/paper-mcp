"""Authenticate and meter every tool call.

Applied to the MCP endpoint only. `/health` and artifact downloads stay open:
a readiness probe that needs a token is useless to an orchestrator, and an
artifact URL is already a capability — its unguessable token *is* the
credential.
"""
from __future__ import annotations

import logging

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from paper_mcp.auth import AuthError, Principal, anonymous_principal, verify_token
from paper_mcp.config import settings
from paper_mcp.quota import QuotaExceededError, quota_store

logger = logging.getLogger(__name__)

# Paths that must work without a token.
_OPEN_PREFIXES = ("/health", "/a/")


def _client_ip(request: Request) -> str:
    # X-Forwarded-For is set by the proxy in front of a public deployment.
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


class AuthQuotaMiddleware(BaseHTTPMiddleware):
    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        path = request.url.path
        if any(path.startswith(prefix) for prefix in _OPEN_PREFIXES):
            return await call_next(request)

        cfg = settings()
        principal: Principal

        if cfg.auth_mode == "open":
            # Metering still applies, keyed by IP — otherwise a development
            # instance left exposed has no brakes at all.
            principal = anonymous_principal(_client_ip(request))
        else:
            header = request.headers.get("authorization", "")
            scheme, _, token = header.partition(" ")
            if scheme.lower() != "bearer" or not token:
                return _unauthorized("a bearer token is required")
            try:
                principal = verify_token(token)
            except AuthError:
                # Deliberately uniform: distinguishing expired from
                # wrong-audience from bad-signature tells an attacker which
                # knob to turn next. The reason is logged, not returned.
                return _unauthorized("token rejected")

        try:
            quota_store().consume(principal.subject_hash, "calls")
        except QuotaExceededError as exc:
            return _too_many(exc)

        request.state.principal = principal
        return await call_next(request)


def _unauthorized(detail: str) -> JSONResponse:
    return JSONResponse(
        {"error": "unauthorized", "detail": detail},
        status_code=401,
        # RFC 6750: tell a compliant client how to authenticate.
        headers={"WWW-Authenticate": 'Bearer realm="paper-mcp"'},
    )


def _too_many(exc: QuotaExceededError) -> JSONResponse:
    return JSONResponse(
        {
            "error": "quota_exceeded",
            "detail": str(exc),
            "resource": exc.resource,
            "retry_after": round(exc.retry_after, 1),
        },
        status_code=429,
        headers={"Retry-After": str(max(1, int(exc.retry_after)))},
    )
