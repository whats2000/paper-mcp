from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from paper_mcp.config import settings
from paper_mcp.server import build_mcp_server, create_app, transport_security

EXPECTED_TOOLS = {"extract_pdf", "get_job"}


@pytest.fixture
def _allow_testserver(monkeypatch: pytest.MonkeyPatch) -> None:
    """TestClient sends `Host: testserver`, which DNS-rebinding protection
    rejects by default — exactly as it would reject an unlisted public
    hostname. Allowlist it the same way an operator allowlists their domain.
    """
    monkeypatch.setenv("PAPER_MCP_ALLOWED_HOSTS", "testserver")


def test_health_reports_ok_and_version(_allow_testserver: None) -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


async def test_the_extraction_tools_are_registered() -> None:
    server = build_mcp_server()

    names = {tool.name for tool in await server.list_tools()}

    assert names >= EXPECTED_TOOLS


async def test_every_tool_declares_its_scope_in_its_description() -> None:
    # SRS NFR-05: a caller must be able to see what a tool reaches.
    server = build_mcp_server()

    for tool in await server.list_tools():
        assert tool.description, f"{tool.name} has no description"
        assert "scope" in tool.description.lower(), (
            f"{tool.name} does not declare its network scope"
        )


async def test_tool_input_schemas_are_generated_from_the_signatures() -> None:
    server = build_mcp_server()
    by_name = {tool.name: tool for tool in await server.list_tools()}

    extract = by_name["extract_pdf"].input_schema
    assert "content_base64" in extract["properties"]
    # filename is a label with a default, so only the bytes are required.
    assert extract["required"] == ["content_base64"]

    job = by_name["get_job"].input_schema
    assert job["required"] == ["job_id"]


def test_unlisted_host_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    # DNS-rebinding protection is on by default; `testserver` is not allowed.
    monkeypatch.setenv("PAPER_MCP_ALLOWED_HOSTS", "paper-mcp.example.org")

    with TestClient(create_app()) as client:
        resp = client.post(
            "/mcp",
            headers={"Accept": "application/json, text/event-stream"},
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    assert resp.status_code == 421


def test_wildcard_disables_rebinding_protection(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PAPER_MCP_ALLOWED_HOSTS", "*")

    assert transport_security(settings()).enable_dns_rebinding_protection is False


def test_mcp_endpoint_answers_at_slash_mcp_without_redirecting(
    _allow_testserver: None,
) -> None:
    # The URL an operator pastes into a connector is `https://host/mcp`. If
    # that 307s to `/mcp/`, a client that does not follow redirects sees a
    # broken server. Assert the direct hit, with redirects disabled so a
    # regression cannot hide behind the test client following them.
    with TestClient(create_app()) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
            follow_redirects=False,
        )

    assert resp.status_code == 200, f"expected a direct 200, got {resp.status_code}"


def test_mcp_endpoint_is_mounted_and_answers_tools_list(_allow_testserver: None) -> None:
    with TestClient(create_app()) as client:
        resp = client.post(
            "/mcp",
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json, text/event-stream",
            },
            json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        )

    assert resp.status_code == 200
    names = {tool["name"] for tool in resp.json()["result"]["tools"]}
    assert names >= EXPECTED_TOOLS


@pytest.mark.parametrize(
    ("configured", "expected"),
    [
        ("INFO", "info"),
        ("WARNING", "warning"),
        ("  Debug  ", "debug"),
        ("critical", "critical"),
        ("nonsense", "info"),
        ("", "info"),
    ],
)
def test_log_level_is_translated_for_uvicorn(configured: str, expected: str) -> None:
    """uvicorn builds its own logging config and ignores basicConfig.

    Without handing it the level explicitly, PAPER_MCP_LOG_LEVEL did nothing:
    measured 18076 bytes over 300 requests at WARNING versus 18222 at INFO.
    An unknown value must fall back rather than crash the process at startup.
    """
    from paper_mcp.server import uvicorn_log_level

    assert uvicorn_log_level(configured) == expected


@pytest.mark.parametrize(
    ("configured", "expected_name"),
    [
        ("INFO", "INFO"),
        ("info", "INFO"),
        ("  warning ", "WARNING"),
        ("DEBUG", "DEBUG"),
        ("nonsense", "INFO"),
    ],
)
def test_stdlib_log_level_accepts_lowercase(configured: str, expected_name: str) -> None:
    """A lower-case level must not kill the process at boot.

    logging.basicConfig raises ValueError: Unknown level: 'info' on anything
    that is not upper-case, and it does so before any log line can explain
    why. This project's own docker-compose.yml wrote `warning`, so the
    container would have crashed on first boot; the real-workflow check
    caught it, the unit suite did not.
    """
    import logging as _logging

    from paper_mcp.server import stdlib_log_level

    assert stdlib_log_level(configured) == getattr(_logging, expected_name)
