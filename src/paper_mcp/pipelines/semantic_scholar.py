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
from pydantic import BaseModel, Field

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
    authors: list[str] = Field(default_factory=list)
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
        # Jitter spreads concurrent retriers apart; not a security context.
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
        open_access_pdf_url=(open_pdf.get("url") if isinstance(open_pdf, dict) else None),
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
    sub_key: str | None
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
