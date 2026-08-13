"""Run the adversarial corpus inside the container, where nsjail exists.

The unit corpus proves the TeX-level hardening on any host. This proves the
jail — the layer that only exists on Linux, and therefore the one a Windows
dev box can never verify. It is the difference between "we configured a
sandbox" and "the sandbox holds".

Standalone on purpose: the runtime image carries no pytest.

    docker run --rm -v "$PWD/scripts:/app/scripts" paper-mcp \
        python /app/scripts/sandbox_corpus.py
"""
from __future__ import annotations

import subprocess
import sys
import tempfile
from pathlib import Path

from paper_mcp.pipelines.latex_compile import run_latex
from paper_mcp.sandbox import build_policy, nsjail_available

_DOC = r"""\documentclass{article}
\begin{document}
%s
\end{document}
"""

_results: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> None:
    _results.append((ok, name, detail))
    print(f"[{'  ok  ' if ok else ' FAIL '}] {name}" + (f"\n           {detail}" if detail else ""),
          flush=True)


def compile_in_jail(tex: str, workdir: Path, timeout_s: float = 60.0):
    workdir.mkdir(parents=True, exist_ok=True)
    policy = build_policy(workdir)
    return policy, run_latex(
        tex, workdir, timeout_s=timeout_s, argv_wrapper=list(policy.argv_prefix)
    )


def main() -> int:
    print(f"paper-mcp sandbox corpus\n{'=' * 72}", flush=True)

    check(nsjail_available(), "nsjail is installed", "the jail layer requires it")
    if not nsjail_available():
        print("\nnsjail missing — this must run inside the container image.")
        return 2

    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)

        policy, result = compile_in_jail(_DOC % "Hello from the jail.", root / "benign")
        check(policy.reason == "nsjail", "policy selects nsjail", policy.reason)
        check(
            result.ok and bool(result.pdf) and result.pdf.startswith(b"%PDF"),
            "a benign document still compiles inside the jail",
            f"engine={result.engine} pages={result.page_count} "
            f"errors={[e.message[:50] for e in result.errors[:2]]}",
        )

        marker = root / "pwned.txt"
        _p, result = compile_in_jail(
            _DOC % rf"\immediate\write18{{touch {marker.as_posix()}}}" + "\nok",
            root / "shell",
        )
        check(not marker.exists(), "shell escape does not execute")

        secret = root / "secret.tex"
        secret.write_text("LEAKED-SECRET", encoding="utf-8")
        _p, result = compile_in_jail(
            _DOC % rf"\input{{{secret.as_posix()}}}", root / "absread"
        )
        leaked = "LEAKED-SECRET" in (result.pdf or b"").decode("latin-1", "ignore")
        check(not leaked, "an absolute-path read is refused")

        outside = root / "outside.tex"
        outside.write_text("OUTSIDE-CONTENT", encoding="utf-8")
        _p, result = compile_in_jail(_DOC % r"\input{../outside.tex}", root / "trav")
        check("OUTSIDE-CONTENT" not in result.log_tail, "a traversal read is refused")

        escaped = root / "escaped.txt"
        compile_in_jail(
            _DOC % rf"\newwrite\o\immediate\openout\o={escaped.as_posix()}"
            rf"\immediate\write\o{{x}}\immediate\closeout\o",
            root / "write",
        )
        check(not escaped.exists(), "a write outside the job directory is refused")

        _p, result = compile_in_jail(
            _DOC % r"\loop\iftrue\repeat", root / "loop", timeout_s=8.0
        )
        check(
            not result.ok and any(e.kind == "timeout" for e in result.errors),
            "unbounded expansion is killed by the timeout",
        )

        # The jail's own guarantee: nothing inside it can reach the network.
        jail = build_policy(root / "net")
        (root / "net").mkdir(parents=True, exist_ok=True)
        probe = (
            "import socket,sys\n"
            "try:\n"
            "    socket.create_connection(('1.1.1.1', 80), 3); sys.exit('REACHED')\n"
            "except OSError:\n"
            "    sys.exit(0)\n"
        )
        completed = subprocess.run(
            [*jail.argv_prefix, sys.executable, "-c", probe],
            capture_output=True, text=True, timeout=60, check=False,
        )
        check(
            completed.returncode == 0 and "REACHED" not in (completed.stdout + completed.stderr),
            "no network reachable from inside the jail",
            f"rc={completed.returncode} {(completed.stdout + completed.stderr).strip()[:80]}",
        )

    failures = sum(1 for ok, _, _ in _results if not ok)
    print(f"\n{'=' * 72}\n{len(_results) - failures} passed, {failures} failed\n{'=' * 72}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
