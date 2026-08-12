"""The bundle — this service's core artifact.

Derived from PaperHub's `PaperAsset` (figures / equations / sections) and
extended with what an agent actually needs over the wire: section markdown,
resolvable figure URLs, and extraction provenance.

The bundle is the figure-grounding contract (SRS §III-3): an agent that cites
`fig-001` is citing something the extractor genuinely found, with a caption
and a URL that resolves.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from paper_mcp.models import PaperRef

# No `pymupdf` member, deliberately. PaperHub shipped crude PyMuPDF document
# extraction once and reversed it — its v2.19 entry records the output as
# "conference-UNusable" (hallucinated \includegraphics, wrong figures from
# filename collisions) — and replaced it with Marker. A service whose value is
# faithful extraction must not offer an unfaithful fallback, so when Marker is
# unavailable a PDF fetch fails loudly instead of degrading (SRS v0.2).
ExtractionEngine = Literal["latex", "marker"]


class FigureRef(BaseModel):
    """One figure, with everything needed to cite or fetch it."""

    id: str
    caption: str = ""
    page: int | None = None
    section: str | None = None
    image_path: str = Field(description="Path inside the bundle zip, e.g. figures/fig-001.png")
    image_url: str | None = Field(
        default=None, description="Resolvable URL for the image, when the artifact is live."
    )


class EquationRef(BaseModel):
    id: str
    latex: str
    section: str | None = None


class SectionRef(BaseModel):
    """A section in the bundle's index.

    `markdown` is populated only for `include="full"`; an outline carries the
    name, order, and size so a caller can choose what to read next without
    paying for the whole paper.
    """

    name: str
    order: int
    char_count: int = 0
    markdown: str | None = None


class SectionContent(BaseModel):
    """One section, returned by `get_section`."""

    bundle_id: str
    name: str
    order: int
    markdown: str


class ArtifactRef(BaseModel):
    zip_url: str
    bytes: int = 0
    expires_at: str | None = None


class ExtractionInfo(BaseModel):
    """Which engine produced this bundle, and what degraded on the way.

    Warnings are part of the contract rather than a log line: LaTeX-to-markdown
    is lossy for tables and custom macros, and a caller reasoning about the
    text deserves to know that before it trusts a table it cannot see.
    """

    engine: ExtractionEngine
    warnings: list[str] = Field(default_factory=list)


class Bundle(BaseModel):
    """Agent-ready representation of one paper."""

    bundle_id: str = Field(description="arxiv:<id> | sha256:<hex>")
    paper: PaperRef
    sections: list[SectionRef] = Field(default_factory=list)
    figures: list[FigureRef] = Field(default_factory=list)
    equations: list[EquationRef] = Field(default_factory=list)
    extraction: ExtractionInfo
    artifact: ArtifactRef | None = None


def outline(bundle: Bundle) -> Bundle:
    """Return a copy with section bodies removed, keeping the index.

    A full paper's markdown routinely exceeds 100k characters. Returning that
    by default would flood the caller's context on its first call, so
    `fetch_paper` defaults to the outline and `get_section` fetches bodies on
    demand — the structural navigation PaperHub validated (SRS §III-3).

    Copies rather than mutating: the caller may still want the full bundle it
    passed in, and silently emptying it would be a nasty surprise.
    """
    trimmed = bundle.model_copy(deep=True)
    for section in trimmed.sections:
        section.markdown = None
    return trimmed
