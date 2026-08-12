from __future__ import annotations

from pathlib import Path

import pymupdf

from paper_mcp.pipelines.latex_asset import latex_figures_and_equations


def _png(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 4, 4))
    pix.save(str(path))
    return path


def _pdf(path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    doc = pymupdf.open()
    doc.new_page(width=40, height=40)
    doc.save(str(path))
    doc.close()
    return path


def test_extracts_a_figure_with_its_caption(tmp_path: Path) -> None:
    src, asset = tmp_path / "src", tmp_path / "asset"
    _png(src / "diagram.png")
    text = (
        r"\section{Model}"
        r"\begin{figure}\includegraphics{diagram.png}"
        r"\caption{The \textbf{Transformer} architecture.}\end{figure}"
    )

    figures, _equations, warnings = latex_figures_and_equations(
        text, latex_source_dir=src, asset_dir=asset
    )

    assert len(figures) == 1
    assert figures[0].id == "fig-001"
    # Formatting commands are unwrapped, their text kept.
    assert figures[0].caption == "The Transformer architecture."
    assert figures[0].section == "Model"
    assert (asset / figures[0].image_path).is_file()
    assert warnings == []


def test_resolves_a_reference_with_no_extension(tmp_path: Path) -> None:
    src, asset = tmp_path / "src", tmp_path / "asset"
    _png(src / "plot.png")
    text = r"\begin{figure}\includegraphics[width=0.5\linewidth]{plot}\caption{P}\end{figure}"

    figures, _eq, _w = latex_figures_and_equations(
        text, latex_source_dir=src, asset_dir=asset
    )

    assert len(figures) == 1
    assert (asset / figures[0].image_path).is_file()


def test_resolves_by_basename_when_the_tarball_was_rerooted(tmp_path: Path) -> None:
    # arXiv tarballs sometimes move figures into subdirectories relative to
    # what the .tex references.
    src, asset = tmp_path / "src", tmp_path / "asset"
    _png(src / "images" / "deep" / "chart.png")
    text = r"\begin{figure}\includegraphics{chart}\caption{C}\end{figure}"

    figures, _eq, _w = latex_figures_and_equations(
        text, latex_source_dir=src, asset_dir=asset
    )

    assert len(figures) == 1


def test_pdf_figures_are_rasterized_to_png(tmp_path: Path) -> None:
    src, asset = tmp_path / "src", tmp_path / "asset"
    _pdf(src / "fig.pdf")
    text = r"\begin{figure}\includegraphics{fig.pdf}\caption{Vector}\end{figure}"

    figures, _eq, _w = latex_figures_and_equations(
        text, latex_source_dir=src, asset_dir=asset
    )

    assert figures[0].image_path.endswith(".png")
    assert (asset / figures[0].image_path).is_file()


def test_an_unresolvable_figure_is_reported_not_invented(tmp_path: Path) -> None:
    # The manifest is the grounding contract: an entry must correspond to a
    # real file, or an agent citing it would be citing nothing.
    src, asset = tmp_path / "src", tmp_path / "asset"
    src.mkdir()
    text = r"\begin{figure}\includegraphics{ghost.png}\caption{Missing}\end{figure}"

    figures, _eq, warnings = latex_figures_and_equations(
        text, latex_source_dir=src, asset_dir=asset
    )

    assert figures == []
    assert any("ghost.png" in w for w in warnings)


def test_extracts_equations_from_environments_and_display_math(tmp_path: Path) -> None:
    src, asset = tmp_path / "src", tmp_path / "asset"
    src.mkdir()
    text = (
        r"\section{Method}"
        r"\begin{equation} E = mc^2 \end{equation}"
        r"\begin{align} a &= b \end{align}"
        r"\[ x = y \]"
    )

    _figs, equations, _w = latex_figures_and_equations(
        text, latex_source_dir=src, asset_dir=asset
    )

    assert [e.id for e in equations] == ["eq-001", "eq-002", "eq-003"]
    assert "E = mc^2" in equations[0].latex
    assert equations[0].section == "Method"


def test_long_captions_are_truncated(tmp_path: Path) -> None:
    src, asset = tmp_path / "src", tmp_path / "asset"
    _png(src / "f.png")
    text = (
        r"\begin{figure}\includegraphics{f.png}\caption{"
        + "x" * 500
        + r"}\end{figure}"
    )

    figures, _eq, _w = latex_figures_and_equations(
        text, latex_source_dir=src, asset_dir=asset
    )

    assert len(figures[0].caption) <= 300


def test_figures_are_numbered_in_document_order(tmp_path: Path) -> None:
    src, asset = tmp_path / "src", tmp_path / "asset"
    _png(src / "a.png")
    _png(src / "b.png")
    text = (
        r"\begin{figure}\includegraphics{a.png}\caption{A}\end{figure}"
        r"\begin{figure}\includegraphics{b.png}\caption{B}\end{figure}"
    )

    figures, _eq, _w = latex_figures_and_equations(
        text, latex_source_dir=src, asset_dir=asset
    )

    assert [f.id for f in figures] == ["fig-001", "fig-002"]
    assert [f.caption for f in figures] == ["A", "B"]
