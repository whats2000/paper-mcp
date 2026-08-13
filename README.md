# paper-mcp

**Ready-to-use tools that give an LLM agent a paper it can actually work with:
the full text as markdown, and an index of every figure with its caption.**

Built for external agent clients (Claude Cowork, Claude Desktop, Cursor, any MCP
framework). It supplies what those clients lack — paper acquisition and faithful
extraction — and nothing else: **no accounts, no stored user data, no
server-side LLM calls, and no agent flows.**

This is *data-processing functionality*, not an agent. Pipelines built on top —
slides, summaries, literature reviews — belong to the calling agent and its own
skills. This service's only job is to make each step precise.

## What you get

`fetch_paper("arxiv:1706.03762")` returns the paper as markdown —

```markdown
## Introduction

The dominant sequence transduction models are based on complex recurrent…

| Model | BLEU |
| --- | --- |
| Base | 27.3 |
| Big  | 28.4 |

$$
\mathrm{Attention}(Q,K,V) = \mathrm{softmax}(QK^T/\sqrt{d_k})V
$$

![fig-001](figures/fig-001.png)

*fig-001: The Transformer architecture.*
```

— alongside the figure index:

```jsonc
"figures": [
  { "id": "fig-001",
    "caption": "The Transformer architecture.",
    "page": 3,
    "image_url": "https://…/a/<token>/figures/fig-001.png" }
]
```

Extraction is **Marker**, and only Marker. Three guarantees, each asserted by
tests and by a real-workflow check:

- **Tables stay tables** — rows and columns intact. A table flattened to a blob
  of cell text invites an agent to read numbers against the wrong column.
- **Equations stay LaTeX** — never prose approximations of maths.
- **Figures are extracted, indexed and captioned** — an index entry exists only
  when the image really decoded to disk, so citing `fig-001` always refers to
  something real.

There is no low-fidelity fallback engine. PaperHub shipped crude PyMuPDF
extraction once, measured the output as "conference-UNusable", and replaced it
with Marker; a service whose value is faithful extraction must not quietly
substitute unfaithful extraction. Without Marker, a PDF fetch reports
`extraction_unavailable` rather than degrading.

Statelessness is the security architecture, not an omission. With no per-user
state, a shared public endpoint has nothing to leak between callers, and
identity degrades to a quota key.

## Status

| Tool | Phase | Status |
| --- | --- | --- |
| `search_arxiv` · `search_papers` · `find_related` · `resolve_paper` | A | ✅ shipped |
| `fetch_paper` · `get_job` + artifact serving | B | ✅ shipped — verified against real Marker |
| `compile_latex` (sandboxed, a tool — not a flow) | C | ✅ shipped — jail verified in-container |
| OIDC auth + quota | D | ✅ shipped |
| Portable skills (examples, not the product) | E | ✅ shipped — served as MCP prompts |

## Connecting a client

The service speaks Streamable HTTP at `/mcp`. A local Claude Code session
connects with an `.mcp.json` pointing at your own instance:

```json
{
  "mcpServers": {
    "paper-mcp": { "type": "http", "url": "http://127.0.0.1:8000/mcp" }
  }
}
```

Against a deployment with `AUTH_MODE=oidc`, the client sends a bearer token
from your IdP on **every** request — there is no session to authenticate once
and reuse:

```json
{
  "mcpServers": {
    "paper-mcp": {
      "type": "http",
      "url": "https://paper-mcp.example.org/mcp",
      "headers": { "Authorization": "Bearer ${PAPER_MCP_TOKEN}" }
    }
  }
}
```

## Development

```bash
uv sync
uv run pytest                    # fast, offline (integration excluded)
uv run pytest -m integration     # spawns a real server, drives it with a real MCP client
uv run ruff check src tests
uv run mypy src

docker compose up -d marker      # required for extraction
uv run paper-mcp
```

### Compiling LaTeX

`compile_latex` executes caller-supplied source, so it runs behind three
layers: TeX flags (`-no-shell-escape`, `openin_any=p`, `openout_any=p`),
**nsjail**, and wall-clock/output caps. One attempt, structured errors with
file and line, no revise loop — the calling agent fixes and resubmits.

**Without a sandbox it refuses.** nsjail is Linux-only, so on a host lacking
it the tool returns `sandbox_unavailable` unless `PAPER_MCP_AUTH_MODE=open`.
Declining costs a caller one retry; running a stranger's program unisolated
costs the host.

The container needs `seccomp=unconfined` (already in `docker-compose.yml`):
Docker's default profile blocks the namespace `clone()` nsjail requires, and
without it every compile fails to launch. Measured — `SYS_ADMIN` and
`--privileged` are *not* needed, which keeps the exemption narrow.

```bash
# the release gate: adversarial corpus inside the jail
docker run --rm --security-opt seccomp=unconfined   -v "$PWD/scripts:/app/scripts:ro" paper-mcp   python /app/scripts/sandbox_corpus.py
```

Ten cases locally and nine in-jail, each failing closed: shell escape does not
execute, absolute-path and traversal reads are refused, writes outside the job
directory are refused, unbounded expansion is killed, no network is reachable
from inside the jail — and a benign document still compiles.

