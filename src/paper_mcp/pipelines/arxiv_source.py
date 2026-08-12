"""arXiv e-print source acquisition: download the tarball, unpack it safely.

# Ported from PaperHub `backend/src/paperhub/pipelines/arxiv_client.py` @ fd65834.
# Adapted: `unpack_source` is split out from the download so the path-traversal
# guard is testable without touching the network — it is the security-critical
# half and deserves tests that always run. The mirror-promotion and
# size-cap-detection behaviour is carried over unchanged; both were learned
# from real arXiv failures, not theorised. PaperHub's `TarballCorrupt` is
# renamed `TarballCorruptError` to match this project's naming rule.
"""
from __future__ import annotations

import logging
import random
import tarfile
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path

import httpx

logger = logging.getLogger(__name__)

# Tarballs can be 30+ MB and export.arxiv.org sometimes throttles to a few
# hundred KB/s. 120s total read budget covers ~50MB at 400KB/s with margin;
# connect stays tight so a hung DNS or firewall fails fast.
_DOWNLOAD_TIMEOUT = httpx.Timeout(120.0, connect=10.0)
# arXiv asks for a contactable User-Agent per their Terms of Use.
# https://info.arxiv.org/help/api/tou.html
_USER_AGENT = "paper-mcp/0.1 (+https://github.com/whats2000/paper-mcp)"

_DOWNLOAD_MAX_ATTEMPTS = 3
_DOWNLOAD_BACKOFF_BASE_S = 2.0
# arXiv's per-IP cooldown is brief; long waits do not help when the real
# problem is a per-connection byte cap rather than a rate-limit window.
_RATE_LIMIT_DEFAULT_BACKOFF_S = 5.0
_RETRYABLE_HTTP_STATUS = frozenset({429, 500, 502, 503, 504})
_TRANSIENT_EXCEPTIONS: tuple[type[BaseException], ...] = (
    httpx.RemoteProtocolError,
    httpx.ReadError,
    httpx.ReadTimeout,
    httpx.ConnectError,
    httpx.ConnectTimeout,
    httpx.PoolTimeout,
)


class TarballCorruptError(RuntimeError):
    """The tarball downloaded fully but is unreadable as gzip+tar.

    Distinct from "no source available" on purpose: the caller can fall back
    to the PDF path, where equation fidelity is lower but the paper is still
    ingestible end to end.
    """


def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return float(value)
    except ValueError:
        pass
    try:
        target = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if target.tzinfo is None:
        target = target.replace(tzinfo=UTC)
    delta = (target - datetime.now(UTC)).total_seconds()
    return delta if delta > 0 else None


def unpack_source(tar_path: Path, dest: Path) -> Path:
    """Unpack an e-print tarball into `dest`, refusing anything that escapes.

    Archive members are attacker-controlled on a public service, so this
    rejects absolute paths, `..` traversal, and non-regular members
    (symlinks and devices are the classic escape vector). Directory layout is
    otherwise preserved, because many papers organise LaTeX in subdirectories
    and flattening would silently break `\\input{sections/foo}` resolution.
    """
    dest.mkdir(parents=True, exist_ok=True)
    dest_resolved = dest.resolve()
    try:
        tar = tarfile.open(tar_path, "r:gz")  # noqa: SIM115 — closed via `with` below
    except (tarfile.ReadError, EOFError, OSError) as exc:
        raise TarballCorruptError(
            f"e-print tarball is unreadable: {type(exc).__name__}: {exc}",
        ) from exc

    with tar:
        for member in tar.getmembers():
            if not member.isreg():
                continue  # symlinks, devices, hardlinks: never extracted
            rel = Path(member.name)
            if rel.is_absolute() or any(part == ".." for part in rel.parts):
                logger.warning("skipping unsafe tar member %r", member.name)
                continue
            target = dest / rel
            if not str(target.resolve()).startswith(str(dest_resolved)):
                logger.warning("skipping escaping tar member %r", member.name)
                continue
            fobj = tar.extractfile(member)
            if fobj is None:
                continue
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(fobj.read())
    return dest


