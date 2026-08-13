from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

from paper_mcp.artifacts import ArtifactStore, content_key, token_for
from paper_mcp.models import InvalidArgumentError


def test_key_is_stable_and_content_addressed() -> None:
    assert content_key(arxiv_id="1706.03762") == "arxiv:1706.03762"
    assert content_key(data=b"abc") == content_key(data=b"abc")
    assert content_key(data=b"abc") != content_key(data=b"abd")
    assert content_key(data=b"abc").startswith("sha256:")


def test_key_requires_something_to_address() -> None:
    with pytest.raises(InvalidArgumentError):
        content_key()


def test_token_does_not_leak_the_key_and_is_unguessable() -> None:
    token = token_for("sha256:deadbeefcafe")

    assert "deadbeef" not in token
    assert len(token) >= 32
    # Stable for the same key within a process, or cached URLs would rot.
    assert token == token_for("sha256:deadbeefcafe")
    assert token != token_for("sha256:deadbeefcaff")


def test_dir_for_shards_by_hash_prefix(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    path = store.dir_for("arxiv:1706.03762")

    assert tmp_path in path.parents
    # Sharded so one directory does not accumulate every paper ever fetched.
    assert path.parent.parent == tmp_path
    assert len(path.parent.name) == 2


@pytest.mark.parametrize(
    "evil",
    [
        "../../etc/passwd",
        "..\\..\\secrets",
        "/etc/passwd",
        "a/../../b",
        "figures/../../../outside.txt",
        "",
    ],
)
def test_resolve_refuses_path_traversal(tmp_path: Path, evil: str) -> None:
    # `rel` is caller-supplied on a public endpoint. This is a security
    # boundary, not input tidying.
    store = ArtifactStore(tmp_path)
    store.dir_for("arxiv:1").mkdir(parents=True)

    with pytest.raises(InvalidArgumentError):
        store.resolve(token_for("arxiv:1"), evil)


def test_resolve_returns_a_path_inside_the_entry(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    entry = store.dir_for("arxiv:1")
    (entry / "figures").mkdir(parents=True)
    (entry / "figures" / "fig-001.png").write_bytes(b"png")

    resolved = store.resolve(token_for("arxiv:1"), "figures/fig-001.png")

    assert resolved.read_bytes() == b"png"


def test_resolve_rejects_an_unknown_token(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)

    with pytest.raises(InvalidArgumentError):
        store.resolve("not-a-real-token", "bundle.json")


def test_resolve_survives_a_store_that_has_never_been_written(tmp_path: Path) -> None:
    # Looking an entry up reads directories, so a cold store must answer
    # "unknown token" rather than raising FileNotFoundError at the caller.
    store = ArtifactStore(tmp_path / "not-created-yet")

    with pytest.raises(InvalidArgumentError):
        store.resolve(token_for("arxiv:1"), "bundle.json")


def test_resolve_refuses_a_token_that_is_not_a_sha256_digest(tmp_path: Path) -> None:
    # A directory really exists under this name, and it is still refused: a
    # token is a sha256 hexdigest or it is nothing. `str.isalnum()` would have
    # accepted this, and unicode digits besides.
    store = ArtifactStore(tmp_path)
    token = "Z" * 64
    entry = tmp_path / token[:2] / token
    entry.mkdir(parents=True)
    (entry / "bundle.json").write_text("{}")

    with pytest.raises(InvalidArgumentError):
        store.resolve(token, "bundle.json")


def test_resolve_returns_a_file_the_entry_really_holds(tmp_path: Path) -> None:
    # The returned path is built from the filesystem, never by joining the
    # caller's string onto a directory — so a spelling that normalizes onto a
    # real file is still refused unless it matches what the entry publishes.
    store = ArtifactStore(tmp_path)
    entry = store.dir_for("arxiv:1")
    entry.mkdir(parents=True)
    (entry / "bundle.json").write_text("{}")

    assert store.resolve(token_for("arxiv:1"), "bundle.json").is_file()
    for spelling in ("./bundle.json", "figures/../bundle.json", "/bundle.json"):
        with pytest.raises(InvalidArgumentError):
            store.resolve(token_for("arxiv:1"), spelling)


def test_resolve_refuses_a_symlink_escaping_the_entry(tmp_path: Path) -> None:
    # The traversal check must happen AFTER resolution, or a symlink walks
    # straight through it.
    store = ArtifactStore(tmp_path)
    entry = store.dir_for("arxiv:1")
    entry.mkdir(parents=True)
    secret = tmp_path / "secret.txt"
    secret.write_text("classified")
    try:
        (entry / "escape").symlink_to(secret)
    except (OSError, NotImplementedError):
        pytest.skip("symlinks not permitted on this platform/account")

    with pytest.raises(InvalidArgumentError):
        store.resolve(token_for("arxiv:1"), "escape")


def test_url_for_uses_the_public_base_and_the_token(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("PAPER_MCP_PUBLIC_BASE_URL", "https://papers.example.org")
    store = ArtifactStore(tmp_path)

    url = store.url_for("arxiv:1706.03762", "figures/fig-001.png")

    assert url.startswith("https://papers.example.org/a/")
    assert url.endswith("/figures/fig-001.png")
    assert "1706.03762" not in url  # the key never appears in the URL


def test_sweep_removes_only_entries_past_ttl(tmp_path: Path) -> None:
    store = ArtifactStore(tmp_path)
    fresh, stale = store.dir_for("arxiv:new"), store.dir_for("arxiv:old")
    for entry in (fresh, stale):
        entry.mkdir(parents=True)
        (entry / "bundle.json").write_text("{}")
    old = time.time() - 60 * 60 * 48
    os.utime(stale / "bundle.json", (old, old))
    os.utime(stale, (old, old))

    removed = store.sweep(ttl_hours=24)

    assert removed == 1
    assert fresh.exists()
    assert not stale.exists()


def test_sweep_is_a_no_op_on_an_empty_store(tmp_path: Path) -> None:
    assert ArtifactStore(tmp_path).sweep(ttl_hours=24) == 0