### The check that decides whether this works

```bash
uv run python scripts/paper_workflow_check.py 1706.03762
```

Drives the real workflow over MCP against the real Marker service and judges
the *content*: does the markdown have tables with separator rows, equations as
LaTeX, a populated and captioned figure index, and figure URLs that download
real image bytes? A green pytest says the code runs; this says an agent can use
the output. Slow by nature — Marker takes roughly a minute per dense page.

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

## Skills

Two starting-point skills ship in `skills/`, also served over `prompts/` so a
Claude client surfaces them as slash commands:

| Skill | Shows an agent how to |
| --- | --- |
| `paper-to-deck` | build a Beamer deck grounded in figures the paper actually contains |
| `deep-read` | answer questions from the bundle rather than from memory of the paper |

They are **examples, not the product.** The calling agent owns its pipelines
and can ignore them entirely — which is exactly why this service ships tools
rather than flows.

## Security

The service is internet-facing and executes caller-supplied LaTeX, so the
controls are verified against a **running container** rather than a test
client. That distinction is not pedantry: `TestClient` follows redirects, and
that is exactly how a `307` on `POST /mcp` passed the suite while a connector
would have broken on it. A property that only holds in-process is a property
of a stack nobody is attacking.

The attack surface exercised, against a real IdP: tokens that are missing,
malformed, expired, minted for another audience, issued by another issuer, or
signed by an unknown key; **`alg=none` signature stripping** and
**RS256→HS256 key confusion**, the two forgeries a header-trusting verifier
accepts; uniform rejection bodies, so a probe cannot learn which check failed;
DNS-rebinding via `Host`; six encodings of artifact path traversal; quota
exhaustion with `Retry-After`; and shell-escape, absolute-path and
escaping-asset probes driven through the real `compile_latex` tool inside the
jail. All 26 defended.

Attack the image you are shipping, and confirm the **installed** package
carries the controls before trusting the result. One run reported a total auth
bypass that did not exist in the code: the image predated the middleware.

The converse matters as much — a service that rejects everyone is trivially
secure and useless. So the whole product is also driven through a real MCP
client bearing a real token, resolve through extraction to a compiled deck,
confirming that **no session identifier is ever issued**: the server is
`stateless_http`, so there is no handle to leak, and every request carries its
own credential.

## Configuration

Environment only (twelve-factor). Nothing is read from a config file.

| Variable | Default | Purpose |
| --- | --- | --- |
| `PAPER_MCP_HOST` / `PAPER_MCP_PORT` | `0.0.0.0` / `8000` | Bind address |
| `PAPER_MCP_ALLOWED_HOSTS` | localhost only | **DNS-rebinding protection.** A public deployment must list its own hostname or every request gets `421`. `*` disables the check and is warned about at boot |
| `PAPER_MCP_ALLOWED_ORIGINS` | derived from allowed hosts | CORS origins for browser clients |
| `PAPER_MCP_AUTH_MODE` | `open` | `open` disables authentication — development only. Anything else requires the OIDC settings below |
| `PAPER_MCP_OIDC_ISSUER` / `PAPER_MCP_OIDC_AUDIENCE` | unset | The IdP to validate bearer tokens against. This service is a resource server: it never issues tokens |
| `PAPER_MCP_SUBJECT_SALT` | per-process | Salt for the HMAC of `sub` used in metering and logs. The raw subject is never logged |
| `PAPER_MCP_QUOTA_CALLS_PER_MINUTE` | `60` | Per-caller call budget |
| `PAPER_MCP_QUOTA_EXTRACTIONS_PER_HOUR` | `20` | Per-caller GPU-extraction budget |
| `PAPER_MCP_QUOTA_COMPILE_SECONDS_PER_HOUR` | `600` | Per-caller compile budget, metered by time spent |
| `PAPER_MCP_UNPAYWALL_EMAIL` | unset | Contact email enabling Unpaywall open-access lookup in `resolve_paper` |
| `PAPER_MCP_S2_API_KEY` | unset | **Effectively required in production** — see below |
| `PAPER_MCP_MARKER_URL` | `http://127.0.0.1:8002` | Marker service. **Required for extraction** — without it `fetch_paper` reports the dependency rather than degrading |
| `PAPER_MCP_MARKER_MAX_PAGES` | `1` | Pages per Marker call. VRAM scales with page *content density*, not page count: one dense two-column page can saturate 6 GB, and a 5-page batch was measured at 21 minutes. Raise only on a bigger GPU |
| `PAPER_MCP_ARTIFACT_ROOT` | `artifacts` | Content-addressed cache for bundles and figure images |
| `PAPER_MCP_ARTIFACT_TTL_HOURS` | `24` | How long artifacts survive before the sweeper reclaims them |
| `PAPER_MCP_LOG_LEVEL` | `INFO` | Log level, applied to uvicorn too. `WARNING` drops per-request access logging: measured 18,222 → 114 bytes over 300 requests. Worth setting for a public deployment — a server whose stdout backs up blocks inside `write()`, and per-request logging is what fills the buffer |

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
