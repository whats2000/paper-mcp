from __future__ import annotations

from pathlib import Path

import pytest

from paper_mcp.pipelines.latex_extract import extract_latex


def _write(root: Path, name: str, body: str) -> Path:
    path = root / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    return path


def test_strips_preamble_and_keeps_it_separately(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\newcommand{\vx}{x}"
        r"\begin{document}\section{Intro} body \end{document}",
    )

    result = extract_latex(tmp_path)

    assert r"\section{Intro}" in result.flattened_text
    assert "body" in result.flattened_text
    assert r"\documentclass" not in result.flattened_text
    # The preamble is kept: author macros are needed to make sense of the math.
    assert r"\newcommand{\vx}{x}" in result.preamble


def test_inlines_input_directives_recursively(tmp_path: Path) -> None:
    _write(tmp_path, "main.tex", r"\begin{document}\input{sections/intro}\end{document}")
    _write(tmp_path, "sections/intro.tex", r"\section{Intro}\input{sections/deep}")
    _write(tmp_path, "sections/deep.tex", "deep body")

    result = extract_latex(tmp_path)

    assert r"\section{Intro}" in result.flattened_text
    assert "deep body" in result.flattened_text


def test_missing_input_target_does_not_abort(tmp_path: Path) -> None:
    # Silently swallowing these once hid a re-rooting bug, so it warns — but a
    # missing include must not fail the whole extraction.
    _write(
        tmp_path,
        "main.tex",
        r"\begin{document}\input{nope}\section{Intro} kept \end{document}",
    )

    result = extract_latex(tmp_path)

    assert "kept" in result.flattened_text


def test_picks_the_real_root_over_an_independently_compilable_fragment(
    tmp_path: Path,
) -> None:
    # arXiv tarballs ship table/figure files with their own \documentclass and
    # \begin{document}. First-match-wins picked whichever sorted first, so a
    # two-table fragment could win over the actual paper.
    _write(
        tmp_path,
        "gb_results_table.tex",
        r"\documentclass{article}\begin{document}\begin{tabular}{c}1\end{tabular}\end{document}",
    )
    _write(
        tmp_path,
        "main.tex",
        r"\documentclass{article}\title{Real Paper}\begin{document}\maketitle"
        r"\section{One}a\section{Two}b\section{Three}c\end{document}",
    )

    result = extract_latex(tmp_path)

    assert result.main_path.name == "main.tex"
    assert "Three" in result.flattened_text


def test_commented_out_begin_document_is_ignored(tmp_path: Path) -> None:
    _write(
        tmp_path,
        "main.tex",
        "% \\begin{document} commented\n"
        r"\documentclass{article}\begin{document}real body\end{document}",
    )

    result = extract_latex(tmp_path)

    assert "real body" in result.flattened_text
    assert "commented" not in result.flattened_text


def test_no_tex_files_is_a_clear_error(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        extract_latex(tmp_path)
