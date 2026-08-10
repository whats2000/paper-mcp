# paper-mcp Plan A — Scaffold + Discovery Tools

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Stand up the `paper-mcp` Python project and ship the four read-only discovery tools (`search_arxiv`, `search_papers`, `find_related`, `resolve_paper`) over a working MCP endpoint.

**Architecture:** A FastAPI app with a mounted FastMCP streamable-HTTP sub-app. Tool handlers are thin shims over `pipelines/` clients ported from PaperHub. Every tool returns the single normalized `PaperRef` shape, so a caller never branches on which upstream answered. No database, no state, no LLM.

**Tech Stack:** Python 3.12+ · `uv` · Pydantic v2 · FastAPI · `mcp` (FastMCP) · `httpx` · `arxiv` · pytest + pytest-asyncio + respx · ruff · mypy --strict

## Global Constraints

- **Python 3.12+**, dependency management is `uv` only — never `pip`, never `python -m venv`, never system python.
- **`mypy --strict` clean** and **`ruff check` clean** are gates on every task. New code is fully annotated.
- **Pydantic v2** models on every tool boundary. Tool JSON schemas are generated from the models — one source of truth (SRS NFR-04).
- **Provenance header required** on every file ported from PaperHub (SRS NFR-06):
  ```python
  # Ported from PaperHub `backend/src/paperhub/pipelines/<file>.py` @ fd65834.
  # Adapted: <what changed and why>.
  ```
- **No server-side LLM inference anywhere** (SRS NFR-01 / I-7). No `litellm`, no provider SDKs in this project's dependency tree.
- **No state.** No database, no session, no per-user record. Phase A persists nothing at all.
- **Conventional Commits**: `action(scope): imperative subject`.
- **Every tool declares its scope in its description** — network egress and resource ceiling (SRS NFR-05).
- `max_results` is clamped server-side to `[1, 50]` on every discovery tool (SRS FR-01).

---

## File Structure

| File | Responsibility |
| --- | --- |
| `pyproject.toml` | Project metadata, deps, ruff/mypy/pytest config |
| `src/paper_mcp/__init__.py` | Version constant |
| `src/paper_mcp/models.py` | `PaperRef`, `OpenAccess`, id normalization, error taxonomy |
| `src/paper_mcp/config.py` | Env-only settings (SRS §III-9) |
| `src/paper_mcp/pipelines/arxiv_client.py` | arXiv search + exact-id lookup (ported) |
| `src/paper_mcp/pipelines/semantic_scholar.py` | S2 search, citation graph, metadata (ported, incl. pacing/retry) |
| `src/paper_mcp/pipelines/unpaywall.py` | Open-access URL resolution (ported) |
| `src/paper_mcp/tools/discovery.py` | The four tool handlers; maps client results → `PaperRef` |
| `src/paper_mcp/server.py` | FastMCP server construction + FastAPI mount + `/health` |
| `tests/` | One test module per source module |

