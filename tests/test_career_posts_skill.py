from pathlib import Path

from services.memory.skill_format import Skill

_SKILL = Path("services/career/skills/linkedin-post-writer/SKILL.md")


def test_skill_template_parses_with_expected_frontmatter():
    sk = Skill.from_markdown(_SKILL.read_text(encoding="utf-8"))
    assert sk.name == "linkedin-post-writer"
    assert sk.version == "2.0.0" and sk.category == "career"
    assert sk.source == "user" and sk.status == "published"
    assert "/api/career/activity" in " ".join(sk.procedure)
    assert "/api/career/posts/draft" in " ".join(sk.procedure)
    assert any("never publish" in p.lower() for p in sk.procedure + sk.pitfalls)
    assert any("app_api" in p for p in sk.pitfalls)
    assert any("140" in v for v in sk.verification)
