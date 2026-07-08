"""Council report runner (Agent Council Phase 2).

Runs one report end-to-end: build the prompt from the report-type template +
project context (deep summary, RAG snippets, grounded scores), call the
report model, parse judged scores, and land the result in the approval queue
(``Report.status = "draft"``). Errors persist as ``status = "error"`` with
detail — never silent.

Local model runs serialise on a single global queue (one GPU); the LLM call,
DB session factory, RAG retrieval, and notifier are all injectable for tests.
Templates: ``data/council/report_types/<type>.md`` (user-editable override)
falls back to the packaged default in ``services/council/report_types/``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import re
import uuid
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.database import Project, Report, SessionLocal
from services.council.scoring import (
    aggregate_scores,
    format_signals,
    grounded_scores,
    parse_judged_scores,
)

logger = logging.getLogger(__name__)

# Canonical component report types → (department, title prefix). Single source
# of truth: the HTTP route's single-report allowlist imports this, and the
# full-council orchestrator runs exactly these five before synthesising. The
# meta ``council_review`` type is deliberately absent — it is only ever created
# by the orchestrator, never the standard create-report path.
COMPONENT_TYPES = {
    "developer_review": ("developer", "Developer Review"),
    "market_opportunity": ("research", "Market Opportunity Report"),
    "launch_strategy": ("business", "Launch Strategy"),
    "architecture_review": ("architect", "Architecture Review"),
    "marketing_audit": ("marketing", "Marketing Audit"),
}

# One report at a time: summaries and reviews serialise on a single GPU.
# Queue state is introspectable for the council dashboard (Busy/Waiting/Ready).
_run_semaphore = asyncio.Semaphore(1)
_queued: list = []          # report ids waiting on the semaphore, FIFO
_current: Optional[str] = None


def queue_state() -> Dict:
    """Snapshot for /api/council/status: the running report id and the ids
    still waiting, in queue order."""
    return {"running": _current, "queued": list(_queued)}

_PACKAGED_TEMPLATE_DIR = Path(__file__).parent / "report_types"

RAG_SNIPPET_COUNT = 6
_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*\{[^`]*\}\s*```\s*$", re.S)

# Real repo evidence cap for the review prompt (file tree + key configs).
COUNCIL_CONTEXT_CHARS = 8000

# Targeted retrieval: one query per review concern beats a single generic sweep,
# so the excerpts actually cover what the review judges (tests, deps, design…).
CONCERN_QUERIES = (
    "tests and CI configuration",
    "build, dependencies, and packaging",
    "entry points and application architecture",
    "error handling and edge cases",
)


def _template_dirs():
    from src.constants import DATA_DIR
    return (Path(DATA_DIR) / "council" / "report_types", _PACKAGED_TEMPLATE_DIR)


def load_template(report_type: str) -> str:
    for d in _template_dirs():
        f = d / f"{report_type}.md"
        if f.is_file():
            try:
                return f.read_text(encoding="utf-8")
            except OSError:
                continue
    raise FileNotFoundError(f"No template for report type '{report_type}'")


def render_template(template: str, mapping: Dict[str, str]) -> str:
    """Replace only known ``{key}`` tokens — report bodies and code excerpts
    are full of braces, so str.format would explode."""
    out = template
    for key, val in mapping.items():
        out = out.replace("{" + key + "}", val)
    return out


def _default_retrieve(project_id: str, query: str, n: int = RAG_SNIPPET_COUNT) -> str:
    """Top chunks from the project's Chroma collection; degrades to a notice."""
    try:
        from services.projects.indexer import collection_name
        from src.chroma_client import get_chroma_client
        from src.embeddings import get_embedding_client

        collection = get_chroma_client().get_collection(collection_name(project_id))
        vec = get_embedding_client().encode([query], normalize_embeddings=True)
        res = collection.query(query_embeddings=[list(map(float, vec[0]))], n_results=n)
        docs = (res.get("documents") or [[]])[0]
        metas = (res.get("metadatas") or [[]])[0]
        parts = []
        for doc, meta in zip(docs, metas):
            parts.append(f"### {(meta or {}).get('file', '?')}\n{doc}")
        return "\n\n".join(parts) or "(project index is empty)"
    except Exception as e:
        logger.warning("DEGRADED: RAG snippets for %s unavailable: %s", project_id, e)
        return "(project index unavailable)"


