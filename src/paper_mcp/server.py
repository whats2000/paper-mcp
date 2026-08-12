"""FastAPI app hosting the MCP streamable-HTTP surface.

# Mount mechanics ported in spirit from PaperHub
# `backend/src/paperhub/mcp/mounting.py` @ fd65834.
# Adapted: no request-context middleware, no database connection, no tracer —
# this service is stateless, so a tool call needs nothing but its arguments.
# Also updated for the `mcp` 2.x API: `MCPServer` replaces 1.x's `FastMCP`;
# transport options (json_response, stateless_http, transport_security) moved
# from `server.settings` into `streamable_http_app()` kwargs; `Tool` exposes
# `input_schema`, not `inputSchema`; and `session_manager` raises unless
# `streamable_http_app()` has already been called.
"""
from __future__ import annotations

import contextlib
import logging
from collections.abc import AsyncIterator
from typing import Any

import uvicorn
from fastapi import FastAPI
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings

from paper_mcp import __version__
from paper_mcp.config import Settings, settings
from paper_mcp.tools.discovery import (
    tool_find_related,
    tool_resolve_paper,
    tool_search_arxiv,
    tool_search_papers,
)

_LOG = logging.getLogger(__name__)

SERVER_NAME = "paper"
MCP_PATH = "/mcp"


def build_mcp_server() -> MCPServer[Any]:
    """Construct the MCP server with the discovery tools registered.

    Tool input schemas are derived from each handler's annotated signature,
    so the Pydantic models are the single source of truth (SRS NFR-04).
    Descriptions declare each tool's network scope (SRS NFR-05).
    """
    server: MCPServer[Any] = MCPServer(name=SERVER_NAME, version=__version__)

    server.add_tool(
        tool_search_arxiv,
        name="search_arxiv",
        description=(
            "Search arXiv by relevance and return normalized paper references. "
            "Metadata only — nothing is downloaded. Network scope: arxiv.org. "
            "Returns at most 50 results."
        ),
    )
    server.add_tool(
        tool_search_papers,
        name="search_papers",
        description=(
            "Search Semantic Scholar's full corpus (broader than arXiv; includes "
            "citation counts and venues). Network scope: api.semanticscholar.org. "
            "Returns at most 50 results."
        ),
    )
    server.add_tool(
        tool_find_related,
        name="find_related",
        description=(
            "Walk the citation graph around a paper. mode='cites' for its "
            "references, 'cited_by' for follow-up work, 'similar' for related "
            "papers. paper_id must be prefixed: arxiv:<id>, ss:<paperId>, or "
            "doi:<doi>. Network scope: api.semanticscholar.org."
        ),
    )
    server.add_tool(
        tool_resolve_paper,
        name="resolve_paper",
        description=(
            "Resolve an arXiv id, DOI, Semantic Scholar id, or free-text title "
            "to a single paper, reporting whether an open-access full-text "
            "source exists. Call this before fetching a paper to avoid spending "
            "an extraction on something with no reachable source. Network "
            "scope: arxiv.org, api.semanticscholar.org, api.unpaywall.org."
        ),
    )
    return server


def transport_security(cfg: Settings) -> TransportSecuritySettings:
    """Build DNS-rebinding protection from configured allowed hosts.

    The MCP transport validates the `Host` header so a hostile page cannot
    drive a locally-bound server. A public deployment must therefore list its
    own hostname in `PAPER_MCP_ALLOWED_HOSTS`; the default covers localhost
    only. A literal `*` disables the check — an explicit operator choice,
    warned about at boot, never a silent default.
    """
    if "*" in cfg.allowed_hosts:
        return TransportSecuritySettings(enable_dns_rebinding_protection=False)
    origins = list(cfg.allowed_origins) or [
        f"{scheme}://{host}" for host in cfg.allowed_hosts for scheme in ("http", "https")
    ]
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=list(cfg.allowed_hosts),
        allowed_origins=origins,
    )


def create_app() -> FastAPI:
    """Build the FastAPI app with the MCP sub-app serving `POST /mcp`.

    The sub-app owns the full `/mcp` path and is mounted at `""` rather than
    built with `streamable_http_path="/"` and mounted at `"/mcp"`. That
    obvious-looking arrangement makes Starlette strip the prefix, leaving the
    sub-app to match `""` against its `"/"` route — so `POST /mcp` answers
    **307 -> /mcp/** instead of doing the work. Test clients follow redirects
    and hide it; a connector configured with `https://host/mcp` may not.
    Mounting at `""` passes the untouched path through, so `/mcp` is a direct
    200. `/health` is registered first and therefore still wins.
    """
    server = build_mcp_server()
    mcp_app = server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        transport_security=transport_security(settings()),
    )
    # `session_manager` is only valid AFTER streamable_http_app() has built it.
    session_manager = server.session_manager

    @contextlib.asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        # Starlette does NOT propagate a mounted sub-app's lifespan, so the
        # session manager's task group must be entered here or the first
        # POST /mcp fails with "Task group is not initialized".
        async with session_manager.run():
            _LOG.info("paper-mcp %s ready; mcp mounted at %s", __version__, MCP_PATH)
            yield

    app = FastAPI(title="paper-mcp", version=__version__, lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "version": __version__,
            "auth_mode": settings().auth_mode,
        }

    app.mount("", mcp_app)
    return app


# uvicorn accepts only these level names, lowercase.
_UVICORN_LEVELS = frozenset({"critical", "error", "warning", "info", "debug", "trace"})


def uvicorn_log_level(level: str) -> str:
    """Translate the configured level into what uvicorn will accept.

    uvicorn builds its own logging config and does not inherit
    `logging.basicConfig`, so the level has to be handed to it explicitly.
    Without this, `PAPER_MCP_LOG_LEVEL` silently does nothing: measured at
    18076 bytes of output for 300 requests at WARNING versus 18222 at INFO,
    a 1% difference. That is not just noise — a server whose stdout backs up
    blocks inside write(), and per-request logging is what fills the buffer.

    An unrecognised value falls back to `info` rather than crashing the
    process on startup over a typo in an env var.
    """
    lowered = level.strip().lower()
    return lowered if lowered in _UVICORN_LEVELS else "info"


def main() -> None:
    """Console-script entry point."""
    cfg = settings()
    logging.basicConfig(level=cfg.log_level)
    if cfg.auth_mode == "open":
        _LOG.warning(
            "AUTH_MODE=open — every caller is unauthenticated. "
            "Do not run this on a public network.",
        )
    if "*" in cfg.allowed_hosts:
        _LOG.warning(
            "PAPER_MCP_ALLOWED_HOSTS=* — DNS-rebinding protection is DISABLED. "
            "Set this to the deployment's hostname.",
        )
    uvicorn.run(
        create_app(),
        host=cfg.host,
        port=cfg.port,
        log_level=uvicorn_log_level(cfg.log_level),
    )
