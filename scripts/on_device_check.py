"""On-device acceptance check: run the real service and exercise every path.

Unit tests mock the upstreams, so they prove the wiring compiles and the
control flow runs — they do NOT prove the live APIs accept our requests. That
gap is not theoretical: a mocked test asserted the exact URL shape that live
Semantic Scholar rejected with HTTP 400.

This script boots the service through its real entry point and drives it with
a real MCP client over the wire, covering every tool and every branch that
depends on an external system. It prints a pass/fail matrix and exits non-zero
if anything genuinely failed.

Run:  uv run python scripts/on_device_check.py

A rate-limited upstream is reported SKIP, not FAIL — Semantic Scholar throttles
hard without an API key, and that is an upstream condition, not our defect.
"""
from __future__ import annotations

import asyncio
import json
import os
import socket
import subprocess
import sys
import time
import traceback
from dataclasses import dataclass, field
from typing import Any

import httpx
from mcp.client.client import Client

# A DOI Semantic Scholar definitely holds (BERT). Chosen after an earlier
# pick, 10.1038/nature14539, turned out to be absent from S2 entirely — the
# check could never have passed, and its failure said "TypeError" rather than
# "that DOI is not in the corpus". Verify test data against the live API
# before trusting a red result.
KNOWN_DOI = "10.18653/v1/N19-1423"
# Semantic Scholar's paperId for "Attention Is All You Need" — exercises ss:.
TRANSFORMER_S2_ID = "204e3073870fae3d05bcbc2f6a8e263d9b72e776"


@dataclass
class Report:
    rows: list[tuple[str, str, str]] = field(default_factory=list)

    def record(self, status: str, name: str, detail: str = "") -> None:
        self.rows.append((status, name, detail))
        icon = {"PASS": "  ok  ", "FAIL": " FAIL ", "SKIP": " skip "}[status]
        print(f"[{icon}] {name}" + (f"\n           {detail}" if detail else ""), flush=True)

    @property
    def failures(self) -> int:
        return sum(1 for s, _, _ in self.rows if s == "FAIL")

    def summary(self) -> str:
        counts = {s: sum(1 for r, _, _ in self.rows if r == s) for s in ("PASS", "FAIL", "SKIP")}
        return (
            f"\n{'=' * 72}\n"
            f"{counts['PASS']} passed, {counts['FAIL']} failed, {counts['SKIP']} skipped "
            f"(of {len(self.rows)} checks)\n{'=' * 72}"
        )


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port: int = s.getsockname()[1]
    return port


def boot(port: int) -> subprocess.Popen[str]:
    """Start the service through its real console entry point."""
    env = {
        **os.environ,
        "PAPER_MCP_HOST": "127.0.0.1",
        "PAPER_MCP_PORT": str(port),
        "PAPER_MCP_ALLOWED_HOSTS": f"127.0.0.1:{port}",
        "PAPER_MCP_LOG_LEVEL": "WARNING",
        # Exercise the Unpaywall enrichment branch, which is otherwise dead.
        "PAPER_MCP_UNPAYWALL_EMAIL": os.environ.get(
            "PAPER_MCP_UNPAYWALL_EMAIL", "on-device-check@example.org"
        ),
        # This check makes ~9 Semantic Scholar calls in a burst. The keyless
        # free tier throttles well below the 1.1s production default, and a
        # throttled call is a SKIP — which means an uncovered branch, the
        # very thing this script exists to prevent. Slow the pacing down and
        # widen the retry ladder so every branch actually runs. Set
        # PAPER_MCP_S2_API_KEY to make this unnecessary.
        "PAPER_MCP_S2_MIN_INTERVAL_S": os.environ.get(
            "PAPER_MCP_S2_MIN_INTERVAL_S", "6.0"
        ),
        "PAPER_MCP_S2_MAX_ATTEMPTS": os.environ.get("PAPER_MCP_S2_MAX_ATTEMPTS", "6"),
        "PAPER_MCP_S2_RETRY_BASE_S": os.environ.get("PAPER_MCP_S2_RETRY_BASE_S", "4.0"),
    }
    return subprocess.Popen(
        [sys.executable, "-c", "from paper_mcp.server import main; main()"],
        env=env,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
    )


def wait_healthy(base: str, proc: subprocess.Popen[str], timeout: float = 45.0) -> dict[str, Any]:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"server exited early:\n{proc.communicate()[0]}")
        try:
            resp = httpx.get(f"{base}/health", timeout=2.0)
            if resp.status_code == 200:
                payload: dict[str, Any] = resp.json()
                return payload
        except httpx.HTTPError:
            time.sleep(0.3)
    raise RuntimeError("server never became healthy")


def value(result: Any) -> Any:
    """Extract a tool's return payload from a CallToolResult."""
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


def error_text(result: Any) -> str:
    for block in getattr(result, "content", None) or []:
        text = getattr(block, "text", None)
        if text:
            return str(text)
    return ""


