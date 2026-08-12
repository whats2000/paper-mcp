"""Turn Marker blocks into the two things an agent needs: markdown + figures.

This is the whole product. Everything downstream of it — slides, summaries,
literature reviews — is a pipeline the calling agent builds from skills. This
service supplies data, so the only question that matters here is whether the
data is faithful.

Faithful means, concretely:

* tables stay tables (rows and columns), never a blob of cell text
* equations stay LaTeX, never prose approximations of maths
* figures are extracted, indexed, and captioned, never invented

# Block shapes ported from PaperHub `pipelines/marker_to_asset.py` @ fd65834.
# Adapted: emits one markdown document plus a figure index rather than
# PaperHub's PaperAsset, and renders tables through `html_table_to_markdown`
# instead of flattening them.
"""
from __future__ import annotations

import base64
import binascii
import html
import logging
import re
from pathlib import Path

from paper_mcp.bundle import FigureRef
from paper_mcp.pipelines.html_to_markdown import html_table_to_markdown
from paper_mcp.pipelines.marker_client import MarkerBlock, MarkerDoc

logger = logging.getLogger(__name__)

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"[ \t]+")
_BLANKS_RE = re.compile(r"\n{3,}")

# Image magic numbers, so a figure is saved with the extension it actually is
# rather than one we assumed.
_MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"\x89PNG\r\n\x1a\n", ".png"),
    (b"\xff\xd8\xff", ".jpg"),
    (b"GIF87a", ".gif"),
    (b"GIF89a", ".gif"),
    (b"RIFF", ".webp"),
)


def _image_ext(data: bytes) -> str:
    for magic, ext in _MAGIC:
        if data.startswith(magic):
            return ext
    return ".png"


def is_group_reference(fragment: str) -> bool:
    """True for a Marker *Group* wrapper that only points at other blocks.

    Marker emits `TableGroup` / `FigureGroup` blocks whose html is a
    `<content-ref src='/page/5/Table/0'>` pointing at the real block, which
    arrives separately. Treating one as a table produced three "table could
    not be rendered" warnings on a real paper whose tables had in fact
    rendered perfectly — a false alarm that tells an agent to distrust good
    data, which is worse than silence.
    """
    return "<content-ref" in fragment and "<table" not in fragment.lower()


def strip_html(fragment: str) -> str:
    """Plain text from a non-table HTML fragment, entities resolved."""
    text = _TAG_RE.sub(" ", fragment or "")
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


def _heading(block: MarkerBlock) -> str:
    """Render a section header, keeping Marker's own hierarchy depth.

    Depth comes from how deep the header sits in `section_hierarchy`; an agent
    reading the markdown gets the paper's real structure rather than a flat
    list of `##`.
    """
    name = strip_html(block.html)
    if not name:
        return ""
    level = min(len(block.section_hierarchy) + 1, 6) if block.section_hierarchy else 2
    return f"\n\n{'#' * level} {name}\n"


def marker_doc_to_bundle_parts(
    doc: MarkerDoc, *, asset_dir: Path
) -> tuple[str, list[FigureRef], list[str]]:
    """Render `(markdown, figures, warnings)` from a Marker document.

    Figure images are written under `asset_dir/figures/`. A figure enters the
    index only when its bytes decoded successfully — an index entry is a
    promise that the image exists.
    """
    figures_dir = asset_dir / "figures"
    parts: list[str] = []
    figures: list[FigureRef] = []
    warnings: list[str] = []

    for block in doc.blocks:
        kind = block.block_type

        # Group wrappers reference blocks that arrive separately; rendering
        # them would duplicate content and warn about tables that are fine.
        if is_group_reference(block.html):
            continue

        if kind == "SectionHeader":
            parts.append(_heading(block))
            continue

        if kind in ("Figure", "Picture", "FigureGroup"):
            caption = block.caption if block.caption is not None else strip_html(block.html)
            if not block.images:
                # Marker saw a figure but produced no image. Say so rather
                # than emit an index entry pointing at nothing.
                if caption:
                    warnings.append(f"figure without an extracted image: {caption[:80]}")
                continue
            raw = next(iter(block.images.values()))
            try:
                data = base64.b64decode(raw, validate=True)
            except (binascii.Error, ValueError):
                warnings.append(f"figure image failed to decode: {caption[:80]}")
                continue
            index = len(figures) + 1
            fid = f"fig-{index:03d}"
            name = f"{fid}{_image_ext(data)}"
            figures_dir.mkdir(parents=True, exist_ok=True)
            (figures_dir / name).write_bytes(data)
            figures.append(
                FigureRef(
                    id=fid,
                    caption=caption,
                    page=block.page,
                    image_path=f"figures/{name}",
                )
            )
            # Reference the figure inline so the agent meets it in context,
            # where the surrounding prose explains it.
            parts.append(f"\n\n![{fid}](figures/{name})\n\n*{fid}: {caption}*\n")
            continue

        if kind in ("Table", "TableGroup"):
            table = html_table_to_markdown(block.html)
            if table:
                parts.append(f"\n\n{table}\n")
            else:
                # Falling back to stripped text would produce the cell-blob
                # this pipeline exists to avoid, so flag it instead.
                # Carry the shape that defeated the renderer: a bare "could
                # not render" is undiagnosable without another GPU run.
                head = re.sub(r"\s+", " ", block.html or "")[:200]
                warnings.append(f"table not rendered as markdown; html head: {head!r}")
            continue

        if kind == "Equation":
            latex = (block.latex or "").strip()
            if latex:
                parts.append(f"\n\n$$\n{latex}\n$$\n")
            else:
                text = strip_html(block.html)
                if text:
                    parts.append(f"\n\n{text}\n")
            continue

        if kind == "ListItem":
            text = strip_html(block.html)
            if text:
                parts.append(f"\n- {text}")
            continue

        if kind in ("PageHeader", "PageFooter"):
            continue  # running heads add nothing and interrupt the prose

        text = strip_html(block.html)
        if text:
            parts.append(f"\n\n{text}\n")

    markdown = _BLANKS_RE.sub("\n\n", "".join(parts)).strip()
    return markdown, figures, warnings
