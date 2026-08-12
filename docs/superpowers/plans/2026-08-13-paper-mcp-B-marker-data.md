# paper-mcp Plan B — Marker data path

> Supersedes `2026-08-13-paper-mcp-B1-bundle-arxiv.md`, which built a
> hand-rolled LaTeX→markdown path alongside Marker. That was the wrong
> architecture: the converter deleted tables outright, and Marker already does
> this job properly (SRS v0.2, v0.3).

**Goal:** `fetch_paper("arxiv:1706.03762")` returns the paper as **markdown + a figure index**, extracted by Marker, cached content-addressed, with figure images served over HTTP.

**Architecture:** acquire PDF → Marker → markdown + figures → artifact store → bundle. Marker is slow (PaperHub measured 21 min for a 5-page dense batch), so `fetch_paper` returns a **job handle** on a cache miss and the work runs in the background. No second extraction engine exists.

**Tech Stack:** Phase A stack + `pymupdf` (page counting, figure rasterization) + the Marker service over HTTP.

## Global Constraints

Plan A's constraints hold (uv, `mypy --strict`, Pydantic v2, provenance headers, no server-side LLM, Conventional Commits, tool scope declared). Plus:

- **No state beyond derived artifacts.** Content-addressed cache + an ephemeral job table. No identity (SRS NFR-01).
- **Every tool call stays inside `_TOOL_BUDGET_S`.** Work that cannot finish in budget becomes a job — that is what jobs are for.
- **Faithfulness is the acceptance bar:** tables stay tables, equations stay LaTeX, a figure index entry means the image exists.

## Status of components

| Component | State |
| --- | --- |
| `bundle.py` — markdown + figure index, `cap_markdown` | ✅ done (7 tests) |
| `artifacts.py` — content-addressed store, traversal guards, TTL | ✅ done (16 tests) |
| `marker_client.py` — async, page-batched, typed errors | ✅ done |
| `html_to_markdown.py` — real markdown tables | ✅ done |
| `marker_to_bundle.py` — blocks → markdown + figures | ✅ done (12 tests) |
| `marker_service/` + `docker-compose.yml` | ✅ done |
| **Task 1** — arXiv PDF acquisition | ✅ done (7 tests) |
| **Task 2** — job store + `get_job` | ✅ done (8 tests) |
| **Task 3** — bundle assembly + cache | ✅ done (9 tests) |
| **Task 4** — `fetch_paper` + artifact route + server wiring | ✅ done |
| **Task 5** — on-device verification against real Marker | ✅ **12 passed, 0 failed** |

---

### Task 1: arXiv PDF acquisition

**Files:** Create `src/paper_mcp/pipelines/arxiv_pdf.py`; remove `arxiv_source.py` (e-print tarball path — Marker ingests PDFs). Test `tests/test_arxiv_pdf.py`.

**Produces:** `async def fetch_arxiv_pdf(arxiv_id: str) -> bytes`

Keep from the tarball port: the export-mirror → main-site promotion on the size-cap signature (bytes received, then the peer hangs up — retrying the same offset hits the same wall), and the contactable User-Agent arXiv's ToU requires.

- [ ] Failing tests: mirror promotion on the size-cap signature; a 429 surfaces as typed `RateLimitedError`; a non-PDF body is rejected rather than handed to Marker.
- [ ] Implement, run, commit.

### Task 2: Job store

**Files:** Create `src/paper_mcp/jobs.py`; Test `tests/test_jobs.py`

**Produces:** `class JobStore` — `submit(kind, content_key, fn) -> str`, `get(job_id) -> JobStatus | None`, `sweep(ttl)`; `class JobStatus(BaseModel)` — `job_id, state ∈ {queued,running,done,error}, progress, result_key, error`

Jobs are **coalesced by `content_key`**: two callers asking for the same uncached paper join one job rather than starting two GPU extractions. Marker work is **globally serialized** by a single-slot semaphore — concurrency on a 6 GB card means OOM, not throughput.

