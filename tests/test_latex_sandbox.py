"""Adversarial corpus — the release gate for compiling untrusted LaTeX.

Every case here is an attack a caller can mount against a public endpoint,
and each must **fail closed**. These run against the real TeX engine, because
the question is what the engine actually does with the hardening applied, not
what we believe it does.

A sandbox change that has not been re-run against this corpus does not ship
(SRS I-8 #4).
"""
from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from paper_mcp.pipelines.latex_compile import run_latex
from paper_mcp.sandbox import build_policy

pytestmark = pytest.mark.skipif(
    shutil.which("pdflatex") is None, reason="TeX not installed"
)

_DOC = r"""\documentclass{article}
\begin{document}
%s
\end{document}
"""


def _compile(tex: str, workdir: Path, timeout_s: float = 60.0):
    policy = build_policy(workdir, auth_mode="open")
    return run_latex(
        tex,
        workdir,
        timeout_s=timeout_s,
        argv_wrapper=list(policy.argv_prefix),
    )


def test_a_benign_document_still_compiles(tmp_path: Path) -> None:
    # The control. Hardening that also blocks legitimate documents is not
    # security, it is breakage.
    result = _compile(_DOC % "Hello, world.", tmp_path)

    assert result.ok is True
    assert result.pdf is not None
    assert result.pdf.startswith(b"%PDF")
    assert result.page_count == 1


def test_shell_escape_is_refused(tmp_path: Path) -> None:
    # \write18 is arbitrary command execution on the host.
    marker = tmp_path / "pwned.txt"
    result = _compile(
        _DOC % rf"\immediate\write18{{echo pwned > {marker.as_posix()}}}" + "\nok",
        tmp_path,
    )

    # The security property: the command did not run. Measured against real
    # TeX Live, the engine simply aborts without naming shell escape, so the
    # absence of the file is the assertion that matters.
    assert not marker.exists(), "shell escape executed — this is RCE"
    assert result.ok is False, r"the document compiled despite attempting \write18"


def test_reading_an_absolute_path_is_refused(tmp_path: Path) -> None:
    # openin_any=p forbids absolute paths, `..`, and dotfiles.
    secret = tmp_path.parent / "secret_marker.tex"
    secret.write_text(r"LEAKED-SECRET-CONTENT", encoding="utf-8")

    result = _compile(_DOC % rf"\input{{{secret.as_posix()}}}", tmp_path)

    assert "LEAKED-SECRET-CONTENT" not in (result.pdf or b"").decode("latin-1", "ignore")
    assert result.ok is False or "LEAKED" not in result.log_tail


def test_traversal_read_is_refused(tmp_path: Path) -> None:
    outside = tmp_path.parent / "outside.tex"
    outside.write_text("OUTSIDE-CONTENT", encoding="utf-8")

    result = _compile(_DOC % r"\input{../outside.tex}", tmp_path)

    assert "OUTSIDE-CONTENT" not in result.log_tail


def test_writing_outside_the_job_directory_is_refused(tmp_path: Path) -> None:
    # openout_any=p confines writes to the job dir.
    target = tmp_path.parent / "escaped_write.txt"

    _compile(
        _DOC % rf"\newwrite\out\immediate\openout\out={target.as_posix()}"
        rf"\immediate\write\out{{escaped}}\immediate\closeout\out",
        tmp_path,
    )

    assert not target.exists(), "TeX wrote outside its job directory"


def test_unbounded_expansion_is_killed_by_the_timeout(tmp_path: Path) -> None:
    # A loop with no exit is the cheapest denial of service against a
    # compile service.
    result = _compile(
        # Genuinely non-terminating: an earlier version used a counter loop
        # that TeX exited on overflow, so it never exercised the timeout.
        _DOC % r"\loop\iftrue\repeat",
        tmp_path,
        timeout_s=8.0,
    )

    assert result.ok is False
    assert any(e.kind == "timeout" for e in result.errors)


def test_a_syntax_error_is_reported_with_a_location(tmp_path: Path) -> None:
    # Not an attack — the everyday case. The caller's model needs to know
    # where to fix, not just that something failed.
    result = _compile(_DOC % r"\undefinedcommandhere", tmp_path)

    assert result.ok is False
    assert result.errors
    assert any("Undefined control sequence" in e.message for e in result.errors)


def test_a_missing_figure_is_reported_as_such(tmp_path: Path) -> None:
    # Distinct from a syntax error: it means an asset was not supplied.
    result = _compile(
        r"""\documentclass{article}\usepackage{graphicx}
\begin{document}\includegraphics{no_such_figure.png}\end{document}""",
        tmp_path,
    )

    assert result.ok is False
    assert any(e.kind == "missing_file" for e in result.errors)


def test_the_policy_refuses_to_compile_unjailed_on_a_public_endpoint() -> None:
    # The decision that matters most: without a jail, a public endpoint must
    # decline rather than run a stranger's program.
    policy = build_policy(Path("/tmp/job"), auth_mode="oidc")

    if policy.reason == "nsjail":
        pytest.skip("nsjail present; the refusal branch does not apply")
    assert policy.available is False
    assert "refusing" in policy.reason.lower()


def test_the_policy_allows_unjailed_compiles_only_in_open_mode() -> None:
    policy = build_policy(Path("/tmp/job"), auth_mode="open")

    assert policy.available is True
    if policy.reason != "nsjail":
        assert policy.reason == "unsandboxed-dev"
