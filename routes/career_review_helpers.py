"""Serialisers, request model and reviewer query for the panel-review routes (plan 02)."""
from __future__ import annotations

import json
from typing import Any, Dict, Optional

from pydantic import BaseModel

from core.database import ApplicationReview, CrewMember
from services.career.seeds import REVIEWER_DEPARTMENTS


class ReviewerUpdate(BaseModel):
    name: Optional[str] = None
    avatar: Optional[str] = None
    personality: Optional[str] = None
    model: Optional[str] = None
    endpoint_url: Optional[str] = None


def _json(s) -> Dict[str, Any]:
    try:
        return json.loads(s) if s else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def review_to_dict(r: ApplicationReview, include_body: bool = False) -> Dict[str, Any]:
    out = {
        "id": r.id, "application_id": r.application_id, "status": r.status, "error": r.error,
        "scores": _json(r.scores),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if include_body:
        out["verdicts"] = _json(r.verdicts)
        out["panel_summary"] = r.panel_summary
    return out


def reviewer_to_dict(m: CrewMember) -> Dict[str, Any]:
    return {"id": m.id, "department": m.department, "name": m.name, "avatar": m.avatar,
            "personality": m.personality, "model": m.model, "endpoint_url": m.endpoint_url}


def reviewer_query(db, user: Optional[str], member_id: Optional[str] = None):
    """CrewMember rows that are career reviewers, ordered by department,
    owner-scoped when a user is resolved, narrowed to one row when ``member_id``
    is given. Every CrewMember reference lives here so callers never mix a
    second mapped class into the same query."""
    q = db.query(CrewMember).filter(CrewMember.department.in_(REVIEWER_DEPARTMENTS))
    if user is not None:
        q = q.filter(CrewMember.owner == user)
    if member_id is not None:
        q = q.filter(CrewMember.id == member_id)
    return q.order_by(CrewMember.department)
