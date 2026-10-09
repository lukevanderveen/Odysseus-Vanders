"""Idempotent seeding for the Career hub: reviewer personas (CrewMember rows
with a ``career_*`` department) and packaged skills (plans 01/03 add the
templates under services/career/skills/).

Mirrors services/council/personas.seed_council_members: never modifies a row
that already exists, so user edits survive every bootstrap.
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.database import CrewMember, SessionLocal

logger = logging.getLogger(__name__)

REVIEWER_DEPARTMENTS = ("career_recruiter", "career_hiring_manager", "career_engineer", "career_hr")

REVIEWER_NAMES: Dict[str, str] = {
    "career_recruiter": "Recruiter screener",
    "career_hiring_manager": "Hiring manager",
    "career_engineer": "Senior engineer",
    "career_hr": "HR / People partner",
}

_PERSONA_FILES = {
    "career_recruiter": "recruiter.md",
    "career_hiring_manager": "hiring_manager.md",
    "career_engineer": "engineer.md",
    "career_hr": "hr.md",
}

_PACKAGED_REVIEWERS = Path(__file__).parent / "reviewers"
_PACKAGED_SKILLS = Path(__file__).parent / "skills"
SKILL_CATEGORY = "career"
RETIRED_SKILL_DIRS = (("general", "linkedin-post-writer", "1.0.0"),)   # (category, name, version to retire)


def _persona_dirs():
    from core.constants import DATA_DIR
    return (Path(DATA_DIR) / "career" / "reviewers", _PACKAGED_REVIEWERS)


def load_persona(department: str) -> str:
    """User override in data/career/reviewers/<file> wins over the packaged text."""
    fname = _PERSONA_FILES[department]
    for d in _persona_dirs():
        f = d / fname
        if f.is_file():
            return f.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"No persona file for {department}")


def seed_reviewers(owner: Optional[str], session_factory: Callable = None) -> int:
    factory = session_factory or SessionLocal
    db = factory()
    try:
        q = db.query(CrewMember).filter(CrewMember.department.in_(REVIEWER_DEPARTMENTS))
        if owner is not None:
            q = q.filter(CrewMember.owner == owner)
        covered = {m.department for m in q.all()}
        created = 0
        for dept in REVIEWER_DEPARTMENTS:
            if dept in covered:
                continue
            db.add(CrewMember(
                id=uuid.uuid4().hex[:12], owner=owner, name=REVIEWER_NAMES[dept],
                personality=load_persona(dept), department=dept,
            ))
            created += 1
        if created:
            db.commit()
        return created
    finally:
        db.close()


def seed_skills(owner: Optional[str], skills_manager, templates_dir: Optional[str] = None) -> List[str]:
    """Copy each packaged skill folder into data/skills/career/<name>/ unless a
    skill with that name already exists anywhere. Retires the listed legacy
    skill folders when their frontmatter version matches. Returns names seeded."""
    src_root = Path(templates_dir) if templates_dir else _PACKAGED_SKILLS
    if not src_root.is_dir():
        return []
    _retire_legacy_skills(skills_manager)
    existing = {s.get("name") for s in skills_manager.load_all()}
    seeded: List[str] = []
    for skill_dir in sorted(p for p in src_root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
        name = skill_dir.name
        if name in existing:
            continue
        dest = Path(skills_manager._skill_dir(SKILL_CATEGORY, name))
        shutil.copytree(skill_dir, dest, dirs_exist_ok=True)
        text = (dest / "SKILL.md").read_text(encoding="utf-8")
        if owner and "\nowner:" not in text:
            text = text.replace("\n---\n", f"\nowner: {owner}\n---\n", 1)
        (dest / "SKILL.md").write_text(text, encoding="utf-8")
        seeded.append(name)
    return seeded


def _retire_legacy_skills(skills_manager) -> None:
    for category, name, version in RETIRED_SKILL_DIRS:
        path = skills_manager._skill_file(category, name)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8").read()
        if f"version: {version}" not in text:
            logger.info("Keeping %s/%s (version differs from %s)", category, name, version)
            continue
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        logger.info("Retired legacy skill %s/%s v%s in favour of the career seed", category, name, version)
