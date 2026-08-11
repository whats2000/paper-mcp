"""End-to-end tests driving a real server with a real MCP client.

These are the tests that catch what unit tests structurally cannot: the
`initialize` handshake, transport framing, redirect behaviour on the exact
URL an operator configures, and JSON-schema validation applied by the
protocol layer rather than by Python.

A live-server test found the trailing-slash redirect that the in-process
`TestClient` hid (it follows redirects), so this suite exists as a standing
guard, not a one-off.

Opt-in: `uv run pytest -m integration`. The `network` subset additionally
reaches real arXiv / Semantic Scholar and is therefore allowed to be slow
and, occasionally, upstream-flaky.
"""
from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import time
from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from mcp.client.client import Client

pytestmark = pytest.mark.integration

_STARTUP_TIMEOUT_S = 45.0


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


@pytest.fixture(scope="module")
def live_server() -> Iterator[str]:
    """Spawn a real `paper-mcp` process and yield its MCP URL.

    Spawned as a subprocess rather than an in-process ASGI app on purpose:
    the entry point, the uvicorn server, the host allowlist, and the mount
    path are all part of what is under test.
    """
    port = _free_port()
    env = {
        **os.environ,
        "PAPER_MCP_HOST": "127.0.0.1",
        "PAPER_MCP_PORT": str(port),
        "PAPER_MCP_ALLOWED_HOSTS": f"127.0.0.1:{port}",
        "PAPER_MCP_LOG_LEVEL": "WARNING",
    }
    proc = subprocess.Popen(
        [sys.executable, "-c", "from paper_mcp.server import main; main()"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )
    base = f"http://127.0.0.1:{port}"
    try:
        deadline = time.monotonic() + _STARTUP_TIMEOUT_S
        while time.monotonic() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"server exited early:\n{proc.communicate()[0]}")
            try:
                if httpx.get(f"{base}/health", timeout=2.0).status_code == 200:
                    break
            except httpx.HTTPError:
                time.sleep(0.3)
        else:
            raise RuntimeError("server did not become healthy in time")
        yield f"{base}/mcp"
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:  # pragma: no cover — defensive
            proc.kill()


def _payload(result: Any) -> Any:
    """Pull the tool's return value out of an MCP CallToolResult.

    Fails loudly on an error result: an assertion against a stringified
    error message is a confusing way to learn that a tool call failed.
    """
    assert result.is_error is not True, f"tool call failed: {result.content}"
    structured = getattr(result, "structured_content", None)
    if isinstance(structured, dict):
        return structured.get("result", structured)
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
    return None


async def test_client_completes_the_initialize_handshake(live_server: str) -> None:
    async with Client(live_server, raise_exceptions=True) as client:
        info = client.server_info

    assert info is not None
    assert info.name == "paper"
    assert info.version == "0.1.0"


async def test_client_lists_all_four_tools_with_scoped_descriptions(
    live_server: str,
) -> None:
    async with Client(live_server, raise_exceptions=True) as client:
        listed = await client.list_tools()

    by_name = {t.name: t for t in listed.tools}
    assert set(by_name) >= {
        "search_arxiv",
        "search_papers",
        "find_related",
        "resolve_paper",
    }
    for tool in by_name.values():
        assert tool.description and "scope" in tool.description.lower()


async def test_protocol_layer_rejects_an_invalid_enum_value(live_server: str) -> None:
    # `mode` is a Literal, so the generated schema constrains it. This must
    # be refused before any handler runs — proof the schema is real and not
    # decorative.
    async with Client(live_server) as client:
        result = await client.call_tool(
            "find_related", {"paper_id": "arxiv:1706.03762", "mode": "nonsense"}
        )

    assert result.is_error is True


async def test_a_tool_error_comes_back_as_an_error_not_a_crash(
    live_server: str,
) -> None:
    async with Client(live_server) as client:
        result = await client.call_tool("resolve_paper", {"identifier": "   "})

    assert result.is_error is True


@pytest.mark.network
async def test_search_arxiv_returns_real_papers(live_server: str) -> None:
    async with Client(live_server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "search_arxiv", {"query": "diffusion transformer", "max_results": 2}
        )

    papers = _payload(result)
    assert isinstance(papers, list) and papers
    for paper in papers:
        assert paper["paper_id"].startswith("arxiv:")
        assert paper["title"]
        assert paper["open_access"]["available"] is True


@pytest.mark.network
async def test_resolve_paper_finds_the_exact_paper_by_arxiv_id(
    live_server: str,
) -> None:
    async with Client(live_server, raise_exceptions=True) as client:
        result = await client.call_tool("resolve_paper", {"identifier": "1706.03762"})

    paper = _payload(result)
    assert paper["paper_id"] == "arxiv:1706.03762"
    assert paper["title"] == "Attention Is All You Need"
    assert paper["year"] == 2017


@pytest.mark.network
async def test_find_related_walks_the_citation_graph(live_server: str) -> None:
    async with Client(live_server, raise_exceptions=True) as client:
        result = await client.call_tool(
            "find_related",
            {"paper_id": "arxiv:1706.03762", "mode": "cited_by", "max_results": 3},
        )

    papers = _payload(result)
    assert isinstance(papers, list) and papers
    assert all(p["title"] for p in papers)
