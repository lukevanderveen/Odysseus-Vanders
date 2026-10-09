"""Packaged career skills are copied into data/skills/career/ once; the legacy
general/linkedin-post-writer v1.0.0 is retired."""
import os
from pathlib import Path

from services.career import seeds
from services.memory.skills import SkillsManager

_V1 = """---
name: linkedin-post-writer
description: old
version: 1.0.0
category: general
status: published
source: user
---

## Procedure

1. old
"""

_TEMPLATE = """---
name: {name}
description: {name} seeded
version: 2.0.0
category: career
status: published
source: user
---

## When to Use

Testing.

## Procedure

1. Call the hub.
"""


def _templates(tmp_path):
    root = tmp_path / "templates"
    for name in ("cover-letter-writer", "linkedin-post-writer"):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_TEMPLATE.format(name=name), encoding="utf-8")
    (root / "linkedin-post-writer" / "examples").mkdir()
    (root / "linkedin-post-writer" / "examples" / "one.md").write_text("example", encoding="utf-8")
    return str(root)


def test_seeds_each_template_once_with_owner(tmp_path):
    sm = SkillsManager(str(tmp_path / "data"))
    seeded = seeds.seed_skills("vanders", sm, templates_dir=_templates(tmp_path))
    assert seeded == ["cover-letter-writer", "linkedin-post-writer"]
    text = Path(sm._skill_file("career", "linkedin-post-writer")).read_text(encoding="utf-8")
    assert "owner: vanders" in text
    assert os.path.isfile(os.path.join(sm._skill_dir("career", "linkedin-post-writer"), "examples", "one.md"))
    assert seeds.seed_skills("vanders", sm, templates_dir=_templates(tmp_path / "again")) == []


def test_retires_legacy_v1_but_keeps_other_versions(tmp_path):
    sm = SkillsManager(str(tmp_path / "data"))
    legacy = Path(sm._skill_file("general", "linkedin-post-writer"))
    legacy.parent.mkdir(parents=True)
    legacy.write_text(_V1, encoding="utf-8")
    seeded = seeds.seed_skills("vanders", sm, templates_dir=_templates(tmp_path))
    assert "linkedin-post-writer" in seeded
    assert not legacy.exists()

    sm2 = SkillsManager(str(tmp_path / "data2"))
    legacy2 = Path(sm2._skill_file("general", "linkedin-post-writer"))
    legacy2.parent.mkdir(parents=True)
    legacy2.write_text(_V1.replace("1.0.0", "1.5.0"), encoding="utf-8")
    seeded2 = seeds.seed_skills("vanders", sm2, templates_dir=_templates(tmp_path / "t2"))
    assert legacy2.exists()
    assert "linkedin-post-writer" not in seeded2   # name taken by the user's own edited skill
