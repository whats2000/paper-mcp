"""OIDC resource-server token verification.

This service validates tokens; it never issues them. Owning issuance,
refresh, revocation and consent is a security-critical subsystem far from
this project's competence, and an IdP the operator already trusts does it
better (SRS §II-4).

Identity exists for **quota and revocation only**. There is no per-user data
to scope — statelessness removed it — so every authenticated caller can reach
every tool. What gets logged and metered is `HMAC(salt, sub)`, not `sub`:
metering needs a stable key, not a record of which person read which paper.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import os
import time
from dataclasses import dataclass
from typing import Any

import httpx
import jwt

from paper_mcp.config import settings

logger = logging.getLogger(__name__)

_JWKS_TTL_SECONDS = 3600.0
# A `kid` we have never seen triggers one refetch, because keys rotate and a
# stale cache would lock every caller out. That refetch is rate-limited so an
# attacker cannot turn unknown `kid`s into a battering ram against the IdP.
_MIN_REFETCH_INTERVAL = 30.0

_jwks_cache: dict[str, Any] | None = None
_jwks_fetched_at = 0.0
_last_refetch_attempt = 0.0


class AuthError(Exception):
    """Token rejected. The reason is logged, never returned.

    Distinguishing "expired" from "wrong audience" from "bad signature" tells
    an attacker which knob to turn next.
    """


@dataclass(frozen=True)
class Principal:
    subject: str
    subject_hash: str
    anonymous: bool = False


def _salt() -> bytes:
    configured = os.environ.get("PAPER_MCP_SUBJECT_SALT")
    if configured:
        return configured.encode("utf-8")
    # Per-process fallback: metering still works within a process, and the
    # hash never leaves it in a form anyone can correlate across restarts.
    return hashlib.sha256(b"paper-mcp-ephemeral-salt").digest()


def subject_hash(subject: str) -> str:
    return hmac.new(_salt(), subject.encode("utf-8"), hashlib.sha256).hexdigest()[:32]


def anonymous_principal(client_ip: str) -> Principal:
    """Identity for open mode, so per-IP metering still has a key."""
    return Principal(
        subject=f"ip:{client_ip}", subject_hash=subject_hash(f"ip:{client_ip}"), anonymous=True
    )


def reset_jwks_cache() -> None:
    global _jwks_cache, _jwks_fetched_at, _last_refetch_attempt
    _jwks_cache = None
    _jwks_fetched_at = 0.0
    _last_refetch_attempt = 0.0


def _jwks_url(issuer: str) -> str:
    return f"{issuer.rstrip('/')}/.well-known/jwks.json"


def _fetch_jwks(issuer: str) -> dict[str, Any]:
    """Fetch the issuer's key set over httpx.

    Deliberately not `PyJWKClient`, which fetches with `urllib`: every other
    outbound call here is httpx, and a second HTTP stack means different
    timeout and proxy behaviour, and nothing respx or a test can observe.
    """
    response = httpx.get(_jwks_url(issuer), timeout=httpx.Timeout(10.0))
    response.raise_for_status()
    payload: dict[str, Any] = response.json()
    return payload


def _signing_key(token: str, issuer: str) -> Any:
    """Resolve the key that signed `token`, refetching once on an unknown kid."""
    global _jwks_cache, _jwks_fetched_at, _last_refetch_attempt

    try:
        kid = jwt.get_unverified_header(token).get("kid")
    except jwt.PyJWTError as exc:
        raise AuthError("malformed token header") from exc

    now = time.monotonic()
    if _jwks_cache is None or now - _jwks_fetched_at > _JWKS_TTL_SECONDS:
        _jwks_cache = _fetch_jwks(issuer)
        _jwks_fetched_at = now

    key = _match_kid(_jwks_cache, kid)
    if key is not None:
        return key

    # Keys rotate; a stale cache must not lock every caller out. One refetch,
    # rate-limited so unknown kids cannot be turned into a battering ram
    # against the IdP.
    if now - _last_refetch_attempt < _MIN_REFETCH_INTERVAL:
        raise AuthError("unknown signing key")
    _last_refetch_attempt = now
    _jwks_cache = _fetch_jwks(issuer)
    _jwks_fetched_at = now
    key = _match_kid(_jwks_cache, kid)
    if key is None:
        raise AuthError("unknown signing key")
    return key


def _match_kid(jwks: dict[str, Any], kid: str | None) -> Any:
    for entry in jwks.get("keys", []):
        if kid is None or entry.get("kid") == kid:
            return jwt.PyJWK(entry).key
    return None


def verify_token(
    token: str, *, issuer: str | None = None, audience: str | None = None
) -> Principal:
    """Verify a bearer JWT and return the caller's principal."""
    cfg = settings()
    issuer = issuer or cfg.oidc_issuer
    audience = audience or cfg.oidc_audience
    if not issuer or not audience:
        raise AuthError("OIDC issuer/audience are not configured")

    try:
        signing_key = _signing_key(token, issuer)
    except AuthError:
        raise
    except (httpx.HTTPError, jwt.PyJWTError, ValueError, KeyError) as exc:
        logger.info("jwks lookup failed: %s", type(exc).__name__)
        raise AuthError("token rejected") from exc

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            signing_key,
            algorithms=["RS256", "RS512", "ES256", "ES384"],
            audience=audience,
            issuer=issuer,
            options={"require": ["exp", "iss", "aud", "sub"]},
        )
    except jwt.PyJWTError as exc:
        # Logged with the reason, returned without it.
        logger.info("token rejected: %s", type(exc).__name__)
        raise AuthError("token rejected") from exc

    subject = str(claims["sub"])
    return Principal(subject=subject, subject_hash=subject_hash(subject))
