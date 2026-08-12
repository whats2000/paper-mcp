"""Flatten a LaTeX source tree into one document body.

# Ported from PaperHub `backend/src/paperhub/pipelines/extract.py` @ fd65834.
# Adapted: only the LaTeX half is carried over — the PDF text/heading
# extraction lives in B2 with the other PDF engines. The main-file scoring
# heuristic is kept verbatim in behaviour; it exists because arXiv tarballs
# ship independently-compilable fragments and first-match-wins picked the
# wrong root (a two-table file beat the real 160KB paper).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

# All of these ignore commented-out lines: a `%`-prefixed \begin{document}
# must not be mistaken for the real one.
_BEGIN_DOC = re.compile(r"(?m)^(?:[^%\n]|\\%)*(\\begin\{document\})")
_END_DOC = re.compile(r"(?m)^(?:[^%\n]|\\%)*(\\end\{document\})")
_INPUT_INCLUDE = re.compile(r"\\(?:input|include)\{([^}]+)\}")
_DOCUMENTCLASS = re.compile(r"(?m)^(?:[^%\n]|\\%)*\\documentclass")
_TITLE_CMD = re.compile(r"(?m)^(?:[^%\n]|\\%)*\\title\{")
_MAKETITLE = re.compile(r"(?m)^(?:[^%\n]|\\%)*\\maketitle")
_SECTION_CMD = re.compile(r"(?m)^(?:[^%\n]|\\%)*\\(?:section|chapter)\b")
_PREFERRED_MAIN_STEMS = frozenset(
    {"main", "paper", "ms", "manuscript", "root", "article", "arxiv"}
)


@dataclass(frozen=True)
class LatexExtract:
    main_path: Path
    flattened_text: str
    # Everything before \begin{document}, with \input chains already inlined.
    # Kept because author macros defined there are what make the body's math
    # interpretable.
    preamble: str = ""


def _main_tex_score(path: Path, text: str) -> float:
    """Heuristic score for how likely a `.tex` file is the paper's root.

    arXiv tarballs frequently ship table/figure files that are independently
    compilable — same `\\documentclass` and `\\begin{document}` as the real
    paper. Picking the first match alphabetically once selected a two-table
    fragment over a 160KB `main.tex`. The true root almost always carries a
    title, a `\\maketitle`, and many sections, and dwarfs a fragment in size.
    """
    score = 0.0
    if _DOCUMENTCLASS.search(text):
        score += 5.0
    if _TITLE_CMD.search(text):
        score += 10.0
    if _MAKETITLE.search(text):
        score += 5.0
    score += min(len(_SECTION_CMD.findall(text)), 10)
    if path.stem.lower() in _PREFERRED_MAIN_STEMS:
        score += 8.0
    score += len(text) / 50_000.0  # minor tiebreak
    return score


def _find_main_tex(source_dir: Path) -> Path:
    candidates = sorted(source_dir.glob("*.tex"))  # deterministic tie order
    if not candidates:
        raise FileNotFoundError(f"no .tex files in {source_dir}")
    texts = {c: c.read_text(encoding="utf-8", errors="ignore") for c in candidates}
    # Prefer files that actually open a document body; among those (or all of
    # them if none do) take the highest score. `max` keeps the first on a tie
    # and `candidates` is sorted, so ties break alphabetically.
    roots = [c for c in candidates if _BEGIN_DOC.search(texts[c])]
    pool = roots or candidates
    return max(pool, key=lambda c: _main_tex_score(c, texts[c]))


def _inline_recursive(text: str, root: Path, seen: set[Path]) -> str:
    def repl(match: re.Match[str]) -> str:
        rel = match.group(1).strip()
        if not rel.endswith(".tex"):
            rel += ".tex"
        target = (root / rel).resolve()
        if target in seen:
            return ""  # cycle guard
        if not target.exists():
            # Warn rather than swallow: silence here once hid a bug where
            # \input{sections/foo} pointed at files the extractor had re-rooted.
            logger.warning("missing \\input/\\include target %r (looked for %s)", rel, target)
            return ""
        seen.add(target)
        return _inline_recursive(
            target.read_text(encoding="utf-8", errors="ignore"), root, seen
        )

    return _INPUT_INCLUDE.sub(repl, text)


def extract_latex(source_dir: Path) -> LatexExtract:
    """Flatten `source_dir` into one body, with the preamble split off.

    `\\input` / `\\include` directives are inlined recursively so the result
    is a single self-contained document body.
    """
    main = _find_main_tex(source_dir)
    raw = main.read_text(encoding="utf-8", errors="ignore")
    flat = _inline_recursive(raw, source_dir, seen={main.resolve()})

    begin = _BEGIN_DOC.search(flat)
    preamble = flat[: begin.start(1)] if begin else ""
    if begin:
        # Slice against group 1, not group 0: group 0 also consumes the
        # comment-free prefix on the same line and would eat real content.
        flat = flat[begin.end(1) :]
    end = _END_DOC.search(flat)
    if end:
        flat = flat[: end.start(1)]
    return LatexExtract(main_path=main, flattened_text=flat.strip(), preamble=preamble)