Splitting by responsibility: transport clients live in `pipelines/` (portable, upstream-shaped), normalization and the MCP contract live in `tools/` (this project's shape). That boundary is what lets Phase B add extraction without touching the tool layer.

---

### Task 1: Project scaffold + quality gates

**Files:**
- Create: `pyproject.toml`, `.gitignore`, `README.md`, `src/paper_mcp/__init__.py`
- Test: `tests/test_smoke.py`

**Interfaces:**
- Consumes: nothing.
- Produces: `paper_mcp.__version__: str`; a working `uv run pytest` / `ruff` / `mypy` toolchain every later task depends on.

- [ ] **Step 1: Write `pyproject.toml`**

```toml
[project]
name = "paper-mcp"
version = "0.1.0"
description = "Stateless MCP service for paper acquisition, extraction, and LaTeX compilation"
readme = "README.md"
requires-python = ">=3.12"
dependencies = [
    "fastapi>=0.115",
    "uvicorn[standard]>=0.32",
    "mcp>=1.2",
    "httpx>=0.27",
    "arxiv>=2.1",
    "pydantic>=2.9",
]

[project.scripts]
paper-mcp = "paper_mcp.server:main"

[dependency-groups]
dev = [
    "pytest>=8.3",
    "pytest-asyncio>=0.24",
    "respx>=0.21",
    "ruff>=0.7",
    "mypy>=1.13",
]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/paper_mcp"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]

[tool.ruff]
line-length = 100
src = ["src", "tests"]

[tool.ruff.lint]
select = ["E", "F", "I", "N", "UP", "B", "SIM", "RUF"]

[tool.mypy]
strict = true
python_version = "3.12"
files = ["src"]
```

- [ ] **Step 2: Write `.gitignore`**

```gitignore
__pycache__/
*.py[cod]
.venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
dist/
build/
*.egg-info/
.env
artifacts/
```

- [ ] **Step 3: Write `src/paper_mcp/__init__.py`**

```python
"""paper-mcp — a stateless MCP service for paper processing and LaTeX compilation."""

__version__ = "0.1.0"
```

- [ ] **Step 4: Write `README.md`**

```markdown
# paper-mcp

A stateless MCP service that turns papers into agent-ready data and compiles LaTeX.

Built for external agent clients (Claude Cowork, Claude Desktop, Cursor, any MCP
framework). It supplies what those clients lack — paper acquisition, faithful
extraction, and a working LaTeX toolchain — and nothing else: no accounts, no
stored user data, no server-side LLM calls.

**Spec:** [docs/superpowers/specs/2026-08-11-paper-mcp-srs.md](docs/superpowers/specs/2026-08-11-paper-mcp-srs.md)

## Development

```bash
uv sync
uv run pytest
uv run ruff check src tests
uv run mypy src
```
```

- [ ] **Step 5: Write the failing test**

`tests/test_smoke.py`:

```python
from paper_mcp import __version__


def test_version_is_exposed() -> None:
    assert __version__ == "0.1.0"
```

- [ ] **Step 6: Sync and run the test to verify it passes**

Run: `uv sync; uv run pytest tests/test_smoke.py -v`
Expected: PASS. (This task's "failing" state is the missing toolchain — if `uv sync` fails, the environment is wrong and nothing later works.)

- [ ] **Step 7: Verify the gates run clean**

Run: `uv run ruff check src tests; uv run mypy src`
Expected: both clean, no findings.

- [ ] **Step 8: Commit**

```bash
git add pyproject.toml uv.lock .gitignore README.md src tests
git commit -m "chore: scaffold paper-mcp project with uv, ruff, mypy, pytest"
```

---

### Task 2: Core models — `PaperRef`, identifiers, error taxonomy

**Files:**
- Create: `src/paper_mcp/models.py`
- Test: `tests/test_models.py`

**Interfaces:**
- Consumes: nothing.
- Produces:
  - `class OpenAccess(BaseModel)` — `available: bool`, `url: str | None`, `source: Literal["arxiv","unpaywall","s2"] | None`, `reason: str | None`
  - `class PaperRef(BaseModel)` — `paper_id: str`, `title: str`, `abstract: str | None`, `year: int | None`, `authors: list[str]`, `arxiv_id: str | None`, `doi: str | None`, `venue: str | None`, `citation_count: int | None`, `open_access: OpenAccess`, `source: Literal["arxiv","semantic_scholar"]`
  - `def normalize_paper_id(*, arxiv_id: str | None, s2_id: str | None, doi: str | None) -> str`
  - `def s2_path_id(paper_id: str) -> str`
  - `def clamp_max_results(value: int) -> int`
  - `class ToolError(Exception)` with `code: str`, `message: str`, `retry_after: float | None`; subclasses `NotFoundError`, `UpstreamError`, `RateLimitedError`, `InvalidArgumentError`

- [ ] **Step 1: Write the failing test**

`tests/test_models.py`:

```python
import pytest

from paper_mcp.models import (
    InvalidArgumentError,
    OpenAccess,
    PaperRef,
    clamp_max_results,
    normalize_paper_id,
    s2_path_id,
)


def test_arxiv_id_is_preferred_identifier() -> None:
    # arXiv wins because it is the identifier with an ingestible source.
    assert normalize_paper_id(arxiv_id="1706.03762", s2_id="abc123", doi="10.5555/x") == (
        "arxiv:1706.03762"
    )


def test_falls_back_to_s2_then_doi() -> None:
    assert normalize_paper_id(arxiv_id=None, s2_id="abc123", doi="10.5555/x") == "ss:abc123"
    assert normalize_paper_id(arxiv_id=None, s2_id=None, doi="10.5555/x") == "doi:10.5555/x"


def test_no_identifier_at_all_is_an_error() -> None:
    with pytest.raises(InvalidArgumentError):
        normalize_paper_id(arxiv_id=None, s2_id=None, doi=None)


def test_s2_path_id_maps_each_prefix_to_upstream_form() -> None:
    assert s2_path_id("arxiv:1706.03762") == "arXiv:1706.03762"
    assert s2_path_id("ss:abc123") == "abc123"
    assert s2_path_id("doi:10.5555/x") == "DOI:10.5555/x"


def test_s2_path_id_rejects_unknown_prefix() -> None:
    with pytest.raises(InvalidArgumentError):
        s2_path_id("pubmed:12345")


@pytest.mark.parametrize(("given", "expected"), [(0, 1), (-5, 1), (8, 8), (50, 50), (999, 50)])
def test_max_results_is_clamped(given: int, expected: int) -> None:
    assert clamp_max_results(given) == expected


def test_paper_ref_defaults_open_access_to_unavailable() -> None:
    ref = PaperRef(
        paper_id="arxiv:1706.03762",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani"],
        source="arxiv",
    )
    assert ref.open_access == OpenAccess(available=False)
    assert ref.abstract is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_models.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paper_mcp.models'`

- [ ] **Step 3: Write the implementation**

`src/paper_mcp/models.py`:

```python
"""Core wire models shared by every tool.

`PaperRef` is deliberately one shape across all upstreams (arXiv, Semantic
Scholar, DOI resolution) so a calling agent never branches on provenance.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MAX_RESULTS_CEILING = 50


class ToolError(Exception):
    """Base for every error a tool returns to the caller.

    Errors are part of the contract (SRS I-8 #7): each names what happened
    and what the caller should do next. They are never bare 500s.
    """

    code = "tool_error"

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class InvalidArgumentError(ToolError):
    code = "invalid_argument"


class NotFoundError(ToolError):
    code = "not_found"


class UpstreamError(ToolError):
    code = "upstream_error"


class RateLimitedError(ToolError):
    code = "rate_limited"


class OpenAccess(BaseModel):
    """Whether an ingestible full-text source exists, and where."""

    available: bool = False
    url: str | None = None
    source: Literal["arxiv", "unpaywall", "s2"] | None = None
    reason: str | None = Field(
        default=None,
        description="Why no source was found, when available is false.",
    )


class PaperRef(BaseModel):
    """One paper, normalized across every discovery upstream."""

    paper_id: str = Field(description="arxiv:<id> | ss:<paperId> | doi:<doi>")
    title: str
    abstract: str | None = None
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    arxiv_id: str | None = None
    doi: str | None = None
    venue: str | None = None
    citation_count: int | None = None
    open_access: OpenAccess = Field(default_factory=OpenAccess)
    source: Literal["arxiv", "semantic_scholar"]


def normalize_paper_id(
    *, arxiv_id: str | None, s2_id: str | None, doi: str | None,
) -> str:
    """Build the canonical `paper_id`, preferring arXiv.

    arXiv wins whenever present because it is the only identifier that
    reliably carries an ingestible source — preferring it makes a later
    `fetch_paper` succeed more often (SRS §III-2).
    """
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    if s2_id:
        return f"ss:{s2_id}"
    if doi:
        return f"doi:{doi}"
    raise InvalidArgumentError(
        "paper has no arXiv id, Semantic Scholar id, or DOI — cannot be addressed",
    )


def s2_path_id(paper_id: str) -> str:
    """Map a canonical `paper_id` to the form Semantic Scholar's URL path wants."""
    prefix, _, rest = paper_id.partition(":")
    if not rest:
        raise InvalidArgumentError(
            f"paper_id must be prefixed (arxiv:/ss:/doi:), got {paper_id!r}",
        )
    match prefix:
        case "arxiv":
            return f"arXiv:{rest}"
        case "ss":
            return rest
        case "doi":
            return f"DOI:{rest}"
        case _:
            raise InvalidArgumentError(
                f"unsupported paper_id prefix {prefix!r} — use arxiv:, ss:, or doi:",
            )


def clamp_max_results(value: int) -> int:
    """Clamp a caller-supplied result count into [1, 50] (SRS FR-01)."""
    return max(1, min(int(value), MAX_RESULTS_CEILING))
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_models.py -v; uv run ruff check src tests; uv run mypy src`
Expected: all PASS, gates clean.

- [ ] **Step 5: Commit**

```bash
git add src/paper_mcp/models.py tests/test_models.py
git commit -m "feat(models): add PaperRef, identifier normalization, and error taxonomy"
```

---

### Task 3: arXiv client (ported) + `PaperRef` mapping

**Files:**
- Create: `src/paper_mcp/pipelines/__init__.py`, `src/paper_mcp/pipelines/arxiv_client.py`
- Test: `tests/test_arxiv_client.py`

**Interfaces:**
- Consumes: `PaperRef`, `OpenAccess`, `normalize_paper_id` from Task 2.
- Produces:
  - `class ArxivResult(BaseModel)` — `arxiv_id, title, authors, year, abstract, pdf_url`
  - `def search_arxiv(query: str, max_results: int = 8) -> list[ArxivResult]`
  - `def fetch_arxiv_by_id(arxiv_id: str) -> ArxivResult | None`
  - `def arxiv_to_ref(result: ArxivResult) -> PaperRef`

Port note: PaperHub's version is copied verbatim for `search_arxiv` / `fetch_arxiv_by_id` / `_id_from_entry_id`. The download-and-unpack half (`download_arxiv_source`, `_download_with_resume`) is **deliberately not ported here** — it belongs to Phase B.

- [ ] **Step 1: Write the failing test**

`tests/test_arxiv_client.py`:

```python
from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from paper_mcp.pipelines import arxiv_client
from paper_mcp.pipelines.arxiv_client import ArxivResult, arxiv_to_ref, search_arxiv


class _FakeAuthor:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEntry:
    def __init__(self, entry_id: str, title: str) -> None:
        self.entry_id = entry_id
        self.title = f"  {title}  "
        self.authors = [_FakeAuthor("Ashish Vaswani")]
        self.published = datetime(2017, 6, 12)
        self.summary = "  We propose the Transformer.  "
        self.pdf_url = "https://arxiv.org/pdf/1706.03762"


class _FakeClient:
    def __init__(self, entries: list[_FakeEntry]) -> None:
        self._entries = entries

    def results(self, search: Any) -> list[_FakeEntry]:
        return self._entries


def test_search_strips_version_suffix_and_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _FakeEntry("http://arxiv.org/abs/1706.03762v5", "Attention Is All You Need")
    monkeypatch.setattr(arxiv_client, "_client", _FakeClient([entry]))

    results = search_arxiv("transformer", max_results=3)

    assert len(results) == 1
    assert results[0].arxiv_id == "1706.03762"          # v5 stripped
    assert results[0].title == "Attention Is All You Need"  # whitespace stripped
    assert results[0].year == 2017


def test_search_clamps_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    class _CapturingClient(_FakeClient):
        def results(self, search: Any) -> list[_FakeEntry]:
            captured["max_results"] = search.max_results
            return []

    monkeypatch.setattr(arxiv_client, "_client", _CapturingClient([]))
    search_arxiv("anything", max_results=999)

    assert captured["max_results"] == 50


def test_arxiv_to_ref_marks_open_access_available() -> None:
    ref = arxiv_to_ref(
        ArxivResult(
            arxiv_id="1706.03762",
            title="Attention Is All You Need",
            authors=["Ashish Vaswani"],
            year=2017,
            abstract="We propose the Transformer.",
            pdf_url="https://arxiv.org/pdf/1706.03762",
        )
    )

    assert ref.paper_id == "arxiv:1706.03762"
    assert ref.source == "arxiv"
    # Every arXiv paper has an ingestible source by definition.
    assert ref.open_access.available is True
    assert ref.open_access.source == "arxiv"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_arxiv_client.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paper_mcp.pipelines'`

- [ ] **Step 3: Create the package marker**

`src/paper_mcp/pipelines/__init__.py`:

```python
"""Transport + extraction clients, ported from PaperHub."""
```

- [ ] **Step 4: Write the implementation**

`src/paper_mcp/pipelines/arxiv_client.py`:

```python
"""arXiv API client — metadata search and exact-id lookup.

# Ported from PaperHub `backend/src/paperhub/pipelines/arxiv_client.py` @ fd65834.
# (PaperHub in turn adapted it from paper2slides-plus/src/arxiv_utils.py.)
# Adapted: dropped the e-print download/unpack half (Phase B owns that);
# added `arxiv_to_ref` so the tool layer never sees an upstream shape;
# `search_arxiv` now clamps `max_results` per SRS FR-01.
"""
from __future__ import annotations

import logging
import re

import arxiv
from pydantic import BaseModel

from paper_mcp.models import OpenAccess, PaperRef, clamp_max_results, normalize_paper_id

logger = logging.getLogger(__name__)

_client = arxiv.Client()

_ARXIV_ID_RE = re.compile(r"(\d{4}\.\d{4,5})(v\d+)?")


class ArxivResult(BaseModel):
    arxiv_id: str
    title: str
    authors: list[str]
    year: int | None
    abstract: str
    pdf_url: str | None = None


def _id_from_entry_id(entry_id: str) -> str:
    """Strip URL prefix + version suffix.

    'http://arxiv.org/abs/2403.01234v2' -> '2403.01234'
    """
    m = _ARXIV_ID_RE.search(entry_id)
    if not m:
        raise ValueError(f"unexpected arxiv entry_id: {entry_id!r}")
    return m.group(1)


def _to_result(entry: object) -> ArxivResult:
    return ArxivResult(
        arxiv_id=_id_from_entry_id(getattr(entry, "entry_id", "")),
        title=str(getattr(entry, "title", "")).strip(),
        authors=[a.name for a in getattr(entry, "authors", [])],
        year=getattr(getattr(entry, "published", None), "year", None),
        abstract=str(getattr(entry, "summary", "")).strip(),
        pdf_url=(
            entry.pdf_url if isinstance(getattr(entry, "pdf_url", None), str) else None
        ),
    )


def search_arxiv(query: str, max_results: int = 8) -> list[ArxivResult]:
    """Relevance search over arXiv. Metadata only — no download."""
    search = arxiv.Search(
        query=query,
        max_results=clamp_max_results(max_results),
        sort_by=arxiv.SortCriterion.Relevance,
    )
    return [_to_result(r) for r in _client.results(search)]


def fetch_arxiv_by_id(arxiv_id: str) -> ArxivResult | None:
    """Exact-id lookup — returns the paper for `arxiv_id`, or None.

    Unlike `search_arxiv` (a relevance query that returns the nearest match
    for ANY string), this uses arXiv's `id_list`, so a bogus id returns None
    rather than a confidently-wrong neighbour. Best-effort: any API error is
    logged and treated as "unverifiable".
    """
    try:
        for r in _client.results(arxiv.Search(id_list=[arxiv_id])):
            return _to_result(r)
    except Exception as exc:  # noqa: BLE001 — best-effort verification
        logger.warning(
            "fetch_arxiv_by_id(%r) failed (%s: %s); treating as unverifiable",
            arxiv_id, type(exc).__name__, exc,
        )
    return None


def arxiv_to_ref(result: ArxivResult) -> PaperRef:
    """Map an arXiv result onto the normalized wire shape.

    Every arXiv paper has an ingestible source, so `open_access` is always
    available here — no Unpaywall round-trip needed.
    """
    return PaperRef(
        paper_id=normalize_paper_id(arxiv_id=result.arxiv_id, s2_id=None, doi=None),
        title=result.title,
        abstract=result.abstract or None,
        year=result.year,
        authors=result.authors,
        arxiv_id=result.arxiv_id,
        open_access=OpenAccess(
            available=True,
            url=result.pdf_url or f"https://arxiv.org/abs/{result.arxiv_id}",
            source="arxiv",
        ),
        source="arxiv",
    )
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/test_arxiv_client.py -v; uv run ruff check src tests; uv run mypy src`
Expected: all PASS, gates clean.

- [ ] **Step 6: Commit**

```bash
git add src/paper_mcp/pipelines tests/test_arxiv_client.py
git commit -m "feat(pipelines): port arXiv search client with PaperRef mapping"
```

---

### Task 4: Semantic Scholar client (ported, with pacing + retry)

**Files:**
- Create: `src/paper_mcp/pipelines/semantic_scholar.py`
- Test: `tests/test_semantic_scholar.py`

**Interfaces:**
- Consumes: `PaperRef`, `OpenAccess`, `normalize_paper_id`, `s2_path_id`, `clamp_max_results`, `RateLimitedError`, `UpstreamError` from Task 2.
- Produces:
  - `class S2Hit(BaseModel)` — `s2_id, title, abstract, year, authors, arxiv_id, doi, venue, citation_count, open_access_pdf_url`
  - `async def search_papers(query: str, max_results: int = 8) -> list[S2Hit]`
  - `async def find_related(paper_id: str, *, mode: Mode, max_results: int = 8) -> list[S2Hit]`
  - `async def fetch_paper_metadata(paper_id: str) -> S2Hit`
  - `def s2_to_ref(hit: S2Hit) -> PaperRef`
  - `Mode = Literal["cites", "cited_by", "similar"]`

Port note: the pacing lock + 429 retry logic is carried over **verbatim in behaviour** — PaperHub learned empirically that the free tier throttles to ~1 req/s even with a key, and that concurrent fan-out silently dropped real papers. Do not "simplify" it away. Generalized from arXiv-only to any `paper_id` prefix.

- [ ] **Step 1: Write the failing test**

`tests/test_semantic_scholar.py`:

```python
from __future__ import annotations

import httpx
import pytest
import respx

from paper_mcp.models import RateLimitedError
from paper_mcp.pipelines import semantic_scholar as ss
from paper_mcp.pipelines.semantic_scholar import S2Hit, find_related, s2_to_ref, search_papers

_SEARCH_URL = "https://api.semanticscholar.org/graph/v1/paper/search"


@pytest.fixture(autouse=True)
def _no_pacing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Neutralize the 1.1s inter-request spacing so tests stay fast."""
    monkeypatch.setattr(ss, "_MIN_INTERVAL_S", 0.0)
    monkeypatch.setattr(ss, "_RETRY_BASE_S", 0.0)

    async def _instant(_seconds: float) -> None:
        return None

    monkeypatch.setattr(ss, "_sleep", _instant)


@respx.mock
async def test_search_papers_coerces_upstream_shape() -> None:
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "paperId": "abc123",
                        "title": "Attention Is All You Need",
                        "abstract": "We propose the Transformer.",
                        "year": 2017,
                        "authors": [{"name": "Ashish Vaswani"}, {"noname": 1}],
                        "externalIds": {"ArXiv": "1706.03762", "DOI": "10.5555/x"},
                        "openAccessPdf": {"url": "https://example.org/p.pdf"},
                        "venue": "NeurIPS",
                        "citationCount": 123456,
                    }
                ]
            },
        )
    )

    hits = await search_papers("transformer", max_results=5)

    assert len(hits) == 1
    hit = hits[0]
    assert hit.s2_id == "abc123"
    assert hit.arxiv_id == "1706.03762"
    assert hit.doi == "10.5555/x"
    assert hit.citation_count == 123456
    assert hit.authors == ["Ashish Vaswani"]  # malformed author entry dropped


@respx.mock
async def test_a_429_is_retried_then_succeeds() -> None:
    route = respx.get(_SEARCH_URL)
    route.side_effect = [
        httpx.Response(429, headers={"retry-after": "0"}),
        httpx.Response(200, json={"data": [{"paperId": "abc123", "title": "T"}]}),
    ]

    hits = await search_papers("transformer")

    assert route.call_count == 2
    assert hits[0].s2_id == "abc123"


@respx.mock
async def test_persistent_429_raises_typed_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(ss, "_MAX_ATTEMPTS", 2)
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(429, headers={"retry-after": "7"})
    )

    with pytest.raises(RateLimitedError) as exc_info:
        await search_papers("transformer")

    assert exc_info.value.retry_after == 7.0


@respx.mock
async def test_find_related_uses_the_citations_endpoint_for_cited_by() -> None:
    route = respx.get(
        "https://api.semanticscholar.org/graph/v1/paper/arXiv:1706.03762/citations"
    ).mock(
        return_value=httpx.Response(
            200, json={"data": [{"citingPaper": {"paperId": "z9", "title": "Follow-up"}}]}
        )
    )

    hits = await find_related("arxiv:1706.03762", mode="cited_by", max_results=3)

    assert route.called
    assert hits[0].title == "Follow-up"


def test_s2_to_ref_prefers_arxiv_id_and_reports_open_access() -> None:
    ref = s2_to_ref(
        S2Hit(
            s2_id="abc123",
            title="Attention Is All You Need",
            arxiv_id="1706.03762",
            open_access_pdf_url="https://example.org/p.pdf",
        )
    )

    assert ref.paper_id == "arxiv:1706.03762"
    assert ref.source == "semantic_scholar"
    assert ref.open_access.available is True
    assert ref.open_access.source == "s2"


def test_s2_to_ref_reports_absent_open_access_with_a_reason() -> None:
    ref = s2_to_ref(S2Hit(s2_id="abc123", title="Closed Paper"))

    assert ref.paper_id == "ss:abc123"
    assert ref.open_access.available is False
    assert ref.open_access.reason is not None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_semantic_scholar.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paper_mcp.pipelines.semantic_scholar'`

- [ ] **Step 3: Write the implementation**

`src/paper_mcp/pipelines/semantic_scholar.py`:

```python
"""Semantic Scholar REST client — search, citation graph, single-paper metadata.

# Ported from PaperHub `backend/src/paperhub/pipelines/semantic_scholar.py` @ fd65834.
# Adapted: dataclasses -> Pydantic models; `find_related` generalized from an
# arXiv id to any canonical `paper_id` prefix; 429 exhaustion now raises the
# shared `RateLimitedError` carrying `retry_after` instead of a local
# exception type; added `venue` + `citationCount` fields and `s2_to_ref`.
#
# The pacing lock + retry ladder is carried over deliberately: PaperHub
# measured the free tier throttling to ~1 req/s EVEN WITH an api key, and a
# concurrent fan-out silently dropped real papers as "not found". Do not
# remove it.
"""
from __future__ import annotations

import asyncio
import os
import random
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any, Literal

import httpx
from pydantic import BaseModel

from paper_mcp.models import (
    OpenAccess,
    PaperRef,
    RateLimitedError,
    UpstreamError,
    clamp_max_results,
    normalize_paper_id,
    s2_path_id,
)

API_BASE = "https://api.semanticscholar.org/graph/v1"
_TIMEOUT = httpx.Timeout(10.0)

_MIN_INTERVAL_S = float(os.environ.get("PAPER_MCP_S2_MIN_INTERVAL_S", "1.1"))
_MAX_ATTEMPTS = int(os.environ.get("PAPER_MCP_S2_MAX_ATTEMPTS", "4"))
_RETRY_BASE_S = float(os.environ.get("PAPER_MCP_S2_RETRY_BASE_S", "1.0"))

_pace_lock = asyncio.Lock()
_last_request_ts = 0.0
_sleep = asyncio.sleep

_USER_AGENT = "paper-mcp/0.1 (+https://github.com/whats2000/paper-mcp)"
_GRAPH_FIELDS = (
    "paperId,title,abstract,year,authors.name,externalIds,openAccessPdf,venue,citationCount"
)

Mode = Literal["cites", "cited_by", "similar"]


class S2Hit(BaseModel):
    s2_id: str
    title: str
    abstract: str | None = None
    year: int | None = None
    authors: list[str] = []
    arxiv_id: str | None = None
    doi: str | None = None
    venue: str | None = None
    citation_count: int | None = None
    open_access_pdf_url: str | None = None


def _parse_retry_after(value: str | None) -> float | None:
    """Parse `Retry-After` (integer seconds OR HTTP-date) into seconds."""
    if not value:
        return None
    try:
        return float(value)
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max((when - datetime.now(UTC)).total_seconds(), 0.0)


def _headers() -> dict[str, str]:
    h = {"User-Agent": _USER_AGENT}
    key = os.environ.get("PAPER_MCP_S2_API_KEY")
    if key:
        h["x-api-key"] = key
    return h


async def _get_with_retry(url: str, params: dict[str, str]) -> httpx.Response:
    """GET through the shared pacing lock, retrying on HTTP 429.

    Spacing + the request happen inside the lock so concurrent callers
    serialize to ~1 req/s; the backoff sleep is OUTSIDE the lock so a
    retrying caller doesn't block everyone else.
    """
    global _last_request_ts
    resp: httpx.Response | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        async with _pace_lock:
            wait = _MIN_INTERVAL_S - (time.monotonic() - _last_request_ts)
            if wait > 0:
                await _sleep(wait)
            async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
                resp = await client.get(url, params=params, headers=_headers())
            _last_request_ts = time.monotonic()
        if resp.status_code != 429 or attempt == _MAX_ATTEMPTS:
            return resp
        retry_after = _parse_retry_after(resp.headers.get("retry-after"))
        backoff = (
            retry_after if retry_after is not None else _RETRY_BASE_S * (2 ** (attempt - 1))
        )
        await _sleep(backoff + random.uniform(0, 0.3))
    assert resp is not None  # loop runs >=1 time
    return resp


def _raise_for_status(resp: httpx.Response, *, what: str) -> None:
    if resp.status_code == 429:
        raise RateLimitedError(
            f"Semantic Scholar rate-limited {what}; retry shortly",
            retry_after=_parse_retry_after(resp.headers.get("retry-after")),
        )
    if resp.status_code >= 400:
        raise UpstreamError(
            f"Semantic Scholar returned HTTP {resp.status_code} for {what}",
        )


def _coerce(item: dict[str, Any]) -> S2Hit:
    external = item.get("externalIds") or {}
    open_pdf = item.get("openAccessPdf") or {}
    return S2Hit(
        s2_id=str(item.get("paperId") or ""),
        title=item.get("title") or "",
        abstract=item.get("abstract"),
        year=item.get("year"),
        authors=[a["name"] for a in item.get("authors") or [] if a.get("name")],
        arxiv_id=external.get("ArXiv"),
        doi=external.get("DOI"),
        venue=item.get("venue") or None,
        citation_count=item.get("citationCount"),
        open_access_pdf_url=(
            open_pdf.get("url") if isinstance(open_pdf, dict) else None
        ),
    )


async def search_papers(query: str, max_results: int = 8) -> list[S2Hit]:
    """Free-text search across Semantic Scholar's corpus."""
    resp = await _get_with_retry(
        f"{API_BASE}/paper/search",
        {
            "query": query,
            "limit": str(clamp_max_results(max_results)),
            "fields": _GRAPH_FIELDS,
        },
    )
    _raise_for_status(resp, what=f"search_papers({query!r})")
    return [_coerce(item) for item in resp.json().get("data") or [] if item]


async def find_related(paper_id: str, *, mode: Mode, max_results: int = 8) -> list[S2Hit]:
    """Walk the citation graph around `paper_id`."""
    upstream_id = s2_path_id(paper_id)
    match mode:
        case "cites":
            url, sub_key = f"{API_BASE}/paper/{upstream_id}/references", "citedPaper"
        case "cited_by":
            url, sub_key = f"{API_BASE}/paper/{upstream_id}/citations", "citingPaper"
        case _:
            url, sub_key = f"{API_BASE}/paper/{upstream_id}/related", None

    resp = await _get_with_retry(
        url, {"limit": str(clamp_max_results(max_results)), "fields": _GRAPH_FIELDS},
    )
    _raise_for_status(resp, what=f"find_related({paper_id!r}, {mode})")
    raw = resp.json().get("data") or []
    items = [(r.get(sub_key) if sub_key else r) for r in raw]
    return [_coerce(i) for i in items if i]


async def fetch_paper_metadata(paper_id: str) -> S2Hit:
    """Single-paper lookup by canonical `paper_id`."""
    resp = await _get_with_retry(
        f"{API_BASE}/paper/{s2_path_id(paper_id)}", {"fields": _GRAPH_FIELDS},
    )
    _raise_for_status(resp, what=f"fetch_paper_metadata({paper_id!r})")
    return _coerce(resp.json())


def s2_to_ref(hit: S2Hit) -> PaperRef:
    """Map a Semantic Scholar hit onto the normalized wire shape."""
    if hit.arxiv_id:
        open_access = OpenAccess(
            available=True,
            url=f"https://arxiv.org/abs/{hit.arxiv_id}",
            source="arxiv",
        )
    elif hit.open_access_pdf_url:
        open_access = OpenAccess(
            available=True, url=hit.open_access_pdf_url, source="s2",
        )
    else:
        open_access = OpenAccess(
            available=False,
            reason=(
                "no arXiv source and no openAccessPdf on record; "
                "try resolve_paper for an Unpaywall lookup"
            ),
        )
    return PaperRef(
        paper_id=normalize_paper_id(
            arxiv_id=hit.arxiv_id, s2_id=hit.s2_id or None, doi=hit.doi,
        ),
        title=hit.title,
        abstract=hit.abstract,
        year=hit.year,
        authors=hit.authors,
        arxiv_id=hit.arxiv_id,
        doi=hit.doi,
        venue=hit.venue,
        citation_count=hit.citation_count,
        open_access=open_access,
        source="semantic_scholar",
    )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest tests/test_semantic_scholar.py -v; uv run ruff check src tests; uv run mypy src`
Expected: all PASS, gates clean.

- [ ] **Step 5: Commit**

```bash
git add src/paper_mcp/pipelines/semantic_scholar.py tests/test_semantic_scholar.py
git commit -m "feat(pipelines): port Semantic Scholar client with pacing and 429 retry"
```

---

### Task 5: Unpaywall resolution + `resolve_paper` logic

**Files:**
- Create: `src/paper_mcp/pipelines/unpaywall.py`, `src/paper_mcp/config.py`, `src/paper_mcp/tools/__init__.py`, `src/paper_mcp/tools/discovery.py`
- Test: `tests/test_unpaywall.py`, `tests/test_discovery.py`

**Interfaces:**
- Consumes: everything from Tasks 2-4.
- Produces:
  - `def settings() -> Settings` with `unpaywall_email: str | None`, `s2_api_key: str | None`, `auth_mode: str`, `public_base_url: str`
  - `async def open_access_urls(doi: str, *, email: str) -> list[str]`
  - `async def tool_search_arxiv(query: str, max_results: int = 8) -> list[PaperRef]`
  - `async def tool_search_papers(query: str, max_results: int = 8) -> list[PaperRef]`
  - `async def tool_find_related(paper_id: str, mode: Mode, max_results: int = 8) -> list[PaperRef]`
  - `async def tool_resolve_paper(identifier: str) -> PaperRef`

- [ ] **Step 1: Write the failing tests**

`tests/test_unpaywall.py`:

```python
from __future__ import annotations

import httpx
import respx

from paper_mcp.pipelines.unpaywall import open_access_urls

_URL = "https://api.unpaywall.org/v2/10.5555/x"


@respx.mock
async def test_returns_best_location_first_then_others() -> None:
    respx.get(_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "best_oa_location": {"url_for_pdf": "https://best.example/p.pdf"},
                "oa_locations": [
                    {"url_for_pdf": "https://best.example/p.pdf"},
                    {"url_for_pdf": "https://other.example/p.pdf"},
                    {"url": "https://landing.example/abs"},
                ],
            },
        )
    )

    urls = await open_access_urls("10.5555/x", email="a@b.c")

    assert urls[0] == "https://best.example/p.pdf"
    assert "https://other.example/p.pdf" in urls
    assert len(urls) == len(set(urls))  # deduplicated


@respx.mock
async def test_upstream_failure_yields_no_urls_rather_than_raising() -> None:
    respx.get(_URL).mock(return_value=httpx.Response(404))

    assert await open_access_urls("10.5555/x", email="a@b.c") == []
```

`tests/test_discovery.py`:

```python
from __future__ import annotations

import pytest

from paper_mcp.models import NotFoundError
from paper_mcp.pipelines.arxiv_client import ArxivResult
from paper_mcp.pipelines.semantic_scholar import S2Hit
from paper_mcp.tools import discovery


async def test_resolve_paper_uses_exact_arxiv_lookup_for_an_arxiv_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        discovery, "fetch_arxiv_by_id",
        lambda _id: ArxivResult(
            arxiv_id="1706.03762", title="Attention Is All You Need",
            authors=[], year=2017, abstract="…",
        ),
    )

    ref = await discovery.tool_resolve_paper("1706.03762")

    assert ref.paper_id == "arxiv:1706.03762"
    assert ref.open_access.available is True


async def test_resolve_paper_falls_back_to_title_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _search(query: str, max_results: int = 8) -> list[S2Hit]:
        return [S2Hit(s2_id="abc123", title="Some Closed Paper")]

    monkeypatch.setattr(discovery, "search_papers", _search)
    monkeypatch.setattr(discovery, "open_access_urls", None)  # unused: no DOI

    ref = await discovery.tool_resolve_paper("Some Closed Paper")

    assert ref.paper_id == "ss:abc123"


async def test_resolve_paper_raises_not_found_when_nothing_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _empty(query: str, max_results: int = 8) -> list[S2Hit]:
        return []

    monkeypatch.setattr(discovery, "search_papers", _empty)

    with pytest.raises(NotFoundError):
        await discovery.tool_resolve_paper("a paper that does not exist anywhere")


async def test_resolve_paper_consults_unpaywall_when_s2_has_no_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _search(query: str, max_results: int = 8) -> list[S2Hit]:
        return [S2Hit(s2_id="abc123", title="Closed", doi="10.5555/x")]

    async def _unpaywall(doi: str, *, email: str) -> list[str]:
        return ["https://repo.example/p.pdf"]

    monkeypatch.setattr(discovery, "search_papers", _search)
    monkeypatch.setattr(discovery, "open_access_urls", _unpaywall)
    monkeypatch.setattr(discovery, "_unpaywall_email", lambda: "a@b.c")

    ref = await discovery.tool_resolve_paper("Closed")

    assert ref.open_access.available is True
    assert ref.open_access.source == "unpaywall"
    assert ref.open_access.url == "https://repo.example/p.pdf"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/test_unpaywall.py tests/test_discovery.py -v`
Expected: FAIL — modules do not exist.

- [ ] **Step 3: Write `src/paper_mcp/config.py`**

```python
"""Environment-only configuration (SRS §III-9, twelve-factor)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    auth_mode: str
    unpaywall_email: str | None
    s2_api_key: str | None
    public_base_url: str
    log_level: str


def settings() -> Settings:
    """Read settings from the environment on every call.

    Deliberately not cached: the process is the unit of configuration, and
    reading a handful of env vars is cheaper than a stale-config bug.
    """
    return Settings(
        auth_mode=os.environ.get("PAPER_MCP_AUTH_MODE", "open"),
        unpaywall_email=os.environ.get("PAPER_MCP_UNPAYWALL_EMAIL") or None,
        s2_api_key=os.environ.get("PAPER_MCP_S2_API_KEY") or None,
        public_base_url=os.environ.get("PAPER_MCP_PUBLIC_BASE_URL", "http://localhost:8000"),
        log_level=os.environ.get("PAPER_MCP_LOG_LEVEL", "INFO"),
    )
```

- [ ] **Step 4: Write `src/paper_mcp/pipelines/unpaywall.py`**

```python
"""Unpaywall open-access URL resolution.

# Ported from PaperHub `backend/src/paperhub/pipelines/unpaywall.py` @ fd65834.
# Adapted: returns the full candidate URL list (best location first) instead
# of a single URL, so `resolve_paper` can report the best one and a later
# fetch can try each in turn; upstream failures degrade to [] rather than
# raising, because "no open access" is a normal answer, not an error.
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_API = "https://api.unpaywall.org/v2"
_TIMEOUT = httpx.Timeout(10.0)


def _pdf_url(location: dict[str, Any] | None) -> str | None:
    if not isinstance(location, dict):
        return None
    url = location.get("url_for_pdf") or location.get("url")
    return url if isinstance(url, str) and url else None


async def open_access_urls(doi: str, *, email: str) -> list[str]:
    """Return candidate open-access URLs for `doi`, best location first.

    Unpaywall requires a contact email as a query parameter. An upstream
    error, a 404 (DOI unknown), or a closed-access paper all yield [].
    """
    try:
        async with httpx.AsyncClient(timeout=_TIMEOUT) as client:
            resp = await client.get(f"{_API}/{doi}", params={"email": email})
        if resp.status_code >= 400:
            return []
        payload = resp.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("unpaywall lookup for %r failed (%s)", doi, type(exc).__name__)
        return []

    urls: list[str] = []
    best = _pdf_url(payload.get("best_oa_location"))
    if best:
        urls.append(best)
    for loc in payload.get("oa_locations") or []:
        url = _pdf_url(loc)
        if url and url not in urls:
            urls.append(url)
    return urls
```

- [ ] **Step 5: Write `src/paper_mcp/tools/__init__.py` and `src/paper_mcp/tools/discovery.py`**

`src/paper_mcp/tools/__init__.py`:

```python
"""MCP tool handlers. These own this project's wire contract."""
```

`src/paper_mcp/tools/discovery.py`:

```python
"""The four read-only discovery tools.

Scope (SRS NFR-05): outbound HTTPS to arxiv.org, api.semanticscholar.org, and
api.unpaywall.org only. No filesystem access, no state, no LLM. Results are
capped at 50 per call.
"""
from __future__ import annotations

import re

from paper_mcp.config import settings
from paper_mcp.models import NotFoundError, OpenAccess, PaperRef
from paper_mcp.pipelines.arxiv_client import arxiv_to_ref, fetch_arxiv_by_id, search_arxiv
from paper_mcp.pipelines.semantic_scholar import (
    Mode,
    fetch_paper_metadata,
    find_related,
    s2_to_ref,
    search_papers,
)
from paper_mcp.pipelines.unpaywall import open_access_urls

_BARE_ARXIV_RE = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5})(v\d+)?$", re.IGNORECASE)
_DOI_RE = re.compile(r"^(?:doi:)?(10\.\d{4,9}/\S+)$", re.IGNORECASE)


def _unpaywall_email() -> str | None:
    return settings().unpaywall_email


async def tool_search_arxiv(query: str, max_results: int = 8) -> list[PaperRef]:
    """Relevance search over arXiv. Metadata only; nothing is downloaded."""
    return [arxiv_to_ref(r) for r in search_arxiv(query, max_results=max_results)]


async def tool_search_papers(query: str, max_results: int = 8) -> list[PaperRef]:
    """Free-text search across Semantic Scholar's corpus."""
    return [s2_to_ref(h) for h in await search_papers(query, max_results=max_results)]


async def tool_find_related(
    paper_id: str, mode: Mode, max_results: int = 8,
) -> list[PaperRef]:
    """Walk the citation graph: what this cites, what cites it, or similar work."""
    return [
        s2_to_ref(h)
        for h in await find_related(paper_id, mode=mode, max_results=max_results)
    ]


async def _enrich_open_access(ref: PaperRef) -> PaperRef:
    """Try Unpaywall when no source is known yet and we have a DOI + email."""
    if ref.open_access.available or not ref.doi:
        return ref
    email = _unpaywall_email()
    if not email:
        return ref.model_copy(
            update={
                "open_access": OpenAccess(
                    available=False,
                    reason=(
                        "no open-access source on record; set PAPER_MCP_UNPAYWALL_EMAIL "
                        "to enable an Unpaywall lookup"
                    ),
                )
            }
        )
    urls = await open_access_urls(ref.doi, email=email)
    if not urls:
        return ref
    return ref.model_copy(
        update={
            "open_access": OpenAccess(available=True, url=urls[0], source="unpaywall")
        }
    )


async def tool_resolve_paper(identifier: str) -> PaperRef:
    """Resolve an arXiv id, DOI, Semantic Scholar id, or title to one paper.

    Reports open-access availability so the caller knows, before spending a
    `fetch_paper`, whether a full-text source exists.
    """
    identifier = identifier.strip()
    if not identifier:
        raise NotFoundError("identifier is empty")

    arxiv_match = _BARE_ARXIV_RE.match(identifier)
    if arxiv_match:
        result = fetch_arxiv_by_id(arxiv_match.group(1))
        if result is not None:
            return arxiv_to_ref(result)

    if _DOI_RE.match(identifier) or identifier.startswith("ss:"):
        prefixed = identifier if ":" in identifier else f"doi:{identifier}"
        hit = await fetch_paper_metadata(prefixed)
        if hit.s2_id or hit.title:
            return await _enrich_open_access(s2_to_ref(hit))

    hits = await search_papers(identifier, max_results=1)
    if not hits:
        raise NotFoundError(
            f"no paper matched {identifier!r} on arXiv or Semantic Scholar; "
            "try a fuller title or an explicit arXiv id / DOI",
        )
    return await _enrich_open_access(s2_to_ref(hits[0]))
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `uv run pytest tests/test_unpaywall.py tests/test_discovery.py -v; uv run ruff check src tests; uv run mypy src`
Expected: all PASS, gates clean.

- [ ] **Step 7: Commit**

```bash
git add src/paper_mcp/config.py src/paper_mcp/pipelines/unpaywall.py src/paper_mcp/tools tests/test_unpaywall.py tests/test_discovery.py
git commit -m "feat(tools): add discovery tool layer with Unpaywall-backed resolve_paper"
```

---

### Task 6: MCP server + FastAPI mount + `/health`

**Files:**
- Create: `src/paper_mcp/server.py`
- Test: `tests/test_server.py`

**Interfaces:**
- Consumes: the four `tool_*` handlers from Task 5.
- Produces:
  - `def build_mcp_server() -> FastMCP`
  - `def create_app() -> FastAPI`
  - `def main() -> None` (the `paper-mcp` console script)

Mount pattern note: Starlette does **not** propagate a mounted sub-app's lifespan to the parent. FastMCP's streamable-HTTP transport needs its session manager started, so the parent lifespan must enter it explicitly — otherwise the first `POST /mcp` raises `RuntimeError: Task group is not initialized`. This is a lesson carried over from PaperHub's `mcp/mounting.py`.

- [ ] **Step 1: Write the failing test**

`tests/test_server.py`:

```python
from __future__ import annotations

from fastapi.testclient import TestClient

from paper_mcp.server import build_mcp_server, create_app

EXPECTED_TOOLS = {"search_arxiv", "search_papers", "find_related", "resolve_paper"}


def test_health_reports_ok_and_version() -> None:
    with TestClient(create_app()) as client:
        resp = client.get("/health")

    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "ok"
    assert body["version"] == "0.1.0"


async def test_all_four_discovery_tools_are_registered() -> None:
    server = build_mcp_server()

    names = {tool.name for tool in await server.list_tools()}

    assert EXPECTED_TOOLS <= names


async def test_every_tool_description_declares_its_scope() -> None:
    # SRS NFR-05: a caller must be able to see what a tool reaches.
    server = build_mcp_server()

    for tool in await server.list_tools():
        assert tool.description, f"{tool.name} has no description"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/test_server.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'paper_mcp.server'`

- [ ] **Step 3: Write the implementation**

`src/paper_mcp/server.py`:

```python
"""FastAPI app hosting the FastMCP streamable-HTTP surface.

# Mount mechanics ported in spirit from PaperHub
# `backend/src/paperhub/mcp/mounting.py` @ fd65834.
# Adapted: no request-context middleware, no database connection, no tracer —
# this service is stateless, so a tool call needs nothing but its arguments.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from fastapi import FastAPI
from mcp.server.fastmcp import FastMCP

from paper_mcp import __version__
from paper_mcp.config import settings
from paper_mcp.tools.discovery import (
    tool_find_related,
    tool_resolve_paper,
    tool_search_arxiv,
    tool_search_papers,
)

_LOG = logging.getLogger(__name__)

SERVER_NAME = "paper"
MCP_PATH = "/mcp"


def build_mcp_server() -> FastMCP:
    """Construct the FastMCP server with the discovery tools registered.

    `streamable_http_path="/"` means mounting at `/mcp` makes `POST /mcp` the
    transport endpoint, which is the URL shape MCP clients expect.
    """
    server = FastMCP(SERVER_NAME, streamable_http_path="/")
    server.settings.json_response = True
    server.settings.stateless_http = True

    server.add_tool(
        tool_search_arxiv,
        name="search_arxiv",
        description=(
            "Search arXiv by relevance and return normalized paper references. "
            "Metadata only — nothing is downloaded. Network scope: arxiv.org. "
            "Returns at most 50 results."
        ),
    )
    server.add_tool(
        tool_search_papers,
        name="search_papers",
        description=(
            "Search Semantic Scholar's full corpus (broader than arXiv, includes "
            "citation counts and venues). Network scope: api.semanticscholar.org. "
            "Returns at most 50 results."
        ),
    )
    server.add_tool(
        tool_find_related,
        name="find_related",
        description=(
            "Walk the citation graph around a paper. mode='cites' for its "
            "references, 'cited_by' for follow-up work, 'similar' for related "
            "papers. paper_id must be prefixed: arxiv:<id>, ss:<paperId>, or "
            "doi:<doi>. Network scope: api.semanticscholar.org."
        ),
    )
    server.add_tool(
        tool_resolve_paper,
        name="resolve_paper",
        description=(
            "Resolve an arXiv id, DOI, Semantic Scholar id, or free-text title "
            "to a single paper, reporting whether an open-access full-text "
            "source exists. Call this before fetch_paper to avoid spending an "
            "extraction on a paper with no reachable source. Network scope: "
            "arxiv.org, api.semanticscholar.org, api.unpaywall.org."
        ),
    )
    return server


def create_app() -> FastAPI:
    """Build the FastAPI app with the MCP sub-app mounted at /mcp."""
    server = build_mcp_server()
    session_manager = server.session_manager

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Starlette does NOT propagate a mounted sub-app's lifespan, so the
        # FastMCP session manager's task group must be entered here or the
        # first POST /mcp fails with "Task group is not initialized".
        async with session_manager.run():
            _LOG.info("paper-mcp %s ready; mcp mounted at %s", __version__, MCP_PATH)
            yield

    app = FastAPI(title="paper-mcp", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "auth_mode": settings().auth_mode,
        }

    app.mount(MCP_PATH, server.streamable_http_app())
    return app


def main() -> None:
    """Console-script entry point."""
    cfg = settings()
    logging.basicConfig(level=cfg.log_level)
    if cfg.auth_mode == "open":
        _LOG.warning(
            "AUTH_MODE=open — every caller is unauthenticated. "
            "Do not run this on a public network.",
        )
    uvicorn.run(create_app(), host="0.0.0.0", port=8000)  # noqa: S104
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `uv run pytest -v; uv run ruff check src tests; uv run mypy src`
Expected: all PASS, gates clean.

- [ ] **Step 5: Manually verify the MCP endpoint answers a real client handshake**

Run:
```bash
uv run paper-mcp &
curl -s -X POST http://127.0.0.1:8000/mcp \
  -H 'Content-Type: application/json' \
  -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list"}'
```
Expected: a JSON-RPC response listing the four tools. Then `curl -s http://127.0.0.1:8000/health` returns `{"status":"ok",...}`.

- [ ] **Step 6: Commit**

```bash
git add src/paper_mcp/server.py tests/test_server.py
git commit -m "feat(server): mount FastMCP streamable-HTTP surface with discovery tools"
```

---

## Phase A Completion Gate

- [ ] `uv run pytest -v` — all green
- [ ] `uv run ruff check src tests` — clean
- [ ] `uv run mypy src` — clean
- [ ] `tools/list` over HTTP returns all four tools with scope-declaring descriptions
- [ ] A real MCP client (Claude Desktop / `mcp` CLI) pointed at `http://localhost:8000/mcp` lists and successfully calls `search_arxiv`

---

## Self-Review

**Spec coverage for Phase A.** FR-01 → Tasks 3, 4, 6 (all three discovery tools, `max_results` clamped in `clamp_max_results`). FR-02 → Task 5 (`tool_resolve_paper` with the arXiv → S2 → Unpaywall ladder). FR-12 (partial) → Task 6 (`/health`, log level). NFR-04 → Task 1 gates + Pydantic models throughout. NFR-05 → Task 6 tool descriptions, asserted by test. NFR-06 → provenance headers in Tasks 3, 4, 5, 6.

**Deferred to later phases by design:** FR-03/04 (bundle, `get_section`) → Phase B · FR-05 (`compile_latex`) → Phase C · FR-06/07 (jobs, artifacts) → Phase B · FR-08/09 (auth, quota) → Phase D · FR-10/11 (skills, container) → Phase E.

**Type consistency check.** `PaperRef`/`OpenAccess` field names are identical across Tasks 2-5. `Mode` is defined once in `semantic_scholar.py` and imported by `discovery.py`. `s2_path_id` is defined in Task 2 and consumed in Task 4. `clamp_max_results` is applied in both `arxiv_client.search_arxiv` and the two S2 functions — deliberately at the client layer so no tool can bypass it.

**Known sharp edge, recorded not deferred:** `tool_resolve_paper`'s title-search fallback takes S2's top hit without a similarity check, so a nonsense title can return a confidently-wrong paper. arXiv ids and DOIs go through exact lookup and are unaffected. Tightening this needs a real query corpus to calibrate against, which Phase B's fixture set provides — it is a Phase B task, not an oversight here.

---

## Subsequent Phases

Each gets its own plan document, written when its dependencies are met (the PaperHub convention):

| Phase | Scope | Depends on |
| --- | --- | --- |
| **B** | Artifact store, job queue, `fetch_paper`, `get_section`; ports `extract`, `latex_to_asset`, `paper_asset`, `marker_client`, `pymupdf_to_asset`, `figures`, `chunker` | A |
| **C** | `compile_latex` + the nsjail sandbox + the adversarial escape corpus (release gate) | A |
| **D** | OIDC resource-server auth, per-subject quota, GPU serialization | A, B, C |
| **E** | Portable skills (`paper-to-deck`, `deep-read`, `figure-grounding`), Dockerfile, compose profiles | B, C |
