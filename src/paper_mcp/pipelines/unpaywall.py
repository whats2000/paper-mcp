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


def _pdf_url(location: Any) -> str | None:
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

    if not isinstance(payload, dict):
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