async def _default_complete(messages, owner: Optional[str] = None) -> str:
    """Report runs resolve the Task model chain (agent-shaped work — cloud
    model when configured), falling back through utility → default."""
    from src.endpoint_resolver import resolve_endpoint, resolve_utility_fallback_candidates
    from src.llm_core import llm_call_async_with_fallback

    url, model, headers = resolve_endpoint("task", owner=owner)
    candidates = ([(url, model, headers)] if url and model else [])
    candidates += resolve_utility_fallback_candidates(owner)
    return await llm_call_async_with_fallback(
        candidates, messages, timeout=900, max_tokens=4000,
    )


def _default_notify(**kw):
    from src.event_bus import get_task_scheduler
    scheduler = get_task_scheduler()
    if scheduler is None:
        logger.info("Report notification dropped (no scheduler): %s", kw.get("task_name"))
        return
    scheduler.add_notification(**kw)


def _strip_score_fence(text: str) -> str:
    """Remove the trailing judged-scores fence from the body — the scores
    column carries it; the body should read as the report."""
    return _JSON_FENCE_RE.sub("", text).strip()


def _repo_context(project_path) -> str:
    """Deterministic repo evidence — file tree + key configs — so the review
    reads real structure instead of guessing at it. Reuses the deep-index
    context pack; degrades to a notice rather than failing the report."""
    try:
        from services.projects.deep_index import build_context
        return build_context(project_path, max_chars=COUNCIL_CONTEXT_CHARS)
    except Exception as e:
        logger.warning("DEGRADED: repo context unavailable for %s: %s", project_path, e)
        return "(repository context unavailable)"


def _gather_evidence(project_id: str, name: str, retrieve: Callable) -> str:
    """Retrieve excerpts per review concern (not one generic query), labelled by
    concern and de-duplicated, so the evidence covers what's being judged."""
    seen = set()
    blocks = []
    for concern in CONCERN_QUERIES:
        try:
            text = (retrieve(project_id, f"{concern} for {name}") or "").strip()
        except Exception:
            continue
        if not text or text in seen:
            continue
        seen.add(text)
        blocks.append(f"### Concern: {concern}\n{text}")
    return "\n\n".join(blocks) or "(project index unavailable)"


def build_messages(report: Report, project: Project, grounded: Dict,
                   retrieve: Callable, context_builder: Optional[Callable] = None) -> list:
    try:
        stack = ", ".join(json.loads(project.stack)) if project.stack else "unclear"
    except (json.JSONDecodeError, TypeError):
        stack = "unclear"
    template = load_template(report.report_type or "developer_review")
    builder = context_builder or _repo_context
    prompt = render_template(template, {
        "project_name": project.name or "?",
        "stack": stack,
        "verified_facts": format_signals(grounded.get("_signals") or {}),
        "repo_context": builder(project.path),
        "deep_summary": project.deep_summary or "(no deep index yet — run Deep Index for a richer review)",
        "rag_snippets": _gather_evidence(project.id, project.name or "?", retrieve),
    })
    return [{"role": "user", "content": prompt}]


