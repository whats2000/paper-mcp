# paper-mcp — Software Requirements Specification

**Status:** v0.1 (founding spec) · **Date:** 2026-08-11
**One spec per project.** This document is the single authoritative specification for `paper-mcp`. Architecture, schema, and scope questions are answered here before code.

---

## Revision History

| Version | Date | Change |
| --- | --- | --- |
| **v0.1** | 2026-08-11 | **Founding spec.** `paper-mcp` is extracted from the **PaperHub** proof-of-concept, which validated the end-to-end flow (acquire a paper → structured extraction → grounded slide authoring → LaTeX compile) inside a single-user local application. PaperHub proved the *pipeline*; it could not be the *product*, because the delivery target is a **remote, multi-user, internet-facing MCP service** that external agent clients (Claude Cowork, Claude Desktop, Cursor, any MCP framework) connect to. The two have incompatible trust models — PaperHub is single-user with no auth, DB-centric, and never handles untrusted input; this service is public, authenticated, stateless, and **executes caller-supplied LaTeX**. Therefore: a new repository, a one-way dependency arrow (this project depends on nothing in PaperHub; PaperHub may later become a *client*), and the pipeline code **copied + adapted with provenance comments** — the same decomposition posture PaperHub itself used on `paper2slides-plus`. Founding decisions recorded here: stateless-by-design (§NFR-01), eight-tool surface (§III-2), inline-structured + content-addressed-artifact delivery (§III-4), nsjail-in-container sandbox (§III-6), OIDC resource-server auth (§III-7), portable **skills** as the third deliverable (§III-8), single container bundle with an optional GPU profile (§III-9). |

---

# Part 0 — Requirement Coverage Matrix

| Requirement | Addressed in |
| --- | --- |
| External agent clients can discover papers | FR-01 (`search_arxiv`, `search_papers`, `find_related`), FR-02 (`resolve_paper`) |
| External agent clients can obtain agent-ready paper content | FR-03 (`fetch_paper` bundle), FR-04 (`get_section`), §III-3 |
| External agent clients can compile LaTeX / Beamer decks | FR-05 (`compile_latex`), §III-6 |
| Long operations survive a remote MCP call timeout | FR-06 (job lifecycle, `get_job`), §III-5 |
| Multiple users share one endpoint without leaking data | NFR-01 (statelessness — nothing per-user exists to leak), FR-08 (identity for quota/revocation, *not* for data separation), §II-1 |
| A public endpoint executing untrusted TeX is safe | NFR-02 + FR-05 + §III-6 (nsjail, no network, rlimits, `-no-shell-escape`); acceptance §I-8 #4 |
| Shared expensive resources are not starved by one caller | FR-09 (per-subject quota, GPU serialization) |
| The proven PaperHub flow is preserved, not re-invented | FR-10 (portable skills), NFR-06 (provenance), §III-8 |
| Reproducible deployment on a cluster host | FR-11 (container + compose profiles), NFR-07, §III-9 |
| Operability without user-data collection | FR-12 (structured logs, no request-body persistence), NFR-01 |

When requirements evolve, a new row is added here alongside its FR/NFR — never silently absorbed.

---

# Part 1 — Software Requirements Specification

## I-1. Background

PaperHub is a paper-aware chat client built as a proof of concept. Over ~40 iterations it established a working pipeline:

- **Acquisition** — arXiv source, Semantic Scholar metadata, Unpaywall open-access resolution.
- **Extraction** — LaTeX-source → structured asset; PDF → structured asset via Marker (GPU, high fidelity) or PyMuPDF (CPU fallback); a unified `PaperAsset` of figures + captions, equations as LaTeX, and an ordered section list.
- **Grounded authoring** — a sequence of prompts (narrative planning → draft → coherence pass → figure verification) that reliably produces a slide deck citing only figures that exist.
- **Compilation** — Beamer/LaTeX compile with engine selection, CJK font injection, overflow detection, and math auditing.

