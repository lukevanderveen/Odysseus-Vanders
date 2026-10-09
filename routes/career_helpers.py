"""Shared serialisers and lookups for the /api/career routers."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException

from core.database import JobApplication

APPLICATION_STATUSES = (
    "drafting", "applied", "acknowledged", "screening", "interview",
    "offer", "rejected", "withdrawn", "ghosted",
)
_WAITING_STATUSES = ("applied", "acknowledged", "screening")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def days_since_event(app: JobApplication, now: Optional[datetime] = None) -> Optional[int]:
    anchor = app.last_event_at or app.applied_at or app.created_at
    if not anchor:
        return None
    return max(0, ((now or datetime.utcnow()) - anchor).days)


def computed_status(app: JobApplication, settings: Dict[str, Any], now: Optional[datetime] = None) -> str:
    if app.status in _WAITING_STATUSES:
        days = days_since_event(app, now)
        if days is not None and days >= int(settings.get("ghosted_after_days", 21)):
            return "ghosted"
    return app.status or "drafting"


def application_to_dict(app: JobApplication, settings: Dict[str, Any],
                        now: Optional[datetime] = None) -> Dict[str, Any]:
    return {
        "id": app.id,
        "company": app.company,
        "role": app.role,
        "source": app.source,
        "url": app.url,
        "jd_text": app.jd_text,
        "applied_at": _iso(app.applied_at),
        "status": app.status,
        "computed_status": computed_status(app, settings, now),
        "days_since_event": days_since_event(app, now),
        "last_event_at": _iso(app.last_event_at),
        "notes": app.notes,
        "cover_letter_doc_id": app.cover_letter_doc_id,
        "latest_review_id": app.latest_review_id,
        "archived": bool(app.archived),
        "created_at": _iso(app.created_at),
        "updated_at": _iso(app.updated_at),
    }


def get_owned_application(db, aid: str, user: Optional[str]) -> JobApplication:
    q = db.query(JobApplication).filter(JobApplication.id == aid)
    if user is not None:
        q = q.filter(JobApplication.owner == user)
    app = q.first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app
