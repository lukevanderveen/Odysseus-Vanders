"""Job tracker pipeline: new inbox mail → prefilter → full fetch → classify →
match/create application → event → reply draft → notification.

Everything external is injectable (fetch, complete, session_factory, notify)
so the pipeline is unit-testable without IMAP or a model.
"""
from __future__ import annotations

import difflib
import logging
import re
import uuid
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional

from core.database import JobApplication, JobApplicationEvent, SessionLocal
from services.career.email_classifier import classify, is_ats_sender, prefilter
from services.career.tracker_fetch import fetch_full
from services.career.tracker_replies import reply_address, should_suggest_reply, suggest_reply, write_cached_reply

logger = logging.getLogger(__name__)

STATUS_FOR_KIND = {
    "confirmation": "acknowledged",
    "interview_invite": "interview",
    "offer": "offer",
    "rejection": "rejected",
    "info_request": "screening",
}
CREATE_KINDS = {"confirmation", "interview_invite", "offer", "rejection", "recruiter_outreach"}
MIN_CONFIDENCE = 60
_FROZEN_STATUSES = {"withdrawn", "offer"}
_SUFFIX_RE = re.compile(r"\b(ltd|limited|inc|plc|llc|gmbh|co|corp|corporation|group|holdings)\b\.?", re.I)


