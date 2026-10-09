"""Career hub studio API — generation endpoints.

Plan 01: cover letters. Plans 02 (reviews/reviewers) and 03 (activity/posts)
add their endpoints inside setup_career_studio_routes so app.py mounts one
router. Owner-scoped via routes.career_helpers.get_owned_application.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request

from core.database import Document, DocumentVersion, JobApplication, SessionLocal
from routes.career_helpers import get_owned_application
from routes.document_helpers import create_library_document
from services.career.claims_store import load_claims, save_claims
from services.career.cover_letter import NoEvidenceError, draft_cover_letter, extract_requirements, rubric_check
from services.career.evidence import Evidence
from services.career.llm import complete
from services.career.settings import load_career_settings
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


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
            warnings = rubric_check(markdown, requirements.get("hard", []))
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

    return router
