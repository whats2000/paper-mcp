"""The four read-only discovery tools.

Scope (SRS NFR-05): outbound HTTPS to arxiv.org, api.semanticscholar.org, and
api.unpaywall.org only. No filesystem access, no state, no LLM. Results are
capped at 50 per call.
"""
from __future__ import annotations

import asyncio
import os
import re
from collections.abc import Coroutine
from typing import Any

from paper_mcp.config import settings
from paper_mcp.models import NotFoundError, OpenAccess, PaperRef, UpstreamError
from paper_mcp.pipelines.arxiv_client import arxiv_to_ref, fetch_arxiv_by_id, search_arxiv
from paper_mcp.pipelines.semantic_scholar import (
    Mode,
    fetch_paper_metadata,
    find_related,
    s2_to_ref,
    search_papers,
)
from paper_mcp.pipelines.unpaywall import open_access_urls

_BARE_ARXIV_RE = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)
_DOI_RE = re.compile(r"^(?:doi:)?(10\.\d{4,9}/\S+)$", re.IGNORECASE)


# The wall-clock ceiling for any single tool call.
#
# Every upstream here retries internally — the Semantic Scholar client on its
# own ladder, the arxiv library on its own — and an unbounded retry outlives
# the MCP client's request timeout. When that happens the caller does not get
# a slow answer, it gets a dropped request, which is strictly worse than a
# typed error it could act on. Measured on device: both upstreams did this.
#
# This is the single guarantee that holds regardless of which dependency
# misbehaves; per-upstream budgets are tuning underneath it, not the contract.
_TOOL_BUDGET_S = float(os.environ.get("PAPER_MCP_TOOL_BUDGET_S", "25"))


async def _bounded[T](coro: Coroutine[Any, Any, T], *, what: str) -> T:
    """Run a tool body under the wall-clock budget, or fail with a typed error.

    Deliberately not `asyncio.wait_for`. That cancels the inner task and then
    *awaits the cancellation*, and a `asyncio.to_thread` call cannot be
    cancelled — so a blocked synchronous upstream keeps `wait_for` waiting
    long past its own timeout. Measured on device: the budget was set to 45s
    and the call still ran until the MCP client gave up on it.

    `asyncio.wait` returns at the deadline whatever the task is doing. The
    orphan is cancelled best-effort and abandoned to finish in the background
    against its own socket timeout; its result is discarded. Leaking a thread
    briefly is the cheaper failure — the caller gets a typed answer on time.
    """
    task: asyncio.Task[T] = asyncio.ensure_future(coro)
    done, _pending = await asyncio.wait({task}, timeout=_TOOL_BUDGET_S)
    if task in done:
        return task.result()
    task.cancel()
    # Consume any eventual exception so the loop does not log
    # "Task exception was never retrieved" for a result nobody wants.
    task.add_done_callback(lambda t: t.cancelled() or t.exception())
    raise UpstreamError(
        f"{what} exceeded the {_TOOL_BUDGET_S:.0f}s budget — the upstream is "
        "slow or throttling; retry shortly",
    )


def _unpaywall_email() -> str | None:
    return settings().unpaywall_email


async def tool_search_arxiv(query: str, max_results: int = 8) -> list[PaperRef]:
    """Relevance search over arXiv. Metadata only; nothing is downloaded."""
    # The `arxiv` library is synchronous and paces its own requests, so
    # calling it directly from an async handler blocks the event loop and
    # serializes every other in-flight request behind it. An on-device check
    # measured three concurrent calls taking 20.5s for exactly this reason.
    results = await _bounded(
        asyncio.to_thread(search_arxiv, query, max_results=max_results),
        what="search_arxiv",
    )
    return [arxiv_to_ref(r) for r in results]


async def tool_search_papers(query: str, max_results: int = 8) -> list[PaperRef]:
    """Free-text search across Semantic Scholar's corpus."""
    hits = await _bounded(
        search_papers(query, max_results=max_results), what="search_papers",
    )
    return [s2_to_ref(h) for h in hits]


async def tool_find_related(
    paper_id: str, mode: Mode, max_results: int = 8,
) -> list[PaperRef]:
    """Walk the citation graph: what this cites, what cites it, or similar work."""
    hits = await _bounded(
        find_related(paper_id, mode=mode, max_results=max_results),
        what=f"find_related({mode})",
    )
    return [s2_to_ref(h) for h in hits]


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
        update={"open_access": OpenAccess(available=True, url=urls[0], source="unpaywall")}
    )


async def tool_resolve_paper(identifier: str) -> PaperRef:
    """Resolve an arXiv id, DOI, Semantic Scholar id, or title to one paper.

    Reports open-access availability so the caller knows, before spending a
    `fetch_paper`, whether a full-text source exists.
    """
    # Budget the whole resolution, not each hop. This walks up to four
    # upstream calls in sequence (arXiv exact -> S2 by id -> S2 search ->
    # Unpaywall); bounding them individually would still let the chain run
    # four budgets long.
    return await _bounded(_resolve_paper(identifier), what="resolve_paper")


async def _resolve_paper(identifier: str) -> PaperRef:
    identifier = identifier.strip()
    if not identifier:
        raise NotFoundError("identifier is empty — pass an arXiv id, DOI, or title")

    arxiv_match = _BARE_ARXIV_RE.match(identifier)
    if arxiv_match:
        result = await asyncio.to_thread(fetch_arxiv_by_id, arxiv_match.group(1))
        if result is not None:
            return arxiv_to_ref(result)

    doi_match = _DOI_RE.match(identifier)
    if doi_match or identifier.lower().startswith("ss:"):
        prefixed = (
            identifier
            if identifier.lower().startswith("ss:")
            else f"doi:{doi_match.group(1)}"
            if doi_match
            else identifier
        )
        try:
            hit = await fetch_paper_metadata(prefixed)
        except NotFoundError:
            # Semantic Scholar not having this DOI is not the end of the
            # road — the title search below may still find the paper. Only
            # fail once every route is exhausted.
            hit = None
        if hit is not None and (hit.s2_id or hit.title):
            return await _enrich_open_access(s2_to_ref(hit))

    hits = await search_papers(identifier, max_results=1)
    if not hits:
        raise NotFoundError(
            f"no paper matched {identifier!r} on arXiv or Semantic Scholar; "
            "try a fuller title or an explicit arXiv id / DOI",
        )
    return await _enrich_open_access(s2_to_ref(hits[0]))