def normalise_company(name: str) -> str:
    s = _SUFFIX_RE.sub(" ", (name or "").lower())
    s = re.sub(r"[^a-z0-9 ]+", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def match_application(db, owner: Optional[str], company: str, role: str,
                      message_refs: List[str]) -> Optional[JobApplication]:
    refs = [r.strip() for r in (message_refs or []) if r and r.strip()]
    if refs:
        eq = db.query(JobApplicationEvent).filter(JobApplicationEvent.message_id.in_(refs),
                                                  JobApplicationEvent.application_id.isnot(None))
        if owner is not None:
            eq = eq.filter(JobApplicationEvent.owner == owner)
        hit = eq.first()
        if hit:
            app = db.get(JobApplication, hit.application_id)
            if app and not app.archived:
                return app
    key = normalise_company(company)
    if not key:
        return None
    q = db.query(JobApplication).filter(JobApplication.archived == False)  # noqa: E712
    if owner is not None:
        q = q.filter(JobApplication.owner == owner)
    same = [a for a in q.all() if normalise_company(a.company) == key]
    if not same:
        return None
    if len(same) == 1 or not role:
        return same[0]
    scored = sorted(same, key=lambda a: difflib.SequenceMatcher(None, role.lower(), (a.role or "").lower()).ratio(), reverse=True)
    best = scored[0]
    ratio = difflib.SequenceMatcher(None, role.lower(), (best.role or "").lower()).ratio()
    return best if ratio >= 0.6 else same[0]


def _parse_date(raw: str) -> Optional[datetime]:
    try:
        dt = parsedate_to_datetime(raw)
        return dt.replace(tzinfo=None) if dt else None
    except Exception:
        return None


def record_event(db, owner: Optional[str], app: Optional[JobApplication], verdict: Dict[str, Any],
                 headers: Dict[str, str], *, uid: str, folder: str, account_id: Optional[str],
                 can_reply: bool, reply_blocked_reason: Optional[str]) -> Optional[JobApplicationEvent]:
    message_id = (headers.get("message_id") or "").strip() or None
    if message_id:
        dq = db.query(JobApplicationEvent).filter(JobApplicationEvent.message_id == message_id)
        if owner is not None:
            dq = dq.filter(JobApplicationEvent.owner == owner)
        if dq.first():
            return None
    ev = JobApplicationEvent(
        id=uuid.uuid4().hex[:12], owner=owner, application_id=app.id if app else None,
        kind=verdict.get("kind", "other"), sentiment=verdict.get("sentiment", "neutral"),
        message_id=message_id, uid=str(uid or ""), folder=folder, account_id=account_id,
        from_address=parseaddr(headers.get("from") or headers.get("from_address") or "")[1] or headers.get("from_address"),
        reply_to=parseaddr(headers.get("reply_to") or "")[1] or None,
        subject=headers.get("subject"), received_at=_parse_date(headers.get("date") or "") or datetime.utcnow(),
        summary=verdict.get("summary"), can_reply=bool(can_reply), reply_blocked_reason=reply_blocked_reason,
        confidence=int(verdict.get("confidence") or 0),
    )
    db.add(ev)
    if app:
        app.last_event_at = ev.received_at
        new_status = STATUS_FOR_KIND.get(ev.kind)
        if new_status and app.status not in _FROZEN_STATUSES:
            app.status = new_status
    db.commit()
    db.refresh(ev)
    return ev


def _default_notify(**kw):
    from src.event_bus import get_task_scheduler
    scheduler = get_task_scheduler()
    if scheduler is None:
        logger.info("Tracker notification dropped (no scheduler): %s", kw.get("body"))
        return
    scheduler.add_notification(**kw)


def _resolve_application(db, owner: str, verdict: Dict[str, Any], headers: Dict[str, str], refs: List[str]):
    """Matched or newly created application (only for confident, creatable kinds)."""
    app = match_application(db, owner, verdict["company"], verdict["role"], refs)
    if app is not None:
        return app, False
    if verdict["confidence"] < MIN_CONFIDENCE or verdict["kind"] not in CREATE_KINDS or not verdict["company"]:
        return None, False
    app = JobApplication(id=uuid.uuid4().hex[:12], owner=owner, company=verdict["company"],
                         role=verdict["role"] or "Unknown role", source="email", status="applied",
                         applied_at=_parse_date(headers.get("date") or "") or datetime.utcnow())
    db.add(app)
    db.flush()
    return app, True


async def _draft_reply(db, owner: str, app: JobApplication, ev: JobApplicationEvent, verdict: Dict[str, Any],
                       body: str, settings: Dict, complete: Callable) -> None:
    if not should_suggest_reply(ev.kind, ev.sentiment, ev.can_reply):
        return
    try:
        ctx = {"kind": ev.kind, "company": app.company, "role": app.role, "subject": ev.subject or "",
               "summary": ev.summary or "", "next_step": verdict.get("next_step", ""), "body": body}
        ev.suggested_reply = await suggest_reply(ctx, settings, complete, owner)
        db.commit()
        write_cached_reply(ev.message_id or "", ev.suggested_reply, uid=ev.uid or "", folder=ev.folder or "INBOX")
    except Exception:
        logger.warning("DEGRADED: reply suggestion failed for %s", ev.message_id, exc_info=True)


def _notify_positive(notify: Callable, owner: str, app: JobApplication, ev: JobApplicationEvent) -> None:
    if ev.sentiment != "positive":
        return
    try:
        notify(task_name="Career tracker", status="success", owner=owner,
               body=f"{app.company} — {app.role}: {ev.kind.replace('_', ' ')}. {ev.summary or ''}".strip())
    except Exception:
        logger.debug("tracker notification failed", exc_info=True)


def _should_skip(headers: Dict[str, str]) -> bool:
    """Self-sent (Odysseus UI) mail and non-ATS bulk mail never reach the model."""
    if headers.get("x_odysseus_origin"):
        return True
    return bool(headers.get("list_unsubscribe")) and not is_ats_sender(headers.get("from_address"))


async def process_message(owner: str, account_id: Optional[str], list_dict: Dict[str, Any], *,
                          complete: Optional[Callable] = None, complete_utility: Optional[Callable] = None,
                          session_factory: Optional[Callable] = None, fetch: Optional[Callable] = None,
                          settings: Optional[Dict] = None, notify: Optional[Callable] = None) -> Optional[Dict[str, Any]]:
    if settings is None:
        from services.career.settings import load_career_settings
        settings = load_career_settings(owner)
    if not prefilter(list_dict, extra_terms=settings.get("prefilter_terms") or ()):
        return None
    if complete is None or complete_utility is None:
        from services.career import llm as _llm
        complete = complete or _llm.complete
        complete_utility = complete_utility or _llm.complete_utility
    folder = list_dict.get("folder") or "INBOX"
    full = await (fetch or fetch_full)(owner, account_id, str(list_dict.get("uid") or ""), folder)
    headers, body = full["headers"], full["body"]
    if _should_skip(headers):
        return None
    verdict = await classify(headers, body, complete_utility, owner)
    if not verdict["is_job_related"]:
        return None
    if verdict["confidence"] < MIN_CONFIDENCE:
        verdict = {**verdict, "kind": "other"}

    addr, blocked = reply_address(headers, body)
    refs = [headers.get("in_reply_to") or "", *(headers.get("references") or "").split()]
    db = (session_factory or SessionLocal)()
    try:
        app, created = _resolve_application(db, owner, verdict, headers, refs)
        ev = record_event(db, owner, app, verdict, headers, uid=list_dict.get("uid") or "", folder=folder,
                          account_id=account_id, can_reply=addr is not None, reply_blocked_reason=blocked)
        if ev is None:
            return None
        if app:
            await _draft_reply(db, owner, app, ev, verdict, body, settings, complete)
            _notify_positive(notify or _default_notify, owner, app, ev)
        return {"event_id": ev.id, "application_id": app.id if app else None, "created_application": created,
                "kind": ev.kind, "sentiment": ev.sentiment}
    finally:
        db.close()


async def on_new_inbox_messages(owner: str, account_id: Optional[str], fresh: List[Dict[str, Any]]) -> None:
    for list_dict in fresh or []:
        try:
            await process_message(owner, account_id, list_dict)
        except Exception:
            logger.warning("tracker: message %s skipped", list_dict.get("uid"), exc_info=True)


def register() -> None:
    from routes.email_routes import register_new_mail_subscriber
    register_new_mail_subscriber(on_new_inbox_messages)