That flow works. What PaperHub cannot do is *serve* it: it is a single-user local application whose value is locked behind its own chat UI. The observed need is the inverse — capable agent clients already exist (Cowork, Desktop, Cursor, bespoke frameworks) and have strong models; **what they lack is paper acquisition, faithful extraction, and a working LaTeX toolchain.**

`paper-mcp` supplies exactly that, and nothing else.

## I-2. Problem Statement

An agent asked to "read this paper and build me a deck" today must: find the paper, download it, parse a PDF badly, hallucinate figure references it cannot verify, and emit LaTeX it has no way to compile. Each step is a known failure mode, and none of them are the model's core competence.

The gap is **tools, not intelligence**. A stateless service that does acquisition, extraction, and compilation deterministically — and hands back structured, grounded data — removes every one of those failure modes without competing with the client's model.

## I-3. Users

| Role | Interaction |
| --- | --- |
| **Agent client** (primary) | Claude Cowork / Desktop / Cursor / LangGraph / any MCP framework. Calls tools over remote MCP; authenticates with an OAuth bearer token. |
| **End user** | A researcher operating that client. Never talks to `paper-mcp` directly; grants their client access via a connector. |
| **Operator** | Runs the container on a cluster host, configures the IdP, sets quotas, provisions the GPU profile and outbound API keys. |

## I-4. Use Cases

**UC-1 — Find a paper.** Agent calls `search_arxiv` / `search_papers` with a topic query, receives ranked `PaperRef`s with abstracts and open-access status, and picks one.

**UC-2 — Read a paper.** Agent calls `fetch_paper`. It receives an outline (metadata, ordered section names, figure manifest with captions, equation list) immediately, then pulls only the sections it needs via `get_section`. No unzip, no PDF parsing, no context blowout.

**UC-3 — Build a grounded deck.** Agent loads the `paper-to-deck` skill, follows its flow against the tools, writes Beamer LaTeX referencing figures **from the manifest** (so they provably exist), calls `compile_latex`, and receives a PDF URL — or structured errors it fixes and resubmits.

**UC-4 — Follow a citation trail.** Agent calls `find_related(paper_id, mode="cited_by")` to walk the citation graph, then `fetch_paper` on the interesting nodes.

**UC-5 — Process a private manuscript.** Agent calls `fetch_paper(url=...)` on a PDF. The content is keyed by its own `sha256`, so it is reachable only by someone who already has the file.

**UC-6 — Long extraction.** A dense scanned PDF exceeds the remote-call budget. `fetch_paper` returns a `job_id`; the agent polls `get_job` and collects the bundle when ready.

## I-5. Functional Requirements

