from __future__ import annotations

from datetime import datetime
from typing import Any

import pytest

from paper_mcp.pipelines import arxiv_client
from paper_mcp.pipelines.arxiv_client import (
    ArxivResult,
    arxiv_to_ref,
    fetch_arxiv_by_id,
    search_arxiv,
)


class _FakeAuthor:
    def __init__(self, name: str) -> None:
        self.name = name


class _FakeEntry:
    def __init__(self, entry_id: str, title: str) -> None:
        self.entry_id = entry_id
        self.title = f"  {title}  "
        self.authors = [_FakeAuthor("Ashish Vaswani")]
        self.published = datetime(2017, 6, 12)  # fixture only
        self.summary = "  We propose the Transformer.  "
        self.pdf_url = "https://arxiv.org/pdf/1706.03762"


class _FakeClient:
    def __init__(self, entries: list[_FakeEntry]) -> None:
        self._entries = entries

    def results(self, search: Any) -> list[_FakeEntry]:
        return self._entries


def test_search_strips_version_suffix_and_whitespace(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _FakeEntry("http://arxiv.org/abs/1706.03762v5", "Attention Is All You Need")
    monkeypatch.setattr(arxiv_client, "_client", _FakeClient([entry]))

    results = search_arxiv("transformer", max_results=3)

    assert len(results) == 1
    assert results[0].arxiv_id == "1706.03762"  # v5 stripped
    assert results[0].title == "Attention Is All You Need"  # whitespace stripped
    assert results[0].year == 2017
    assert results[0].abstract == "We propose the Transformer."


def test_search_clamps_max_results(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, int] = {}

    class _CapturingClient(_FakeClient):
        def results(self, search: Any) -> list[_FakeEntry]:
            captured["max_results"] = search.max_results
            return []

    monkeypatch.setattr(arxiv_client, "_client", _CapturingClient([]))
    search_arxiv("anything", max_results=999)

    assert captured["max_results"] == 50


def test_fetch_by_id_returns_none_when_upstream_raises(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class _ExplodingClient:
        def results(self, search: Any) -> list[_FakeEntry]:
            raise RuntimeError("arxiv is down")

    monkeypatch.setattr(arxiv_client, "_client", _ExplodingClient())

    # Best-effort verification: an unreachable arXiv means "unverifiable",
    # not a failed tool call.
    assert fetch_arxiv_by_id("1706.03762") is None


def test_fetch_by_id_returns_the_paper(monkeypatch: pytest.MonkeyPatch) -> None:
    entry = _FakeEntry("http://arxiv.org/abs/1706.03762v5", "Attention Is All You Need")
    monkeypatch.setattr(arxiv_client, "_client", _FakeClient([entry]))

    result = fetch_arxiv_by_id("1706.03762")

    assert result is not None
    assert result.arxiv_id == "1706.03762"


def test_arxiv_to_ref_marks_open_access_available() -> None:
    ref = arxiv_to_ref(
        ArxivResult(
            arxiv_id="1706.03762",
            title="Attention Is All You Need",
            authors=["Ashish Vaswani"],
            year=2017,
            abstract="We propose the Transformer.",
            pdf_url="https://arxiv.org/pdf/1706.03762",
        )
    )

    assert ref.paper_id == "arxiv:1706.03762"
    assert ref.source == "arxiv"
    # Every arXiv paper has an ingestible source by definition.
    assert ref.open_access.available is True
    assert ref.open_access.source == "arxiv"
    assert ref.open_access.url == "https://arxiv.org/pdf/1706.03762"


def test_arxiv_to_ref_falls_back_to_abs_url_without_a_pdf_url() -> None:
    ref = arxiv_to_ref(
        ArxivResult(
            arxiv_id="1706.03762", title="T", authors=[], year=None, abstract="",
        )
    )

    assert ref.open_access.url == "https://arxiv.org/abs/1706.03762"
    assert ref.abstract is None  # empty abstract normalizes to None
