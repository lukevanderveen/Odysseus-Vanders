"""Career hub studio API — generation endpoints.

Plan 01: cover letters. Plans 02 (reviews/reviewers) and 03 (activity/posts)
add their endpoints inside setup_career_studio_routes so app.py mounts one
router. Owner-scoped via routes.career_helpers.get_owned_application.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request

from core.database import ApplicationReview, Document, DocumentVersion, JobApplication, SessionLocal
from routes.career_helpers import get_owned_application
from routes.career_letter_routes import register_letter_routes
from routes.career_posts_routes import register_post_routes
from routes.career_review_helpers import ReviewerUpdate, review_to_dict, reviewer_query, reviewer_to_dict
from routes.document_helpers import create_library_document
from services.career.activity_remote import gather_activity
from services.career.claims_store import load_claims, save_claims
from services.career.posts import draft_post
from services.career.cover_letter import NoEvidenceError, draft_cover_letter, extract_requirements
from services.career.cv_overlap import flags_for_owner
from services.career.evidence import Evidence
from services.career.letter_checks import CheckContext, banned_phrases, run_checks, warnings as check_warnings
from services.career.llm import complete, complete_utility
from services.career.review_runner import queue_state, run_review
from services.career.seeds import seed_reviewers
from services.career.settings import load_career_settings
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

_review_tasks: set = set()   # keeps fire-and-forget panel runs alive
TAGGER_MAX_TOKENS = 2500     # a 400-word letter echoed back sentence by sentence, as JSON


def upsert_cover_letter_document(db, app: JobApplication, owner: Optional[str], markdown: str) -> Tuple[str, int]:
    """Create the library document on first draft; add a version on later drafts."""
    title = f"Cover letter — {app.company} {app.role}"
    doc = db.get(Document, app.cover_letter_doc_id) if app.cover_letter_doc_id else None
    if doc is None:
        doc = create_library_document(db, owner=owner, title=title, content=markdown, language="markdown")
        app.cover_letter_doc_id = doc.id
        db.commit()
        return doc.id, 1
    new_ver = (doc.version_count or 1) + 1
    doc.current_content = markdown
    doc.version_count = new_ver
    db.add(DocumentVersion(id=str(uuid.uuid4()), document_id=doc.id, version_number=new_ver,
                           content=markdown, summary="Regenerated cover letter", source="ai"))
    db.commit()
    return doc.id, new_ver


def check_context(db, app: JobApplication, requirements: Dict[str, Any]) -> CheckContext:
    """Other company names come from the same owner's applications only."""
    others = {c for (c,) in db.query(JobApplication.company)
              .filter(JobApplication.owner == app.owner, JobApplication.id != app.id).all() if c}
    return CheckContext(company=app.company or "", role=app.role or "", jd_text=app.jd_text or "",
                        hard_requirements=requirements.get("hard", []), other_companies=sorted(others),
                        banned_phrases=banned_phrases(load_career_settings(app.owner)))


def _empty_payload() -> Dict[str, Any]:
    return {"doc_id": None, "version": 0, "claims": [], "rubric_warnings": [], "requirements": {}}


