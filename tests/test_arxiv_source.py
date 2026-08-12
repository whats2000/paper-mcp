from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from paper_mcp.pipelines.arxiv_source import TarballCorruptError, unpack_source


def _tar(path: Path, members: dict[str, bytes]) -> Path:
    with tarfile.open(path, "w:gz") as tar:
        for name, data in members.items():
            info = tarfile.TarInfo(name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
    return path


def test_unpack_keeps_directory_layout(tmp_path: Path) -> None:
    # Many arXiv papers organise LaTeX in subdirectories; flattening would
    # silently break \input{sections/foo} resolution.
    src = _tar(
        tmp_path / "ok.tar.gz",
        {"main.tex": b"\\input{sections/intro}", "sections/intro.tex": b"hello"},
    )
    dest = tmp_path / "out"

    unpack_source(src, dest)

    assert (dest / "main.tex").read_bytes() == b"\\input{sections/intro}"
    assert (dest / "sections" / "intro.tex").read_bytes() == b"hello"


def test_unpack_refuses_members_escaping_the_destination(tmp_path: Path) -> None:
    # A malicious e-print must not write outside its own directory. This is
    # attacker-controlled input on a public service.
    src = _tar(
        tmp_path / "evil.tar.gz",
        {"../escaped.tex": b"x", "nested/../../also_escaped.tex": b"x", "ok.tex": b"fine"},
    )
    dest = tmp_path / "out"

    unpack_source(src, dest)

    assert (dest / "ok.tex").read_bytes() == b"fine"
    assert not (tmp_path / "escaped.tex").exists()
    assert not (tmp_path / "also_escaped.tex").exists()
    assert list(dest.rglob("*escaped*")) == []


def test_unpack_refuses_absolute_member_paths(tmp_path: Path) -> None:
    src = _tar(tmp_path / "abs.tar.gz", {"/tmp/pwned.tex": b"x", "ok.tex": b"fine"})
    dest = tmp_path / "out"

    unpack_source(src, dest)

    assert (dest / "ok.tex").exists()
    assert not Path("/tmp/pwned.tex").exists()


def test_unpack_skips_non_regular_members(tmp_path: Path) -> None:
    # Symlinks and devices in an archive are a classic escape vector.
    path = tmp_path / "link.tar.gz"
    with tarfile.open(path, "w:gz") as tar:
        link = tarfile.TarInfo("evil-link")
        link.type = tarfile.SYMTYPE
        link.linkname = "/etc/passwd"
        tar.addfile(link)
        data = b"fine"
        ok = tarfile.TarInfo("ok.tex")
        ok.size = len(data)
        tar.addfile(ok, io.BytesIO(data))
    dest = tmp_path / "out"

    unpack_source(path, dest)

    assert (dest / "ok.tex").exists()
    assert not (dest / "evil-link").exists()


def test_corrupt_tarball_raises_a_typed_error(tmp_path: Path) -> None:
    # A truncated download must be distinguishable from "the paper has no
    # source", so the caller can fall back to the PDF path rather than give up.
    bad = tmp_path / "bad.tar.gz"
    bad.write_bytes(b"not a gzip stream at all")

    with pytest.raises(TarballCorruptError):
        unpack_source(bad, tmp_path / "out")


def test_unpack_is_idempotent(tmp_path: Path) -> None:
    src = _tar(tmp_path / "ok.tar.gz", {"main.tex": b"body"})
    dest = tmp_path / "out"

    unpack_source(src, dest)
    unpack_source(src, dest)

    assert (dest / "main.tex").read_bytes() == b"body"
