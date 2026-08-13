# paper-mcp — Software Requirements Specification

**Status:** v1.0 · **Date:** 2026-08-13
**One spec per project.** This document is the single authoritative specification for `paper-mcp`. Architecture, schema, and scope questions are answered here before code.

---

## Revision History

| Version | Date | Change |
| --- | --- | --- |
| **v1.0** | 2026-08-13 | **Scope reduced to one job: turn a PDF into agent-ready structure.** The surface becomes two tools — `extract_pdf` and `get_job`. Discovery, paper fetching, and LaTeX compilation are removed. *Discovery* because it does not work well enough to ship: measured across a full verification session, `search_papers` returned a typed `rate_limited` on every attempt without an API key and never once produced a result, and `search_arxiv` surfaced none of the three canonical WMT papers for a query naming that exact benchmark. The stable parts (`find_related` in all three modes, id-based `resolve_paper`) go with them, because a mediocre discovery surface next to a good extraction surface teaches a calling agent to distrust both — and an agent already has better ways to find a paper. *Fetching* because the caller can fetch its own PDF, and content-addressed caching keyed on the bytes (`content_key(data=…)`, already implemented) preserves the deduplication that `fetch_paper`'s arXiv cache provided. *LaTeX* because authoring a deck is the caller's pipeline, not this service's. The consequences are larger than the deletions: nsjail and five TeX Live package sets leave the image (~1 GB → a fraction of it), `seccomp=unconfined` is no longer required, and **the threat model that dominated v0.1–v0.4 — a public endpoint executing untrusted TeX — ceases to exist rather than needing defence.** One risk is *added*: accepting caller-supplied PDFs removes the arXiv-only restriction that earlier revisions identified as the control over who may feed `marker-pdf`'s unpatchable `pillow` (section numbers in the rows below refer to the document as it stood at that revision). On a shared endpoint that risk is borne by every caller, so the control is replaced rather than dropped (NFR-02, §III-6). Finally, without an arXiv lookup there is no verified bibliographic metadata, so the bundle stops asserting any: `PaperRef` is replaced by observed facts (§III-3). |
| **v0.4** | 2026-08-13 | **Security is verified against the running service, and artifact URLs are derived rather than stored.** Two verification layers are added to §III-10 as release gates, run from local harnesses rather than committed scripts: an attack matrix against the **container** with a real IdP (26 controls: token forgery incl. `alg=none` and RS256→HS256 confusion, audience/issuer/expiry/signature, uniform rejection bodies, `Host` rebinding, six traversal encodings, quota + `Retry-After`, sandbox probes through the real tool), and its converse — the whole product driven through a real MCP client **with the guards armed**, because a service that rejects everyone is trivially secure and useless. Neither may use an in-process test client: `TestClient` follows redirects, which is exactly how a `307` on `POST /mcp` passed the suite. A security check must also prove the **installed** artifact carries the controls it attacks — an early run reported a total auth bypass that existed only in a stale image. That layer immediately paid for itself by finding a defect no unit test could see: §III-4 bundles **persisted absolute artifact URLs**, so a cache surviving a redeploy on a new origin (a named volume, by design) replayed dead figure links while the files sat intact on disk — silent, because the bundle still looked complete. A URL is deployment state, not content: it is now materialized on every serve from the current `public_base_url`, and `bundle.json` stores no origin at all. |
| **v0.3** | 2026-08-13 | **Purpose sharpened: ready-to-use TOOLS for precise paper processing — data first, tools not flows.** The service provides *data-processing functionality*; the pipelines built on it (slides, summaries, literature reviews) belong to the calling agent and its own skills, which can assemble them freely. Consequences: the bundle collapses to **markdown + a figure index** (FR-03), because that is what an agent needs to work precisely — the section index, equation index, and outline/full duality are removed, since Marker emits headings an agent navigates natively and inlines equations as LaTeX, making a parallel index a second copy to keep honest. `get_section` is deleted; FR-04 becomes artifact retrieval. `compile_latex` stays, explicitly **as a tool rather than an agent flow** — it compiles and returns structured errors; it never loops, revises, or authors. Portable skills (FR-10) are demoted from a headline deliverable to a convenience: useful starting points, but the calling agent owns its pipelines. |
| **v0.2** | 2026-08-13 | **Marker is the required PDF extraction engine; the PyMuPDF fallback is removed.** Correcting spec drift: v0.1 recorded a `default` (CPU, PyMuPDF) compose profile with Marker as an opt-in `gpu` upgrade, and claimed "v1 is not blocked on GPU capacity." That contradicted the scope actually chosen for v1 and, worse, re-proposed an experiment PaperHub had already run and reversed — its v2.19 entry records crude PyMuPDF figure extraction producing "conference-UNusable" output (hallucinated `\includegraphics`, wrong figures from filename collisions), replaced by Marker-based structured ingestion. This service exists to supply *faithful* extraction, so quietly substituting unfaithful extraction is the one degradation it must not offer. Consequences: `fetch_paper`'s engine enum drops `pymupdf` (FR-03); Marker ships as a required component rather than a profile (FR-11); a Marker-less host reports `extraction_unavailable` for PDFs instead of degrading (§III-9); PDF ingestion is asynchronous by necessity, since PaperHub measured 21 minutes for a 5-page dense batch (§III-9, FR-06). PyMuPDF stays only for rasterizing individual vector figure files in the LaTeX path — image conversion, not document understanding. |
| **v0.1** | 2026-08-11 | **Founding spec.** `paper-mcp` is extracted from the **PaperHub** proof-of-concept, which validated the end-to-end flow (acquire a paper → structured extraction → grounded slide authoring → LaTeX compile) inside a single-user local application. PaperHub proved the *pipeline*; it could not be the *product*, because the delivery target is a **remote, multi-user, internet-facing MCP service** that external agent clients (Claude Cowork, Claude Desktop, Cursor, any MCP framework) connect to. The two have incompatible trust models — PaperHub is single-user with no auth, DB-centric, and never handles untrusted input; this service is public, authenticated, stateless, and **executes caller-supplied LaTeX**. Therefore: a new repository, a one-way dependency arrow (this project depends on nothing in PaperHub; PaperHub may later become a *client*), and the pipeline code **copied + adapted with provenance comments** — the same decomposition posture PaperHub itself used on `paper2slides-plus`. Founding decisions recorded here: stateless-by-design (§NFR-01), eight-tool surface (§III-2), inline-structured + content-addressed-artifact delivery (§III-4), nsjail-in-container sandbox (§III-6), OIDC resource-server auth (§III-7), portable **skills** as the third deliverable (§III-8), single container bundle with an optional GPU profile (§III-9). |

