"""Content-addressed artifact store.

The only thing this service persists (SRS FR-07, NFR-01). Entries hold derived
data from public papers keyed by content: no identity, no session, no database
row. Two callers asking for the same paper share one entry, which is
deduplication of public data rather than a leak — and it is what makes the
cache-hit path fast enough for NFR-03.

**Why the URL token is just `sha256(key)`.** The security property required is
that an artifact is reachable only by someone entitled to it, and content
addressing already provides it: a private manuscript's key is `sha256:<hash of
its own bytes>`, so deriving its URL requires already possessing the file. A
public arXiv paper's URL is derivable from its public id, which leaks nothing
— the paper is public. An HMAC with a server secret was tried first and bought
nothing: it cannot be reversed to a directory, so lookups needed a full scan
plus a marker file, and a per-process secret would have invalidated every
cached URL on restart.
"""
from __future__ import annotations

import hashlib
import logging
import shutil
import time
from pathlib import Path

from paper_mcp.config import settings
from paper_mcp.models import InvalidArgumentError

_LOG = logging.getLogger(__name__)


def content_key(*, arxiv_id: str | None = None, data: bytes | None = None) -> str:
    """Build the canonical content key for an artifact.

    arXiv ids are used directly: the id already identifies immutable content,
    and reusing it means `fetch_paper(paper_id)` needs no translation between
    the discovery tools' `paper_id` space and this one.
    """
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    if data is not None:
        return f"sha256:{hashlib.sha256(data).hexdigest()}"
    raise InvalidArgumentError("content_key needs either an arxiv_id or the bytes to hash")


def token_for(key: str) -> str:
    """The opaque URL path segment addressing `key`.

    Also the on-disk directory name, so resolving a URL is a direct lookup
    rather than a search, and stays stable across restarts.
    """
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


class ArtifactStore:
    """Disk-backed store, sharded by token prefix.

    Behind an interface so an S3 backend can replace it without touching tool
    code (SRS §II-6); v1 is a single host and object storage would be
    operational surface for capacity that does not yet exist.
    """

    def __init__(self, root: Path) -> None:
        self.root = Path(root)

    def dir_for(self, key: str) -> Path:
        return self.dir_for_token(token_for(key))

    def dir_for_token(self, token: str) -> Path:
        return self.root / token[:2] / token

    def ensure(self, key: str) -> Path:
        entry = self.dir_for(key)
        entry.mkdir(parents=True, exist_ok=True)
        return entry

    def resolve(self, token: str, rel: str) -> Path:
        """Map a public `(token, rel)` pair onto a real file inside the store.

        `rel` arrives from the network on a public endpoint, so this is a
        security boundary rather than input tidying. The containment check runs
        *after* `.resolve()`: checking the raw string alone would let a symlink
        inside the entry point anywhere on the host.
        """
        if not rel or rel.strip() in {"", "."}:
            raise InvalidArgumentError("artifact path is empty")
        if not token or not token.isalnum() or len(token) != 64:
            raise InvalidArgumentError("unknown or expired artifact token")
        # Reject Windows-style traversal explicitly — on POSIX, `Path` treats
        # "..\\.." as one innocent-looking filename.
        if "\\" in rel:
            raise InvalidArgumentError(f"illegal artifact path: {rel!r}")
        candidate_rel = Path(rel)
        if candidate_rel.is_absolute() or any(part == ".." for part in candidate_rel.parts):
            raise InvalidArgumentError(f"illegal artifact path: {rel!r}")

        entry = self.dir_for_token(token)
        if not entry.is_dir():
            raise InvalidArgumentError("unknown or expired artifact token")

        target = (entry / candidate_rel).resolve()
        if not target.is_relative_to(entry.resolve()):
            raise InvalidArgumentError(f"artifact path escapes its bundle: {rel!r}")
        if not target.exists():
            raise InvalidArgumentError(f"no such artifact file: {rel!r}")
        return target

    def url_for(self, key: str, rel: str) -> str:
        base = settings().public_base_url.rstrip("/")
        return f"{base}/a/{token_for(key)}/{rel.lstrip('/')}"

    def sweep(self, ttl_hours: float) -> int:
        """Delete entries whose most recent activity is older than the TTL.

        Uses the newest mtime inside an entry, so one still being read survives
        while one nobody has wanted for a day is reclaimed. Expiry is safe by
        construction here: an artifact is derived data and can always be
        rebuilt from its key.
        """
        if not self.root.exists():
            return 0
        cutoff = time.time() - ttl_hours * 3600
        removed = 0
        for shard in self.root.iterdir():
            if not shard.is_dir():
                continue
            for entry in shard.iterdir():
                if not entry.is_dir():
                    continue
                newest = max(
                    (p.stat().st_mtime for p in entry.rglob("*") if p.is_file()),
                    default=entry.stat().st_mtime,
                )
                if newest < cutoff:
                    shutil.rmtree(entry, ignore_errors=True)
                    removed += 1
        if removed:
            _LOG.info("artifact sweep removed %d entries older than %sh", removed, ttl_hours)
        return removed
