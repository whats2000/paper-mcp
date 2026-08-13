"""Compile caller-supplied LaTeX, once, with hardening.

# Engine selection and CJK handling ported from PaperHub
# `backend/src/paperhub/pipelines/slide_pipeline/compile.py` @ fd65834.
# Adapted: no revise loop. PaperHub retried with LLM edits because it was an
# agent; this is a tool (SRS v0.3) — it compiles, reports structured errors,
# and lets the caller's model decide what to change.

Hardening applied at the TeX level. It is the innermost of three layers, not
the whole defence: the jail (`paper_mcp.sandbox`) and the wall-clock/output
caps sit outside it, because each stops something the others do not.
"""
from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

logger = logging.getLogger(__name__)

# Source signals that the document needs a Unicode engine. A CJK deck built
# for xeCJK silently drops every CJK glyph under pdflatex — a wrong-looking
# PDF rather than an error — so the requirement is honoured, not hardcoded.
_XELATEX_TRIGGERS = ("xecjk", "fontspec", "ctex", "% !tex program = xelatex")

# Refuse shell escape, and confine file reads/writes to the job directory.
# `p` means "paranoid": no absolute paths, no `..`, no dotfiles.
_HARDENING_ENV = {
    "openin_any": "p",
    "openout_any": "p",
    "shell_escape": "f",
    "max_print_line": "1000",
}
_HARDENING_ARGS = (
    "-no-shell-escape",
    "-interaction=nonstopmode",
    "-halt-on-error",
    "-file-line-error",
)

_ENGINES = {"pdflatex", "xelatex", "lualatex"}

# `./file.tex:12: Undefined control sequence` — enabled by -file-line-error.
_FILE_LINE_RE = re.compile(r"^(?P<file>[^:\n]+):(?P<line>\d+):\s*(?P<message>.+)$", re.MULTILINE)
# Fallback for engines/messages that ignore -file-line-error.
_BANG_RE = re.compile(r"^! (?P<message>.+)$", re.MULTILINE)
# Missing assets surface in several shapes depending on which package
# complains — `! LaTeX Error: File ... not found`, or (measured against real
# TeX Live) `./main.tex:2: Package pdftex.def Error: File `x.png' not found`.
# Matching the common substring catches all of them.
_MISSING_FILE_RE = re.compile(r"File `(?P<file>[^']+)' not found", re.MULTILINE)
_OVERFULL_RE = re.compile(r"^(Overfull|Underfull) \\[hv]box.*$", re.MULTILINE)


@dataclass
class CompileError:
    message: str
    file: str | None = None
    line: int | None = None
    kind: str = "latex_error"


@dataclass
class CompileResult:
    ok: bool
    engine: str
    pdf: bytes | None = None
    page_count: int = 0
    log_tail: str = ""
    errors: list[CompileError] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def select_engine(tex: str, requested: str = "auto") -> str:
    """Resolve which engine to run.

    `auto` picks xelatex when the source declares a Unicode-engine dependency
    and xelatex exists, else pdflatex.
    """
    if requested in _ENGINES:
        resolved = shutil.which(requested)
        if resolved:
            return resolved
        logger.warning("requested engine %s is not installed; falling back", requested)

    low = tex.lower()
    if any(trigger in low for trigger in _XELATEX_TRIGGERS):
        xelatex = shutil.which("xelatex")
        if xelatex:
            return xelatex
        logger.warning(
            "source needs xelatex (xeCJK/fontspec/ctex) but it is not installed; "
            "using pdflatex — Unicode glyphs may silently vanish",
        )
    return shutil.which("pdflatex") or "pdflatex"


def parse_errors(log: str) -> list[CompileError]:
    """Turn a TeX log into located, actionable errors.

    A raw log is not actionable: the caller's model needs to know *where* the
    failure is to fix it. Missing-file errors are called out separately
    because they usually mean a figure was referenced but not supplied — a
    different fix from a syntax error.
    """
    errors: list[CompileError] = []
    seen: set[tuple[str | None, int | None, str]] = set()
    key: tuple[str | None, int | None, str]

    for match in _MISSING_FILE_RE.finditer(log):
        key = (match.group("file"), None, "missing_file")
        if key not in seen:
            seen.add(key)
            errors.append(
                CompileError(
                    message=f"File `{match.group('file')}' not found",
                    file=match.group("file"),
                    kind="missing_file",
                )
            )

    for match in _FILE_LINE_RE.finditer(log):
        message = match.group("message").strip()
        if not message.startswith(("Undefined", "Missing", "Emergency", "LaTeX Error", "Package")):
            continue
        key = (match.group("file"), int(match.group("line")), message)
        if key in seen:
            continue
        seen.add(key)
        errors.append(
            CompileError(
                message=message,
                file=match.group("file"),
                line=int(match.group("line")),
            )
        )

    if not errors:
        for match in _BANG_RE.finditer(log):
            message = match.group("message").strip()
            key = (None, None, message)
            if key not in seen:
                seen.add(key)
                errors.append(CompileError(message=message))
    return errors


