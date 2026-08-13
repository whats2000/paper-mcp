"""Expose the skill bundles as MCP prompts.

Skills are examples, not the product (SRS FR-10). They show a calling agent
how to compose these tools; the agent owns its pipelines and is free to
ignore them. Serving the same text over `prompts/` means a Claude client
surfaces them as slash commands without anyone copying files around.

Read from `skills/<name>/SKILL.md` so there is exactly one copy — a skill
duplicated into Python would drift from the one contributors edit.
"""
from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

logger = logging.getLogger(__name__)

# Repo layout in development; the container copies skills next to the package.
_CANDIDATE_ROOTS = (
    Path(__file__).resolve().parent.parent.parent / "skills",
    Path(__file__).resolve().parent / "skills",
    Path("/app/skills"),
)

_FRONT_MATTER = "---"


@lru_cache(maxsize=1)
def skills_root() -> Path | None:
    for candidate in _CANDIDATE_ROOTS:
        if candidate.is_dir():
            return candidate
    return None


def _strip_front_matter(text: str) -> tuple[str, str]:
    """Split YAML front matter off, returning `(description, body)`.

    The description is what a client shows in a prompt list, so it comes from
    the skill's own front matter rather than being restated here.
    """
    description = ""
    if text.startswith(_FRONT_MATTER):
        _, _, rest = text.partition("\n")
        front, _, body = rest.partition(f"{_FRONT_MATTER}\n")
        for line in front.splitlines():
            if line.startswith("description:"):
                description = line.split(":", 1)[1].strip()
        return description, body.lstrip("\n")
    return description, text


def load_skills() -> dict[str, tuple[str, str]]:
    """Return `{name: (description, body)}` for every skill on disk."""
    root = skills_root()
    if root is None:
        logger.info("no skills directory found; prompts/ will be empty")
        return {}
    skills: dict[str, tuple[str, str]] = {}
    for path in sorted(root.glob("*/SKILL.md")):
        try:
            description, body = _strip_front_matter(path.read_text(encoding="utf-8"))
        except OSError as exc:
            logger.warning("skill %s is unreadable: %s", path.parent.name, exc)
            continue
        skills[path.parent.name] = (description, body)
    return skills
