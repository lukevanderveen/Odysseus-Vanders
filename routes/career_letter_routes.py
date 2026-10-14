"""Career hub letter-quality endpoints (plan 05a) — baseline scoring of past letters.

Registered onto the /api/career studio router by routes.career_studio_routes,
which passes its collaborators as callables so tests patch one module.
"""
from __future__ import annotations

from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, Query, Request

from core.database import Document, JobApplication
from services.career.claims_store import load_claims
from services.career.letter_baseline import load_baseline, save_baseline, score_letter, summarise

MAX_BASELINE_LETTERS = 20


def _recent_letters(db, owner: Optional[str], limit: int, check_context: Callable) -> List[Dict[str, Any]]:
    """Plain dicts so the DB session can close before the tagging calls."""
    apps = (db.query(JobApplication)
            .filter(JobApplication.owner == owner, JobApplication.cover_letter_doc_id.isnot(None))
            .order_by(JobApplication.updated_at.desc()).limit(limit).all())
    out = []
    for app in apps:
        doc = db.get(Document, app.cover_letter_doc_id)
        if not doc or doc.owner != owner or not (doc.current_content or "").strip():
            continue
        requirements = (load_claims(owner, app.id) or {}).get("requirements") or {}
        out.append({"application_id": app.id, "company": app.company, "role": app.role,
                    "letter": doc.current_content, "ctx": check_context(db, app, requirements)})
    return out


def register_letter_routes(router: APIRouter, owner_of: Callable[[Request], Optional[str]], *,
                           session_factory: Callable[[], Any], check_context: Callable,
                           complete: Callable) -> None:

    @router.post("/letters/baseline")
    async def run_baseline(request: Request, limit: int = Query(5, ge=1, le=MAX_BASELINE_LETTERS)):
        user = owner_of(request)
        db = session_factory()
        try:
            letters = _recent_letters(db, user, limit, check_context)
        finally:
            db.close()
        rows = []
        for item in letters:
            scored = await score_letter(item["letter"], item["ctx"], complete, user)
            rows.append({"application_id": item["application_id"], "company": item["company"],
                         "role": item["role"], **scored})
        result = {"run_at": datetime.utcnow().isoformat(), "letters": rows, "summary": summarise(rows)}
        save_baseline(user, result)
        return result

    @router.get("/letters/baseline")
    def get_baseline(request: Request):
        return load_baseline(owner_of(request)) or {"letters": [], "summary": None}
