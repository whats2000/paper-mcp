"""Core wire models shared by every tool.

`PaperRef` is deliberately one shape across all upstreams (arXiv, Semantic
Scholar, DOI resolution) so a calling agent never branches on provenance.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

MAX_RESULTS_CEILING = 50


class ToolError(Exception):
    """Base for every error a tool returns to the caller.

    Errors are part of the contract (SRS I-8 #7): each names what happened
    and what the caller should do next. They are never bare 500s.
    """

    code = "tool_error"

    def __init__(self, message: str, *, retry_after: float | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.retry_after = retry_after


class InvalidArgumentError(ToolError):
    code = "invalid_argument"


class NotFoundError(ToolError):
    code = "not_found"


class UpstreamError(ToolError):
    code = "upstream_error"


class RateLimitedError(ToolError):
    code = "rate_limited"


class OpenAccess(BaseModel):
    """Whether an ingestible full-text source exists, and where."""

    available: bool = False
    url: str | None = None
    source: Literal["arxiv", "unpaywall", "s2"] | None = None
    reason: str | None = Field(
        default=None,
        description="Why no source was found, when available is false.",
    )


class PaperRef(BaseModel):
    """One paper, normalized across every discovery upstream."""

    paper_id: str = Field(description="arxiv:<id> | ss:<paperId> | doi:<doi>")
    title: str
    abstract: str | None = None
    year: int | None = None
    authors: list[str] = Field(default_factory=list)
    arxiv_id: str | None = None
    doi: str | None = None
    venue: str | None = None
    citation_count: int | None = None
    open_access: OpenAccess = Field(default_factory=OpenAccess)
    source: Literal["arxiv", "semantic_scholar"]


def normalize_paper_id(
    *, arxiv_id: str | None, s2_id: str | None, doi: str | None,
) -> str:
    """Build the canonical `paper_id`, preferring arXiv.

    arXiv wins whenever present because it is the only identifier that
    reliably carries an ingestible source — preferring it makes a later
    `fetch_paper` succeed more often (SRS §III-2).
    """
    if arxiv_id:
        return f"arxiv:{arxiv_id}"
    if s2_id:
        return f"ss:{s2_id}"
    if doi:
        return f"doi:{doi}"
    raise InvalidArgumentError(
        "paper has no arXiv id, Semantic Scholar id, or DOI — cannot be addressed",
    )


def s2_path_id(paper_id: str) -> str:
    """Map a canonical `paper_id` to the form Semantic Scholar's URL path wants."""
    prefix, _, rest = paper_id.partition(":")
    if not rest:
        raise InvalidArgumentError(
            f"paper_id must be prefixed (arxiv:/ss:/doi:), got {paper_id!r}",
        )
    match prefix:
        case "arxiv":
            return f"arXiv:{rest}"
        case "ss":
            return rest
        case "doi":
            return f"DOI:{rest}"
        case _:
            raise InvalidArgumentError(
                f"unsupported paper_id prefix {prefix!r} — use arxiv:, ss:, or doi:",
            )


def clamp_max_results(value: int) -> int:
    """Clamp a caller-supplied result count into [1, 50] (SRS FR-01)."""
    return max(1, min(int(value), MAX_RESULTS_CEILING))
