from __future__ import annotations

from paper_mcp.bundle import (
    Bundle,
    DocumentRef,
    FigureRef,
    cap_markdown,
)


def _bundle(**overrides: object) -> Bundle:
    base: dict[str, object] = {
        "bundle_id": "sha256:abc123",
        "document": DocumentRef(
            content_sha256="abc123", bytes=2048, pages=15, title="Attention Is All You Need"
        ),
        "markdown": "## Introduction\n\nbody text",
    }
    base.update(overrides)
    return Bundle(**base)  # type: ignore[arg-type]


def test_a_bundle_is_markdown_plus_a_figure_index() -> None:
    bundle = _bundle(
        figures=[
            FigureRef(
                id="fig-001",
                caption="The Transformer architecture.",
                page=3,
                image_path="figures/fig-001.png",
                image_url="https://example.org/a/tok/figures/fig-001.png",
            )
        ]
    )

    assert "## Introduction" in bundle.markdown
    assert bundle.figures[0].caption == "The Transformer architecture."
    assert bundle.figures[0].image_url is not None


def test_figures_default_to_empty_not_none() -> None:
    # A caller iterating the index must never have to null-check it.
    assert _bundle().figures == []
    assert _bundle().artifact is None


def test_extraction_defaults_to_marker() -> None:
    # There is no second engine to choose between.
    assert _bundle().extraction.engine == "marker"


def test_short_markdown_is_not_capped() -> None:
    text, truncated = cap_markdown("short paper", limit=100)

    assert text == "short paper"
    assert truncated is False


def test_long_markdown_is_capped_and_says_so() -> None:
    # Silent truncation would let an agent conclude a paper simply does not
    # discuss something it discusses on page 30.
    body = "\n".join(f"line {i}" for i in range(1000))

    text, truncated = cap_markdown(body, limit=200)

    assert truncated is True
    assert len(text) <= 200


def test_capping_cuts_on_a_line_boundary() -> None:
    body = "aaaa\nbbbb\ncccc\ndddd\neeee"

    text, _ = cap_markdown(body, limit=12)

    assert not text.endswith("cc")  # not mid-token
    assert text.endswith("bbbb")


def test_capping_does_not_cut_at_the_boundary_when_it_would_lose_most_of_it() -> None:
    # One enormous line: cutting at the last newline would throw away nearly
    # everything, so the hard cap wins.
    body = "x" * 50 + "\n" + "y" * 500

    text, truncated = cap_markdown(body, limit=300)

    assert truncated is True
    assert len(text) > 100
