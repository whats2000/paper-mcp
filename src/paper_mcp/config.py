"""Environment-only configuration (SRS §III-9, twelve-factor)."""
from __future__ import annotations

import os
from dataclasses import dataclass


@dataclass(frozen=True)
class Settings:
    auth_mode: str
    unpaywall_email: str | None
    s2_api_key: str | None
    public_base_url: str
    log_level: str


def settings() -> Settings:
    """Read settings from the environment on every call.

    Deliberately not cached: the process is the unit of configuration, and
    reading a handful of env vars is cheaper than a stale-config bug.
    """
    return Settings(
        auth_mode=os.environ.get("PAPER_MCP_AUTH_MODE", "open"),
        unpaywall_email=os.environ.get("PAPER_MCP_UNPAYWALL_EMAIL") or None,
        s2_api_key=os.environ.get("PAPER_MCP_S2_API_KEY") or None,
        public_base_url=os.environ.get("PAPER_MCP_PUBLIC_BASE_URL", "http://localhost:8000"),
        log_level=os.environ.get("PAPER_MCP_LOG_LEVEL", "INFO"),
    )