- [ ] Failing tests: two submissions for one key share a job id; a failing job records a typed error rather than vanishing; `get` on an unknown id returns `None`; the semaphore serializes.
- [ ] Implement, run, commit.

### Task 3: Bundle assembly + cache

**Files:** Create `src/paper_mcp/pipelines/build_bundle.py`; Test alongside.

**Produces:** `async def build_bundle(paper: PaperRef, *, store, marker) -> Bundle`, `def load_cached(key, *, store) -> Bundle | None`

Flow: content key → cache hit returns immediately → else fetch PDF → Marker (batched by `MARKER_MAX_PAGES`) → `marker_doc_to_bundle_parts` → write `bundle.json`, `markdown.md`, `figures/`, `bundle.zip` → `Bundle` with resolvable `image_url`s.

- [ ] Failing tests: cache hit does no network; figure URLs resolve through the store; `extraction.warnings` survives into the bundle; a Marker failure produces a typed error, not a half-written cache entry.
- [ ] Implement, run, commit.

### Task 4: `fetch_paper`, artifact route, wiring

**Files:** Create `src/paper_mcp/tools/fetch.py`, `src/paper_mcp/api/artifacts.py`; modify `server.py`.

**Produces:** `async def tool_fetch_paper(paper_id: str) -> Bundle | JobHandle`, `async def tool_get_job(job_id: str) -> JobStatus`, `GET /a/{token}/{rel:path}`

`fetch_paper` returns the bundle directly on a cache hit and a job handle otherwise. `/health` reports Marker reachability, since it is a required dependency.

- [ ] Failing tests: cache hit returns a bundle; miss returns a job handle; unknown job id is a typed error; the artifact route serves a figure and refuses `../` traversal.
- [ ] Implement, run, commit.

### Task 5: On-device verification

- [ ] Extend `scripts/on_device_check.py`: `fetch_paper` on a real arXiv id → job → poll → bundle; assert the markdown contains a real markdown table (`| --- |`), at least one `$$` equation, and ≥1 figure whose `image_url` downloads with image magic bytes; assert a second call is a fast cache hit; assert traversal on the artifact route is refused.
- [ ] Run against the real Marker container. Commit.

**This is the acceptance gate for the whole plan.** Unit tests cannot tell whether Marker's output is *faithful* — only a real extraction of a real paper can.

## Lessons carried forward

1. Never `stdout=PIPE` without draining — a full pipe freezes the server (measured: frozen at request 61).
2. `asyncio.wait_for` does not bound uncancellable work; use `asyncio.wait`.
3. Blocking calls on the event loop serialize the server (20.5s → 0.7s once threaded).
4. Verify fixtures against the live API before trusting a red result.
5. `cmd | tail` reports **tail's** exit code — use `set -o pipefail`.
6. The unit suite is necessary, never sufficient.


---

## Outcome — verified 2026-08-13

`scripts/paper_workflow_check.py 1706.03762`, cold cache, against the real
Marker container:

```
extraction completed             332s (15 pages)
markdown has substance           43,358 chars
structure survived as headings   26 headings
tables survived as markdown      58 table rows, separator present
equations survived as LaTeX      10 display-math blocks
figure index populated           6 figures, 6/6 captioned
figure image downloads           HTTP 200, 66,822 bytes, real JPEG
extraction warnings              none
second call is a cache hit       0.01s
artifact route refuses traversal HTTP 404
```

Three defects the unit suite could not have found, each caught by this check:

1. **`PAPER_MCP_LOG_LEVEL=info` crashed the server at boot** — `basicConfig`
   rejects lower case. This project's own compose file used `warning`, so the
   container would have died on first start.
2. **The check's own image-magic comparison was wrong**, reporting a valid
   JPEG as not-an-image.
3. **Three tables were falsely reported unrenderable.** The enriched warning
   showed the cause: Marker `TableGroup` wrappers whose html is a
   `<content-ref>` pointing at the real table block. The tables had rendered
   correctly all along; a false "data missing" warning is worse than none.

Only (1) and (3) were product defects, and neither was reachable without
running the real workflow against real Marker.
