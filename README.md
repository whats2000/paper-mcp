# paper-mcp

A stateless MCP service that turns papers into agent-ready data and compiles LaTeX.

Built for external agent clients (Claude Cowork, Claude Desktop, Cursor, any MCP
framework). It supplies what those clients lack — paper acquisition, faithful
extraction, and a working LaTeX toolchain — and nothing else: **no accounts, no
stored user data, no server-side LLM calls.**

That last part is the security architecture, not an omission. With no per-user
state, a shared public endpoint has nothing to leak between callers, and
identity degrades to a quota key.

## Status

**Phase A complete** — the four discovery tools are live over MCP.

| Tool | Phase | Status |
| --- | --- | --- |
| `search_arxiv` · `search_papers` · `find_related` · `resolve_paper` | A | ✅ shipped |
| `fetch_paper` · `get_section` · `get_job` | B | planned |
| `compile_latex` (sandboxed) | C | planned |
| OIDC auth + quota | D | planned |
| Portable skills + container | E | planned |

## Development

```bash
uv sync
uv run pytest                    # fast, offline (integration excluded)
uv run pytest -m integration     # spawns a real server, drives it with a real MCP client
uv run ruff check src tests
uv run mypy src
uv run paper-mcp
```

The integration suite is the one that matters for protocol correctness. It
spawns an actual `paper-mcp` process and connects with the MCP client library,
so it exercises the `initialize` handshake, transport framing, and the exact
URL an operator pastes into a connector. It has already caught two bugs the
in-process tests structurally could not see: a `307` redirect on `POST /mcp`
(the in-process test client follows redirects), and a Semantic Scholar field
name accepted by one endpoint and rejected by another.

Verify it end to end:

```bash
curl -s http://127.0.0.1:8000/health
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```

## Configuration

Environment only (twelve-factor). Nothing is read from a config file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PAPER_MCP_HOST` / `PAPER_MCP_PORT` | `0.0.0.0` / `8000` | Bind address |
| `PAPER_MCP_ALLOWED_HOSTS` | localhost only | **DNS-rebinding protection.** A public deployment must list its own hostname or every request gets `421`. `*` disables the check and is warned about at boot |
| `PAPER_MCP_ALLOWED_ORIGINS` | derived from allowed hosts | CORS origins for browser clients |
| `PAPER_MCP_AUTH_MODE` | `open` | `open` disables authentication — development only |
| `PAPER_MCP_UNPAYWALL_EMAIL` | unset | Contact email enabling Unpaywall open-access lookup in `resolve_paper` |
| `PAPER_MCP_S2_API_KEY` | unset | Semantic Scholar key; raises the rate limit |
| `PAPER_MCP_LOG_LEVEL` | `INFO` | Log level |

## Documentation

- **Spec:** [docs/superpowers/specs/2026-08-11-paper-mcp-srs.md](docs/superpowers/specs/2026-08-11-paper-mcp-srs.md) — the single authoritative specification
- **Plans:** [docs/superpowers/plans/](docs/superpowers/plans/)

## Provenance

The extraction and LaTeX pipelines are ported from the
[PaperHub](https://github.com/whats2000/PaperHub) proof-of-concept, which
validated the end-to-end flow. Every ported file carries a header naming its
source and what was adapted. The dependency arrow points one way — this project
imports nothing from PaperHub.
