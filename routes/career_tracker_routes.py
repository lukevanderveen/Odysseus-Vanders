"""Career tracker API — dashboard summary, unsorted triage, backfill, nudges.

Registers the tracker as a new-mail subscriber at setup so the live path is
active as soon as the router is mounted.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.database import JobApplication, JobApplicationEvent, SessionLocal
from routes.career_helpers import application_to_dict, computed_status, days_since_event, get_owned_application
from services.career.settings import load_career_settings
from services.career.tracker import fetch_full, record_event, register
from services.career.tracker_replies import suggest_reply
from services.career.tracker_scan import scan_backfill
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

_ACTIVE = {"applied", "acknowledged", "screening", "interview", "offer"}
_AWAITING = {"applied", "acknowledged", "screening"}
_CLOSED = {"rejected", "withdrawn", "ghosted"}


class ScanRequest(BaseModel):
    days: int = Field(30, ge=1, le=365)


class AssignRequest(BaseModel):
    application_id: str


class LinkEmailRequest(BaseModel):
    application_id: str
    uid: str
    folder: str = "INBOX"
    account_id: Optional[str] = None


def _iso(dt: Optional[datetime]) -> str:
    return dt.isoformat() if dt else ""


def event_to_dict(ev: JobApplicationEvent, app: Optional[JobApplication] = None) -> Dict[str, Any]:
    return {
        "id": ev.id, "application_id": ev.application_id, "kind": ev.kind, "sentiment": ev.sentiment,
        "message_id": ev.message_id, "uid": ev.uid, "folder": ev.folder or "INBOX", "account_id": ev.account_id,
        "from_address": ev.from_address, "reply_to": ev.reply_to, "subject": ev.subject,
        "received_at": _iso(ev.received_at), "summary": ev.summary, "can_reply": bool(ev.can_reply),
        "reply_blocked_reason": ev.reply_blocked_reason, "suggested_reply": ev.suggested_reply,
        "confidence": ev.confidence or 0, "company": app.company if app else None, "role": app.role if app else None,
    }


def build_summary(db, user: Optional[str], settings: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    aq = db.query(JobApplication).filter(JobApplication.archived == False)  # noqa: E712
    eq = db.query(JobApplicationEvent)
    if user is not None:
        aq = aq.filter(JobApplication.owner == user)
        eq = eq.filter(JobApplicationEvent.owner == user)
    apps = aq.all()
    events = eq.order_by(JobApplicationEvent.received_at.desc()).all()
    by_id = {a.id: a for a in apps}
    with_events = {e.application_id for e in events if e.application_id}

    tiles = {"active": 0, "awaiting": 0, "interviews": 0, "offers": 0, "rejected": 0}
    awaiting, closed = [], []
    nudge_days = int(settings.get("nudge_after_days", 10))
    for a in apps:
        st = computed_status(a, settings, now)
        tiles["active"] += st in _ACTIVE
        tiles["awaiting"] += st in _AWAITING
        tiles["interviews"] += st == "interview"
        tiles["offers"] += st == "offer"
        tiles["rejected"] += st == "rejected"
        d = application_to_dict(a, settings, now)
        if st in _AWAITING:
            days = days_since_event(a, now)
            d["nudge_due"] = days is not None and days >= nudge_days
            awaiting.append(d)
        elif st in _CLOSED:
            closed.append(d)
    sent = [a for a in apps if a.status != "drafting"]
    tiles["response_rate"] = round(100 * len([a for a in sent if a.id in with_events]) / len(sent)) if sent else 0
    awaiting.sort(key=lambda d: -(d["days_since_event"] or 0))

    stamps = [e.created_at for e in events if e.created_at] + [a.updated_at for a in apps if a.updated_at]
    return {
        "tiles": tiles,
        "recent": [event_to_dict(e, by_id.get(e.application_id)) for e in events if e.application_id][:20],
        "awaiting": awaiting, "closed": closed,
        "unsorted": [event_to_dict(e) for e in events if not e.application_id],
        "updated_at": _iso(max(stamps)) if stamps else "",
    }


def setup_career_tracker_routes() -> APIRouter:
    router = APIRouter(prefix="/api/career/tracker", tags=["career"])
    register()

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _owned_event(db, eid: str, user: Optional[str]) -> JobApplicationEvent:
        q = db.query(JobApplicationEvent).filter(JobApplicationEvent.id == eid)
        if user is not None:
            q = q.filter(JobApplicationEvent.owner == user)
        ev = q.first()
        if not ev:
            raise HTTPException(404, "Event not found")
        return ev

    @router.get("/summary")
    def summary(request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            return build_summary(db, user, load_career_settings(user))
        finally:
            db.close()

    @router.get("/events")
    def events(request: Request, application_id: str = Query(...)):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, application_id, user)
            rows = (db.query(JobApplicationEvent).filter(JobApplicationEvent.application_id == app.id)
                    .order_by(JobApplicationEvent.received_at.desc()).all())
            return {"events": [event_to_dict(e, app) for e in rows]}
        finally:
            db.close()

    @router.post("/scan")
    async def scan(body: ScanRequest, request: Request):
        user = _owner(request)
        return await scan_backfill(user or "", body.days)

    @router.post("/events/{eid}/assign")
    def assign(eid: str, body: AssignRequest, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            ev = _owned_event(db, eid, user)
            app = get_owned_application(db, body.application_id, user)
            ev.application_id = app.id
            app.last_event_at = ev.received_at or datetime.utcnow()
            db.commit()
            db.refresh(ev)
            return event_to_dict(ev, app)
        finally:
            db.close()

    @router.post("/events/{eid}/dismiss")
    def dismiss(eid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            ev = _owned_event(db, eid, user)
            db.delete(ev)
            db.commit()
            return {"id": eid, "dismissed": True}
        finally:
            db.close()

    @router.post("/applications/{aid}/nudge")
    async def nudge(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            ctx = {"kind": "nudge", "company": app.company, "role": app.role, "subject": "", "summary": "",
                   "next_step": "", "body": ""}
        finally:
            db.close()
        from services.career.llm import complete
        return {"reply": await suggest_reply(ctx, load_career_settings(user), complete, user)}

    @router.post("/link-email")
    async def link_email(body: LinkEmailRequest, request: Request):
        user = _owner(request)
        try:
            full = await fetch_full(user or "", body.account_id, body.uid, body.folder)
        except RuntimeError as e:
            raise HTTPException(404, str(e))
        db = SessionLocal()
        try:
            app = get_owned_application(db, body.application_id, user)
            verdict = {"kind": "other", "sentiment": "neutral", "confidence": 100, "summary": "Linked manually"}
            ev = record_event(db, user, app, verdict, full["headers"], uid=body.uid, folder=body.folder,
                              account_id=body.account_id, can_reply=False, reply_blocked_reason=None)
            if ev is None:
                raise HTTPException(409, "That email is already recorded")
            return event_to_dict(ev, app)
        finally:
            db.close()

    return router