def build_synthesis_messages(report: Report, project: Project,
                             components: List[Dict]) -> list:
    """Council-review prompt: hand the synthesiser every department's review
    (its body and scores) under the ``{member_reports}`` token."""
    try:
        stack = ", ".join(json.loads(project.stack)) if project.stack else "unclear"
    except (json.JSONDecodeError, TypeError):
        stack = "unclear"
    blocks = []
    for c in components:
        dept = c.get("department", "?")
        scores = json.dumps(c.get("scores") or {}, default=str)
        blocks.append(f"### {dept} department\n{c.get('body') or '(no body)'}\n\nScores: {scores}")
    member_reports = "\n\n---\n\n".join(blocks) or "(no department reviews completed)"
    # Reuse a component's already-computed signals — no extra file walk.
    signals = next(
        ((((c.get("scores") or {}).get("grounded") or {}).get("_signals"))
         for c in components
         if (((c.get("scores") or {}).get("grounded") or {}).get("_signals"))),
        {},
    )
    template = load_template("council_review")
    prompt = render_template(template, {
        "project_name": project.name or "?",
        "stack": stack,
        "verified_facts": format_signals(signals),
        "member_reports": member_reports,
    })
    return [{"role": "user", "content": prompt}]


def _finish(db, report: Report, *, status: str, body: Optional[str] = None,
            scores: Optional[Dict] = None, error: Optional[str] = None):
    report.status = status
    report.body = body
    report.scores = json.dumps(scores) if scores is not None else None
    report.error = error
    report.finished_at = datetime.utcnow()
    db.commit()


async def _complete_under_queue(report_id: str, messages: list,
                                complete: Optional[Callable], owner: Optional[str]) -> str:
    """Run one completion on the single-GPU queue, tracking running/queued
    state for the dashboard. Cleans the queue and re-raises on failure."""
    global _current
    _queued.append(report_id)
    try:
        async with _run_semaphore:
            _queued.remove(report_id)
            _current = report_id
            try:
                if complete is None:
                    return await _default_complete(messages, owner=owner)
                return await complete(messages)
            finally:
                _current = None
    except Exception:
        if report_id in _queued:   # failed before the semaphore was acquired
            _queued.remove(report_id)
        raise


async def run_report(
    report_id: str,
    complete: Optional[Callable] = None,
    session_factory: Callable = None,
    retrieve: Callable = _default_retrieve,
    notify: Callable = _default_notify,
    context_builder: Optional[Callable] = None,
) -> Dict:
    """Run one report to completion. Returns ``{"status": "draft"|"error", ...}``
    — never raises into the caller."""
    factory = session_factory or SessionLocal
    db = factory()
    try:
        report = db.query(Report).filter(Report.id == report_id).first()
        if not report:
            return {"status": "error", "error": "report not found"}
        project = db.query(Project).filter(Project.id == report.project_id).first()
        if not project:
            _finish(db, report, status="error", error="project row vanished")
            return {"status": "error", "error": "project row vanished"}

        owner, title = report.owner, report.title
        report.started_at = datetime.utcnow()
        db.commit()

        grounded = grounded_scores(project.path)
        messages = build_messages(report, project, grounded, retrieve, context_builder)

        try:
            raw = await _complete_under_queue(report_id, messages, complete, owner)
        except Exception as e:
            logger.warning("Report %s failed: %s", report_id, e)
            _finish(db, report, status="error", error=str(e))
            notify(task_name=title, status="failed", task_id=report_id, owner=owner)
            return {"status": "error", "error": str(e)}

        raw = (raw or "").strip()
        if not raw:
            _finish(db, report, status="error", error="model returned an empty report")
            notify(task_name=title, status="failed", task_id=report_id, owner=owner)
            return {"status": "error", "error": "model returned an empty report"}

        scores = {"grounded": grounded, "judged": parse_judged_scores(raw)}
        _finish(db, report, status="draft", body=_strip_score_fence(raw), scores=scores)
        notify(task_name=title, status="completed", task_id=report_id, owner=owner,
               body="Report drafted — awaiting your approval.")
        return {"status": "draft", "report_id": report_id}
    finally:
        db.close()


