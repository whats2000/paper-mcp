"""Slice a flattened LaTeX body into per-section markdown.

**New code, not a port.** PaperHub's `SectionAsset` carries only `(name,
order)` because it navigated papers by chunk lookup and rendered HTML for
display; it never needed section text. This service's bundle is markdown, so
the bodies have to be produced here.

The conversion is deliberately lossy and shallow. A faithful LaTeX-to-markdown
translator is a project in itself, and the fidelity path for hard documents is
Marker in B2. What this must get right is that an agent reading the output can
tell what the paper says: prose survives, math survives verbatim, and markup
noise disappears. Tables and exotic macros degrade, which is recorded in
`ExtractionInfo.warnings` rather than hidden.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

# \section, \subsection, \subsubsection — starred variants included, since
# Acknowledgements and Appendices are routinely starred and an agent still
# wants to find them. Subsections are kept as first-class entries: the whole
# point of an outline is targeted reads, and a 40-page paper with six
# top-level sections is not navigable.
_HEADING_RE = re.compile(
    r"\\(?:sub){0,2}section\*?\s*\{((?:[^{}]|\{[^{}]*\})*)\}",
)
_ABSTRACT_RE = re.compile(r"\\begin\{abstract\}", re.IGNORECASE)

# Comment stripping: a `%` not preceded by a backslash runs to end of line.
_COMMENT_RE = re.compile(r"(?<!\\)%[^\n]*")
# Environments whose *content* is worth keeping as-is (math).
_MATH_ENV_RE = re.compile(
    r"\\begin\{(equation\*?|align\*?|gather\*?|multline\*?)\}(.*?)\\end\{\1\}",
    re.DOTALL,
)
_DISPLAY_MATH_RE = re.compile(r"\\\[(.*?)\\\]", re.DOTALL)
# Environments that add nothing to a text reading of the paper.
_DROP_ENV_RE = re.compile(
    r"\\begin\{(figure\*?|table\*?|tabular|thebibliography|tikzpicture)\}.*?"
    r"\\end\{\1\}",
    re.DOTALL,
)
_CITE_LIKE_RE = re.compile(r"\\(?:cite[a-z]*|ref|label|eqref|footnote)\s*\{[^{}]*\}")
_CMD_WITH_ARG_RE = re.compile(r"\\[A-Za-z]+\*?\s*(?:\[[^\]]*\])?\{((?:[^{}]|\{[^{}]*\})*)\}")
_BARE_CMD_RE = re.compile(r"\\[A-Za-z]+\*?")
_LEFTOVER_BRACES_RE = re.compile(r"[{}]")
_MANY_BLANK_LINES_RE = re.compile(r"\n{3,}")
_SPACES_RE = re.compile(r"[ \t]{2,}")

# `\%` -> `%` and friends. Done last so stripping never sees a bare `%` it
# would treat as a comment.
_ESCAPES = {
    r"\%": "%",
    r"\$": "$",
    r"\&": "&",
    r"\_": "_",
    r"\#": "#",
    r"\{": "{",
    r"\}": "}",
    r"\~": "~",
    "``": '"',
    "''": '"',
    r"\\": "\n",
}


@dataclass(frozen=True)
class SectionSlice:
    name: str
    order: int
    body: str


def latex_to_markdown(body: str) -> str:
    """Convert a LaTeX fragment into readable markdown-ish text.

    Math is preserved rather than stripped: `$x^2$` means something to a
    model reading the paper, and deleting it would quietly change what the
    text claims.
    """
    text = _COMMENT_RE.sub("", body)
    text = _DROP_ENV_RE.sub(" ", text)
    # Keep the math, drop the environment wrapper.
    text = _MATH_ENV_RE.sub(lambda m: f"\n\n$$\n{m.group(2).strip()}\n$$\n\n", text)
    text = _DISPLAY_MATH_RE.sub(lambda m: f"\n\n$$\n{m.group(1).strip()}\n$$\n\n", text)
    text = _CITE_LIKE_RE.sub("", text)

    # Unwrap commands that carry readable content (\textbf{x} -> x). Repeated
    # because arguments nest; bounded so a pathological document cannot spin.
    for _ in range(4):
        replaced = _CMD_WITH_ARG_RE.sub(lambda m: m.group(1), text)
        if replaced == text:
            break
        text = replaced

    text = _BARE_CMD_RE.sub("", text)
    text = _LEFTOVER_BRACES_RE.sub("", text)
    for escaped, plain in _ESCAPES.items():
        text = text.replace(escaped, plain)
    text = _SPACES_RE.sub(" ", text)
    text = _MANY_BLANK_LINES_RE.sub("\n\n", text)
    return text.strip()


def split_sections(flattened_text: str) -> list[SectionSlice]:
    """Slice a flattened LaTeX body into sections, in document order.

    Text before the first heading is kept rather than dropped — that is where
    the abstract lives, and it is often the single most useful passage in the
    paper. It is named `Abstract` when an abstract environment is present and
    `Preamble` otherwise.
    """
    if not flattened_text.strip():
        return []

    headings = [
        (m.start(), m.end(), m.group(1).strip()) for m in _HEADING_RE.finditer(flattened_text)
    ]
    slices: list[SectionSlice] = []

    lead = flattened_text[: headings[0][0]] if headings else flattened_text
    if lead.strip():
        name = "Abstract" if _ABSTRACT_RE.search(lead) else "Preamble"
        slices.append(SectionSlice(name=name, order=0, body=lead))

    for index, (_, body_start, name) in enumerate(headings):
        body_end = headings[index + 1][0] if index + 1 < len(headings) else len(flattened_text)
        slices.append(
            SectionSlice(
                name=name or f"Section {index + 1}",
                order=index + 1,
                body=flattened_text[body_start:body_end],
            )
        )
    return slices
