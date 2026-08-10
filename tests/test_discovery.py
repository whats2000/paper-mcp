from __future__ import annotations

import pytest

from paper_mcp.models import NotFoundError
from paper_mcp.pipelines.arxiv_client import ArxivResult
from paper_mcp.pipelines.semantic_scholar import S2Hit
from paper_mcp.tools import discovery


def _s2_search_returning(*hits: S2Hit) -> object:
    async def _search(query: str, max_results: int = 8) -> list[S2Hit]:
        return list(hits)

    return _search


async def test_search_arxiv_returns_normalized_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery,
        "search_arxiv",
        lambda query, max_results=8: [
            ArxivResult(
                arxiv_id="1706.03762",
                title="Attention Is All You Need",
                authors=["Ashish Vaswani"],
                year=2017,
                abstract="…",
            )
        ],
    )

    refs = await discovery.tool_search_arxiv("transformer")

    assert [r.paper_id for r in refs] == ["arxiv:1706.03762"]
    assert refs[0].source == "arxiv"


async def test_search_papers_returns_normalized_refs(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        discovery, "search_papers", _s2_search_returning(S2Hit(s2_id="abc", title="T")),
    )

    refs = await discovery.tool_search_papers("transformer")

    assert refs[0].paper_id == "ss:abc"
    assert refs[0].source == "semantic_scholar"


async def test_find_related_passes_mode_through(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: dict[str, object] = {}

    async def _related(paper_id: str, *, mode: str, max_results: int = 8) -> list[S2Hit]:
        seen["paper_id"] = paper_id
        seen["mode"] = mode
        return [S2Hit(s2_id="z9", title="Follow-up")]

    monkeypatch.setattr(discovery, "find_related", _related)

    refs = await discovery.tool_find_related("arxiv:1706.03762", "cited_by")

    assert seen == {"paper_id": "arxiv:1706.03762", "mode": "cited_by"}
    assert refs[0].title == "Follow-up"


async def test_resolve_paper_uses_exact_arxiv_lookup_for_an_arxiv_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        discovery,
        "fetch_arxiv_by_id",
        lambda _id: ArxivResult(
            arxiv_id="1706.03762",
            title="Attention Is All You Need",
            authors=[],
            year=2017,
            abstract="…",
        ),
    )

    ref = await discovery.tool_resolve_paper("1706.03762")

    assert ref.paper_id == "arxiv:1706.03762"
    assert ref.open_access.available is True


async def test_resolve_paper_accepts_a_prefixed_arxiv_id_with_version(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: dict[str, str] = {}

    def _by_id(arxiv_id: str) -> ArxivResult:
        seen["arxiv_id"] = arxiv_id
        return ArxivResult(
            arxiv_id=arxiv_id, title="T", authors=[], year=None, abstract="",
        )

    monkeypatch.setattr(discovery, "fetch_arxiv_by_id", _by_id)

    await discovery.tool_resolve_paper("arXiv:1706.03762v5")

    # The bare id is what arXiv's id_list wants — prefix and version stripped.
    assert seen["arxiv_id"] == "1706.03762"


async def test_resolve_paper_falls_back_to_title_search(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        discovery,
        "search_papers",
        _s2_search_returning(S2Hit(s2_id="abc123", title="Some Closed Paper")),
    )

    ref = await discovery.tool_resolve_paper("Some Closed Paper")

    assert ref.paper_id == "ss:abc123"


async def test_resolve_paper_raises_not_found_when_nothing_matches(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(discovery, "search_papers", _s2_search_returning())

    with pytest.raises(NotFoundError):
        await discovery.tool_resolve_paper("a paper that does not exist anywhere")


async def test_resolve_paper_rejects_an_empty_identifier() -> None:
    with pytest.raises(NotFoundError):
        await discovery.tool_resolve_paper("   ")


async def test_resolve_paper_consults_unpaywall_when_s2_has_no_source(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def _unpaywall(doi: str, *, email: str) -> list[str]:
        return ["https://repo.example/p.pdf"]

    monkeypatch.setattr(
        discovery,
        "search_papers",
        _s2_search_returning(S2Hit(s2_id="abc123", title="Closed", doi="10.5555/x")),
    )
    monkeypatch.setattr(discovery, "open_access_urls", _unpaywall)
    monkeypatch.setattr(discovery, "_unpaywall_email", lambda: "a@b.c")

    ref = await discovery.tool_resolve_paper("Closed")

    assert ref.open_access.available is True
    assert ref.open_access.source == "unpaywall"
    assert ref.open_access.url == "https://repo.example/p.pdf"


async def test_resolve_paper_explains_when_unpaywall_is_unconfigured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        discovery,
        "search_papers",
        _s2_search_returning(S2Hit(s2_id="abc123", title="Closed", doi="10.5555/x")),
    )
    monkeypatch.setattr(discovery, "_unpaywall_email", lambda: None)

    ref = await discovery.tool_resolve_paper("Closed")

    assert ref.open_access.available is False
    assert "PAPER_MCP_UNPAYWALL_EMAIL" in (ref.open_access.reason or "")
