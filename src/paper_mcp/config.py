"""Environment-only configuration (SRS §III-9, twelve-factor)."""
from __future__ import annotations

import os
from dataclasses import dataclass

_DEFAULT_ALLOWED_HOSTS = ("localhost", "localhost:8000", "127.0.0.1", "127.0.0.1:8000")


@dataclass(frozen=True)
class Settings:
    auth_mode: str
    unpaywall_email: str | None
    s2_api_key: str | None
    public_base_url: str
    log_level: str
    allowed_hosts: tuple[str, ...]
    allowed_origins: tuple[str, ...]
    host: str
    port: int


def _csv(name: str, default: tuple[str, ...] = ()) -> tuple[str, ...]:
    raw = os.environ.get(name)
    if raw is None:
        return default
    return tuple(part.strip() for part in raw.split(",") if part.strip())


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
        # DNS-rebinding protection. A public deployment MUST set this to its
        # own hostname; the default only covers local development. "*"
        # disables the check entirely and is logged loudly at boot.
        allowed_hosts=_csv("PAPER_MCP_ALLOWED_HOSTS", _DEFAULT_ALLOWED_HOSTS),
        allowed_origins=_csv("PAPER_MCP_ALLOWED_ORIGINS"),
        # Bind address. In the container this is 0.0.0.0:8000 behind a proxy;
        # locally it must be overridable so the service can sit alongside
        # whatever else already owns the port.
        host=os.environ.get("PAPER_MCP_HOST", "0.0.0.0"),
        port=int(os.environ.get("PAPER_MCP_PORT", "8000")),
    )
