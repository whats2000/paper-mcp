from __future__ import annotations

import base64
from pathlib import Path

from paper_mcp.pipelines.marker_client import MarkerBlock, MarkerDoc
from paper_mcp.pipelines.marker_to_bundle import marker_doc_to_bundle_parts

_PNG = base64.b64encode(
    b"\x89PNG\r\n\x1a\n" + b"0" * 32
).decode()


def _doc(*blocks: MarkerBlock) -> MarkerDoc:
    return MarkerDoc(blocks=list(blocks))


def test_tables_survive_as_markdown_tables(tmp_path: Path) -> None:
    # The whole reason this pipeline exists. A results table flattened into a
    # blob of cell text is worse than useless — an agent will read numbers
    # against the wrong columns.
    doc = _doc(
        MarkerBlock(
            block_type="Table",
            html=(
                "<table><tr><th>Model</th><th>BLEU</th></tr>"
                "<tr><td>Base</td><td>27.3</td></tr>"
                "<tr><td>Big</td><td>28.4</td></tr></table>"
            ),
        )
    )

    markdown, _figures, warnings = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert "| Model | BLEU |" in markdown
    assert "| --- | --- |" in markdown
    assert "| Base | 27.3 |" in markdown
    assert "| Big | 28.4 |" in markdown
    assert warnings == []


def test_equations_stay_latex(tmp_path: Path) -> None:
    doc = _doc(MarkerBlock(block_type="Equation", latex=r"E = mc^2", html="<p>E = mc2</p>"))

    markdown, _f, _w = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert "$$" in markdown
    assert "E = mc^2" in markdown


def test_figures_are_extracted_indexed_and_captioned(tmp_path: Path) -> None:
    doc = _doc(
        MarkerBlock(
            block_type="Figure",
            images={"img0": _PNG},
            caption="The Transformer architecture.",
            page=3,
        )
    )

    markdown, figures, _w = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert len(figures) == 1
    assert figures[0].id == "fig-001"
    assert figures[0].caption == "The Transformer architecture."
    assert figures[0].page == 3
    # The image is on disk, so the index entry is a promise that holds.
    assert (tmp_path / figures[0].image_path).is_file()
    # And it is referenced inline, where the surrounding prose explains it.
    assert "![fig-001]" in markdown
    assert "The Transformer architecture." in markdown


def test_a_figure_without_an_image_is_reported_not_indexed(tmp_path: Path) -> None:
    doc = _doc(MarkerBlock(block_type="Figure", caption="Missing pixels", images={}))

    _md, figures, warnings = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert figures == []
    assert any("Missing pixels" in w for w in warnings)


def test_undecodable_image_bytes_do_not_produce_an_index_entry(tmp_path: Path) -> None:
    doc = _doc(MarkerBlock(block_type="Figure", caption="Corrupt", images={"i": "!!!not b64"}))

    _md, figures, warnings = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert figures == []
    assert any("decode" in w for w in warnings)


def test_section_headers_become_markdown_headings(tmp_path: Path) -> None:
    doc = _doc(
        MarkerBlock(block_type="SectionHeader", html="<h1>Introduction</h1>"),
        MarkerBlock(block_type="Text", html="<p>We propose the Transformer.</p>"),
    )

    markdown, _f, _w = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert "## Introduction" in markdown
    assert "We propose the Transformer." in markdown


def test_heading_depth_follows_marker_hierarchy(tmp_path: Path) -> None:
    doc = _doc(
        MarkerBlock(
            block_type="SectionHeader",
            html="<h2>Attention</h2>",
            section_hierarchy={"1": "/page/0/SectionHeader/0", "2": "/page/1/SectionHeader/3"},
        )
    )

    markdown, _f, _w = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert "### Attention" in markdown


def test_running_heads_are_dropped(tmp_path: Path) -> None:
    # Page headers repeat on every page and interrupt the prose mid-sentence.
    doc = _doc(
        MarkerBlock(block_type="PageHeader", html="<p>Preprint. Under review.</p>"),
        MarkerBlock(block_type="Text", html="<p>Real content.</p>"),
        MarkerBlock(block_type="PageFooter", html="<p>12</p>"),
    )

    markdown, _f, _w = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert "Preprint" not in markdown
    assert "Real content." in markdown


def test_list_items_become_markdown_bullets(tmp_path: Path) -> None:
    doc = _doc(
        MarkerBlock(block_type="ListItem", html="<li>first</li>"),
        MarkerBlock(block_type="ListItem", html="<li>second</li>"),
    )

    markdown, _f, _w = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert "- first" in markdown
    assert "- second" in markdown


def test_html_entities_are_resolved(tmp_path: Path) -> None:
    doc = _doc(MarkerBlock(block_type="Text", html="<p>Wang &amp; Li &lt; 5%</p>"))

    markdown, _f, _w = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert "Wang & Li < 5%" in markdown


def test_an_unrenderable_table_is_flagged_not_flattened(tmp_path: Path) -> None:
    doc = _doc(MarkerBlock(block_type="Table", html="<table></table>"))

    markdown, _f, warnings = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert "table" in " ".join(warnings).lower()
    assert markdown == ""


def test_figures_are_numbered_in_document_order(tmp_path: Path) -> None:
    doc = _doc(
        MarkerBlock(block_type="Figure", images={"a": _PNG}, caption="A"),
        MarkerBlock(block_type="Figure", images={"b": _PNG}, caption="B"),
    )

    _md, figures, _w = marker_doc_to_bundle_parts(doc, asset_dir=tmp_path)

    assert [f.id for f in figures] == ["fig-001", "fig-002"]
    assert [f.caption for f in figures] == ["A", "B"]
