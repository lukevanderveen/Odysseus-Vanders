"""Career hub core API — bootstrap, settings, applications.

docs/plans/career-hub-design.md. Owner-scoped like council/projects: filter by
owner only when a user is resolved; foreign rows are 404.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, field_validator

from core.database import CrewMember, JobApplication, SessionLocal
from routes.career_files_routes import register_file_routes
from routes.career_helpers import APPLICATION_STATUSES, application_to_dict, get_owned_application
from services.career.seeds import REVIEWER_DEPARTMENTS, seed_reviewers, seed_skills
from services.career.settings import load_career_settings, save_career_settings
from src.auth_helpers import get_current_user
from src.search.content import fetch_webpage_content

logger = logging.getLogger(__name__)


class ApplicationCreate(BaseModel):
    company: str
    role: str
    url: Optional[str] = None
    jd_text: Optional[str] = None
    jd_url: Optional[str] = None
    applied_at: Optional[datetime] = None
    status: str = "drafting"
    notes: Optional[str] = None

    @field_validator("company", "role")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str) -> str:
        if v not in APPLICATION_STATUSES:
            raise ValueError(f"status must be one of {APPLICATION_STATUSES}")
        return v


class ApplicationUpdate(BaseModel):
    company: Optional[str] = None
    role: Optional[str] = None
    url: Optional[str] = None
    jd_text: Optional[str] = None
    applied_at: Optional[datetime] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    archived: Optional[bool] = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in APPLICATION_STATUSES:
            raise ValueError(f"status must be one of {APPLICATION_STATUSES}")
        return v


async def _jd_from_url(url: str) -> str:
    try:
        data = await asyncio.to_thread(fetch_webpage_content, url, 8)
    except Exception:
        logger.warning("JD fetch failed for %s", url, exc_info=True)
        return ""
    return (data or {}).get("content") or ""


def setup_career_routes(skills_manager, rag_manager) -> APIRouter:
    router = APIRouter(prefix="/api/career", tags=["career"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _has_cv(user: Optional[str], settings: Dict[str, Any]) -> bool:
        return bool(settings.get("cv_filename"))

    @router.get("/bootstrap")
    def bootstrap(request: Request):
        user = _owner(request)
        seed_reviewers(user, session_factory=SessionLocal)
        try:
            seeded = seed_skills(user, skills_manager) if skills_manager else []
        except Exception:
            logger.warning("DEGRADED: career skill seeding failed", exc_info=True)
            seeded = []
        settings = load_career_settings(user)
        db = SessionLocal()
        try:
            aq = db.query(JobApplication).filter(JobApplication.archived == False)  # noqa: E712
            rq = db.query(CrewMember).filter(CrewMember.department.in_(REVIEWER_DEPARTMENTS))
            if user is not None:
                aq = aq.filter(JobApplication.owner == user)
                rq = rq.filter(CrewMember.owner == user)
            reviewers = [{"id": m.id, "department": m.department, "name": m.name}
                         for m in rq.order_by(CrewMember.name).all()]
            return {
                "settings": settings,
                "counts": {"applications": aq.count()},
                "has_cv": _has_cv(user, settings),
                "reviewers": reviewers,
                "seeded_skills": seeded,
            }
        finally:
            db.close()

    @router.get("/settings")
    def get_settings(request: Request):
        return load_career_settings(_owner(request))

    @router.put("/settings")
    def put_settings(body: Dict[str, Any], request: Request):
        return save_career_settings(_owner(request), body or {})

    @router.get("/applications")
    def list_applications(request: Request, include_archived: int = Query(0)):
        user = _owner(request)
        settings = load_career_settings(user)
        db = SessionLocal()
        try:
            q = db.query(JobApplication)
            if user is not None:
                q = q.filter(JobApplication.owner == user)
            if not include_archived:
                q = q.filter(JobApplication.archived == False)  # noqa: E712
            rows = q.order_by(JobApplication.created_at.desc()).all()
            return {"applications": [application_to_dict(a, settings) for a in rows]}
        finally:
            db.close()

    @router.post("/applications", status_code=201)
    async def create_application(body: ApplicationCreate, request: Request):
        user = _owner(request)
        jd_text = (body.jd_text or "").strip()
        if not jd_text and body.jd_url:
            jd_text = await _jd_from_url(body.jd_url)
        db = SessionLocal()
        try:
            app = JobApplication(
                id=uuid.uuid4().hex[:12], owner=user, company=body.company, role=body.role,
                url=body.url or body.jd_url, jd_text=jd_text or None, applied_at=body.applied_at,
                status=body.status, notes=body.notes, source="manual",
            )
            db.add(app)
            db.commit()
            db.refresh(app)
            return application_to_dict(app, load_career_settings(user))
        finally:
            db.close()

    @router.get("/applications/{aid}")
    def get_application(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            return application_to_dict(get_owned_application(db, aid, user), load_career_settings(user))
        finally:
            db.close()

    @router.put("/applications/{aid}")
    def update_application(aid: str, body: ApplicationUpdate, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            for field in ("company", "role", "url", "jd_text", "applied_at", "status", "notes", "archived"):
                val = getattr(body, field)
                if val is None:
                    continue
                if field in ("company", "role") and isinstance(val, str):
                    val = val.strip()
                setattr(app, field, val)
            db.commit()
            db.refresh(app)
            return application_to_dict(app, load_career_settings(user))
        finally:
            db.close()

    @router.delete("/applications/{aid}")
    def archive_application(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            app.archived = True
            db.commit()
            return {"id": aid, "archived": True}
        finally:
            db.close()

    register_file_routes(router, _owner, rag_manager)
    return router