def parse_warnings(log: str) -> list[str]:
    """Layout warnings worth surfacing.

    Overfull boxes are how a deck silently runs text off the slide, and
    pdflatex treats them as warnings it happily ignores.
    """
    return [m.group(0).strip() for m in _OVERFULL_RE.finditer(log)][:20]


def page_count(pdf: bytes) -> int:
    try:
        import pymupdf

        with pymupdf.open(stream=pdf, filetype="pdf") as doc:  # type: ignore[no-untyped-call]
            count: int = doc.page_count
        return count
    except Exception:  # a PDF we cannot measure is still a PDF
        return 0


def run_latex(
    tex: str,
    workdir: Path,
    *,
    engine: str = "auto",
    timeout_s: float = 120.0,
    argv_wrapper: list[str] | None = None,
    max_log_chars: int = 8000,
) -> CompileResult:
    """Compile `tex` in `workdir`, exactly once.

    `argv_wrapper` prefixes the command — this is how the jail is applied,
    keeping sandbox mechanics out of the compile logic.
    """
    workdir.mkdir(parents=True, exist_ok=True)
    source = workdir / "main.tex"
    source.write_text(tex, encoding="utf-8")

    resolved = select_engine(tex, engine)
    argv = [*(argv_wrapper or []), resolved, *_HARDENING_ARGS, "main.tex"]
    env = {
        "PATH": "/usr/bin:/bin:/usr/local/bin",
        "HOME": str(workdir),
        "TEXMFVAR": str(workdir / ".texmf"),
        **_HARDENING_ENV,
    }

    try:
        # argv is built here and never passed through a shell.
        completed = subprocess.run(
            argv,
            cwd=workdir,
            env=env,
            capture_output=True,
            text=True,
            errors="replace",
            timeout=timeout_s,
            check=False,
        )
        log = completed.stdout + completed.stderr
    except subprocess.TimeoutExpired as exc:
        # `text=True` makes TimeoutExpired.stdout a str, not bytes; calling
        # .decode() on it raised AttributeError and turned a clean timeout
        # into a crash. Handle both, since the type depends on how run() was
        # configured.
        raw = exc.stdout or ""
        partial = raw.decode("utf-8", "replace") if isinstance(raw, bytes) else str(raw)
        return CompileResult(
            ok=False,
            engine=Path(resolved).stem,
            log_tail=partial[-max_log_chars:],
            errors=[
                CompileError(
                    message=(
                        f"compilation exceeded {timeout_s:.0f}s and was killed — "
                        "usually unbounded macro expansion"
                    ),
                    kind="timeout",
                )
            ],
        )
    except FileNotFoundError as exc:
        return CompileResult(
            ok=False,
            engine=Path(resolved).stem,
            errors=[
                CompileError(
                    message=f"LaTeX engine not available: {exc}", kind="engine_unavailable"
                )
            ],
        )

    # The .log file carries more than stdout when the engine is terse.
    log_file = workdir / "main.log"
    if log_file.is_file():
        log += "\n" + log_file.read_text(encoding="utf-8", errors="replace")

    pdf_path = workdir / "main.pdf"
    pdf = pdf_path.read_bytes() if pdf_path.is_file() else None
    errors = parse_errors(log)
    # A PDF can exist alongside errors; trust the engine's exit status for
    # ok-ness, and report the errors either way.
    return CompileResult(
        ok=completed.returncode == 0 and pdf is not None,
        engine=Path(resolved).stem,
        pdf=pdf,
        page_count=page_count(pdf) if pdf else 0,
        log_tail=log[-max_log_chars:],
        errors=errors,
        warnings=parse_warnings(log),
    )
