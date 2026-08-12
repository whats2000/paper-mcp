# paper-mcp Plan B1 — Artifact store + arXiv bundle

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax.

**Goal:** `fetch_paper("arxiv:1706.03762")` returns an agent-ready bundle — metadata, ordered sections with markdown, figure manifest, equations — plus a content-addressed zip URL; and `get_section(bundle_id, section)` reads one section back without unzipping.

**Architecture:** A content-addressed artifact store on disk keyed `arxiv:<id>` / `sha256:<hex>`. The arXiv-LaTeX path is synchronous (seconds); PDF extraction and the job model are deliberately **deferred to B2**, so this plan ships one complete, fast path rather than half of three.

**Tech Stack:** Existing Phase A stack + `pymupdf`, used solely to rasterize vector *figure files* (`.pdf`/`.eps` referenced by `\includegraphics`) into PNGs. That is image conversion of one already-identified figure, not document extraction — PDF *understanding* is Marker's job and Marker's alone (SRS v0.2).

## Global Constraints

Everything from Plan A's Global Constraints applies unchanged (uv only, `mypy --strict`, Pydantic v2, provenance headers on ported files, no server-side LLM, Conventional Commits, scope declared per tool). Additionally:

- **No user state.** The artifact store holds derived data from public papers keyed by content hash. No identity, no session, no DB row (SRS NFR-01).
- **Every tool call stays inside `_TOOL_BUDGET_S`** (Phase A's `_bounded`). A fetch that cannot finish in budget is a B2 job, not a longer wait.
- **Path traversal is a security boundary, not a nicety.** Tarball members and `get_section`/artifact paths are attacker-controlled input on a public service.

## Scope

| In B1 | Deferred to B2 |
| --- | --- |
| Artifact store + TTL sweep | PDF extraction (Marker, required) |
| arXiv e-print source download | Job store + `get_job` |
| LaTeX → sections/figures/equations | `fetch_paper(url=...)` for arbitrary PDFs |
| `fetch_paper` (sync, arXiv) · `get_section` | Unpaywall-sourced PDF ingestion |

## File Structure

| File | Responsibility |
| --- | --- |
| `src/paper_mcp/bundle.py` | `Bundle`, `SectionRef`, `SectionContent`, `FigureRef`, `EquationRef`, `ArtifactRef` |
| `src/paper_mcp/artifacts.py` | Content-addressed store: key → dir, opaque URL token, TTL sweep |
| `src/paper_mcp/pipelines/arxiv_source.py` | e-print tarball download + safe unpack (ported) |
| `src/paper_mcp/pipelines/latex_extract.py` | flatten `\input`, strip preamble (ported) |
| `src/paper_mcp/pipelines/latex_asset.py` | figures/equations/sections from flattened LaTeX (ported) |
| `src/paper_mcp/pipelines/sections.py` | **new** — slice flattened LaTeX into per-section markdown |
| `src/paper_mcp/tools/fetch.py` | `fetch_paper`, `get_section` |
| `src/paper_mcp/api/artifacts.py` | `GET /a/<token>/…` static artifact serving |

---

### Task 1: Bundle models

**Files:** Create `src/paper_mcp/bundle.py`; Test `tests/test_bundle.py`

**Interfaces — Produces:**
- `class FigureRef(BaseModel)`: `id, caption, page, section, image_path, image_url`
- `class EquationRef(BaseModel)`: `id, latex, section`
- `class SectionRef(BaseModel)`: `name, order, char_count, markdown: str | None`
- `class SectionContent(BaseModel)`: `bundle_id, name, order, markdown`
- `class ArtifactRef(BaseModel)`: `zip_url, bytes, expires_at`
- `class Bundle(BaseModel)`: `bundle_id, paper: PaperRef, sections: list[SectionRef], figures, equations, extraction: ExtractionInfo, artifact: ArtifactRef | None`
- `class ExtractionInfo(BaseModel)`: `engine: Literal["latex","marker"]`, `warnings: list[str]`
- `def outline(bundle: Bundle) -> Bundle` — returns a copy with every `SectionRef.markdown` set to `None`

- [ ] **Step 1: Write the failing test**

```python
def test_outline_drops_section_markdown_but_keeps_the_index() -> None:
    full = Bundle(
        bundle_id="arxiv:1706.03762",
        paper=PaperRef(paper_id="arxiv:1706.03762", title="T", source="arxiv"),
        sections=[SectionRef(name="Intro", order=1, char_count=4, markdown="body")],
        extraction=ExtractionInfo(engine="latex"),
    )

    trimmed = outline(full)

    assert trimmed.sections[0].markdown is None
    assert trimmed.sections[0].char_count == 4      # the index survives
    assert full.sections[0].markdown == "body"      # original untouched
```

- [ ] **Step 2:** Run `uv run pytest tests/test_bundle.py -v` — expect FAIL (module missing).
- [ ] **Step 3:** Implement `bundle.py` with the models above. `outline` uses `model_copy(deep=True)` then clears `markdown`; it must not mutate its argument.
- [ ] **Step 4:** Run tests + `ruff` + `mypy` — expect PASS.
- [ ] **Step 5:** Commit — `feat(bundle): add bundle models with an outline projection`.

**Why `outline` exists:** a full paper's markdown routinely exceeds 100k characters. Inlining it by default would flood the caller's context on the first call (SRS §III-3). Outline-first makes `get_section` the natural second step.

---

### Task 2: Content-addressed artifact store

**Files:** Create `src/paper_mcp/artifacts.py`; Test `tests/test_artifacts.py`

**Interfaces — Produces:**
- `def content_key(*, arxiv_id: str | None = None, data: bytes | None = None) -> str` → `arxiv:<id>` or `sha256:<hex>`
- `def token_for(key: str) -> str` — HMAC-derived opaque path token
- `class ArtifactStore`: `__init__(root: Path)`, `dir_for(key) -> Path`, `url_for(key, rel) -> str`, `resolve(token, rel) -> Path`, `write_zip(key) -> Path`, `sweep(ttl_hours) -> int`

- [ ] **Step 1: Write the failing tests**

```python
def test_key_is_stable_and_content_addressed(tmp_path: Path) -> None:
    assert content_key(arxiv_id="1706.03762") == "arxiv:1706.03762"
    assert content_key(data=b"abc") == content_key(data=b"abc")
    assert content_key(data=b"abc") != content_key(data=b"abd")


def test_token_does_not_leak_the_key(tmp_path: Path) -> None:
    token = token_for("sha256:deadbeef")
    assert "deadbeef" not in token
    assert len(token) >= 32          # unguessable


def test_resolve_refuses_path_traversal(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    token = token_for("arxiv:1")
    store.dir_for("arxiv:1").mkdir(parents=True)
    for evil in ("../../etc/passwd", "..\\..\\secrets", "/etc/passwd", "a/../../b"):
        with pytest.raises(InvalidArgumentError):
            store.resolve(token, evil)


def test_sweep_removes_only_entries_past_ttl(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    fresh, stale = store.dir_for("arxiv:new"), store.dir_for("arxiv:old")
    for d in (fresh, stale):
        d.mkdir(parents=True)
        (d / "bundle.json").write_text("{}")
    old = time.time() - 60 * 60 * 48
    os.utime(stale / "bundle.json", (old, old))
    os.utime(stale, (old, old))

    removed = store.sweep(ttl_hours=24)

    assert removed == 1
    assert fresh.exists() and not stale.exists()
```

- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3: Implement.** Layout `<root>/<sha[:2]>/<sha>/`. `token_for` = `hmac.new(secret, key, sha256).hexdigest()` where the secret comes from `PAPER_MCP_ARTIFACT_SECRET` (generated per-process if unset, with a warning — a restart then invalidates outstanding URLs, which is acceptable for derived data). `resolve` must reject any `rel` that, after `(dir / rel).resolve()`, does not stay under `dir.resolve()`.
- [ ] **Step 4:** Tests + gates PASS.
- [ ] **Step 5:** Commit — `feat(artifacts): add content-addressed store with TTL sweep`.

**Security note for the implementer:** `resolve` takes a caller-supplied path on a public service. Reject absolute paths, `..` segments, and anything escaping after symlink resolution — check *after* `.resolve()`, not before, or a symlink defeats it.

---

### Task 3: arXiv source acquisition (ported)

**Files:** Create `src/paper_mcp/pipelines/arxiv_source.py`; Test `tests/test_arxiv_source.py`

Port from PaperHub `pipelines/arxiv_client.py` @ fd65834: `_download_with_resume`, `download_arxiv_source`, `TarballCorrupt`. **Carry over the mirror-promotion logic and the tarball path-traversal guard verbatim in behaviour** — both were learned from real failures.

**Interfaces — Produces:**
- `class TarballCorrupt(RuntimeError)`
- `def download_arxiv_source(arxiv_id: str, *, cache_root: Path) -> Path` → the unpacked `source/` dir

- [ ] **Step 1: Write the failing tests**

```python
def test_unpack_refuses_members_escaping_the_source_dir(tmp_path: Path) -> None:
    # A malicious e-print must not write outside its own directory.
    tar_path = tmp_path / "evil.tar.gz"
    with tarfile.open(tar_path, "w:gz") as tar:
        for name in ("../escaped.tex", "/abs.tex", "ok.tex"):
            data = b"x"
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))

    dest = tmp_path / "out"
    unpack_source(tar_path, dest)

    assert (dest / "ok.tex").exists()
    assert not (tmp_path / "escaped.tex").exists()
    assert not Path("/abs.tex").exists()


def test_corrupt_tarball_raises_a_typed_error(tmp_path: Path) -> None:
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a gzip stream")

    with pytest.raises(TarballCorrupt):
        unpack_source(bad, tmp_path / "out")
```

- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement, splitting `unpack_source(tar_path, dest)` out of the download so the traversal guard is testable without network.
- [ ] **Step 4:** Tests + gates PASS.
- [ ] **Step 5:** Commit — `feat(pipelines): port arXiv e-print download with safe unpack`.

---

### Task 4: LaTeX extraction (ported) + section bodies (new)

**Files:** Create `src/paper_mcp/pipelines/latex_extract.py`, `latex_asset.py`, `sections.py`; Tests alongside.

Port `extract_latex` (+ `_find_main_tex`, `_inline_recursive`) and `latex_source_to_asset` (+ figure staging, caption cleaning, equation collection). **`sections.py` is new** — PaperHub's `SectionAsset` carries only `(name, order)` because it used chunks and rendered HTML; the bundle needs section *text*.

**Interfaces — Produces:**
- `def extract_latex(source_dir: Path) -> LatexExtract` (`main_path`, `flattened_text`, `preamble`)
- `def latex_source_to_asset(latex_source_dir: Path, flattened_text: str, *, source_dir: Path) -> PaperAsset`
- `def split_sections(flattened_text: str) -> list[tuple[str, int, str]]` → `(name, order, body_latex)`
- `def latex_to_markdown(body: str) -> str` — strip commands, keep math as `$…$`, keep paragraphs

- [ ] **Step 1: Write the failing tests**

```python
def test_split_sections_slices_bodies_between_headings() -> None:
    text = r"\section{Intro} alpha \section{Method} beta \subsection{Detail} gamma"

    sections = split_sections(text)

    assert [(n, o) for n, o, _ in sections] == [("Intro", 1), ("Method", 2), ("Detail", 3)]
    assert "alpha" in sections[0][2]
    assert "beta" in sections[1][2]
    assert "alpha" not in sections[1][2]


def test_text_before_the_first_section_is_kept_as_a_preamble_section() -> None:
    # Abstracts live here; dropping them would lose the most useful passage.
    sections = split_sections(r"\begin{abstract} we propose \end{abstract} \section{Intro} a")

    assert sections[0][0].lower() in {"abstract", "preamble"}
    assert "we propose" in sections[0][2]


def test_latex_to_markdown_keeps_math_and_drops_markup() -> None:
    md = latex_to_markdown(r"We use \textbf{attention} where $x^2$ holds \cite{foo}.")

    assert "attention" in md
    assert "$x^2$" in md
    assert "textbf" not in md
```

- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement. `split_sections` uses the ported `_section_map` offsets; a non-empty run before the first `\section` becomes order 0 named `Abstract` when an `abstract` environment is present, else `Preamble`.
- [ ] **Step 4:** Tests + gates PASS.
- [ ] **Step 5:** Commit — `feat(pipelines): port LaTeX extraction and add section splitting`.

---

### Task 5: Bundle assembly + cache

**Files:** Create `src/paper_mcp/pipelines/build_bundle.py`; Test `tests/test_build_bundle.py`

**Interfaces — Produces:**
- `async def build_arxiv_bundle(arxiv_id: str, *, store: ArtifactStore, paper: PaperRef) -> Bundle`
- `def load_cached(key: str, *, store: ArtifactStore) -> Bundle | None`

- [ ] **Step 1: Write the failing tests** — a cache hit returns without touching the network; a rebuild writes `bundle.json` + `bundle.zip`; figures carry resolvable `image_url`s.
- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement: download → `extract_latex` → `latex_source_to_asset` → `split_sections` → assemble `Bundle` → write `bundle.json` + zip → return. Blocking work runs via `asyncio.to_thread` (Phase A lesson: synchronous work on the event loop serializes the whole server).
- [ ] **Step 4:** Tests + gates PASS.
- [ ] **Step 5:** Commit — `feat(pipelines): assemble and cache arXiv bundles`.

---

### Task 6: `fetch_paper` + `get_section` + artifact serving

**Files:** Create `src/paper_mcp/tools/fetch.py`, `src/paper_mcp/api/artifacts.py`; modify `server.py`; Tests alongside.

**Interfaces — Produces:**
- `async def tool_fetch_paper(paper_id: str, include: Literal["outline","full"] = "outline") -> Bundle`
- `async def tool_get_section(bundle_id: str, section: str) -> SectionContent`
- `GET /a/{token}/{rel:path}` serving artifact files

- [ ] **Step 1: Write the failing tests**

```python
async def test_fetch_paper_defaults_to_outline() -> None:
    bundle = await tool_fetch_paper("arxiv:1706.03762")
    assert all(s.markdown is None for s in bundle.sections)
    assert bundle.sections[0].char_count > 0


async def test_get_section_matches_by_name_or_index() -> None:
    by_name = await tool_get_section("arxiv:1706.03762", "Introduction")
    by_index = await tool_get_section("arxiv:1706.03762", "1")
    assert by_name.markdown == by_index.markdown


async def test_expired_bundle_says_so_instead_of_returning_empty() -> None:
    with pytest.raises(ToolError) as exc:
        await tool_get_section("arxiv:0000.00000", "Intro")
    assert "fetch_paper" in str(exc.value)      # names the next step
```

- [ ] **Step 2:** Run — expect FAIL.
- [ ] **Step 3:** Implement; register both tools in `build_mcp_server()` with scope-declaring descriptions (SRS NFR-05), and mount the artifact route.
- [ ] **Step 4:** Tests + gates PASS.
- [ ] **Step 5:** Commit — `feat(tools): add fetch_paper and get_section`.

---

### Task 7: On-device verification

- [ ] **Step 1:** Extend `scripts/on_device_check.py` with: `fetch_paper` on a real arXiv id (cold), a second call proving the **cache hit** is fast, `get_section` by name and by index, an artifact URL that actually downloads, a traversal attempt against `/a/<token>/../../etc/passwd` returning 4xx, and an expired/unknown `bundle_id` returning a typed error.
- [ ] **Step 2:** Run `uv run python scripts/on_device_check.py`; every new check green.
- [ ] **Step 3:** Commit.

**Server output goes to a file, never an undrained pipe** — the harness already does this; do not regress it (see the B-phase note below).

---

## Notes carried from Phase A

These cost real debugging time; do not rediscover them.

1. **Never `stdout=PIPE` without draining.** A full pipe blocks the server inside `write()` and freezes its event loop. Measured: frozen at request 61.
2. **`asyncio.wait_for` does not bound uncancellable work.** `to_thread` cannot be cancelled, so `wait_for` waits for it anyway. Use `asyncio.wait`.
3. **Synchronous work on the event loop serializes the server.** Three concurrent calls: 20.5s → 0.7s once dispatched to threads.
4. **Verify test fixtures against the live API.** A DOI absent from the corpus produced a failure that looked like a code defect.
5. **The unit suite is necessary and not sufficient.** Every defect that mattered in Phase A was found on device, not by pytest.

## Self-Review

**Spec coverage:** FR-03 → Tasks 1, 4, 5, 6 (bundle, `include` default outline, content-addressed key). FR-04 → Task 6 (`get_section`, typed expiry error). FR-07 → Task 2 (store, TTL, unguessable token). NFR-01 → Task 2 (derived data only, no identity). NFR-02-adjacent → Tasks 2, 3 (traversal guards on caller-supplied paths and tarball members).

**Deferred by design:** FR-05 (compile) → Phase C. FR-06 (jobs) → B2, together with PDF extraction — the two are coupled, since it is PDF work that exceeds the request budget.

**Type consistency:** `PaperRef` is Phase A's, unchanged. `Bundle.paper` reuses it rather than redefining metadata. `content_key` returns the same string space as `PaperRef.paper_id` for arXiv (`arxiv:<id>`), so `fetch_paper(paper_id)` needs no translation.

**Known sharp edge, recorded:** `latex_to_markdown` is lossy by nature — tables and custom macros degrade. B1 accepts that and records it in `ExtractionInfo.warnings`; the fidelity path is Marker in B2.