def _download_with_resume(url: str, target_path: Path) -> None:
    """Download `url` to `target_path`, resuming by byte range where possible.

    Fails fast on the size-cap signature — bytes received, then the connection
    dropped. arXiv's export mirror caps per-connection delivery for large
    papers (measured: an 8MB cap on a 41MB e-print), and retrying from the
    same offset hits the same wall every time. The caller should switch
    mirrors or methods instead of spinning here.
    """
    last_exc: BaseException | None = None
    for attempt in range(1, _DOWNLOAD_MAX_ATTEMPTS + 1):
        existing = target_path.stat().st_size if target_path.exists() else 0
        headers = {"User-Agent": _USER_AGENT}
        if existing > 0:
            headers["Range"] = f"bytes={existing}-"
        before = existing

        try:
            with httpx.stream(
                "GET", url, timeout=_DOWNLOAD_TIMEOUT, follow_redirects=True, headers=headers,
            ) as resp:
                if resp.status_code == 416 and existing > 0:
                    return  # already complete
                if resp.status_code in _RETRYABLE_HTTP_STATUS:
                    if attempt >= _DOWNLOAD_MAX_ATTEMPTS:
                        resp.raise_for_status()
                    wait = _parse_retry_after(resp.headers.get("retry-after"))
                    backoff = (
                        wait if wait is not None else _RATE_LIMIT_DEFAULT_BACKOFF_S
                    ) + random.uniform(0, 0.5)
                    logger.warning(
                        "arxiv %s: HTTP %d (attempt %d/%d); sleeping %.1fs",
                        url, resp.status_code, attempt, _DOWNLOAD_MAX_ATTEMPTS, backoff,
                    )
                    time.sleep(backoff)
                    continue
                if existing > 0 and resp.status_code == 200:
                    # Server ignored the Range header; restart cleanly.
                    target_path.unlink(missing_ok=True)
                    existing = 0
                resp.raise_for_status()
                mode = "ab" if existing > 0 else "wb"
                with target_path.open(mode) as fh:
                    for chunk in resp.iter_bytes():
                        fh.write(chunk)
            return
        except _TRANSIENT_EXCEPTIONS as exc:
            last_exc = exc
            now = target_path.stat().st_size if target_path.exists() else 0
            if now - before > 0:
                # Size-cap signature: bytes arrived, then the peer hung up.
                # Retrying from the same offset hits the same limit.
                logger.warning(
                    "arxiv %s: dropped mid-stream after %d bytes; size cap, not retrying",
                    url, now - before,
                )
                raise
            if attempt >= _DOWNLOAD_MAX_ATTEMPTS:
                raise
            time.sleep(_DOWNLOAD_BACKOFF_BASE_S * (2 ** (attempt - 1)) + random.uniform(0, 0.5))
    if last_exc is not None:  # pragma: no cover — loop returns or raises
        raise last_exc


def download_arxiv_source(arxiv_id: str, *, cache_root: Path) -> Path:
    """Download and unpack an arXiv e-print, returning the source directory.

    Two mirrors are tried in order. The export mirror is the documented
    programmatic endpoint and is preferred, but it caps per-connection
    delivery and drops large transfers mid-stream; the main site does not.
    Promotion happens ONLY on the size-cap signature — other transient errors
    are not size-related, and bouncing to the main site would just add load
    there.
    """
    target_dir = cache_root / arxiv_id
    target_dir.mkdir(parents=True, exist_ok=True)
    tar_path = target_dir / f"{arxiv_id}.tar.gz"
    source_dir = target_dir / "source"

    try:
        _download_with_resume(f"https://export.arxiv.org/src/{arxiv_id}", tar_path)
    except httpx.RemoteProtocolError:
        received = tar_path.stat().st_size if tar_path.exists() else 0
        if received <= 0:
            raise  # no bytes at all: a transport failure, not a size cap
        logger.warning(
            "arxiv %s: export mirror size cap at %d bytes; retrying via arxiv.org",
            arxiv_id, received,
        )
        # Different server: a Range header pointing at the old offset is
        # meaningless, so start clean.
        tar_path.unlink(missing_ok=True)
        _download_with_resume(f"https://arxiv.org/src/{arxiv_id}", tar_path)

    try:
        return unpack_source(tar_path, source_dir)
    finally:
        tar_path.unlink(missing_ok=True)
