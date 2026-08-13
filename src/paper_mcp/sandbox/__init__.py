"""Isolation for compiling untrusted LaTeX.

TeX is Turing-complete, `\\write18` shells out, `\\input` reads arbitrary
paths and `\\openout` writes them. Compiling caller-supplied source is running
a stranger's program, so this decides *whether* that is allowed and *how* it
is confined.

Three layers, each defeating something the others do not: TeX flags (in
`pipelines.latex_compile`), this jail, and the wall-clock/output caps. nsjail
is Linux-only, so a Windows dev box has no jail — which is handled by
refusing, not by pretending.
"""
from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

from paper_mcp.config import settings

logger = logging.getLogger(__name__)

NSJAIL = "nsjail"

# Generous enough for a real deck, tight enough that a fork bomb or a runaway
# expansion dies rather than taking the host with it.
_MAX_CPU_SECONDS = 120
_MAX_ADDRESS_SPACE_MB = 2048
_MAX_FILE_SIZE_MB = 128
_MAX_PIDS = 32


@dataclass(frozen=True)
class SandboxPolicy:
    available: bool
    reason: str
    argv_prefix: tuple[str, ...] = ()

    @property
    def sandboxed(self) -> bool:
        return bool(self.argv_prefix)


def nsjail_available() -> bool:
    return shutil.which(NSJAIL) is not None


def build_policy(workdir: Path, *, auth_mode: str | None = None) -> SandboxPolicy:
    """Decide how — or whether — to compile.

    The refusal is the important branch. Running a stranger's TeX with no jail
    on a public endpoint is worse than declining to run it: a declined compile
    costs the caller a retry, an escaped one costs the host. So an
    unsandboxed compile is permitted only in explicitly-open development mode.
    """
    mode = auth_mode if auth_mode is not None else settings().auth_mode

    if nsjail_available():
        return SandboxPolicy(
            available=True,
            reason="nsjail",
            argv_prefix=tuple(_nsjail_argv(workdir)),
        )

    if mode == "open":
        logger.warning(
            "compiling WITHOUT a sandbox: nsjail is not installed. Acceptable only "
            "for local development; TeX-level hardening is the sole defence.",
        )
        return SandboxPolicy(
            available=True,
            reason="unsandboxed-dev",
        )

    return SandboxPolicy(
        available=False,
        reason=(
            "nsjail is not installed, so caller-supplied LaTeX cannot be isolated. "
            "Refusing to compile: this endpoint is not in development mode. "
            "Install nsjail (it ships in the container image) or set "
            "PAPER_MCP_AUTH_MODE=open for local use."
        ),
    )


def _nsjail_argv(workdir: Path) -> list[str]:
    """nsjail invocation confining one compile.

    No network at all, a read-only view of the TeX installation, a writable
    tmpfs for the job, an unprivileged uid, and rlimits that bound cpu,
    memory, output size and process count.
    """
    return [
        NSJAIL,
        "--quiet",
        "--mode", "o",              # run once, then exit
        "--hostname", "sandbox",
        "--user", "65534",          # nobody
        "--group", "65534",
        # nsjail clones a fresh network namespace by default, so there is no
        # route out; dropping loopback too leaves no interface at all.
        "--iface_no_lo",
        "--rlimit_cpu", str(_MAX_CPU_SECONDS),
        "--rlimit_as", str(_MAX_ADDRESS_SPACE_MB),
        "--rlimit_fsize", str(_MAX_FILE_SIZE_MB),
        "--rlimit_nproc", str(_MAX_PIDS),
        "--cwd", str(workdir),
        # Read-only system; the job directory is the only writable path.
        "--bindmount_ro", "/usr:/usr",
        "--bindmount_ro", "/bin:/bin",
        "--bindmount_ro", "/lib:/lib",
        "--bindmount_ro", "/lib64:/lib64",
        "--bindmount", f"{workdir}:{workdir}",
        "--",
    ]
