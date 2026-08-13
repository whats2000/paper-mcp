from __future__ import annotations

from pathlib import Path

import pytest

from paper_mcp.server import build_mcp_server
from paper_mcp.skills import _strip_front_matter, load_skills


def test_skills_are_discovered() -> None:
    skills = load_skills()

    assert {"paper-to-deck", "deep-read"} <= set(skills)


def test_each_skill_has_a_description_and_a_body() -> None:
    for name, (description, body) in load_skills().items():
        assert description, f"{name} has no description for a prompt list"
        assert len(body) > 200, f"{name} body looks empty"
        # Front matter must not leak into the served text.
        assert not body.startswith("---"), f"{name} still carries front matter"


def test_front_matter_is_split_off() -> None:
    description, body = _strip_front_matter(
        "---\nname: x\ndescription: Does a thing.\n---\n# Title\n\nBody.\n"
    )

    assert description == "Does a thing."
    assert body.startswith("# Title")


def test_text_without_front_matter_is_returned_whole() -> None:
    description, body = _strip_front_matter("# Just a body\n")

    assert description == ""
    assert body.startswith("# Just a body")


async def test_skills_are_served_as_mcp_prompts() -> None:
    # A Claude client surfaces these as slash commands, so nobody has to copy
    # files around to use them.
    prompts = await build_mcp_server().list_prompts()

    assert {"paper-to-deck", "deep-read"} <= {p.name for p in prompts}
    assert all(p.description for p in prompts)


def test_the_deck_skill_states_the_grounding_contract() -> None:
    # The one thing this skill exists to prevent is a deck citing a figure the
    # paper does not contain.
    body = load_skills()["paper-to-deck"][1]

    assert "figures" in body
    assert "does not exist" in body
    assert "sandbox_unavailable" in body  # must not retry a deployment failure


def test_a_missing_skills_directory_is_not_fatal(monkeypatch: pytest.MonkeyPatch) -> None:
    # Skills are a convenience; their absence must not stop the server.
    import paper_mcp.skills as skills_mod

    skills_mod.skills_root.cache_clear()
    monkeypatch.setattr(skills_mod, "_CANDIDATE_ROOTS", (Path("/no/such/dir"),))

    assert load_skills() == {}
    skills_mod.skills_root.cache_clear()
