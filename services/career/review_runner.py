"""Reviewer panel runner (plan 02).

One review at a time on its own semaphore (independent of the council queue):
four reviewer completions (each with its persona as the system prompt) then a
chaired panel synthesis. Results land on ApplicationReview; the application's
latest_review_id is updated; a notification is queued. Never raises into the
caller — failures land as status="error".
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Callable, Dict, Optional

from core.database import ApplicationReview, Document, JobApplication, SessionLocal
from services.career.review_prompts import (
    build_context, build_panel_messages, build_reviewer_messages, member_candidate, snapshot_reviewers,
)
from services.career.review_scoring import (
    REVIEW_DIMS, aggregate_panel, parse_panel_verdict, parse_review_block, strip_json_fence,
)
from services.career.seeds import seed_reviewers

logger = logging.getLogger(__name__)

_run_semaphore = asyncio.Semaphore(1)
_queued: list = []
_current: Optional[str] = None

LLM_TIMEOUT = 900
MAX_TOKENS = 3000


def queue_state() -> Dict:
    return {"running": _current, "queued": list(_queued)}


async def _default_complete(messages, owner):
    from services.career.llm import complete
    return await complete(messages, owner)


def _default_evidence(owner, query, k=8):
    from services.career.evidence import cv_chunks
    return cv_chunks(owner, query, k)


def _default_notify(**kw):
    from src.event_bus import get_task_scheduler
    scheduler = get_task_scheduler()
    if scheduler is None:
        logger.info("Review notification dropped (no scheduler): %s", kw.get("task_name"))
        return
    scheduler.add_notification(**kw)


async def _call(member: Optional[Dict], messages: list, complete, owner, session_factory) -> str:
    """Reviewer with a pinned model → direct call; otherwise the injected/default completer."""
    candidate = member_candidate(member, owner, session_factory) if member else None
    if candidate and complete is None:
        from src.llm_core import llm_call_async_with_fallback
        return await llm_call_async_with_fallback([candidate], messages, timeout=LLM_TIMEOUT, max_tokens=MAX_TOKENS)
    if complete is None:
        return await _default_complete(messages, owner)
    return await complete(messages)


def _finish(db, review: ApplicationReview, *, status: str, verdicts=None, panel_summary=None,
            scores=None, error=None):
    review.status = status
    review.verdicts = json.dumps(verdicts) if verdicts is not None else None
    review.panel_summary = panel_summary
    review.scores = json.dumps(scores) if scores is not None else None
    review.error = error
    review.finished_at = datetime.utcnow()
    db.commit()


def _load_context(db, review: ApplicationReview, evidence, session_factory):
    app = db.get(JobApplication, review.application_id)
    if not app:
        raise ValueError("Application not found")
    doc = db.get(Document, app.cover_letter_doc_id) if app.cover_letter_doc_id else None
    if not doc or not (doc.current_content or "").strip():
        raise ValueError("No cover letter to review — draft one first")
    seed_reviewers(app.owner, session_factory=session_factory)
    members = snapshot_reviewers(app.owner, session_factory)
    if not members:
        raise ValueError("No reviewers configured")
    query = f"{app.role} at {app.company}: {(app.jd_text or '')[:400]}"
    try:
        chunks = evidence(app.owner, query, 8) or []
    except Exception:
        logger.warning("DEGRADED: CV evidence unavailable for review %s", review.id, exc_info=True)
        chunks = []
    return app.owner, build_context(app.owner, app.company, app.role, app.jd_text, doc.current_content, chunks, members)


async def _run_panel(ctx, complete, owner, session_factory) -> tuple:
    verdicts: Dict[str, Dict] = {}
    for member in ctx.members:
        raw = await _call(member, build_reviewer_messages(member, ctx), complete, owner, session_factory)
        parsed = parse_review_block(raw, member["department"])
        verdicts[member["department"]] = {
            "name": member["name"], "verdict": parsed["verdict"],
            "body": strip_json_fence(raw), "scores": parsed["scores"],
        }
    panel_raw = await _call(None, build_panel_messages(ctx, verdicts), complete, owner, session_factory)
    scores = aggregate_panel(verdicts)
    scores["panel_verdict"] = parse_panel_verdict(panel_raw)
    return verdicts, strip_json_fence(panel_raw), scores


async def _under_queue(review_id: str, work):
    global _current
    _queued.append(review_id)
    try:
        async with _run_semaphore:
            _queued.remove(review_id)
            _current = review_id
            try:
                return await work()
            finally:
                _current = None
    except Exception:
        if review_id in _queued:
            _queued.remove(review_id)
        raise


async def run_review(review_id: str, complete: Optional[Callable] = None, session_factory: Callable = None,
                     evidence: Optional[Callable] = None, notify: Optional[Callable] = None) -> Dict:
    factory = session_factory or SessionLocal
    evidence = evidence or _default_evidence
    notify = notify or _default_notify
    db = factory()
    try:
        review = db.get(ApplicationReview, review_id)
        if not review:
            return {"status": "error", "review_id": review_id, "error": "Review not found"}
        review.started_at = datetime.utcnow()
        db.commit()
        try:
            owner, ctx = _load_context(db, review, evidence, factory)
        except ValueError as e:
            _finish(db, review, status="error", error=str(e))
            return {"status": "error", "review_id": review_id, "error": str(e)}
    finally:
        db.close()

    try:
        verdicts, panel_summary, scores = await _under_queue(
            review_id, lambda: _run_panel(ctx, complete, owner, factory))
    except Exception as e:
        logger.warning("Review %s failed: %s", review_id, e)
        db = factory()
        try:
            _finish(db, db.get(ApplicationReview, review_id), status="error", error=str(e)[:500])
        finally:
            db.close()
        return {"status": "error", "review_id": review_id, "error": str(e)}

    db = factory()
    try:
        review = db.get(ApplicationReview, review_id)
        _finish(db, review, status="done", verdicts=verdicts, panel_summary=panel_summary, scores=scores)
        app = db.get(JobApplication, review.application_id)
        app.latest_review_id = review_id
        db.commit()
        company, role = app.company, app.role
    finally:
        db.close()
    try:
        notify(task_name="Career review", status="success", task_id=review_id, owner=owner,
               body=f"Panel verdict for {company} — {role}: {scores['panel_verdict']} (overall {scores['overall']})")
    except Exception:
        logger.debug("review notify failed", exc_info=True)
    return {"status": "done", "review_id": review_id}
