"""Career hub post endpoints (plan 03) — activity preview, drafting, lifecycle,
per-project disclosure.

Registered onto the /api/career studio router by routes.career_studio_routes,
which passes its collaborators as callables so one patch point (the studio
module) serves tests. Every CareerPost / JobApplication reference lives here.
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from core.database import CareerPost, JobApplication
from services.career.activity_remote import SOURCES
from services.career.disclosure import LEVELS, apply_disclosure
from services.career.posts import POST_TYPES, post_to_dict
from services.career.settings import load_career_settings, save_career_settings

POST_STATUSES = ("draft", "posted", "discarded")
MAX_STORED_SOURCES = 20


class DraftRequest(BaseModel):
    post_type: str
    days: int = 14
    sources: List[str] = ["git", "github", "trello"]
    project_ids: List[str] = []
    n_variants: int = 2

    @field_validator("post_type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in POST_TYPES:
            raise ValueError(f"post_type must be one of {POST_TYPES}")
        return v


class PostUpdate(BaseModel):
    status: Optional[str] = None
    body: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is not None and v not in POST_STATUSES:
            raise ValueError(f"status must be one of {POST_STATUSES}")
        return v


class DisclosureUpdate(BaseModel):
    project_id: str
    level: str

    @field_validator("level")
    @classmethod
    def _level(cls, v: str) -> str:
        if v not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        return v


def parse_sources(raw: List[str]) -> set:
    chosen = {s.strip() for s in raw if s and s.strip() in SOURCES}
    return chosen or set(SOURCES)


def owned_post(db, pid: str, user: Optional[str]) -> CareerPost:
    q = db.query(CareerPost).filter(CareerPost.id == pid)
    if user is not None:
        q = q.filter(CareerPost.owner == user)
    post = q.first()
    if not post:
        raise HTTPException(404, "Post not found")
    return post


def tracker_stats(db, user: Optional[str]) -> Dict[str, int]:
    """Counts for narrative posts; the tracker (plan 04) owns the data, so this
    stays a small query on JobApplication and degrades to zeros."""
    try:
        q = db.query(JobApplication).filter(JobApplication.archived == False)  # noqa: E712
        if user is not None:
            q = q.filter(JobApplication.owner == user)
        apps = q.all()
        return {"applications": len(apps),
                "interviews": sum(1 for a in apps if a.status in ("interview", "offer"))}
    except Exception:
        return {"applications": 0, "interviews": 0}


def register_post_routes(router: APIRouter, owner_of: Callable[[Request], Optional[str]], *,
                         session_factory: Callable[[], Any], gather: Callable, draft: Callable) -> None:

    @router.get("/activity")
    async def activity_preview(request: Request, days: int = Query(14), sources: str = Query("git,github,trello"),
                               project_ids: str = Query("")):
        user = owner_of(request)
        settings = load_career_settings(user)
        ids = [p for p in project_ids.split(",") if p.strip()] or None
        items, warnings = await gather(user, days, parse_sources(sources.split(",")), ids)
        return {"items": [i.to_dict() for i in apply_disclosure(items, settings)], "warnings": warnings}

    @router.post("/posts/draft", status_code=201)
    async def draft_posts(body: DraftRequest, request: Request):
        user = owner_of(request)
        settings = load_career_settings(user)
        items, warnings = await gather(user, body.days, parse_sources(body.sources), body.project_ids or None)
        safe = apply_disclosure(items, settings)
        if not safe:
            raise HTTPException(409, "No activity in that window. " + " ".join(warnings))
        stats = None
        if body.post_type == "narrative" and settings.get("narrative_uses_tracker_stats"):
            db = session_factory()
            try:
                stats = tracker_stats(db, user)
            finally:
                db.close()
        variants = await draft(safe, body.post_type, settings, user, n_variants=body.n_variants,
                               tracker_stats=stats)
        db = session_factory()
        try:
            rows = []
            for v in variants:
                post = CareerPost(
                    id=uuid.uuid4().hex[:12], owner=user, post_type=body.post_type, body=v["body"],
                    hooks=json.dumps(v["hooks"]), sources=json.dumps([i.to_dict() for i in safe[:MAX_STORED_SOURCES]]),
                    window_days=body.days, status="draft",
                )
                db.add(post)
                rows.append(post)
            db.commit()
            for r in rows:
                db.refresh(r)
            return {"posts": [post_to_dict(r) for r in rows], "warnings": warnings}
        finally:
            db.close()

    @router.get("/posts")
    def list_posts(request: Request, status: str = Query("")):
        user = owner_of(request)
        db = session_factory()
        try:
            q = db.query(CareerPost)
            if user is not None:
                q = q.filter(CareerPost.owner == user)
            q = q.filter(CareerPost.status == status) if status else q.filter(CareerPost.status != "discarded")
            return {"posts": [post_to_dict(p) for p in q.order_by(CareerPost.created_at.desc()).all()]}
        finally:
            db.close()

    @router.put("/posts/{pid}")
    def update_post(pid: str, body: PostUpdate, request: Request):
        db = session_factory()
        try:
            post = owned_post(db, pid, owner_of(request))
            if body.body is not None:
                post.body = body.body
            if body.status is not None:
                post.status = body.status
                if body.status == "posted":
                    post.posted_at = datetime.utcnow()
            db.commit()
            db.refresh(post)
            return post_to_dict(post)
        finally:
            db.close()

    @router.delete("/posts/{pid}")
    def discard_post(pid: str, request: Request):
        db = session_factory()
        try:
            post = owned_post(db, pid, owner_of(request))
            post.status = "discarded"
            db.commit()
            return {"id": pid, "status": "discarded"}
        finally:
            db.close()

    @router.put("/projects-disclosure")
    def set_disclosure(body: DisclosureUpdate, request: Request):
        user = owner_of(request)
        current = load_career_settings(user).get("project_disclosure") or {}
        current[body.project_id] = body.level
        return save_career_settings(user, {"project_disclosure": current})
