from __future__ import annotations

import httpx
import pytest
import respx

from paper_mcp.models import NotFoundError, RateLimitedError, UpstreamError
from paper_mcp.pipelines.arxiv_pdf import fetch_arxiv_pdf

_EXPORT = "https://export.arxiv.org/pdf/1706.03762"
_MAIN = "https://arxiv.org/pdf/1706.03762"
_PDF = b"%PDF-1.7\n" + b"x" * 64


@respx.mock
async def test_downloads_from_the_export_mirror() -> None:
    respx.get(_EXPORT).mock(return_value=httpx.Response(200, content=_PDF))

    data = await fetch_arxiv_pdf("1706.03762")

    assert data.startswith(b"%PDF")


@respx.mock
async def test_promotes_to_the_main_site_on_a_dropped_transfer() -> None:
    # The size-cap signature: the export mirror caps per-connection delivery
    # and hangs up mid-stream. Retrying the same mirror hits the same wall.
    respx.get(_EXPORT).mock(side_effect=httpx.RemoteProtocolError("peer closed"))
    main = respx.get(_MAIN).mock(return_value=httpx.Response(200, content=_PDF))

    data = await fetch_arxiv_pdf("1706.03762")

    assert main.called
    assert data.startswith(b"%PDF")


@respx.mock
async def test_a_429_is_a_typed_rate_limit() -> None:
    respx.get(_EXPORT).mock(return_value=httpx.Response(429))

    with pytest.raises(RateLimitedError):
        await fetch_arxiv_pdf("1706.03762")


@respx.mock
async def test_a_404_is_typed_not_found() -> None:
    respx.get("https://export.arxiv.org/pdf/9999.99999").mock(
        return_value=httpx.Response(404)
    )

    with pytest.raises(NotFoundError):
        await fetch_arxiv_pdf("9999.99999")


@respx.mock
async def test_a_non_pdf_body_is_refused_before_it_reaches_marker() -> None:
    # arXiv serves an HTML holding page for withdrawn papers. Handing that to
    # Marker would burn GPU minutes and produce nonsense.
    respx.get(_EXPORT).mock(
        return_value=httpx.Response(200, content=b"<html><body>withdrawn</body></html>")
    )

    with pytest.raises(UpstreamError, match="non-PDF"):
        await fetch_arxiv_pdf("1706.03762")


@respx.mock
async def test_both_mirrors_failing_raises_a_typed_error() -> None:
    respx.get(_EXPORT).mock(side_effect=httpx.RemoteProtocolError("dropped"))
    respx.get(_MAIN).mock(side_effect=httpx.RemoteProtocolError("dropped"))

    with pytest.raises(UpstreamError):
        await fetch_arxiv_pdf("1706.03762")


@respx.mock
async def test_sends_a_contactable_user_agent() -> None:
    # Required by arXiv's Terms of Use.
    route = respx.get(_EXPORT).mock(return_value=httpx.Response(200, content=_PDF))

    await fetch_arxiv_pdf("1706.03762")

    assert "paper-mcp" in route.calls.last.request.headers["user-agent"]
