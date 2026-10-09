"""Council personas (Agent Council Phase 3).

A council member is a CrewMember with ``department`` set. The five defaults
are seeded per owner on first council use — idempotently, and never touching
members the user already assigned to a department. Each persona's report
procedure lives in its template under report_types/; the personality here is
the standing system prompt for chat with that member.
"""

from __future__ import annotations

from typing import Callable, Optional
import uuid

from core.database import CrewMember, SessionLocal

DEPARTMENTS = ("research", "business", "architect", "developer", "marketing")

_SEEDS = {
    "research": {
        "name": "Research",
        "personality": (
            "You are the Research department of the user's project council. "
            "You investigate markets, competitors, and prior art, and you ground "
            "every claim in the provided context — write 'unclear' rather than "
            "guessing. Your written output is frank and evidence-first."
        ),
    },
    "business": {
        "name": "Business",
        "personality": (
            "You are the Business department of the user's project council. "
            "You think in launch readiness, monetisation paths, pricing, and "
            "go-to-market sequencing. You distinguish facts from your own "
            "judgement and commit to concrete recommendations with reasoning."
        ),
    },
    "architect": {
        "name": "Architect",
        "personality": (
            "You are the Architecture department of the user's project council. "
            "You review system design: boundaries, coupling, data flow, failure "
            "modes, and scaling limits. You only state what the provided code "
            "and docs support, and you rank concerns by blast radius."
        ),
    },
    "developer": {
        "name": "Developer",
        "personality": (
            "You are the Developer department of the user's project council. "
            "You assess engineering health: tests, CI, dependencies, code "
            "hygiene, and delivery risk. You are frank, specific, and you tie "
            "every observation to evidence from the repository."
        ),
    },
    "marketing": {
        "name": "Marketing",
        "personality": (
            "You are the Marketing department of the user's project council. "
            "You assess positioning, audience, naming, and channels. You label "
            "opinion as opinion, keep recommendations actionable, and favour "
            "low-cost validation steps over grand campaigns."
        ),
    },
}


def seed_council_members(owner: Optional[str], session_factory: Callable = None) -> int:
    """Create the default member for each department the owner doesn't already
    cover. Returns the number created. Existing members (including user-made
    ones with a department) are never modified."""
    factory = session_factory or SessionLocal
    db = factory()
    try:
        q = db.query(CrewMember).filter(CrewMember.department.in_(DEPARTMENTS))
        if owner is not None:
            q = q.filter(CrewMember.owner == owner)
        covered = {m.department for m in q.all()}

        created = 0
        for department in DEPARTMENTS:
            if department in covered:
                continue
            seed = _SEEDS[department]
            db.add(CrewMember(
                id=uuid.uuid4().hex[:12],
                owner=owner,
                name=seed["name"],
                personality=seed["personality"],
                department=department,
            ))
            created += 1
        if created:
            db.commit()
        return created
    finally:
        db.close()
