"""Extract figures and equations from a flattened LaTeX document.

# Ported from PaperHub `backend/src/paperhub/pipelines/latex_to_asset.py` @ fd65834.
# Adapted: emits this project's `FigureRef` / `EquationRef` directly instead of
# PaperHub's intermediate `PaperAsset`, and returns warnings rather than only
# logging them — a caller reasoning about the paper needs to know a figure was
# referenced but could not be resolved.

The figure manifest is the grounding contract (SRS §III-3): an entry exists
only when a real image file was found and staged, so an agent citing
`fig-001` is citing something that demonstrably exists. A reference that
cannot be resolved produces a warning, never a manifest entry.
"""
from __future__ import annotations

import logging
import re
import shutil
from pathlib import Path

import pymupdf

from paper_mcp.bundle import EquationRef, FigureRef

logger = logging.getLogger(__name__)

_SECTION_RE = re.compile(r"\\(?:sub){0,2}section\*?\s*\{([^}]+)\}")
_FIGURE_ENV_RE = re.compile(r"\\begin\{figure\*?\}(.*?)\\end\{figure\*?\}", re.DOTALL)
_INCLUDEGRAPHICS_RE = re.compile(r"\\includegraphics(?:\[[^\]]*\])?\s*\{([^}]+)\}")
_CAPTION_RE = re.compile(r"\\caption\{((?:[^{}]|\{[^{}]*\})*)\}")
_EQUATION_ENV_RE = re.compile(
    r"\\begin\{(equation\*?|align\*?)\}(.*?)\\end\{\1\}", re.DOTALL
)
_DISPLAY_MATH_RE = re.compile(r"\\\[(.*?)\\\]", re.DOTALL)

_CMD_INNER_RE = re.compile(r"\\[A-Za-z]+\{([^{}]*)\}")
_CMD_BARE_RE = re.compile(r"\\[A-Za-z]+")
_WHITESPACE_RE = re.compile(r"\s+")

# Tried in order when a reference carries no extension.
_RESOLVE_EXTS = (".pdf", ".png", ".jpg", ".jpeg", ".eps")
_RASTERIZE_DPI = 200
_CAPTION_MAX = 300


def _section_map(text: str) -> list[tuple[int, str]]:
    return [(m.start(), m.group(1).strip()) for m in _SECTION_RE.finditer(text)]


def _section_at(offset: int, sections: list[tuple[int, str]]) -> str | None:
    """The most recent heading before `offset`."""
    name: str | None = None
    for section_offset, section_name in sections:
        if section_offset <= offset:
            name = section_name
        else:
            break
    return name


def _resolve_figure(ref: str, source_dir: Path) -> Path | None:
    """Resolve an `\\includegraphics` reference to a real file.

    Tries the reference verbatim, then each known extension, then a recursive
    search by basename — arXiv tarballs are routinely re-rooted so that the
    path in the `.tex` no longer matches where the file landed.
    """
    ref = ref.strip()
    direct = source_dir / ref
    if direct.is_file():
        return direct
    if not Path(ref).suffix:
        for ext in _RESOLVE_EXTS:
            candidate = source_dir / (ref + ext)
            if candidate.is_file():
                return candidate
        basename = Path(ref).name
        for ext in _RESOLVE_EXTS:
            hits = sorted(source_dir.rglob(basename + ext))
            if hits:
                return hits[0]
    return None


def _clean_caption(raw: str) -> str:
    """Turn a LaTeX caption body into readable plain text."""
    text = raw
    previous = None
    while previous != text:  # chains like \textbf{\emph{x}}
        previous = text
        text = _CMD_INNER_RE.sub(r"\1", text)
    text = _CMD_BARE_RE.sub(" ", text)
    text = text.replace("\\\\", " ").replace("~", " ")
    return _WHITESPACE_RE.sub(" ", text).strip()[:_CAPTION_MAX]


def _stage_figure(resolved: Path, figures_dir: Path, index: int) -> str:
    """Copy or rasterize a figure into the asset dir; return its relative path."""
    figures_dir.mkdir(parents=True, exist_ok=True)
    if resolved.suffix.lower() in (".pdf", ".eps"):
        name = f"fig-{index:03d}.png"
        # pymupdf ships partial type information; `open` is untyped.
        with pymupdf.open(resolved) as doc:  # type: ignore[no-untyped-call]
            doc.load_page(0).get_pixmap(dpi=_RASTERIZE_DPI).save(str(figures_dir / name))
    else:
        name = f"fig-{index:03d}{resolved.suffix}"
        shutil.copy2(resolved, figures_dir / name)
    return f"figures/{name}"


def latex_figures_and_equations(
    flattened_text: str, *, latex_source_dir: Path, asset_dir: Path
) -> tuple[list[FigureRef], list[EquationRef], list[str]]:
    """Build the figure and equation manifests for a flattened LaTeX body.

    Returns `(figures, equations, warnings)`. Warnings name references that
    could not be resolved or staged; they are returned rather than only logged
    because the caller surfaces them in `ExtractionInfo`, where an agent can
    see that the paper mentions a figure this bundle does not contain.
    """
    sections = _section_map(flattened_text)
    figures_dir = asset_dir / "figures"
    figures: list[FigureRef] = []
    warnings: list[str] = []

    for env in _FIGURE_ENV_RE.finditer(flattened_text):
        body = env.group(1)
        graphic = _INCLUDEGRAPHICS_RE.search(body)
        if graphic is None:
            continue
        ref = graphic.group(1)
        resolved = _resolve_figure(ref, latex_source_dir)
        if resolved is None:
            warnings.append(f"figure reference could not be resolved: {ref}")
            continue
        index = len(figures) + 1
        try:
            image_path = _stage_figure(resolved, figures_dir, index)
        except Exception as exc:  # one bad figure must not fail the whole paper
            warnings.append(f"figure {ref} could not be staged: {type(exc).__name__}")
            continue
        caption_match = _CAPTION_RE.search(body)
        figures.append(
            FigureRef(
                id=f"fig-{index:03d}",
                caption=_clean_caption(caption_match.group(1)) if caption_match else "",
                section=_section_at(env.start(), sections),
                image_path=image_path,
            )
        )

    equations: list[EquationRef] = []
    found: list[tuple[int, str]] = [
        (m.start(), m.group(2).strip()) for m in _EQUATION_ENV_RE.finditer(flattened_text)
    ]
    found += [(m.start(), m.group(1).strip()) for m in _DISPLAY_MATH_RE.finditer(flattened_text)]
    for offset, latex in sorted(found):
        if not latex:
            continue
        equations.append(
            EquationRef(
                id=f"eq-{len(equations) + 1:03d}",
                latex=latex,
                section=_section_at(offset, sections),
            )
        )

    return figures, equations, warnings
