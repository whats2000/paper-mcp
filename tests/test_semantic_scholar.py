from __future__ import annotations

import re

import httpx
import pytest
import respx

from paper_mcp.models import NotFoundError, RateLimitedError, UpstreamError
from paper_mcp.pipelines import semantic_scholar as ss
from paper_mcp.pipelines.semantic_scholar import (
    S2Hit,
    fetch_paper_metadata,
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
async def test_upstream_error_carries_the_response_body() -> None:
    # A bare status code once forced a manual probe of the live API to find
    # out why a request was rejected. The upstream's own explanation must
    # reach the caller.
    respx.get(_SEARCH_URL).mock(
        return_value=httpx.Response(
            400, json={"error": "Unrecognized or unsupported fields: [authors.name]"}
        )
    )

    with pytest.raises(UpstreamError, match=re.escape("authors.name")):
        await search_papers("transformer")


@respx.mock
async def test_requests_plain_authors_not_the_dotted_subselection() -> None:
    # Regression guard: /citations, /references and /related reject
    # `authors.name` with HTTP 400 even though /paper/search accepts it.
    # Plain `authors` is accepted by all of them.
    route = respx.get(
        "https://api.semanticscholar.org/graph/v1/paper/arXiv:1706.03762/citations"
    ).mock(return_value=httpx.Response(200, json={"data": []}))

    await find_related("arxiv:1706.03762", mode="cited_by")

    fields = route.calls.last.request.url.params["fields"]
    assert "authors" in fields
    assert "authors.name" not in fields


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
async def test_find_related_similar_uses_the_recommendations_service() -> None:
    # `graph/v1/paper/{id}/related` does not exist — it 404s with a misleading
    # "Paper with id <id>/related not found". Similar papers come from a
    # separate service, under a different response key.
    route = respx.get(
        "https://api.semanticscholar.org/recommendations/v1/papers/forpaper/abc123"
    ).mock(
        return_value=httpx.Response(
            200, json={"recommendedPapers": [{"paperId": "z9", "title": "Kin"}]}
        )
    )

    hits = await find_related("ss:abc123", mode="similar")

    assert hits[0].title == "Kin"
    # Without `from`, the service returns an empty list for most papers.
    assert route.calls.last.request.url.params["from"] == "all-cs"


@respx.mock
async def test_a_404_is_not_found_not_an_upstream_fault() -> None:
    # Semantic Scholar simply not having a paper is an ordinary answer; the
    # caller can still fall back to a title search.
    respx.get(
        "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1/x"
    ).mock(return_value=httpx.Response(404, json={"error": "not found"}))

    with pytest.raises(NotFoundError):
        await fetch_paper_metadata("doi:10.1/x")


@respx.mock
async def test_a_404_carries_the_upstream_body() -> None:
    # A 404 body distinguishes "no such paper" from "no such route" — the
    # message "Paper with id <id>/related not found" is what revealed that a
    # whole endpoint did not exist. An earlier version dropped it, which is
    # precisely the diagnostic that had to be recovered by hand.
    respx.get(
        "https://api.semanticscholar.org/graph/v1/paper/DOI:10.1/x"
    ).mock(
        return_value=httpx.Response(
            404, json={"error": "Paper with id DOI:10.1/x/related not found"}
        )
    )

    with pytest.raises(NotFoundError, match=re.escape("/related not found")):
        await fetch_paper_metadata("doi:10.1/x")


@respx.mock
async def test_citation_results_without_the_nested_paper_are_skipped() -> None:
    respx.get(
        "https://api.semanticscholar.org/graph/v1/paper/arXiv:1706.03762/citations"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {"citingPaper": None},
                    {},
                    {"citingPaper": {"paperId": "z9", "title": "Real"}},
                ]
            },
        )
    )

    hits = await find_related("arxiv:1706.03762", mode="cited_by")

    assert [h.title for h in hits] == ["Real"]


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


@respx.mock
async def test_retry_ladder_is_bounded_by_a_wall_clock_deadline(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A throttled call must return before an MCP client's read timeout.

    Attempt counts alone do not bound latency: pacing plus exponential
    backoff let one call run for minutes. A measured on-device run died that
    way — the client timed out and dropped the connection instead of
    receiving the typed rate-limit error the ladder was about to raise.
    """
    monkeypatch.setattr(ss, "_MAX_ATTEMPTS", 50)
    monkeypatch.setattr(ss, "_RETRY_BASE_S", 30.0)
    monkeypatch.setattr(ss, "_DEADLINE_S", 5.0)

    slept: list[float] = []

    async def _record(seconds: float) -> None:
        slept.append(seconds)

    monkeypatch.setattr(ss, "_sleep", _record)
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(429))

    with pytest.raises(RateLimitedError):
        await search_papers("transformer")

    # It must give up rather than sleep a 30s backoff against a 5s budget.
    assert sum(slept) <= 5.0, f"slept {sum(slept)}s against a 5s deadline"


@respx.mock
async def test_pacing_is_inside_the_budget_not_just_backoff(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Total call time must be bounded, pacing included.

    Bounding only the backoff sleep was not enough: with several attempts the
    per-attempt pacing dominated, a call ran ~60s on device, and the MCP
    dispatcher timed the request out. The caller then saw a dead connection
    instead of a typed rate-limit error.

    Uses a virtual clock, because a no-op sleep advances no real time and the
    deadline would never trip.
    """

    class _Clock:
        now = 0.0

        def monotonic(self) -> float:
            return self.now

    clock = _Clock()
    slept: list[float] = []

    async def _advance(seconds: float) -> None:
        clock.now += seconds
        slept.append(seconds)

    monkeypatch.setattr(ss, "time", clock)
    monkeypatch.setattr(ss, "_sleep", _advance)
    monkeypatch.setattr(ss, "_MAX_ATTEMPTS", 10)
    monkeypatch.setattr(ss, "_MIN_INTERVAL_S", 6.0)
    monkeypatch.setattr(ss, "_RETRY_BASE_S", 1.0)
    monkeypatch.setattr(ss, "_DEADLINE_S", 20.0)
    monkeypatch.setattr(ss, "_last_request_ts", 0.0)
    respx.get(_SEARCH_URL).mock(return_value=httpx.Response(429))

    with pytest.raises(RateLimitedError):
        await search_papers("transformer")

    # Ten attempts at 6s pacing would be 60s+; the budget must stop it early.
    assert clock.now <= 26.0, f"call ran {clock.now}s against a 20s budget"
