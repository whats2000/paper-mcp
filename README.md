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

Phase A (scaffold + discovery tools) — in progress.

| Tool | Phase | Status |
| --- | --- | --- |
| `search_arxiv` · `search_papers` · `find_related` · `resolve_paper` | A | in progress |
| `fetch_paper` · `get_section` · `get_job` | B | planned |
| `compile_latex` | C | planned |

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run mypy src
uv run paper-mcp          # serves on :8000, MCP at POST /mcp
```

## Documentation

- **Spec:** [docs/superpowers/specs/2026-08-11-paper-mcp-srs.md](docs/superpowers/specs/2026-08-11-paper-mcp-srs.md) — the single authoritative specification
- **Plans:** [docs/superpowers/plans/](docs/superpowers/plans/)

## Provenance

The extraction and LaTeX pipelines are ported from the
[PaperHub](https://github.com/whats2000/PaperHub) proof-of-concept, which
validated the end-to-end flow. Every ported file carries a header naming its
source and what was adapted. The dependency arrow points one way — this project
imports nothing from PaperHub.
