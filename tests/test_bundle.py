from __future__ import annotations

from paper_mcp.bundle import (
    Bundle,
    EquationRef,
    ExtractionInfo,
    FigureRef,
    SectionRef,
    outline,
)
from paper_mcp.models import PaperRef


def _bundle(**overrides: object) -> Bundle:
    base: dict[str, object] = {
        "bundle_id": "arxiv:1706.03762",
        "paper": PaperRef(
            paper_id="arxiv:1706.03762", title="Attention Is All You Need", source="arxiv"
        ),
        "sections": [
            SectionRef(name="Introduction", order=1, char_count=4, markdown="body"),
            SectionRef(name="Method", order=2, char_count=6, markdown="method"),
        ],
        "extraction": ExtractionInfo(engine="latex"),
    }
    base.update(overrides)
    return Bundle(**base)  # type: ignore[arg-type]


def test_outline_drops_section_markdown_but_keeps_the_index() -> None:
    full = _bundle()

    trimmed = outline(full)

    assert [s.markdown for s in trimmed.sections] == [None, None]
    # The index is the whole point of an outline — names, order, and size stay.
    assert [s.name for s in trimmed.sections] == ["Introduction", "Method"]
    assert [s.char_count for s in trimmed.sections] == [4, 6]


def test_outline_does_not_mutate_the_original() -> None:
    full = _bundle()

    outline(full)

    assert [s.markdown for s in full.sections] == ["body", "method"]


def test_a_bundle_carries_figures_and_equations() -> None:
    bundle = _bundle(
        figures=[
            FigureRef(
                id="fig-001",
                caption="The Transformer architecture.",
                page=3,
                section="Model Architecture",
                image_path="figures/fig-001.png",
                image_url="https://example.org/a/tok/figures/fig-001.png",
            )
        ],
        equations=[
            EquationRef(id="eq-001", latex=r"\mathrm{Attention}(Q,K,V)", section="Model")
        ],
    )

    assert bundle.figures[0].id == "fig-001"
    assert bundle.equations[0].latex.startswith(r"\mathrm")


def test_figures_and_equations_default_to_empty_not_none() -> None:
    # A caller iterating the manifest must never have to null-check it.
    bundle = _bundle()

    assert bundle.figures == []
    assert bundle.equations == []
    assert bundle.artifact is None


def test_extraction_records_the_engine_and_any_warnings() -> None:
    # latex_to_markdown is lossy for tables and custom macros; the caller is
    # told which engine produced this and what degraded.
    bundle = _bundle(
        extraction=ExtractionInfo(engine="latex", warnings=["table markup dropped"])
    )

    assert bundle.extraction.engine == "latex"
    assert bundle.extraction.warnings == ["table markup dropped"]