---

# Part 0 — Requirement Coverage Matrix

| Concern | Covered by |
| --- | --- |
| An agent can turn a PDF it holds into precise, agent-ready structure | FR-01 (`extract_pdf`), §III-3 |
| Extraction longer than a remote call budget still completes | FR-02 (`get_job`), §III-5 |
| Figures and full text are retrievable without bloating the response | FR-03, FR-04, §III-4 |
| Multiple users share one endpoint without leaking data | NFR-01 (statelessness — nothing per-user exists to leak), FR-05 (identity for quota only) |
| A caller's malicious PDF cannot harm the host or other callers | NFR-02, §III-6 (Marker containment); acceptance §I-8 #5 |
| Shared GPU is not starved by one caller | FR-06 (per-subject quota, GPU serialization) |
| Extraction never loses data silently | FR-01 (cell accounting), acceptance §I-8 #3 |
| A figure citation always resolves to what its caption claims | FR-01 (figure index integrity), acceptance §I-8 #4 |
| Operability without user-data collection | FR-08, NFR-01 |

---

# Part 1 — Software Requirements Specification

## I-1. Background

`paper-mcp` supplies one capability that agents cannot do well themselves: turning a paper PDF into structure faithful enough to compute on. The extraction pipeline is ported from the [PaperHub](https://github.com/whats2000/PaperHub) proof-of-concept, which validated it inside a single-user local application. PaperHub proved the pipeline; the delivery target here is a remote, multi-user MCP service.

v1.0 narrows the service to that one capability. Everything else it offered — finding papers, downloading them, compiling LaTeX — either did not work well enough to ship, or is work the calling agent is already better placed to do.

## I-2. Problem Statement

An agent handed a paper PDF has no reliable way to read it precisely. Naïve text extraction destroys exactly what matters: tables flatten into a blob of cell values, equations degrade into prose approximations, and figures are lost or invented. An agent that reads `27.3 | 38.1` under a heading of `Training Cost (FLOPs)` will report a BLEU score as a compute budget, and nothing about the output looks wrong.

The service exists to make that failure impossible, or — where the underlying extraction genuinely loses data — impossible to *miss*.

## I-3. Users

External agent clients (Claude Cowork, Claude Desktop, Cursor, any MCP framework) acting for a human researcher, on a shared endpoint. A caller is a quota subject, never an account.

## I-4. Use Cases

1. A coworker hands an agent a PDF; the agent uploads the bytes and reads the paper's structure.
2. An agent extracts results tables from several papers in one domain and joins them into a dataset, carrying provenance and knowing which tables were incomplete.
3. An agent answers "show me the architecture diagram" by resolving a figure id to an image that genuinely contains it.

## I-5. Functional Requirements

| ID | Name | Description |
| --- | --- | --- |
| **FR-01** | PDF extraction | `extract_pdf(content_base64, filename?)` returns the document as **markdown plus a figure index** — the two things an agent needs to work precisely. Markdown carries real tables (rows and columns intact), equations as LaTeX, and Marker's heading structure; the index carries `{id, caption, page, image_url}` per extracted figure. **Marker produces it**; nothing re-derives or "improves" its output. Keyed `sha256:<hex>` of the bytes, so a repeat upload — by the same caller or a different one — is a cache hit and joins one extraction. Markdown is capped at 200k characters with an explicit `markdown_truncated` flag; the full text always remains in the artifact zip. Uploads over `PAPER_MCP_MAX_UPLOAD_BYTES` (default 25 MB) are rejected at the boundary with a typed error. **Extraction that loses data must say so:** when a rendered table carries fewer cells than Marker found, the shortfall is reported in `extraction.warnings`, because a silently truncated table is indistinguishable from a complete one. **A figure is indexed once:** a figure whose bounding box lies wholly inside another figure's on the same page is a panel of it and is not indexed separately, since both inherit the same caption and the crop would resolve to an image the caption misdescribes. |
| **FR-02** | Job lifecycle | Extraction exceeds a remote-call budget, so `extract_pdf` returns either a completed bundle (cache hit) or `{job_id, state}`. `get_job(job_id)` returns `{state ∈ {queued, running, done, error}, progress?, result?, error?}`. Jobs are ephemeral runtime records (§III-5) with a TTL, keyed by an unguessable id, holding **no caller identity beyond the quota subject**. Two callers uploading identical bytes join one job. |
| **FR-03** | Artifact retrieval | Bundle artifacts (figure images, the full markdown, the zip) are served at `GET /a/<token>/<path>` from the content-addressed store. `token` is unguessable, `path` is validated against traversal, and an unknown or expired token returns a typed error naming `extract_pdf` as the next step — never a silent empty. |
| **FR-04** | Artifact store | Bundles are written to a **content-addressed** store (§III-4) and served over HTTP at an unguessable path with an expiry. A TTL sweeper reclaims them. Artifacts hold only derived data; no identity, no session, no DB row. Artifact URLs are **derived at serve time** from the configured public base URL and never persisted (v0.4). Re-deriving an expired artifact is always possible from its key, given the same bytes. |
| **FR-05** | Authentication | The service is an **OIDC resource server**: it validates a bearer JWT against a configured issuer + audience (JWKS fetched and cached) and rejects anything else with `401`. It is **not** an authorization server — the operator brings an IdP. Identity exists for **quota and revocation only**; it grants no data scoping, because there is no per-user data (NFR-01). An `AUTH_MODE=open` setting exists for local development and closed networks and **must** be logged loudly at boot. |
| **FR-06** | Quota + fair use | Per-subject token buckets on (a) calls/minute and (b) extractions/hour, configured by the operator. Marker GPU work is **globally serialized** (one dense page can saturate ~6 GB VRAM — a PaperHub-measured constraint). Exceeding a bucket returns a typed `quota_exceeded` error with `retry_after`, never a hang. Unauthenticated mode falls back to per-IP buckets. |
| **FR-07** | Containerized deployment | `docker compose up` starts the service **and** Marker; Marker is a required component, not an opt-in profile, so the NVIDIA container toolkit and a GPU are baseline requirements. A GPU-less host reports `extraction_unavailable` rather than degrading silently — substituting unfaithful extraction is the one degradation this service must not offer (v0.2). `MARKER_MAX_PAGES` bounds per-call VRAM. The service image carries no TeX distribution and no nsjail, and runs under Docker's default seccomp profile. |
| **FR-08** | Observability | Structured JSON logs per call: tool name, subject hash, duration, outcome, artifact key, extraction engine. **Request bodies and document content are never logged.** API keys and paths are redacted. A `/health` endpoint reports service and Marker reachability, and — when Marker's accuracy pass is enabled — the **model name** backing it, so `use_llm: true` can be checked against a model that still exists rather than merely asserting a key was present. |

## I-6. Non-Functional Requirements

| ID | Category | Target |
| --- | --- | --- |
| **NFR-01** | **Statelessness** | No user accounts, no sessions, no history, no per-user library, **no server-side LLM calls**. The only persistence is a content-addressed artifact cache of derived data and an ephemeral job table. *This is the security architecture*: a shared endpoint is safe because there is nothing per-user to cross. Any proposal introducing per-user state must first amend this requirement. |
| **NFR-02** | **Decoder containment** | Marker is treated as a sandbox boundary, not a trusted internal service. `marker-pdf` pins `pillow<11` at every released version while current image-decode advisories are fixed in 12.3.0; upgrading is not available, so the control is what an exploit can reach. The Marker container runs with **no network egress, non-root, a read-only root filesystem with a tmpfs scratch, all capabilities dropped, and a hard memory limit**, published on loopback only. Uploads are size-capped (FR-01) and page-capped. Verified by an adversarial corpus that is a **release gate** (§I-8 #5). |
| **NFR-03** | Latency | Cache hit ≤ 500 ms. Extraction is asynchronous by default; measured warm throughput is ~10 s per page on a 6 GB consumer GPU, so a 15-page paper completes in ~2.5 minutes. |
| **NFR-04** | Typing | Python 3.12+, Pydantic v2 models on every tool boundary, `mypy --strict` clean, `ruff` clean. Tool input schemas are generated from the models — one source of truth. |
| **NFR-05** | Declared scope per tool | Every tool declares its scope in its description (network egress, filesystem reach, resource ceiling). With v1.0 the honest declaration is narrow: **the service makes no outbound network calls at all.** Out-of-scope arguments are rejected at the boundary with a typed error, never partially executed. |
| **NFR-06** | Provenance | Every file copied from PaperHub carries a header comment naming the source path and commit. Adaptation is expected; silent copying is not. The dependency arrow points one way — `paper-mcp` imports nothing from PaperHub. |
| **NFR-07** | Reproducibility | Pinned dependencies via `uv` lockfile; pinned base image digest. **A pinned application does not imply a pinned runtime:** `marker-pdf` is pinned at 1.10.2 while its transitive `torch` floated to 2.13, which dispatches through Triton and JIT-compiles a CUDA module on first use — so the image must carry `gcc` *and* `libc6-dev`, or every GPU inference fails at runtime with a healthy-looking service. Runtime toolchain requirements are part of reproducibility, not build convenience. |

## I-7. Out of Scope

- **No discovery.** No search, no citation graph, no resolution by title or DOI. Removed in v1.0 on measured evidence, not preference.
- **No paper fetching.** The caller supplies bytes. The service makes no outbound requests.
- **No LaTeX compilation, no document generation.** Authoring belongs to the calling agent.
- **No server-side LLM inference.** *(The one exception is Marker's operator-configured `use_llm` extraction-accuracy pass, an internal quality knob on a deterministic pipeline, never exposed as a tool.)*
- **No user accounts, profiles, libraries, or history.** Identity is a quota key (FR-05).
- **No vector store or embeddings.** Navigation is structural.
- **No multi-node HA or horizontal autoscaling in v1.** Single host, single artifact volume, behind an interface so an S3 backend can be added without touching tool code.
- **No document viewer or web frontend** beyond artifact download endpoints and `/health`.

## I-8. Acceptance Criteria

1. **Connector reachability.** A remote MCP client pointed at the deployed URL completes the OAuth flow, lists both tools, and completes an extraction.
2. **Extraction fidelity, verified against source.** For a fixture corpus, values extracted from tables match the PDFs read directly — verified per value, not spot-checked. Where the same figure is reported by two different papers, the extracted values agree.
3. **Never silently lossy (release gate).** For a table whose render drops cells, `extraction.warnings` reports the shortfall with counts. A run that loses data without a warning fails this criterion.
4. **Figure index integrity (release gate).** Every indexed figure resolves over HTTP to an image whose content matches its caption, no two index entries share a caption, and no entry is a sub-panel of another. Verified by opening the images, not by counting them.
5. **Containment holds (release gate).** An adversarial PDF corpus — malformed streams, decompression bombs, oversized and zero-byte inputs, encrypted files — fails closed with a typed error, with no egress from the Marker container, no process outliving its limits, and no host filesystem write outside the jail. Any failure blocks release.
6. **Statelessness verified.** After a full functional run, the deployment holds no record linking any subject to any document. Verified by inspecting every persisted store.
7. **Quota enforced.** A caller exceeding each configured bucket receives `quota_exceeded` with `retry_after`; a second caller is unaffected during that window.
8. **No silent failure.** Every error path — unreadable PDF, oversize upload, extraction failure, expired artifact, quota — returns a **typed, actionable** error naming the next step.

---

# Part 2 — Technology Selection Analysis

## II-1. Why stateless, when the PoC was database-centric?

PaperHub is single-user and stores everything. A shared endpoint that stores per-user data must then defend it. Storing nothing removes the class of failure rather than defending against it: there is no per-user record to leak, so a leak requires inventing state first (NFR-01).

## II-2. Why remove discovery rather than fix it?

The failure was measured, not assumed. Across a full verification session, `search_papers` returned `rate_limited` on every attempt and never once produced a result without an API key; `search_arxiv` — which needs no key and does work — returned none of the three canonical papers for a query naming the exact benchmark those papers introduced. Requiring an operator to obtain a third-party API key so that one tool becomes usable is a poor trade when the capability is not the product.

The stable parts were removed with the rest. `find_related` worked reliably in all three modes and `resolve_paper` worked for arXiv and S2 ids, but a tool surface teaches a calling agent what the service is for. A narrow surface that is entirely trustworthy is worth more than a broad one where two tools are excellent, two are adequate, and one never works.

## II-3. Why containment rather than validation, for untrusted PDFs?

The advisories in `marker-pdf`'s dependency tree are image-decode bugs in `pillow`, reachable through images that are otherwise perfectly legitimate. Pre-validating a PDF cannot reliably detect one; the payload is a valid image. Capping size and page count defeats resource exhaustion, but the decode path stays reachable by design — the service exists to decode documents.

So the control is what an exploit reaches, not whether it triggers. A Marker container with no egress, no capabilities, a read-only root, and a memory ceiling turns a decoder RCE from a foothold into a dead end. This is the same posture v0.1 chose for LaTeX with nsjail, applied to the one untrusted input that remains.

## II-4. Why accept uploads at all, when arXiv-only was a control?

Because the restriction protected less than it appeared to. Anyone can publish a PDF to arXiv, so "arXiv-only" bounded *casual* access to the decoder rather than establishing trust in the content. Meanwhile it excluded the service's central use case: a colleague hands you a paper. Trading a weak control for the actual product is worthwhile only if the strong control replaces it, which is what NFR-02 requires.

## II-5. Why an OIDC resource server rather than our own auth?

Issuing credentials means storing them, rotating them, and being responsible for their compromise — all of which contradict NFR-01. Validating someone else's tokens requires no stored secret beyond a cached public key.

---

# Part 3 — System Architecture

## III-1. Overview

Two containers. `paper-mcp` serves the MCP endpoint, owns the artifact store and job table, and makes **no outbound network calls**. `marker` performs extraction on the GPU, published on loopback only, reachable solely from `paper-mcp` over the compose network.

```
agent client ──MCP/HTTP──> paper-mcp ──compose net──> marker (GPU, no egress)
                              │
                              └── artifact store (content-addressed, TTL)
```

## III-2. MCP surface

Two tools: `extract_pdf` (FR-01) and `get_job` (FR-02). Both declare their scope in their descriptions per NFR-05.

## III-3. The bundle

```
{ document: {content_sha256, bytes, pages, title?, filename?},
  markdown: str, markdown_truncated: bool,
  figures: [{id, caption, page, image_path, image_url}],
  extraction: {engine, pages, warnings[]},
  artifact: {zip_url, bytes, expires_at} }
```

`document` states only what was observed. Without an arXiv or Semantic Scholar lookup there is no verified bibliographic record, and the service must not assert one: an agent that cites a title the document does not carry has been misled by its tool. `title` is best-effort from the first heading Marker found and is explicitly derived; `filename` is a caller-supplied label, echoed rather than trusted. Callers needing authoritative metadata resolve it themselves, where the provenance is theirs.

## III-4. Artifact store

Content-addressed by `sha256` of the input bytes, sharded two levels. Each entry holds `bundle.json`, `markdown.md`, `figures/`, and `bundle.zip`. Served at `GET /a/<token>/<path>` with traversal made impossible by construction rather than detected. URLs are materialized at serve time from the current public base URL and never persisted (v0.4).

## III-5. Job model

An ephemeral table keyed by unguessable id, holding state, progress, and a content key. Jobs deduplicate on the content key so concurrent identical uploads join one extraction. Records carry no identity beyond the quota subject and expire on a TTL.

## III-6. Containment

Marker is the boundary (NFR-02): loopback-published, no egress, non-root, read-only root with tmpfs scratch, `cap-drop ALL`, memory-limited. `paper-mcp` itself needs no elevated privileges — with LaTeX removed, `seccomp=unconfined` is gone and the default profile applies.

## III-7. Auth and quota

Bearer JWT validated against issuer, audience, expiry, and signature, with uniform rejection bodies so failures are not an oracle. DNS-rebinding protection via an allowed-hosts list. Per-subject token buckets (FR-06); GPU work globally serialized.

## III-8. Deployment

One compose file, two services, two named volumes (artifacts, Marker weights). The Marker image must carry a C toolchain (`gcc` + `libc6-dev`) for Triton's runtime JIT (NFR-07), and pins its Gemini model explicitly — marker-pdf's own default has been retired once already, and the failure was silent: every call answered 404 while the service reported healthy.

## III-9. Testing

Unit tests are fast and offline, with Marker and the network faked. Two layers run against the **running container** and are release gates: an adversarial matrix (auth, traversal, quota, malicious PDFs) and its converse — the product driven through a real MCP client with the guards armed, because a service that rejects everyone is trivially secure and useless. Neither may use an in-process test client, which follows redirects and hides transport defects. A check must prove the **installed** artifact carries the controls it attacks; an early run reported a bypass that existed only in a stale image.

Fidelity is verified against source documents, not fixtures alone: extracted values are compared to the PDFs read directly, and agreement between two papers reporting the same number is treated as evidence.

## III-10. Provenance contract

Every file copied from PaperHub carries:

```
# Ported from PaperHub `backend/src/paperhub/pipelines/<file>.py` @ <commit>.
# Adapted: <what changed and why>.
```

## III-11. Closing principle

The service supplies data an agent can compute on. Its single obligation is that the data be faithful — and where faithfulness fails, that the failure be loud. A wrong number that looks right is the only outcome worth blocking a release over.
