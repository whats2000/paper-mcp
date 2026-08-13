"""Shared test isolation.

The service keeps a few process-level singletons — a quota store, a JWKS
cache, an artifact store — because they are genuinely process-scoped at
runtime. In tests they leak: a case that installs a 2-calls-per-minute limit
leaves it in place, and every later case gets 429s from a limit it never set.
That produced a suite where `test_server.py` passed alone and failed in the
full run, which is the most expensive kind of failure to read.
"""
from __future__ import annotations

from collections.abc import Iterator

import pytest


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    """Give every test a clean process-level state, before and after."""
    import paper_mcp.auth as auth_mod
    import paper_mcp.quota as quota_mod
    import paper_mcp.tools.extract as fetch_mod

    def clear() -> None:
        quota_mod.reset_quota_store()
        auth_mod.reset_jwks_cache()
        fetch_mod._store = None
        fetch_mod._marker = None
        fetch_mod._jobs = None

    clear()
    yield
    clear()
