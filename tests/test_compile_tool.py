from __future__ import annotations

import base64
import shutil
from pathlib import Path

import pytest

from paper_mcp.models import InvalidArgumentError
from paper_mcp.tools.compile import CompileAsset, tool_compile_latex

_TEX = r"\documentclass{article}\begin{document}Hello\end{document}"
_needs_tex = pytest.mark.skipif(shutil.which("pdflatex") is None, reason="TeX not installed")


async def test_empty_source_is_rejected() -> None:
    with pytest.raises(InvalidArgumentError):
        await tool_compile_latex("   ")


async def test_oversized_source_is_rejected() -> None:
    with pytest.raises(InvalidArgumentError, match="characters"):
        await tool_compile_latex("x" * 3_000_000)


@pytest.mark.parametrize(
    "evil", ["../escape.png", "/etc/passwd", "a/../../b.png", "..\\win.png"]
)
async def test_asset_paths_cannot_escape_the_job_directory(evil: str) -> None:
    # Same boundary as the artifact route: caller-controlled paths.
    with pytest.raises(InvalidArgumentError):
        await tool_compile_latex(
            _TEX,
            assets=[CompileAsset(path=evil, content_base64=base64.b64encode(b"x").decode())],
        )


async def test_a_non_base64_asset_is_rejected() -> None:
    with pytest.raises(InvalidArgumentError, match="base64"):
        await tool_compile_latex(
            _TEX, assets=[CompileAsset(path="fig.png", content_base64="!!!not base64")]
        )


async def test_too_many_assets_are_rejected() -> None:
    payload = base64.b64encode(b"x").decode()
    with pytest.raises(InvalidArgumentError, match="too many"):
        await tool_compile_latex(
            _TEX,
            assets=[CompileAsset(path=f"f{i}.png", content_base64=payload) for i in range(100)],
        )


@_needs_tex
async def test_a_valid_document_compiles_and_returns_a_pdf_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PAPER_MCP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPER_MCP_AUTH_MODE", "open")
    import paper_mcp.tools.fetch as fetch_mod

    fetch_mod._store = None

    result = await tool_compile_latex(_TEX)

    assert result.ok is True
    assert result.page_count == 1
    assert result.pdf_url is not None
    assert result.errors == []


@_needs_tex
async def test_a_broken_document_returns_located_errors_not_an_exception(
    tmp_path: Path, monkeypatch
) -> None:
    # The caller's model needs somewhere to look, and a failed compile is an
    # ordinary outcome rather than a tool failure.
    monkeypatch.setenv("PAPER_MCP_ARTIFACT_ROOT", str(tmp_path))
    monkeypatch.setenv("PAPER_MCP_AUTH_MODE", "open")
    import paper_mcp.tools.fetch as fetch_mod

    fetch_mod._store = None

    result = await tool_compile_latex(
        r"\documentclass{article}\begin{document}\nosuchmacro\end{document}"
    )

    assert result.ok is False
    assert result.errors
    assert result.pdf_url is None


async def test_it_refuses_when_no_sandbox_is_available_on_a_public_endpoint(
    monkeypatch,
) -> None:
    # The decision that matters: decline rather than run a stranger's program.
    import paper_mcp.sandbox as sandbox_mod

    monkeypatch.setattr(sandbox_mod, "nsjail_available", lambda: False)
    monkeypatch.setenv("PAPER_MCP_AUTH_MODE", "oidc")

    result = await tool_compile_latex(_TEX)

    assert result.ok is False
    assert any(e.kind == "sandbox_unavailable" for e in result.errors)