| ID | Name | Description |
| --- | --- | --- |
| **FR-01** | Discovery tools | `search_arxiv(query, max_results)` (arXiv API), `search_papers(query, max_results)` (Semantic Scholar `/graph/v1/paper/search`), `find_related(paper_id, mode ∈ {cites, cited_by, similar}, max_results)` (S2 citation graph). All return `PaperRef[]` (§III-2). Pure functions: no writes, no state, identical output for identical input modulo upstream data. `max_results` clamped server-side to `[1, 50]`. |
| **FR-02** | Paper resolution | `resolve_paper(identifier)` accepts an arXiv ID, DOI, S2 paper ID, or free-text title and returns a canonical `PaperRef` plus **open-access availability** — arXiv source first, then `openAccessPdf`, then Unpaywall (when `UNPAYWALL_EMAIL` is configured). Returns `open_access.available=false` with the reason rather than erroring when no ingestible source exists. |
| **FR-03** | Paper bundle | `fetch_paper(paper_id \| url, include ∈ {outline, full}, engine ∈ {auto, latex, marker, pymupdf})` produces an **agent-ready bundle** (§III-3): canonical metadata, ordered sections, figure manifest (id, caption, page, section, image path + URL), equations as LaTeX, extraction provenance, and a content-addressed **zip URL** for the binary artifacts. **`include="outline"` is the default** — sections are listed but their markdown is omitted — so a large paper never floods the caller's context. `include="full"` inlines all section markdown. Bundles are keyed `arxiv:<id>` or `sha256:<hex>`; a repeat call is a cache hit and returns immediately. |
| **FR-04** | Section read | `get_section(bundle_id, section)` returns one section's markdown from an existing bundle without unzipping anything. `section` matches by exact name or order index. Unknown `bundle_id` (expired or never created) returns a typed `bundle_expired` error instructing the caller to re-`fetch_paper` — never a silent empty. |
| **FR-05** | LaTeX compilation | `compile_latex(tex, assets?, engine ∈ {auto, pdflatex, xelatex, lualatex}, timeout_s?)` compiles caller-supplied source **inside the sandbox** (§III-6) and returns `{ok, engine, page_count, pdf_url, log_tail, errors[], warnings[]}`. `errors[]` is **structured** (`file`, `line`, `message`, `kind`) parsed from the log, not a raw dump. `warnings[]` carries overflow-box and math-audit findings ported from PaperHub. `assets` supplies figures by relative path (base64 or an artifact URL from a prior `fetch_paper`). **Exactly one compile attempt** — there is no server-side revise loop; the caller's model fixes the errors. Engine auto-selection ports PaperHub's `select_engine` + CJK font logic. |
| **FR-06** | Job lifecycle | Any tool whose work may exceed the remote-call budget returns either a completed result or `{job_id, state}`. `get_job(job_id)` returns `{state ∈ {queued, running, done, error}, progress?, result?, error?}`. Jobs are ephemeral runtime records (§III-5) with a TTL, keyed by an unguessable id, holding **no caller identity beyond the quota subject**. A job whose result is content-addressed is deduplicated: two callers requesting the same uncached paper join one job. |
| **FR-07** | Artifact store | Bundles and compiled PDFs are written to a **content-addressed** store (§III-4) and served over HTTP at an unguessable path with an expiry. A TTL sweeper reclaims them. Artifacts hold only derived data; no identity, no session, no DB row. Re-deriving an expired artifact is always possible from its key. |
| **FR-08** | Authentication | The service is an **OIDC resource server**: it validates a bearer JWT against a configured issuer + audience (JWKS fetched and cached) and rejects anything else with `401`. It is **not** an authorization server — the operator brings an IdP. Identity exists for **quota and revocation only**; it grants no data scoping, because there is no per-user data (NFR-01). An `AUTH_MODE=open` setting exists for local development and closed networks and **must** be logged loudly at boot. |
| **FR-09** | Quota + fair use | Per-subject token buckets on (a) calls/minute, (b) extractions/hour, (c) compile-seconds/hour, configured by the operator. Marker GPU work is **globally serialized** (one dense page can saturate ~6 GB VRAM — a PaperHub-measured constraint). Exceeding a bucket returns a typed `quota_exceeded` error with `retry_after`, never a hang. Unauthenticated mode falls back to per-IP buckets. |
| **FR-10** | Portable skills | The proven PaperHub flows ship as **portable skill bundles** (`skills/<name>/SKILL.md`) **and** as MCP `prompts/` (Claude clients surface those as slash commands): `paper-to-deck` (narrative planning → draft → coherence → figure verification), `deep-read` (agentic section navigation — outline first, targeted reads, no vector store), `figure-grounding` (cite only manifest figures). This is where the *intelligence* lives: the client's model executes the flow, the service supplies the tools. **No server-side LLM inference** (NFR-01). Skills are authored through the `writing-agent-prompts` discipline (≥2 variants × query set × judged comparison), never hand-written once. |
| **FR-11** | Containerized deployment | One image carries the service, TeX Live (with `beamer`, `metropolis`, Fira, CJK fonts), PyMuPDF, pandoc, and nsjail. `docker compose` profiles: `default` (CPU, PyMuPDF extraction) and `gpu` (adds the Marker service). The image is the unit of deployment; no host-level dependency beyond a container runtime, and — for the `gpu` profile — the NVIDIA container toolkit. |
| **FR-12** | Observability | Structured JSON logs per call: tool name, subject hash, duration, outcome, artifact key, extraction engine, sandbox exit reason. **Request bodies, LaTeX source, and paper content are never logged.** API keys and paths are redacted (ported redactor). A `/health` endpoint reports service, TeX, and Marker reachability. |

