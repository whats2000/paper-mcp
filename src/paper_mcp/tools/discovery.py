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

_BARE_ARXIV_RE = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)
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
        s2_to_ref(h) for h in await find_related(paper_id, mode=mode, max_results=max_results)
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
        update={"open_access": OpenAccess(available=True, url=urls[0], source="unpaywall")}
    )


async def tool_resolve_paper(identifier: str) -> PaperRef:
    """Resolve an arXiv id, DOI, Semantic Scholar id, or title to one paper.

    Reports open-access availability so the caller knows, before spending a
    `fetch_paper`, whether a full-text source exists.
    """
    identifier = identifier.strip()
    if not identifier:
        raise NotFoundError("identifier is empty — pass an arXiv id, DOI, or title")

    arxiv_match = _BARE_ARXIV_RE.match(identifier)
    if arxiv_match:
        result = fetch_arxiv_by_id(arxiv_match.group(1))
        if result is not None:
            return arxiv_to_ref(result)

    doi_match = _DOI_RE.match(identifier)
    if doi_match or identifier.lower().startswith("ss:"):
        prefixed = identifier if identifier.lower().startswith("ss:") else (
            f"doi:{doi_match.group(1)}" if doi_match else identifier
        )
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
