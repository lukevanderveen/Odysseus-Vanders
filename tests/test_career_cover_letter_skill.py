"""Packaged cover-letter-writer skill parses and points the agent at the hub routes."""
from pathlib import Path

from services.memory.skill_format import Skill

_SKILL = Path("services/career/skills/cover-letter-writer/SKILL.md")


def test_skill_parses_with_expected_frontmatter():
    sk = Skill.from_markdown(_SKILL.read_text(encoding="utf-8"), path=str(_SKILL))
    assert sk.name == "cover-letter-writer"
    assert sk.category == "career"
    assert sk.status == "published"
    assert sk.source == "user"
    assert sk.when_to_use
    assert len(sk.procedure) >= 5
    assert sk.pitfalls and sk.verification


def test_skill_names_the_routes_and_constraints():
    text = _SKILL.read_text(encoding="utf-8")
    assert "POST /api/career/applications" in text
    assert "/cover-letter" in text
    assert "app_api" in text
    assert "#document-" in text
    assert "admin" in text.lower()
    assert "Setup" in text
    assert "never invent" in text.lower()
