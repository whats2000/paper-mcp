from __future__ import annotations

import httpx
import pytest
import respx

from paper_mcp.models import RateLimitedError, UpstreamError
from paper_mcp.pipelines import semantic_scholar as ss
from paper_mcp.pipelines.semantic_scholar import (
    S2Hit,
    find_related,
    s2_to_ref,
    search_papers,
)

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
    assert hit.venue == "NeurIPS"
    assert hit.citation_count == 123456
    assert hit.authors == ["Ashish Vaswani"]  # malformed author entry dropped


@respx.mock
async def test_search_papers_clamps_the_limit_it_sends() -> None:
    route = respx.get(_SEARCH_URL).mock(return_value=httpx.Response(200, json={"data": []}))

    await search_papers("transformer", max_results=999)

    assert route.calls.last.request.url.params["limit"] == "50"


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
    assert exc_info.value.code == "rate_limited"


@respx.mock
async def test_server_error_raises_upstream_error() -> None:
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(503))

    with pytest.raises(UpstreamError):
        await search_papers("transformer")


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


@respx.mock
async def test_find_related_similar_reads_papers_directly() -> None:
    respx.get(
        "https://api.semanticscholar.org/graph/v1/paper/abc123/related"
    ).mock(
        return_value=httpx.Response(200, json={"data": [{"paperId": "z9", "title": "Kin"}]})
    )

    hits = await find_related("ss:abc123", mode="similar")

    assert hits[0].title == "Kin"


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
    assert ref.open_access.source == "arxiv"


def test_s2_to_ref_uses_open_access_pdf_when_there_is_no_arxiv_id() -> None:
    ref = s2_to_ref(
        S2Hit(s2_id="abc123", title="OA Paper", open_access_pdf_url="https://x.org/p.pdf")
    )

    assert ref.open_access.available is True
    assert ref.open_access.source == "s2"
    assert ref.open_access.url == "https://x.org/p.pdf"


def test_s2_to_ref_reports_absent_open_access_with_a_reason() -> None:
    ref = s2_to_ref(S2Hit(s2_id="abc123", title="Closed Paper"))

    assert ref.paper_id == "ss:abc123"
    assert ref.open_access.available is False
    assert ref.open_access.reason is not None
