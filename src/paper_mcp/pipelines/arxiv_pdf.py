"""Fetch an arXiv paper's PDF, which is what Marker ingests.

# Mirror-promotion behaviour ported from PaperHub
# `backend/src/paperhub/pipelines/arxiv_client.py` @ fd65834.
# Adapted: async, returns bytes rather than writing to a cache directory, and
# drops byte-range resume — Marker needs the whole document in memory anyway,
# and resume never helped against the failure it was written for.

Replaces the e-print tarball path. LaTeX source would in principle give exact
equations and vector figures, but only via a LaTeX→markdown converter good
enough not to degrade tables — and that is precisely what went wrong. Marker
is what this job was designed around (SRS v0.2, v0.3).
"""
from __future__ import annotations

import logging

import httpx

from paper_mcp.models import NotFoundError, RateLimitedError, UpstreamError

logger = logging.getLogger(__name__)

# arXiv asks for a contactable User-Agent per their Terms of Use.
# https://info.arxiv.org/help/api/tou.html
_USER_AGENT = "paper-mcp/0.1 (+https://github.com/whats2000/paper-mcp)"
_TIMEOUT = httpx.Timeout(180.0, connect=10.0)
_PDF_MAGIC = b"%PDF"
# Refuse absurd downloads outright rather than feeding a GPU pipeline
# something that will never finish.
_MAX_PDF_BYTES = 200 * 1024 * 1024

# The export mirror is arXiv's documented programmatic endpoint and is
# preferred, but it caps per-connection delivery and drops large transfers
# mid-stream (PaperHub measured an 8MB cap on a 41MB e-print). The main site
# does not. Promotion happens only on that signature.
_MIRRORS = (
    "https://export.arxiv.org/pdf/{arxiv_id}",
    "https://arxiv.org/pdf/{arxiv_id}",
)


async def _get(url: str, client: httpx.AsyncClient) -> bytes:
    received = bytearray()
    async with client.stream(
        "GET", url, headers={"User-Agent": _USER_AGENT}, follow_redirects=True
    ) as resp:
        if resp.status_code == 429:
            raise RateLimitedError(
                f"arXiv rate-limited the PDF download for {url}; retry shortly",
            )
        if resp.status_code == 404:
            raise NotFoundError(f"arXiv has no PDF at {url}")
        if resp.status_code >= 400:
            raise UpstreamError(f"arXiv returned HTTP {resp.status_code} for {url}")
        async for chunk in resp.aiter_bytes():
            received.extend(chunk)
            if len(received) > _MAX_PDF_BYTES:
                raise UpstreamError(
                    f"PDF exceeds the {_MAX_PDF_BYTES // (1024 * 1024)}MB ceiling",
                )
    return bytes(received)


async def fetch_arxiv_pdf(arxiv_id: str, *, client: httpx.AsyncClient | None = None) -> bytes:
    """Download `arxiv_id`'s PDF, promoting to the main site on a size cap.

    The size-cap signature is bytes received followed by the peer hanging up.
    Retrying the same mirror from the same offset hits the same wall every
    time, so the only useful response is a different mirror.
    """
    owned = client is None
    http = client or httpx.AsyncClient(timeout=_TIMEOUT)
    try:
        last_error: Exception | None = None
        for index, template in enumerate(_MIRRORS):
            url = template.format(arxiv_id=arxiv_id)
            try:
                data = await _get(url, http)
            except (httpx.RemoteProtocolError, httpx.ReadError) as exc:
                last_error = exc
                if index + 1 < len(_MIRRORS):
                    logger.warning(
                        "arxiv %s: %s from %s; promoting to the next mirror",
                        arxiv_id, type(exc).__name__, url,
                    )
                    continue
                raise UpstreamError(
                    f"arXiv PDF download failed for {arxiv_id}: {type(exc).__name__}",
                ) from exc
            except httpx.HTTPError as exc:
                raise UpstreamError(
                    f"arXiv PDF download failed for {arxiv_id}: {type(exc).__name__}",
                ) from exc

            if not data.startswith(_PDF_MAGIC):
                # arXiv serves an HTML holding page for withdrawn or
                # not-yet-processed papers. Handing that to Marker would waste
                # GPU minutes and yield nonsense.
                raise UpstreamError(
                    f"arXiv returned a non-PDF body for {arxiv_id} "
                    f"(starts with {data[:16]!r}); the paper may be withdrawn",
                )
            return data

        raise UpstreamError(  # pragma: no cover — loop returns or raises
            f"arXiv PDF download failed for {arxiv_id}: {last_error}",
        )
    finally:
        if owned:
            await http.aclose()
