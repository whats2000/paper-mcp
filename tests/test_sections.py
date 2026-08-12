from __future__ import annotations

from paper_mcp.pipelines.sections import latex_to_markdown, split_sections


def test_split_sections_slices_bodies_between_headings() -> None:
    text = r"\section{Intro} alpha \section{Method} beta \subsection{Detail} gamma"

    sections = split_sections(text)

    assert [(s.name, s.order) for s in sections] == [
        ("Intro", 1),
        ("Method", 2),
        ("Detail", 3),
    ]
    assert "alpha" in sections[0].body
    assert "beta" in sections[1].body
    assert "alpha" not in sections[1].body
    assert "gamma" in sections[2].body


def test_text_before_the_first_section_is_kept() -> None:
    # Abstracts live here. Dropping everything before \section would discard
    # the single most useful passage in the paper.
    sections = split_sections(
        r"\begin{abstract} we propose a transformer \end{abstract} \section{Intro} a"
    )

    assert sections[0].name == "Abstract"
    assert "we propose a transformer" in sections[0].body
    assert sections[1].name == "Intro"


def test_leading_text_without_an_abstract_is_called_preamble() -> None:
    sections = split_sections(r"some front matter \section{Intro} a")

    assert sections[0].name == "Preamble"
    assert "front matter" in sections[0].body


def test_starred_sections_are_included() -> None:
    sections = split_sections(r"\section*{Acknowledgements} thanks \section{Refs} r")

    assert [s.name for s in sections] == ["Acknowledgements", "Refs"]


def test_a_document_with_no_sections_yields_one_body() -> None:
    sections = split_sections("just a blob of text with no headings")

    assert len(sections) == 1
    assert "blob" in sections[0].body


def test_empty_input_yields_no_sections() -> None:
    assert split_sections("   ") == []


def test_latex_to_markdown_keeps_math_and_drops_markup() -> None:
    md = latex_to_markdown(r"We use \textbf{attention} where $x^2$ holds \cite{foo}.")

    assert "attention" in md
    assert "$x^2$" in md
    assert "textbf" not in md
    assert "cite" not in md


def test_latex_to_markdown_preserves_display_math() -> None:
    md = latex_to_markdown(r"Then \begin{equation} E = mc^2 \end{equation} follows.")

    assert "E = mc^2" in md


def test_latex_to_markdown_drops_comments() -> None:
    md = latex_to_markdown("visible % hidden reviewer note\nnext line")

    assert "visible" in md
    assert "hidden reviewer note" not in md


def test_latex_to_markdown_keeps_paragraph_breaks() -> None:
    md = latex_to_markdown("first para\n\nsecond para")

    assert "\n\n" in md


def test_latex_to_markdown_unescapes_common_escapes() -> None:
    md = latex_to_markdown(r"50\% of \$5 \& more\_things")

    assert "50%" in md
    assert "$5" in md
    assert "& more_things" in md