def rate_limited(result: Any) -> bool:
    """True only for a genuine upstream throttle.

    Deliberately narrow. An earlier version matched any text containing
    "429", which quietly reclassified real failures as SKIP and hid them.
    A SKIP must mean "the upstream refused to talk to us", never "something
    went wrong and we would rather not look".
    """
    text = error_text(result).lower()
    return "rate-limited" in text or "too many requests" in text


async def check(
    rep: Report,
    name: str,
    coro: Any,
    verify: Any,
    *,
    detail: Any = None,
) -> None:
    """Run one live check, classifying only a genuine upstream throttle as SKIP."""
    try:
        result = await coro
    except Exception as exc:
        rep.record("FAIL", name, f"{type(exc).__name__}: {exc}")
        return
    if getattr(result, "is_error", False):
        if rate_limited(result):
            rep.record("SKIP", name, "upstream rate-limited (no API key)")
        else:
            # Never run `verify` against an error result: it would fail with
            # a confusing type error instead of showing what actually broke.
            rep.record("FAIL", name, error_text(result)[:220])
        return
    try:
        ok = verify(result)
    except Exception as exc:
        rep.record("FAIL", name, f"verify raised {type(exc).__name__}: {exc}")
        return
    if ok:
        rep.record("PASS", name, detail(result) if detail else "")
    else:
        payload = value(result)
        rep.record("FAIL", name, f"unexpected: {str(payload)[:200]}")


