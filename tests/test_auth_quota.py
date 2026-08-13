"""Auth and quota, exercised through the real HTTP surface.

Tokens are signed with a locally generated RSA key and the JWKS is served by
a mocked issuer, so this covers the actual verification path rather than a
stubbed `verify_token`.
"""
from __future__ import annotations

import json
import time
from typing import Any

import httpx
import jwt
import pytest
import respx
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient

from paper_mcp import auth as auth_mod
from paper_mcp.quota import QuotaExceededError, QuotaLimits, QuotaStore, reset_quota_store
from paper_mcp.server import create_app

_ISSUER = "https://idp.example.org"
_AUDIENCE = "paper-mcp"
_JWKS_URL = f"{_ISSUER}/.well-known/jwks.json"
_KID = "test-key-1"


@pytest.fixture(scope="module")
def keypair() -> Any:
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _jwks(key: Any) -> dict[str, Any]:
    public_jwk = json.loads(jwt.algorithms.RSAAlgorithm.to_jwk(key.public_key()))
    public_jwk.update({"kid": _KID, "use": "sig", "alg": "RS256"})
    return {"keys": [public_jwk]}


def _token(key: Any, **overrides: Any) -> str:
    now = int(time.time())
    claims = {
        "iss": _ISSUER,
        "aud": _AUDIENCE,
        "sub": "user-123",
        "iat": now,
        "exp": now + 300,
    }
    claims.update(overrides)
    return jwt.encode(claims, key, algorithm="RS256", headers={"kid": _KID})


@pytest.fixture
def secured(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_MCP_AUTH_MODE", "oidc")
    monkeypatch.setenv("PAPER_MCP_OIDC_ISSUER", _ISSUER)
    monkeypatch.setenv("PAPER_MCP_OIDC_AUDIENCE", _AUDIENCE)
    monkeypatch.setenv("PAPER_MCP_ALLOWED_HOSTS", "testserver")
    monkeypatch.setenv("PAPER_MCP_SUBJECT_SALT", "test-salt")
    auth_mod.reset_jwks_cache()
    reset_quota_store()


def _post(client: TestClient, token: str | None = None) -> httpx.Response:
    headers = {"Accept": "application/json, text/event-stream"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return client.post(
        "/mcp", headers=headers, json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
    )


# --- token verification ---------------------------------------------------


@respx.mock
def test_a_valid_token_is_accepted(secured: None, keypair: Any) -> None:
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))

    with TestClient(create_app()) as client:
        response = _post(client, _token(keypair))

    assert response.status_code == 200
    assert "tools" in response.json()["result"]


@respx.mock
def test_no_token_is_rejected(secured: None, keypair: Any) -> None:
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))

    with TestClient(create_app()) as client:
        response = _post(client)

    assert response.status_code == 401
    # RFC 6750: a compliant client needs to be told how to authenticate.
    assert "Bearer" in response.headers.get("www-authenticate", "")


@respx.mock
def test_a_token_for_another_audience_is_rejected(secured: None, keypair: Any) -> None:
    # The classic confused-deputy: a token minted for a different service.
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))

    with TestClient(create_app()) as client:
        response = _post(client, _token(keypair, aud="some-other-service"))

    assert response.status_code == 401


@respx.mock
def test_an_expired_token_is_rejected(secured: None, keypair: Any) -> None:
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))
    past = int(time.time()) - 60

    with TestClient(create_app()) as client:
        response = _post(client, _token(keypair, exp=past, iat=past - 300))

    assert response.status_code == 401


@respx.mock
def test_a_token_from_another_issuer_is_rejected(secured: None, keypair: Any) -> None:
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))

    with TestClient(create_app()) as client:
        response = _post(client, _token(keypair, iss="https://evil.example"))

    assert response.status_code == 401


@respx.mock
def test_a_token_signed_by_the_wrong_key_is_rejected(secured: None, keypair: Any) -> None:
    # The attack that matters most: a well-formed token someone else signed.
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))
    attacker = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    with TestClient(create_app()) as client:
        response = _post(client, _token(attacker))

    assert response.status_code == 401


@respx.mock
def test_rejections_do_not_reveal_which_check_failed(secured: None, keypair: Any) -> None:
    # Telling an attacker "expired" versus "wrong audience" tells them which
    # knob to turn next.
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))
    past = int(time.time()) - 60

    with TestClient(create_app()) as client:
        bodies = {
            _post(client, _token(keypair, exp=past, iat=past - 300)).text,
            _post(client, _token(keypair, aud="other")).text,
            _post(client, _token(keypair, iss="https://evil.example")).text,
        }

    assert len(bodies) == 1, f"responses differ and leak the reason: {bodies}"


