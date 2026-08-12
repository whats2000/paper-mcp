"""`fetch_paper` and `get_job` — turning a paper id into usable data.

A cache hit returns the bundle immediately. A miss starts a background job,
because Marker takes minutes on a dense paper and holding an MCP request open
that long gets the connection dropped rather than answered.
"""
from __future__ import annotations

import logging
import re
from typing import Literal

from pydantic import BaseModel, Field

from paper_mcp.artifacts import ArtifactStore
from paper_mcp.bundle import Bundle
from paper_mcp.config import settings
from paper_mcp.jobs import JobStatus, JobStore
from paper_mcp.models import InvalidArgumentError, NotFoundError, PaperRef
from paper_mcp.pipelines.arxiv_client import arxiv_to_ref, fetch_arxiv_by_id
from paper_mcp.pipelines.build_bundle import build_bundle, bundle_key, load_cached
from paper_mcp.pipelines.marker_client import MarkerClient

logger = logging.getLogger(__name__)

_ARXIV_RE = re.compile(r"^(?:arxiv:)?(\d{4}\.\d{4,5})(?:v\d+)?$", re.IGNORECASE)

_store: ArtifactStore | None = None
_jobs: JobStore | None = None
_marker: MarkerClient | None = None


def artifact_store() -> ArtifactStore:
    global _store
    if _store is None:
        _store = ArtifactStore(settings().artifact_root)
    return _store


def job_store() -> JobStore:
    global _jobs
    if _jobs is None:
        _jobs = JobStore()
    return _jobs


def marker_client() -> MarkerClient:
    global _marker
    if _marker is None:
        _marker = MarkerClient(settings().marker_url)
    return _marker


class FetchResult(BaseModel):
    """Either the paper, or a handle to the extraction producing it."""

    status: Literal["ready", "extracting"]
    bundle: Bundle | None = None
    job: JobStatus | None = None
    hint: str = Field(
        default="",
        description="What the caller should do next.",
    )


def _arxiv_id(paper_id: str) -> str:
    match = _ARXIV_RE.match(paper_id.strip())
    if not match:
        raise InvalidArgumentError(
            f"{paper_id!r} is not an arXiv id. Only arXiv papers can be fetched today; "
            "use resolve_paper to find where an open-access copy lives.",
        )
    return match.group(1)


async def _paper_ref(arxiv_id: str) -> PaperRef:
    """Metadata for the bundle. Falls back to a bare reference.

    A metadata lookup failing must not stop an extraction: the markdown is
    what the caller came for, and a missing author list is a lesser loss than
    no paper at all.
    """
    import asyncio

    result = await asyncio.to_thread(fetch_arxiv_by_id, arxiv_id)
    if result is not None:
        return arxiv_to_ref(result)
    return PaperRef(paper_id=f"arxiv:{arxiv_id}", title=arxiv_id, source="arxiv",
                    arxiv_id=arxiv_id)


async def tool_fetch_paper(paper_id: str) -> FetchResult:
    """Fetch a paper as agent-ready markdown plus a figure index."""
    arxiv_id = _arxiv_id(paper_id)
    store = artifact_store()
    key = f"arxiv:{arxiv_id}"

    cached = load_cached(key, store=store)
    if cached is not None:
        return FetchResult(
            status="ready",
            bundle=cached,
            hint="Cached. markdown holds the paper; figures[].image_url resolves to images.",
        )

    cfg = settings()
    paper = await _paper_ref(arxiv_id)

    async def run() -> str:
        bundle = await build_bundle(
            paper,
            store=store,
            marker=marker_client(),
            max_pages=cfg.marker_max_pages,
            ttl_hours=cfg.artifact_ttl_hours,
        )
        return bundle.bundle_id

    job = job_store().submit(content_key=bundle_key(paper), run=run)
    return FetchResult(
        status="extracting",
        job=job,
        hint=(
            f"Extraction started (job {job.job_id}). Marker takes roughly a minute per "
            "dense page. Poll get_job, or call fetch_paper again — it returns the "
            "bundle once the cache is warm."
        ),
    )


async def tool_get_job(job_id: str) -> JobStatus:
    """Check a background extraction."""
    status = job_store().get(job_id)
    if status is None:
        raise NotFoundError(
            f"no job {job_id!r}. Job handles are forgotten on restart; call "
            "fetch_paper again — a finished extraction is a cache hit.",
        )
    return status
