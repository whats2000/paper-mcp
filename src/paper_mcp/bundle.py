"""The bundle — a paper an LLM agent can use immediately.

Two things, because two things are what an agent actually needs:

* `markdown` — the paper as readable text, with real tables and equations as
  LaTeX. Marker produces it; nothing here re-derives or "improves" it.
* `figures` — the figure index: an id, a caption, and a URL that resolves to
  the extracted image.

Deliberately not here: a section index, an equation index, and an
outline/full duality. Marker emits `##` headings, so an agent navigates the
markdown itself; it inlines equations as LaTeX, so a parallel index would be
a second copy to keep honest. PaperHub needed those because it drove a
citation canvas over character offsets. This service does not.
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
ExtractionEngine = Literal["marker"]

# Cap on inlined markdown. Not a feature — a guard: a long paper can exceed
# 300k characters, and an MCP client that has to buffer that in one response
# is a client that falls over. The full text always lives in the artifact zip.
MARKDOWN_INLINE_LIMIT = 200_000


class FigureRef(BaseModel):
    """One extracted figure: what it shows, and where to get it.

    The index is the grounding contract. An entry exists only when Marker
    actually extracted an image, so an agent citing `fig-001` is citing
    something that demonstrably exists — and the caption is what makes it
    usable without fetching the pixels.
    """

    id: str
    caption: str = ""
    page: int | None = None
    image_path: str = Field(description="Path inside the bundle zip, e.g. figures/fig-001.png")
    image_url: str | None = None


class ExtractionInfo(BaseModel):
    engine: ExtractionEngine = "marker"
    pages: int = 0
    warnings: list[str] = Field(default_factory=list)


class ArtifactRef(BaseModel):
    # Optional in the model, always populated on the wire: URLs are derived at
    # serve time rather than stored, so the persisted form has none. See
    # `build_bundle.attach_urls`.
    zip_url: str | None = None
    bytes: int = 0
    expires_at: str | None = None


class Bundle(BaseModel):
    """A paper, ready for an agent to read."""

    bundle_id: str = Field(description="arxiv:<id> | sha256:<hex>")
    paper: PaperRef
    markdown: str = ""
    markdown_truncated: bool = Field(
        default=False,
        description="True when markdown was capped; the full text is in the artifact zip.",
    )
    figures: list[FigureRef] = Field(default_factory=list)
    extraction: ExtractionInfo = Field(default_factory=ExtractionInfo)
    artifact: ArtifactRef | None = None


def cap_markdown(text: str, limit: int = MARKDOWN_INLINE_LIMIT) -> tuple[str, bool]:
    """Trim inlined markdown to `limit`, reporting whether it was trimmed.

    Cuts on a line boundary so the tail is not a half-sentence, and never
    silently: the caller sets `markdown_truncated` so an agent knows to reach
    for the zip rather than assume it has the whole paper.
    """
    if len(text) <= limit:
        return text, False
    clipped = text[:limit]
    boundary = clipped.rfind("\n")
    if boundary > limit // 2:
        clipped = clipped[:boundary]
    return clipped, True
