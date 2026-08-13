"""Assemble a cached, agent-ready bundle for a paper.

The one place acquisition, extraction, and the artifact store meet. Everything
it produces is derived from public inputs and keyed by content, so a second
request for the same paper is a cache hit rather than another GPU minute.
"""
from __future__ import annotations

import logging
import zipfile
from datetime import UTC, datetime, timedelta
from pathlib import Path

from paper_mcp.artifacts import ArtifactStore, content_key
from paper_mcp.bundle import ArtifactRef, Bundle, ExtractionInfo, cap_markdown
from paper_mcp.models import InvalidArgumentError, PaperRef
from paper_mcp.pipelines.arxiv_pdf import fetch_arxiv_pdf
from paper_mcp.pipelines.marker_client import MarkerClient, page_count
from paper_mcp.pipelines.marker_to_bundle import marker_doc_to_bundle_parts

logger = logging.getLogger(__name__)

BUNDLE_JSON = "bundle.json"
MARKDOWN_FILE = "markdown.md"
BUNDLE_ZIP = "bundle.zip"


def bundle_key(paper: PaperRef) -> str:
    """Content key for a paper reference."""
    if paper.arxiv_id:
        return content_key(arxiv_id=paper.arxiv_id)
    raise InvalidArgumentError(
        f"{paper.paper_id} has no arXiv id; fetching non-arXiv PDFs by URL is not yet "
        "supported — resolve_paper reports where an open-access copy lives",
    )


def attach_urls(bundle: Bundle, *, store: ArtifactStore) -> Bundle:
    """Materialize artifact URLs from the *current* public base URL.

    A URL is deployment state, not content, so none is ever persisted. The
    cache outlives the deployment — a named volume survives a redeploy behind
    a new hostname or port — and a bundle that stored its origin replayed it
    verbatim afterwards: every figure link pointing at somewhere that no
    longer answered, while the files sat perfectly intact on disk. Silent,
    because the bundle itself still looks complete.

    Deriving on every serve costs a string join and makes the failure
    impossible: the token and the path inside the entry are content, and only
    the origin comes from configuration.
    """
    for figure in bundle.figures:
        figure.image_url = store.url_for(bundle.bundle_id, figure.image_path)
    if bundle.artifact is not None:
        bundle.artifact.zip_url = store.url_for(bundle.bundle_id, BUNDLE_ZIP)
    return bundle


def load_cached(key: str, *, store: ArtifactStore) -> Bundle | None:
    """Return the cached bundle for `key`, or None.

    A half-written entry (no `bundle.json`) reads as a miss, so an interrupted
    extraction is retried rather than served as a truncated paper.
    """
    manifest = store.dir_for(key) / BUNDLE_JSON
    if not manifest.is_file():
        return None
    try:
        cached = Bundle.model_validate_json(manifest.read_text(encoding="utf-8"))
    except (ValueError, OSError) as exc:
        logger.warning("cached bundle %s is unreadable (%s); rebuilding", key, exc)
        return None
    return attach_urls(cached, store=store)


def _write_zip(entry: Path) -> int:
    """Pack the entry's artifacts, excluding the zip itself."""
    zip_path = entry / BUNDLE_ZIP
    zip_path.unlink(missing_ok=True)
    with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(entry.rglob("*")):
            if path.is_file() and path.name != BUNDLE_ZIP:
                archive.write(path, path.relative_to(entry).as_posix())
    return zip_path.stat().st_size


async def build_bundle(
    paper: PaperRef,
    *,
    store: ArtifactStore,
    marker: MarkerClient,
    max_pages: int = 1,
    ttl_hours: float = 24.0,
) -> Bundle:
    """Fetch, extract, and cache a paper; return the bundle.

    Marker is the only engine (SRS v0.2). If it is unreachable the error says
    so rather than falling back to something that would produce worse output.
    """
    key = bundle_key(paper)
    cached = load_cached(key, store=store)
    if cached is not None:
        logger.debug("bundle cache hit for %s", key)
        return cached

    if not paper.arxiv_id:  # pragma: no cover — bundle_key already guards
        raise InvalidArgumentError(f"{paper.paper_id} has no arXiv id")

    pdf = await fetch_arxiv_pdf(paper.arxiv_id)
    pages = page_count(pdf)
    logger.info("extracting %s (%d pages) via marker", key, pages)

    doc = await marker.extract(pdf, max_pages=max_pages)

    entry = store.ensure(key)
    markdown, figures, warnings = marker_doc_to_bundle_parts(doc, asset_dir=entry)
    if not markdown.strip():
        warnings.append("marker returned no text for this document")

    # Full text on disk; the inline copy may be capped.
    (entry / MARKDOWN_FILE).write_text(markdown, encoding="utf-8")

    inline, truncated = cap_markdown(markdown)
    zip_bytes = _write_zip(entry)

    bundle = Bundle(
        bundle_id=key,
        paper=paper,
        markdown=inline,
        markdown_truncated=truncated,
        figures=figures,
        extraction=ExtractionInfo(engine="marker", pages=pages, warnings=warnings),
        artifact=ArtifactRef(
            bytes=zip_bytes,
            expires_at=(datetime.now(UTC) + timedelta(hours=ttl_hours)).isoformat(),
        ),
    )
    # Written last: its presence is what makes the entry a cache hit, so an
    # interrupted run leaves a miss rather than a half-paper. Written *before*
    # URLs are attached, so the stored form carries no origin.
    (entry / BUNDLE_JSON).write_text(bundle.model_dump_json(indent=1), encoding="utf-8")
    return attach_urls(bundle, store=store)