def setup_career_studio_routes(rag_manager) -> APIRouter:
    router = APIRouter(prefix="/api/career", tags=["career"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    @router.post("/applications/{aid}/cover-letter")
    async def draft_letter(aid: str, request: Request):
        user = _owner(request)
        if rag_manager is None:
            raise HTTPException(503, "RAG is not available — the embedding service is required to read your CV")
        settings = load_career_settings(user)
        if not settings.get("cv_filename"):
            raise HTTPException(409, "No CV on file — upload one in Career → Setup first.")
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            if not (app.jd_text or "").strip():
                raise HTTPException(409, "This application has no job description — paste one or give a posting URL.")
            requirements = await extract_requirements(app.jd_text, complete, user)
            try:
                markdown, claims = await draft_cover_letter(
                    app, user, complete, Evidence(user, rag=rag_manager),
                    requirements=requirements, settings=settings)
            except NoEvidenceError as e:
                raise HTTPException(409, str(e))
            ctx = check_context(db, app, requirements)
            ctx.cv_flags = await asyncio.to_thread(flags_for_owner, user, markdown)
            warnings = check_warnings(run_checks(markdown, ctx))
            doc_id, version = upsert_cover_letter_document(db, app, user, markdown)
            payload = {"doc_id": doc_id, "version": version, "claims": claims,
                       "rubric_warnings": warnings, "requirements": requirements}
            save_claims(user, aid, payload)
            return payload
        finally:
            db.close()

    @router.get("/applications/{aid}/cover-letter")
    def get_letter(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            stored = load_claims(user, aid) or _empty_payload()
            stored["doc_id"] = app.cover_letter_doc_id
            return stored
        finally:
            db.close()

    # ── Reviewer panel (plan 02) ────────────────────────────────────────────

    @router.post("/applications/{aid}/reviews")
    async def create_review(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            doc = db.get(Document, app.cover_letter_doc_id) if app.cover_letter_doc_id else None
            if not doc or not (doc.current_content or "").strip():
                raise HTTPException(409, "Draft a cover letter before running the panel")
            rid = uuid.uuid4().hex[:12]
            db.add(ApplicationReview(id=rid, owner=user, application_id=app.id, status="running"))
            db.commit()
        finally:
            db.close()
        task = asyncio.create_task(run_review(rid))
        _review_tasks.add(task)
        task.add_done_callback(_review_tasks.discard)
        return {"review_id": rid, "status": "running"}

    @router.get("/applications/{aid}/reviews")
    def list_reviews(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            rows = (db.query(ApplicationReview).filter(ApplicationReview.application_id == app.id)
                    .order_by(ApplicationReview.created_at.desc()).all())
            return {"reviews": [review_to_dict(r) for r in rows]}
        finally:
            db.close()

    @router.get("/reviews/{rid}")
    def get_review(rid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            q = db.query(ApplicationReview).filter(ApplicationReview.id == rid)
            if user is not None:
                q = q.filter(ApplicationReview.owner == user)
            review = q.first()
            if not review:
                raise HTTPException(404, "Review not found")
            return review_to_dict(review, include_body=True)
        finally:
            db.close()

    @router.get("/reviewers")
    def list_reviewers(request: Request):
        user = _owner(request)
        seed_reviewers(user, session_factory=SessionLocal)
        db = SessionLocal()
        try:
            rows = reviewer_query(db, user).all()
            return {"reviewers": [reviewer_to_dict(m) for m in rows]}
        finally:
            db.close()

    @router.get("/reviewers/status")
    def reviewers_status(request: Request):
        user = _owner(request)
        state = queue_state()
        db = SessionLocal()
        try:
            running = None
            if state["running"]:
                q = db.query(ApplicationReview).filter(ApplicationReview.id == state["running"])
                if user is not None:
                    q = q.filter(ApplicationReview.owner == user)
                running = state["running"] if q.first() else None
            rows = reviewer_query(db, user).all()
            return {
                "running": running,
                "queued": list(state["queued"]),
                "reviewers": [{"id": m.id, "department": m.department, "name": m.name,
                               "status": "Busy" if running else "Ready"} for m in rows],
            }
        finally:
            db.close()

    @router.put("/reviewers/{mid}")
    def update_reviewer(mid: str, body: ReviewerUpdate, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            member = reviewer_query(db, user, member_id=mid).first()
            if not member:
                raise HTTPException(404, "Reviewer not found")
            for field in ("name", "avatar", "personality", "model", "endpoint_url"):
                val = getattr(body, field)
                if val is not None:
                    setattr(member, field, val)
            db.commit()
            return reviewer_to_dict(member)
        finally:
            db.close()

    # ── Activity + LinkedIn posts (plan 03) ─────────────────────────────────
    # Collaborators are passed as callables that resolve this module's globals
    # at call time, so tests patch gather_activity / draft_post / SessionLocal here.
    register_post_routes(
        router, _owner,
        session_factory=lambda: SessionLocal(),
        gather=lambda *a, **kw: gather_activity(*a, **kw),
        draft=lambda *a, **kw: draft_post(*a, **kw),
    )

    # ── Letter quality baseline (plan 05a) ──────────────────────────────────
    register_letter_routes(
        router, _owner,
        session_factory=lambda: SessionLocal(),
        check_context=check_context,
        complete=lambda messages, owner=None: complete_utility(messages, owner, max_tokens=TAGGER_MAX_TOKENS),
        cv_flags=lambda owner, letter: flags_for_owner(owner, letter),
    )
    return router
