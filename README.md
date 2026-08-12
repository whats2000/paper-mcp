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

The interpreter is pinned in `.python-version` (3.13) so every contributor and
the container build agree on one runtime (NFR-07).

> **If uv warns `VIRTUAL_ENV=… does not match the project environment path .venv`**
> — and your editor reports this project's dependencies as "not installed" —
> both have one cause: **VS Code's selected Python interpreter.**
>
> The Python extension exports the selected interpreter as `VIRTUAL_ENV` into
> its terminals and into extension-spawned processes (which is how Claude Code's
> shells inherit it). If that interpreter is a *bare* Python install rather than
> a virtualenv — a uv-managed `…/uv/python/cpython-3.x…/python.exe`, say, which
> has no `pyvenv.cfg` — then uv correctly refuses it and Pylance resolves
> against an interpreter lacking this project's packages.
>
> `.vscode/settings.json` fixes it for this repo by pointing
> `python.defaultInterpreterPath` at `${workspaceFolder}/.venv`; **reload the
> window** for it to take effect. To fix it everywhere, repoint the user-level
> `python.defaultInterpreterPath` in your VS Code settings.
>
> Things that do **not** fix it, tested: pinning `.python-version` (it selects
> an interpreter; the warning compares environment *paths*), and
> `UV_NO_ACTIVE=1`. There is no `pyproject.toml` or `uv.toml` key for it. The
> ad-hoc escape is `uv run --no-active …` — a real flag, though `uv run --help`
> documents only its `--active` counterpart.

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
| `PAPER_MCP_S2_API_KEY` | unset | **Effectively required in production** — see below |
| `PAPER_MCP_LOG_LEVEL` | `INFO` | Log level |

### Semantic Scholar needs an API key

Measured on-device against the live API: the keyless tier throttles the
**search** endpoint so hard it is unusable. A single `search_papers` call
still returned HTTP 429 after **237 seconds** of paced retries, and a title
lookup after **873 seconds**. Single-paper lookups (`/paper/{id}` by arXiv id,
DOI, or S2 id) and the citation-graph endpoints answered fine throughout.

So without `PAPER_MCP_S2_API_KEY`:

| Works | Unreliable |
| --- | --- |
| `search_arxiv` (different upstream) | `search_papers` |
| `resolve_paper` by arXiv id / DOI / `ss:` id | `resolve_paper` by free-text title |
| `find_related` (all three modes) | |

Callers get a typed `rate_limited` error carrying `retry_after`, never a hang
or a silently empty result — but the capability is degraded. Get a key at
<https://www.semanticscholar.org/product/api>.

### On-device acceptance check

```bash
uv run python scripts/on_device_check.py
```

Boots the service through its real entry point and drives it with a real MCP
client over the wire, covering every tool and every externally-dependent
branch. Exits non-zero on failure; a throttled upstream is reported `skip`,
never `pass`. Set `PAPER_MCP_S2_API_KEY` for a run with no skips.

## Documentation

- **Spec:** [docs/superpowers/specs/2026-08-11-paper-mcp-srs.md](docs/superpowers/specs/2026-08-11-paper-mcp-srs.md) — the single authoritative specification
- **Plans:** [docs/superpowers/plans/](docs/superpowers/plans/)

## Provenance

The extraction and LaTeX pipelines are ported from the
[PaperHub](https://github.com/whats2000/PaperHub) proof-of-concept, which
validated the end-to-end flow. Every ported file carries a header naming its
source and what was adapted. The dependency arrow points one way — this project
imports nothing from PaperHub.