async def run_council_review(
    report_id: str,
    components: List[Dict],
    complete: Optional[Callable] = None,
    session_factory: Callable = None,
    notify: Callable = _default_notify,
) -> Dict:
    """Synthesise the component reviews into one council report. Scores are the
    aggregate of the components (not re-judged); the body is the unified report.
    Shares the single-GPU queue with ``run_report``. Never raises."""
    factory = session_factory or SessionLocal
    db = factory()
    try:
        report = db.query(Report).filter(Report.id == report_id).first()
        if not report:
            return {"status": "error", "error": "report not found"}
        project = db.query(Project).filter(Project.id == report.project_id).first()
        if not project:
            _finish(db, report, status="error", error="project row vanished")
            return {"status": "error", "error": "project row vanished"}

        owner, title = report.owner, report.title
        report.started_at = datetime.utcnow()
        db.commit()

        messages = build_synthesis_messages(report, project, components)
        try:
            raw = await _complete_under_queue(report_id, messages, complete, owner)
        except Exception as e:
            logger.warning("Council review %s failed: %s", report_id, e)
            _finish(db, report, status="error", error=str(e))
            notify(task_name=title, status="failed", task_id=report_id, owner=owner)
            return {"status": "error", "error": str(e)}

        raw = (raw or "").strip()
        if not raw:
            _finish(db, report, status="error", error="model returned an empty report")
            notify(task_name=title, status="failed", task_id=report_id, owner=owner)
            return {"status": "error", "error": "model returned an empty report"}

        scores = aggregate_scores([c.get("scores") for c in components])
        _finish(db, report, status="draft", body=_strip_score_fence(raw), scores=scores)
        notify(task_name=title, status="completed", task_id=report_id, owner=owner,
               body="Council review drafted — awaiting your approval.")
        return {"status": "draft", "report_id": report_id}
    finally:
        db.close()


def _create_report_row(factory, *, owner, project_id, department, report_type, title) -> str:
    rid = uuid.uuid4().hex[:12]
    db = factory()
    try:
        db.add(Report(id=rid, owner=owner, project_id=project_id, department=department,
                      report_type=report_type, title=title, status="running"))
        db.commit()
    finally:
        db.close()
    return rid


async def run_full_council(
    project_id: str,
    owner: Optional[str],
    complete: Optional[Callable] = None,
    session_factory: Callable = None,
    retrieve: Callable = _default_retrieve,
    notify: Callable = _default_notify,
    context_builder: Optional[Callable] = None,
) -> Dict:
    """Run every department review for a project, then synthesise them into one
    council report. Component reviews run sequentially on the shared queue and
    are kept (``approved``) so they fill the project's tabs without flooding the
    approval inbox; only the synthesis lands as a ``draft`` to approve."""
    factory = session_factory or SessionLocal
    db = factory()
    try:
        project = db.query(Project).filter(Project.id == project_id).first()
        if not project:
            return {"status": "error", "error": "project not found"}
        project_name = project.name or "?"
    finally:
        db.close()

    components: List[Dict] = []
    for report_type, (department, prefix) in COMPONENT_TYPES.items():
        rid = _create_report_row(factory, owner=owner, project_id=project_id,
                                 department=department, report_type=report_type,
                                 title=f"{prefix}: {project_name}")
        await run_report(rid, complete=complete, session_factory=factory,
                         retrieve=retrieve, notify=notify, context_builder=context_builder)
        db = factory()
        try:
            row = db.get(Report, rid)
            if row and row.status == "draft":
                row.status = "approved"   # kept as a tab, not an inbox item
                db.commit()
                components.append({
                    "department": department,
                    "title": row.title,
                    "body": row.body,
                    "scores": json.loads(row.scores) if row.scores else {},
                })
        finally:
            db.close()

    rid = _create_report_row(factory, owner=owner, project_id=project_id,
                             department="council", report_type="council_review",
                             title=f"Council Review: {project_name}")
    syn = await run_council_review(rid, components, complete=complete,
                                   session_factory=factory, notify=notify)
    return {"status": syn.get("status"), "report_id": rid, "components": len(components)}
