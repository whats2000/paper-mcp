"""`compile_latex` — turn caller-supplied LaTeX into a PDF, or into errors.

A tool, not a flow (SRS v0.3). It compiles once and reports; it never revises,
retries with edits, or authors. The calling agent owns the fix-and-resubmit
loop, because that is where the model and the user's context live.
"""
from __future__ import annotations

import asyncio
import base64
import binascii
import logging
import shutil
import tempfile
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from paper_mcp.artifacts import content_key
from paper_mcp.models import InvalidArgumentError
from paper_mcp.pipelines.latex_compile import CompileError, run_latex
from paper_mcp.sandbox import build_policy
from paper_mcp.tools.fetch import artifact_store

logger = logging.getLogger(__name__)

MAX_TEX_CHARS = 2_000_000
MAX_ASSETS = 64
MAX_ASSET_BYTES = 32 * 1024 * 1024


class CompileAsset(BaseModel):
    """A file the document needs, e.g. a figure from a bundle."""

    path: str = Field(description="Relative path the LaTeX refers to, e.g. figures/fig-001.png")
    content_base64: str


class CompileErrorOut(BaseModel):
    message: str
    file: str | None = None
    line: int | None = None
    kind: str = "latex_error"


class CompileOutput(BaseModel):
    ok: bool
    engine: str = ""
    page_count: int = 0
    pdf_url: str | None = None
    errors: list[CompileErrorOut] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    log_tail: str = ""


def _out(errors: list[CompileError]) -> list[CompileErrorOut]:
    return [
        CompileErrorOut(message=e.message, file=e.file, line=e.line, kind=e.kind) for e in errors
    ]


def _stage_assets(assets: list[CompileAsset], workdir: Path) -> None:
    """Write caller-supplied files into the job directory.

    `path` is attacker-controlled, so this is the same security boundary as
    the artifact route: no absolute paths, no traversal, and containment
    checked after resolution so a symlink cannot walk out.
    """
    if len(assets) > MAX_ASSETS:
        raise InvalidArgumentError(f"too many assets: {len(assets)} > {MAX_ASSETS}")

    root = workdir.resolve()
    for asset in assets:
        rel = Path(asset.path)
        if rel.is_absolute() or any(part == ".." for part in rel.parts) or "\\" in asset.path:
            raise InvalidArgumentError(f"illegal asset path: {asset.path!r}")
        try:
            data = base64.b64decode(asset.content_base64, validate=True)
        except (binascii.Error, ValueError) as exc:
            raise InvalidArgumentError(f"asset {asset.path!r} is not valid base64") from exc
        if len(data) > MAX_ASSET_BYTES:
            raise InvalidArgumentError(f"asset {asset.path!r} exceeds the size ceiling")
        target = (workdir / rel).resolve()
        if not target.is_relative_to(root):
            raise InvalidArgumentError(f"asset path escapes the job directory: {asset.path!r}")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)


async def tool_compile_latex(
    tex: str,
    assets: list[CompileAsset] | None = None,
    engine: Literal["auto", "pdflatex", "xelatex", "lualatex"] = "auto",
    timeout_s: float = 120.0,
) -> CompileOutput:
    """Compile LaTeX to a PDF. One attempt; errors come back structured."""
    if not tex.strip():
        raise InvalidArgumentError("tex is empty")
    if len(tex) > MAX_TEX_CHARS:
        raise InvalidArgumentError(f"tex exceeds {MAX_TEX_CHARS} characters")

    workdir = Path(tempfile.mkdtemp(prefix="paper-mcp-tex-"))
    try:
        policy = build_policy(workdir)
        if not policy.available:
            # Refusing beats running a stranger's program unisolated.
            return CompileOutput(
                ok=False,
                errors=[CompileErrorOut(message=policy.reason, kind="sandbox_unavailable")],
            )

        _stage_assets(assets or [], workdir)

        # The engine is a blocking subprocess; off the event loop it would
        # stall every other request for the duration of a compile.
        result = await asyncio.to_thread(
            run_latex,
            tex,
            workdir,
            engine=engine,
            timeout_s=min(timeout_s, 300.0),
            argv_wrapper=list(policy.argv_prefix),
        )

        pdf_url: str | None = None
        if result.pdf:
            # Content-addressed like everything else: the same source compiled
            # twice resolves to one artifact.
            key = content_key(data=result.pdf)
            entry = artifact_store().ensure(key)
            (entry / "output.pdf").write_bytes(result.pdf)
            pdf_url = artifact_store().url_for(key, "output.pdf")

        return CompileOutput(
            ok=result.ok,
            engine=result.engine,
            page_count=result.page_count,
            pdf_url=pdf_url,
            errors=_out(result.errors),
            warnings=result.warnings,
            log_tail=result.log_tail,
        )
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
