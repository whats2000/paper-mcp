import pytest

from paper_mcp.models import (
    InvalidArgumentError,
    OpenAccess,
    PaperRef,
    clamp_max_results,
    normalize_paper_id,
    s2_path_id,
)


def test_arxiv_id_is_preferred_identifier() -> None:
    # arXiv wins because it is the identifier with an ingestible source.
    assert normalize_paper_id(arxiv_id="1706.03762", s2_id="abc123", doi="10.5555/x") == (
        "arxiv:1706.03762"
    )


def test_falls_back_to_s2_then_doi() -> None:
    assert normalize_paper_id(arxiv_id=None, s2_id="abc123", doi="10.5555/x") == "ss:abc123"
    assert normalize_paper_id(arxiv_id=None, s2_id=None, doi="10.5555/x") == "doi:10.5555/x"


def test_no_identifier_at_all_is_an_error() -> None:
    with pytest.raises(InvalidArgumentError):
        normalize_paper_id(arxiv_id=None, s2_id=None, doi=None)


def test_s2_path_id_maps_each_prefix_to_upstream_form() -> None:
    assert s2_path_id("arxiv:1706.03762") == "arXiv:1706.03762"
    assert s2_path_id("ss:abc123") == "abc123"
    assert s2_path_id("doi:10.5555/x") == "DOI:10.5555/x"


def test_s2_path_id_rejects_unknown_prefix() -> None:
    with pytest.raises(InvalidArgumentError):
        s2_path_id("pubmed:12345")


def test_s2_path_id_rejects_unprefixed_id() -> None:
    with pytest.raises(InvalidArgumentError):
        s2_path_id("1706.03762")


@pytest.mark.parametrize(("given", "expected"), [(0, 1), (-5, 1), (8, 8), (50, 50), (999, 50)])
def test_max_results_is_clamped(given: int, expected: int) -> None:
    assert clamp_max_results(given) == expected


def test_paper_ref_defaults_open_access_to_unavailable() -> None:
    ref = PaperRef(
        paper_id="arxiv:1706.03762",
        title="Attention Is All You Need",
        authors=["Ashish Vaswani"],
        source="arxiv",
    )
    assert ref.open_access == OpenAccess(available=False)
    assert ref.abstract is None


def test_tool_errors_carry_a_code_and_optional_retry_after() -> None:
    from paper_mcp.models import RateLimitedError

    err = RateLimitedError("slow down", retry_after=7.0)

    assert err.code == "rate_limited"
    assert err.retry_after == 7.0
    assert str(err) == "slow down"