@respx.mock
def test_a_garbage_token_is_rejected_not_crashed(secured: None, keypair: Any) -> None:
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))

    with TestClient(create_app()) as client:
        response = _post(client, "not.a.jwt")

    assert response.status_code == 401


# --- open paths -----------------------------------------------------------


@respx.mock
def test_health_stays_open(secured: None, keypair: Any) -> None:
    # A readiness probe that needs a token is useless to an orchestrator.
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))
    respx.get("http://127.0.0.1:8002/health").mock(return_value=httpx.Response(200))

    with TestClient(create_app()) as client:
        response = client.get("/health")

    assert response.status_code == 200


# --- quota ----------------------------------------------------------------


def test_a_bucket_refills_over_time() -> None:
    store = QuotaStore(QuotaLimits(calls_per_minute=60.0))

    for i in range(60):
        store.consume("subject", "calls", now=1000.0 + i * 0.001)
    with pytest.raises(QuotaExceededError):
        store.consume("subject", "calls", now=1000.1)

    # A minute later the bucket is full again.
    store.consume("subject", "calls", now=1060.0)


def test_exhaustion_reports_how_long_to_wait() -> None:
    store = QuotaStore(QuotaLimits(calls_per_minute=60.0))
    for _ in range(60):
        store.consume("s", "calls", now=500.0)

    with pytest.raises(QuotaExceededError) as exc:
        store.consume("s", "calls", now=500.0)

    assert exc.value.resource == "calls"
    assert 0 < exc.value.retry_after <= 2.0


def test_one_caller_cannot_starve_another() -> None:
    # The whole point: buckets are per subject.
    store = QuotaStore(QuotaLimits(calls_per_minute=10.0))
    for _ in range(10):
        store.consume("greedy", "calls", now=100.0)

    with pytest.raises(QuotaExceededError):
        store.consume("greedy", "calls", now=100.0)
    store.consume("polite", "calls", now=100.0)  # unaffected


def test_expensive_resources_are_metered_separately() -> None:
    # Burning the call budget must not block an extraction, and vice versa.
    store = QuotaStore(QuotaLimits(calls_per_minute=1.0, extractions_per_hour=1.0))
    store.consume("s", "calls", now=0.0)
    store.consume("s", "extractions", now=0.0)

    with pytest.raises(QuotaExceededError):
        store.consume("s", "calls", now=0.0)
    with pytest.raises(QuotaExceededError):
        store.consume("s", "extractions", now=0.0)


def test_compile_seconds_are_charged_by_duration() -> None:
    # One pathological document costs more than ten ordinary ones, so the
    # meter is time rather than call count.
    store = QuotaStore(QuotaLimits(compile_seconds_per_hour=100.0))

    store.consume("s", "compile_seconds", amount=90.0, now=0.0)

    with pytest.raises(QuotaExceededError):
        store.consume("s", "compile_seconds", amount=20.0, now=0.0)


@respx.mock
def test_over_quota_returns_429_with_retry_after(secured: None, keypair: Any) -> None:
    respx.get(_JWKS_URL).mock(return_value=httpx.Response(200, json=_jwks(keypair)))
    import paper_mcp.quota as quota_mod

    quota_mod._store = QuotaStore(QuotaLimits(calls_per_minute=2.0))
    token = _token(keypair)

    with TestClient(create_app()) as client:
        statuses = [_post(client, token).status_code for _ in range(4)]
        final = _post(client, token)

    assert 429 in statuses or final.status_code == 429
    if final.status_code == 429:
        assert final.headers.get("retry-after")
        assert final.json()["error"] == "quota_exceeded"


def test_open_mode_still_meters_by_ip(monkeypatch: pytest.MonkeyPatch) -> None:
    # A development instance left exposed should still have brakes.
    monkeypatch.setenv("PAPER_MCP_AUTH_MODE", "open")
    monkeypatch.setenv("PAPER_MCP_ALLOWED_HOSTS", "testserver")
    import paper_mcp.quota as quota_mod

    quota_mod._store = QuotaStore(QuotaLimits(calls_per_minute=2.0))

    with TestClient(create_app()) as client:
        statuses = [_post(client).status_code for _ in range(5)]

    assert 429 in statuses


def test_subject_hash_does_not_expose_the_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    # Metering needs a stable key, not a record of who read what.
    monkeypatch.setenv("PAPER_MCP_SUBJECT_SALT", "salt")
    digest = auth_mod.subject_hash("user@example.com")

    assert "user@example.com" not in digest
    assert digest == auth_mod.subject_hash("user@example.com")
    assert digest != auth_mod.subject_hash("other@example.com")