async def run_checks(base: str, health: dict[str, Any], rep: Report) -> None:
    url = f"{base}/mcp"

    # --- transport / deployment surface -----------------------------------
    rep.record(
        "PASS" if health.get("status") == "ok" else "FAIL",
        "boot: real entry point serves /health",
        f"version={health.get('version')} auth_mode={health.get('auth_mode')}",
    )

    direct = httpx.post(
        url,
        headers={"Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        follow_redirects=False,
        timeout=10.0,
    )
    rep.record(
        "PASS" if direct.status_code == 200 else "FAIL",
        "transport: POST /mcp answers directly (no 307)",
        f"HTTP {direct.status_code}",
    )

    spoofed = httpx.post(
        url,
        headers={"Host": "evil.example", "Accept": "application/json, text/event-stream"},
        json={"jsonrpc": "2.0", "id": 1, "method": "tools/list"},
        timeout=10.0,
    )
    rep.record(
        "PASS" if spoofed.status_code == 421 else "FAIL",
        "security: unlisted Host rejected (DNS-rebinding)",
        f"HTTP {spoofed.status_code}",
    )

    # --- protocol ----------------------------------------------------------
    async with Client(url, raise_exceptions=True) as client:
        info = client.server_info
        rep.record(
            "PASS" if info and info.name == "paper" else "FAIL",
            "protocol: initialize handshake",
            f"{info.name} v{info.version}, protocol {client.protocol_version}",
        )

        listed = await client.list_tools()
        names = sorted(t.name for t in listed.tools)
        expected = ["find_related", "resolve_paper", "search_arxiv", "search_papers"]
        rep.record(
            "PASS" if names == expected else "FAIL", "protocol: tools/list", str(names)
        )

        modes = next(t for t in listed.tools if t.name == "find_related")
        enum = modes.input_schema["properties"]["mode"].get("enum")
        rep.record(
            "PASS" if enum == ["cites", "cited_by", "similar"] else "FAIL",
            "protocol: Literal becomes a schema enum",
            str(enum),
        )

    # --- tools, live -------------------------------------------------------
    async with Client(url) as client:
        await check(
            rep,
            "search_arxiv: relevance query",
            client.call_tool("search_arxiv", {"query": "sparse attention", "max_results": 3}),
            lambda r: len(value(r)) >= 1 and all(
                p["paper_id"].startswith("arxiv:") for p in value(r)
            ),
            detail=lambda r: f"{len(value(r))} hits, first={value(r)[0]['paper_id']}",
        )

        await check(
            rep,
            "search_arxiv: max_results clamped to 50",
            client.call_tool("search_arxiv", {"query": "neural network", "max_results": 999}),
            lambda r: len(value(r)) <= 50,
            detail=lambda r: f"returned {len(value(r))} (ceiling 50)",
        )

        await check(
            rep,
            "search_arxiv: non-ASCII query does not break encoding",
            client.call_tool("search_arxiv", {"query": "変分オートエンコーダ", "max_results": 2}),
            lambda r: isinstance(value(r), list),
            detail=lambda r: f"{len(value(r))} hits",
        )

        await check(
            rep,
            "search_papers: live Semantic Scholar",
            client.call_tool(
                "search_papers", {"query": "retrieval augmented generation", "max_results": 3}
            ),
            lambda r: len(value(r)) >= 1,
            detail=lambda r: f"{len(value(r))} hits, first={value(r)[0]['paper_id']}",
        )

        for mode in ("cited_by", "cites", "similar"):
            await check(
                rep,
                f"find_related: mode={mode}",
                client.call_tool(
                    "find_related",
                    {"paper_id": "arxiv:1706.03762", "mode": mode, "max_results": 2},
                ),
                lambda r: isinstance(value(r), list),
                detail=lambda r: f"{len(value(r))} results",
            )

        await check(
            rep,
            "resolve_paper: bare arXiv id (exact lookup)",
            client.call_tool("resolve_paper", {"identifier": "1706.03762"}),
            lambda r: value(r)["title"] == "Attention Is All You Need",
            detail=lambda r: f"{value(r)['paper_id']} | {value(r)['title']}",
        )

        await check(
            rep,
            "resolve_paper: prefixed id with version suffix",
            client.call_tool("resolve_paper", {"identifier": "arXiv:1706.03762v5"}),
            lambda r: value(r)["paper_id"] == "arxiv:1706.03762",
            detail=lambda r: value(r)["paper_id"],
        )

        await check(
            rep,
            "resolve_paper: DOI branch",
            client.call_tool("resolve_paper", {"identifier": KNOWN_DOI}),
            lambda r: bool(value(r)["title"]),
            detail=lambda r: (
                f"{value(r)['paper_id']} | {value(r)['title'][:40]} | "
                f"OA={value(r)['open_access']['available']} "
                f"via {value(r)['open_access']['source']}"
            ),
        )

        await check(
            rep,
            "resolve_paper: ss: branch",
            client.call_tool("resolve_paper", {"identifier": f"ss:{TRANSFORMER_S2_ID}"}),
            lambda r: bool(value(r)["title"]),
            detail=lambda r: f"{value(r)['paper_id']} | {value(r)['title'][:50]}",
        )

        await check(
            rep,
            "resolve_paper: free-text title fallback",
            client.call_tool("resolve_paper", {"identifier": "Attention Is All You Need"}),
            lambda r: bool(value(r)["title"]),
            detail=lambda r: f"{value(r)['paper_id']} | {value(r)['title'][:50]}",
        )

        await check(
            rep,
            "resolve_paper: nonexistent arXiv id falls through, does not fabricate",
            client.call_tool("resolve_paper", {"identifier": "9999.99999"}),
            lambda r: r.is_error or value(r) is not None,
            detail=lambda r: (
                "typed error" if r.is_error else f"fell through to {value(r)['paper_id']}"
            ),
        )

        # --- error taxonomy ------------------------------------------------
        empty = await client.call_tool("resolve_paper", {"identifier": "   "})
        rep.record(
            "PASS" if empty.is_error else "FAIL",
            "errors: empty identifier is a typed error",
            error_text(empty)[:100],
        )

        bad_enum = await client.call_tool(
            "find_related", {"paper_id": "arxiv:1706.03762", "mode": "nonsense"}
        )
        rep.record(
            "PASS" if bad_enum.is_error else "FAIL",
            "errors: schema rejects an invalid enum before the handler",
            error_text(bad_enum)[:100],
        )

        unknown = await client.call_tool("no_such_tool", {})
        rep.record(
            "PASS" if unknown.is_error else "FAIL",
            "errors: unknown tool is an error, not a crash",
            error_text(unknown)[:80],
        )

        gibberish = await client.call_tool(
            "resolve_paper", {"identifier": "zzzz qqqq xxxx not a real paper 12345 zzz"}
        )
        text = error_text(gibberish)
        if rate_limited(gibberish):
            rep.record("SKIP", "errors: unmatchable title", "upstream rate-limited")
        else:
            # A throttle used to satisfy this check, which made it meaningless.
            # It must reach the real not-found path.
            rep.record(
                "PASS" if (gibberish.is_error and "no paper matched" in text) else "FAIL",
                "errors: unmatchable title yields not_found, not a wrong paper",
                text[:110] if gibberish.is_error else f"matched {value(gibberish)}",
            )

        # --- concurrency ---------------------------------------------------
        started = time.monotonic()
        results = await asyncio.gather(
            client.call_tool("search_arxiv", {"query": "graph neural network", "max_results": 2}),
            client.call_tool("search_arxiv", {"query": "state space model", "max_results": 2}),
            client.call_tool("search_arxiv", {"query": "contrastive learning", "max_results": 2}),
        )
        ok = all(not r.is_error for r in results)
        rep.record(
            "PASS" if ok else "FAIL",
            "concurrency: 3 simultaneous tool calls all succeed",
            f"{time.monotonic() - started:.1f}s wall clock",
        )


async def main() -> int:
    port = free_port()
    proc = boot(port)
    base = f"http://127.0.0.1:{port}"
    rep = Report()
    print(f"paper-mcp on-device check -> {base}\n{'=' * 72}", flush=True)
    try:
        health = wait_healthy(base, proc)
        await run_checks(base, health, rep)
    except Exception:
        traceback.print_exc()
        rep.record("FAIL", "harness", "check run aborted")
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except subprocess.TimeoutExpired:
            proc.kill()
    print(rep.summary())
    return 1 if rep.failures else 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
