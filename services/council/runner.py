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
from datetime import datetime
from pathlib import Path
from typing import Callable, Dict, Optional

from core.database import Project, Report, SessionLocal
from services.council.scoring import grounded_scores, parse_judged_scores

logger = logging.getLogger(__name__)

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


def build_messages(report: Report, project: Project, grounded: Dict,
                   retrieve: Callable) -> list:
    try:
        stack = ", ".join(json.loads(project.stack)) if project.stack else "unclear"
    except (json.JSONDecodeError, TypeError):
        stack = "unclear"
    template = load_template(report.report_type or "developer_review")
    snippets = retrieve(project.id, f"architecture, entry points, tests for {project.name}")
    prompt = render_template(template, {
        "project_name": project.name or "?",
        "stack": stack,
        "grounded_scores": json.dumps(grounded, indent=2, default=str),
        "deep_summary": project.deep_summary or "(no deep index yet — run Deep Index for a richer review)",
        "rag_snippets": snippets,
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


async def run_report(
    report_id: str,
    complete: Optional[Callable] = None,
    session_factory: Callable = None,
    retrieve: Callable = _default_retrieve,
    notify: Callable = _default_notify,
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
        messages = build_messages(report, project, grounded, retrieve)

        global _current
        _queued.append(report_id)
        try:
            async with _run_semaphore:
                _queued.remove(report_id)
                _current = report_id
                try:
                    if complete is None:
                        raw = await _default_complete(messages, owner=owner)
                    else:
                        raw = await complete(messages)
                finally:
                    _current = None
        except Exception as e:
            if report_id in _queued:   # failed before the semaphore was acquired
                _queued.remove(report_id)
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
