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
from typing import Any

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


def _to_result(entry: Any) -> ArxivResult:
    pdf_url = getattr(entry, "pdf_url", None)
    return ArxivResult(
        arxiv_id=_id_from_entry_id(str(getattr(entry, "entry_id", ""))),
        title=str(getattr(entry, "title", "")).strip(),
        authors=[a.name for a in getattr(entry, "authors", [])],
        year=getattr(getattr(entry, "published", None), "year", None),
        abstract=str(getattr(entry, "summary", "")).strip(),
        pdf_url=pdf_url if isinstance(pdf_url, str) else None,
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
    except Exception as exc:  # best-effort verification: any failure is "unknown"
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