## I-6. Non-Functional Requirements

| ID | Category | Target |
| --- | --- | --- |
| **NFR-01** | **Statelessness** | No user accounts, no sessions, no chat history, no per-user library, **no server-side LLM calls**. The only persistence is a content-addressed artifact cache of derived public-paper data and an ephemeral job table. *This is the security architecture*: a shared endpoint is safe because there is nothing per-user to cross. Any proposal introducing per-user state must first amend this requirement. |
| **NFR-02** | **Sandbox containment** | Caller-supplied TeX executes with no network, a read-only root, a tmpfs-only work dir, an unprivileged uid, and cpu/mem/pids/wall-clock/output limits. Escape attempts fail closed and are logged. Verified by an adversarial test suite that is a **release gate**, not a nice-to-have (§I-8 #4). |
| **NFR-03** | Latency | Discovery tools ≤ 3 s p95. `fetch_paper` cache hit ≤ 500 ms. arXiv-LaTeX extraction ≤ 30 s p95 (synchronous). PDF extraction is asynchronous by default. `compile_latex` on a ≤ 30-slide deck ≤ 45 s p95. |
| **NFR-04** | Typing | Python 3.12+, Pydantic v2 models on every tool boundary, `mypy --strict` clean, `ruff` clean. Tool input schemas are generated from the models — one source of truth. |
| **NFR-05** | Declared scope per tool | Every tool declares its scope in its description (network egress, filesystem reach, resource ceiling). Out-of-scope arguments are rejected at the boundary with a typed error, never partially executed. |
| **NFR-06** | Provenance | Every file copied from PaperHub carries a header comment naming the source path and commit. Adaptation is expected; silent copying is not. The dependency arrow points one way — `paper-mcp` imports nothing from PaperHub. |
| **NFR-07** | Reproducibility | Pinned dependencies via `uv` lockfile; pinned base image digest; the container builds from a clean checkout with no network access to anything unpinned. |

## I-7. Out of Scope

- **No server-side LLM inference.** Not for extraction cleanup, not for slide authoring, not for error repair. Intelligence ships as skills (FR-10). *(The one exception is Marker's operator-configured `use_llm` extraction-accuracy pass, which is an internal quality knob on a deterministic pipeline, disabled by default and never exposed as a tool.)*
- **No user accounts, profiles, libraries, or history.** Identity is a quota key (FR-08).
- **No chat interface, no agent loop, no orchestration.** Callers bring their own.
- **No vector store or embeddings.** Navigation is structural (outline → targeted section read), the approach PaperHub validated.
- **No dependency on PaperHub**, and no obligation to stay in sync with it.
- **No multi-node HA or horizontal autoscaling in v1.** Single host, single artifact volume. The artifact store is behind an interface so an S3 backend can be added without touching tool code.
- **No billing, metering export, or per-seat licensing.**
- **No document viewer, rendering UI, or web frontend** beyond artifact download endpoints and `/health`.

## I-8. Acceptance Criteria

1. **Connector reachability.** A Claude Cowork custom connector pointed at the deployed URL completes the OAuth flow, lists all eight tools, and successfully calls `search_arxiv`. *(Note: Cowork brokers remote MCP through Anthropic's servers, so the endpoint must be publicly reachable — a VPN-only host fails this criterion by construction.)*
2. **End-to-end grounded deck.** From a cold cache, an agent using the `paper-to-deck` skill turns an arXiv ID into a compiled PDF, and **every figure in the deck appears in the bundle's figure manifest** — zero hallucinated figures across 5 test papers.
3. **Extraction parity with the PoC.** For a 10-paper fixture corpus, `fetch_paper` produces section lists and figure manifests matching PaperHub's cached `PaperAsset` output (same section count, same figure ids/captions) — proving the port preserved behaviour.
4. **Sandbox holds (release gate).** An adversarial corpus — `\write18{...}`, `\input{/etc/passwd}`, `\openout` to an absolute path, a network fetch, a fork bomb, unbounded expansion, and a multi-GB output — **all fail closed** with a typed error, no host filesystem read outside the jail, no egress, and no process outliving its wall-clock limit. Any failure blocks release.
5. **Statelessness verified.** After a full functional test run, the deployment holds no record linking any subject to any paper, query, or compile. Verified by inspecting every persisted store.
6. **Quota enforced.** A caller exceeding each configured bucket receives `quota_exceeded` with `retry_after`; a second caller is unaffected during that window.
7. **No silent failure.** Every error path — unresolvable paper, no open-access source, extraction failure, compile failure, expired bundle, sandbox kill, quota — returns a **typed, actionable** error naming the next step. Verified case by case.

---

# Part 2 — Technology Selection Analysis

## II-1. Why stateless, when the PoC was database-centric?

The moment the delivery target became "a service multiple people connect to," PaperHub's design became a liability: `paper_content`, `chat_sessions`, and `memories` are all global with no owner column, so exposing them would leak every user's library, session titles, and standing preferences to every other user.

Three postures were considered:

| Posture | Verdict |
| --- | --- |
| Deployment boundary (one instance per user) | Rejected — contradicts the actual requirement of a shared cluster service |
| Capability-scoped workspaces | **Rejected as a half-measure.** It isolates sessions while the shared library and global memory — the content that actually matters — stay shared. It buys the feeling of safety, not safety |
| Real multi-tenancy (`owner_id` everywhere, auth, filtered queries) | Rejected as scope: it reverses the PoC's foundational assumption and touches every table, query, and agent |

The chosen fourth option — **delete the state** — dissolves the problem instead of managing it. With no per-user data, tenancy is not a requirement at all; identity degrades to a quota key. This is why NFR-01 is written as a *security* requirement, not a performance one.

## II-2. Why not just add tools to PaperHub?

Incompatible trust models, and the difference is not cosmetic:

| | PaperHub | paper-mcp |
| --- | --- | --- |
| Users | one, no auth | many, authenticated |
| Network | localhost / private compose | public internet (Anthropic's IPs inbound) |
| State | SQLite is the point | none by design |
| Untrusted input | none — the operator types it | **caller-supplied LaTeX, executed** |
| Deps | LangGraph, aiosqlite, React | extraction + TeX + sandbox |

A service that executes untrusted code for strangers must not share a process, a deployment, or a dependency tree with a single-user local app that has no auth.

## II-3. Why nsjail inside the container, not Docker-in-Docker or a VM?

`pdflatex` on caller-supplied input is arbitrary code execution — TeX is Turing-complete, `\write18` shells out, `\input` reads arbitrary paths, and `\openout` writes them.

| Option | Verdict |
| --- | --- |
| Container-per-compile via the Docker socket | **Rejected.** Mounting the socket grants a compile job root on the host — strictly worse than no sandbox |
| microVM (Firecracker) per compile | Over-engineered for v1: hundreds of ms of boot per compile, a second image lifecycle, and cluster privileges we may not have |
| **nsjail inside the service container** | **Chosen.** Namespaces + seccomp + rlimits, no privileged host access, ~ms overhead, one image. Defence in depth: the container is already a boundary; nsjail makes each compile a boundary within it |

TeX-level hardening (`-no-shell-escape`, `openin_any=p`, `openout_any=p`) is applied **as well** — belt and braces, since each defeats a different class of attack.

## II-4. Why an OIDC resource server rather than our own auth?

Cowork's connector UI takes an OAuth client ID/secret; it does not offer custom auth headers, which rules out the simpler API-key design. Building an authorization server means owning token issuance, refresh, revocation, and consent — a security-critical subsystem far from this project's competence. Validating a JWT against a configured issuer + JWKS is ~100 lines and delegates the hard parts to an IdP the operator already trusts. The `AUTH_MODE=open` escape hatch keeps local development friction-free while being loud enough that nobody ships it by accident.

## II-5. Why skills instead of server-side generation?

A `generate_slides` tool that runs the authoring prompts server-side would (a) spend the operator's LLM budget on the caller's work, (b) make the service an agent competing with a client that already has a better model and the user's actual context, and (c) reintroduce non-determinism into a service whose value proposition is determinism.

Shipping the same prompts as **portable skills** keeps the proven flow while inverting who pays and who thinks. The service stays a pure function; the expertise still transfers.

## II-6. Why SQLite for jobs and local disk for artifacts?

v1 is a single host, so Redis and S3 would add operational surface for capacity that does not yet exist. Both sit behind interfaces (`JobStore`, `ArtifactStore`) so the swap is a backend implementation, not a refactor. This is a deliberate YAGNI call recorded so the eventual multi-node work knows exactly where to cut in.

---

# Part 3 — System Architecture

## III-1. Overview

```
   Claude Cowork / Desktop / Cursor / any MCP client
                     │
                     │  remote MCP (streamable HTTP)
                     │  Authorization: Bearer <JWT>
                     ▼
┌──────────────────────────────────────────────────────────┐
│  paper-mcp container                                     │
│                                                          │
│   FastAPI ── FastMCP (8 tools + prompts)                 │
│      │                                                   │
│      ├── auth.py      OIDC resource server (JWKS cache)  │
│      ├── quota.py     token buckets per subject          │
│      ├── jobs.py      SQLite job store + worker pool     │
│      ├── artifacts.py content-addressed store + TTL      │
│      │                                                   │
│      └── tools/                                          │
│           discovery.py  fetch.py  latex.py               │
│                │           │         │                   │
│                ▼           ▼         ▼                   │
│          pipelines/ (ported)    sandbox/ (nsjail)        │
└──────────────────────────────────────────────────────────┘
        │                │                    │
   arXiv · S2 ·      Marker (gpu          TeX Live
   Unpaywall         profile, optional)
```

## III-2. MCP surface

Eight tools. Input/output models are Pydantic v2; JSON schemas are generated from them (NFR-04).

```
search_arxiv(query, max_results=8)                       → PaperRef[]
search_papers(query, max_results=8)                      → PaperRef[]
find_related(paper_id, mode, max_results=8)              → PaperRef[]
resolve_paper(identifier)                                → PaperRef
fetch_paper(paper_id|url, include="outline", engine="auto")
                                                         → Bundle | JobHandle
get_section(bundle_id, section)                          → SectionContent
compile_latex(tex, assets=[], engine="auto", timeout_s=120)
                                                         → CompileOutput
get_job(job_id)                                          → JobStatus
```

**`PaperRef`** — one shape across all sources, so an agent never branches on provenance:

```jsonc
{
  "paper_id": "arxiv:1706.03762",        // arxiv:<id> | ss:<paperId> | doi:<doi>
  "title": "Attention Is All You Need",
  "abstract": "…",                        // nullable
  "year": 2017,                           // nullable
  "authors": ["Ashish Vaswani", "…"],
  "arxiv_id": "1706.03762",               // nullable
  "doi": null,
  "venue": "NeurIPS",                     // nullable
  "citation_count": 123456,               // nullable
  "open_access": { "available": true, "url": "https://…", "source": "arxiv" },
  "source": "semantic_scholar"
}
```

`paper_id` prefers `arxiv:` when an arXiv ID exists — it is the identifier with an ingestible source, so preferring it makes `fetch_paper` succeed more often.

## III-3. The bundle

The core artifact, derived from PaperHub's `PaperAsset` (`FigureAsset` / `EquationAsset` / `SectionAsset`) extended with markdown and URLs:

```jsonc
{
  "bundle_id": "arxiv:1706.03762",       // or "sha256:<hex>"
  "paper": { /* PaperRef */ },
  "sections": [
    { "name": "Introduction", "order": 1, "char_count": 4821,
      "markdown": "…" }                   // present only when include="full"
  ],
  "figures": [
    { "id": "fig-001", "caption": "The Transformer architecture.",
      "page": 3, "section": "Model Architecture",
      "image_path": "figures/fig-001.png",
      "image_url": "https://…/a/<key>/figures/fig-001.png" }
  ],
  "equations": [ { "id": "eq-001", "latex": "\\mathrm{Attention}(Q,K,V)=…",
                   "section": "Model Architecture" } ],
  "extraction": { "engine": "latex", "warnings": [] },
  "artifact": { "zip_url": "https://…/a/<key>/bundle.zip",
                "bytes": 4821004, "expires_at": "2026-08-12T…Z" }
}
```

**Why `include="outline"` is the default.** A full paper's markdown can exceed 100k characters. Defaulting to inline-everything would blow the caller's context on the first call and make the service feel unusable on long papers. Outline-first mirrors what PaperHub proved works (structural navigation, then targeted reads) and makes `get_section` the natural second step rather than an afterthought.

**Figure grounding.** The manifest is the contract: an agent that cites `fig-001` is citing something the extractor actually found, with a caption and a resolvable URL. This is the mechanism behind acceptance criterion #2.

## III-4. Artifact store

Content-addressed, keyed by `arxiv:<id>` or `sha256:<hex>` of the source bytes:

```
$ARTIFACT_ROOT/<sha256(key)[:2]>/<sha256(key)>/
    bundle.json          # the full bundle incl. all section markdown
    bundle.zip           # figures/, source, rendered assets
    figures/…
    pdf/<sha256(tex)>.pdf
```

Served at `GET /a/<opaque>/…` where `<opaque>` is derived from the key — unguessable, so a privately uploaded manuscript is reachable only by someone who already possesses the file (and could compute its hash anyway). Every response carries an expiry; a sweeper reclaims entries past TTL by last-access time.

Two callers requesting the same public paper share one entry. That is deduplication of public data, not a leak — and it is what makes the cache-hit path fast enough for NFR-03.

Behind an `ArtifactStore` interface (`put`, `url_for`, `open`, `expire`) so S3 can be added later (§II-6).

## III-5. Job model

```
fetch_paper → cached?          → Bundle (sync)
            → arXiv LaTeX?     → Bundle (sync, ≤30 s)
            → PDF extraction   → JobHandle {job_id, state:"queued", poll_after_ms}
```

`jobs` (SQLite, ephemeral): `job_id` (unguessable), `kind`, `content_key`, `state`, `subject_hash`, `progress`, `result_key`, `error`, `created_at`, `expires_at`.

**Coalescing:** a job is keyed by `content_key`, so concurrent requests for the same uncached paper join one job rather than starting N GPU extractions. **GPU serialization:** Marker work runs through a single-slot semaphore (PaperHub measured one dense two-column page saturating ~6 GB VRAM; concurrency there means OOM, not throughput). `subject_hash` exists only for quota accounting and is a salted hash, not an identity record.

## III-6. The sandbox

Every `compile_latex` invocation runs in a fresh nsjail:

| Layer | Control |
| --- | --- |
| Network | none — no interfaces in the namespace |
| Filesystem | read-only rootfs; tmpfs work dir; only the TeX tree and the job's inputs bind-mounted read-only |
| Identity | unprivileged uid/gid, no new privileges, dropped capabilities |
| Resources | rlimits on cpu, address space, file size, pids; wall-clock kill; output byte cap |
| TeX flags | `-no-shell-escape`, `-interaction=nonstopmode`, `openin_any=p`, `openout_any=p`, `max_print_line` bounded |
| Result | only the PDF and the log leave the jail, both size-capped |

Failure modes are typed: `compile_error` (TeX rejected it), `timeout`, `resource_limit`, `sandbox_violation`. All four are normal outcomes returned to the caller — never a 500, never a partial write.

The adversarial corpus in §I-8 #4 is a release gate. A change to the sandbox that has not been re-run against it does not ship.

## III-7. Auth and quota

```
Bearer JWT → verify signature (JWKS, cached, key-rotation aware)
           → verify iss, aud, exp, nbf
           → subject = sub
           → subject_hash = HMAC(salt, sub)     # what we log and meter
```

No claim beyond `sub` is used for authorization, because there is nothing to authorize *against* — every caller can reach every tool. Quota buckets are keyed on `subject_hash`, held in memory with periodic persistence (a restart forgiving one window is acceptable; the alternative is a Redis dependency for no real gain).

`AUTH_MODE=open` disables verification for local development, falls back to per-IP buckets, and emits a startup banner plus a warning on every call.

## III-8. Skills

```
skills/paper-to-deck/SKILL.md      # narrative planning → draft → coherence → figure check
skills/deep-read/SKILL.md          # outline-first navigation, targeted section reads
skills/figure-grounding/SKILL.md   # cite only manifest figures; verification step
```

Each is also registered as an MCP prompt so Claude clients expose it as a slash command. Skills reference tools by name and describe the *flow*; they contain no service-specific secrets and remain useful if copied into a client's own skill directory.

Authoring discipline (mandatory): clear directive first line, concise, XML-delimited injected context, multi-shot examples for ambiguous corners, and adoption only after a ≥2-variant × query-set × judged comparison. The PaperHub prompts are the **starting point**, not the deliverable — they were written for server-side execution against a known state and must be rewritten for a client agent holding different context.

## III-9. Deployment

```
docker/
  Dockerfile              # service + TeX Live + PyMuPDF + pandoc + nsjail
  docker-compose.yml      # profile: default (CPU)
  docker-compose.gpu.yml  # profile: gpu → adds marker service
```

Configuration is environment-only (twelve-factor): `OIDC_ISSUER`, `OIDC_AUDIENCE`, `AUTH_MODE`, `ARTIFACT_ROOT`, `ARTIFACT_TTL_HOURS`, `QUOTA_*`, `MARKER_URL`, `MARKER_MAX_PAGES`, `S2_API_KEY`, `UNPAYWALL_EMAIL`, `PUBLIC_BASE_URL`, `LOG_LEVEL`.

The `gpu` profile is opt-in; without it, PDF extraction uses PyMuPDF and the service still satisfies every functional requirement at lower extraction fidelity. **This is why v1 is not blocked on GPU capacity.**

## III-10. Testing

| Layer | Approach |
| --- | --- |
| Unit | Pure functions on fixtures — parsers, `PaperRef` normalization, log-error extraction, key derivation |
| Contract | Every tool's JSON schema snapshot-tested; a breaking change must be deliberate |
| Extraction parity | The 10-paper fixture corpus vs. PaperHub's cached `PaperAsset` output (acceptance #3) |
| Sandbox adversarial | The escape corpus (acceptance #4) — **release gate** |
| Integration | An MCP client against the running container: list tools, run the full UC-3 loop |
| Load | Concurrent extraction + compile under quota, asserting GPU serialization and no starvation |

External APIs are mocked in unit/contract tests; a separately-marked suite exercises them live.

## III-11. Provenance contract

Files ported from PaperHub carry:

```python
# Ported from PaperHub `backend/src/paperhub/pipelines/<file>.py` @ <commit>.
# Adapted: <what changed and why>.
```

Expected adaptations: removing `aiosqlite`/session parameters, replacing tracer steps with structured logs, replacing workspace paths with the artifact store, and dropping LLM-dependent code paths (which become skills).

---

## III-12. Closing principle

**The service is a pure function; the intelligence is portable text.** Every design decision here follows from that sentence — statelessness, no server-side inference, content-addressing, skills as a deliverable, and identity that means nothing more than a quota key. When a future change makes one of those uncomfortable, re-read this line before amending the requirement.
