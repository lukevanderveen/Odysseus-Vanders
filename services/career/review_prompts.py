"""Message building for the reviewer panel: a detached snapshot of each
reviewer, the per-reviewer prompt, the panel synthesis prompt, and the
optional per-member model override (CrewMember.model / endpoint_url)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from core.database import CrewMember, SessionLocal
from services.career.review_scoring import dims_json_example
from services.career.seeds import REVIEWER_DEPARTMENTS, REVIEWER_NAMES
from services.career.templates import load_template, render_template

PROMPT_DIR = "reviewers/prompts"
CV_QUERY_CHARS = 400
MAX_CHARS = {"jd": 6000, "cover_letter": 6000, "cv_excerpt": 5000}


@dataclass
class ReviewContext:
    owner: Optional[str]
    company: str
    role: str
    jd: str
    cover_letter: str
    cv_excerpt: str
    members: List[Dict] = field(default_factory=list)   # snapshots, in REVIEWER_DEPARTMENTS order


def _clip(text: str, key: str) -> str:
    text = (text or "").strip()
    return text[: MAX_CHARS[key]] if len(text) > MAX_CHARS[key] else text


def snapshot_reviewers(owner: Optional[str], session_factory: Callable = None) -> List[Dict]:
    """Plain dicts so the runner can close its DB session before the long LLM calls."""
    factory = session_factory or SessionLocal
    db = factory()
    try:
        q = db.query(CrewMember).filter(CrewMember.department.in_(REVIEWER_DEPARTMENTS))
        if owner is not None:
            q = q.filter(CrewMember.owner == owner)
        rows = {m.department: m for m in q.all()}
        return [{
            "id": rows[d].id, "department": d,
            "name": rows[d].name or REVIEWER_NAMES[d],
            "personality": rows[d].personality or "",
            "model": rows[d].model or "", "endpoint_url": rows[d].endpoint_url or "",
        } for d in REVIEWER_DEPARTMENTS if d in rows]
    finally:
        db.close()


def build_context(owner, company, role, jd, cover_letter, cv_chunks: List[str], members) -> ReviewContext:
    return ReviewContext(
        owner=owner, company=company or "?", role=role or "?",
        jd=_clip(jd, "jd") or "(no job description on file)",
        cover_letter=_clip(cover_letter, "cover_letter"),
        cv_excerpt=_clip("\n\n".join(c for c in cv_chunks if c), "cv_excerpt") or "(no CV excerpt available)",
        members=list(members),
    )


def build_reviewer_messages(member: Dict, ctx: ReviewContext) -> list:
    prompt = render_template(load_template(PROMPT_DIR, "reviewer"), {
        "reviewer_name": member["name"], "company": ctx.company, "role": ctx.role,
        "jd": ctx.jd, "cover_letter": ctx.cover_letter, "cv_excerpt": ctx.cv_excerpt,
        "dims_json_example": dims_json_example(member["department"]),
    })
    system = member["personality"] or f"You are {member['name']} on a hiring panel."
    return [{"role": "system", "content": system}, {"role": "user", "content": prompt}]


def build_panel_messages(ctx: ReviewContext, verdicts: Dict[str, Dict]) -> list:
    blocks = []
    for dept in REVIEWER_DEPARTMENTS:
        v = verdicts.get(dept)
        if not v:
            continue
        blocks.append(f"## {v['name']} — verdict: {v['verdict']}\n{v['body']}")
    prompt = render_template(load_template(PROMPT_DIR, "panel"), {
        "company": ctx.company, "role": ctx.role,
        "reviews_markdown": "\n\n---\n\n".join(blocks) or "(no reviews completed)",
    })
    return [{"role": "user", "content": prompt}]


def resolve_task_endpoint(owner: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    """Module-level so tests can monkeypatch it (endpoint_resolver touches settings + DB)."""
    from src.endpoint_resolver import resolve_endpoint
    return resolve_endpoint("task", owner=owner)


def member_candidate(member: Dict, owner: Optional[str],
                     session_factory: Callable = None) -> Optional[Tuple[str, str, Dict]]:
    """(chat_url, model, headers) when the reviewer pins a model, else None.

    A pinned endpoint_url is a base URL (the Reviewers editor stores
    ModelEndpoint.base_url); its API key comes from the matching ModelEndpoint
    row. A pinned model without an endpoint runs on the Task endpoint."""
    model = (member.get("model") or "").strip()
    if not model:
        return None
    endpoint_url = (member.get("endpoint_url") or "").strip()
    if not endpoint_url:
        url, _default_model, headers = resolve_task_endpoint(owner)
        return (url, model, headers or {}) if url else None
    from core.database import ModelEndpoint
    from src.endpoint_resolver import build_chat_url, build_headers, normalize_base
    base = normalize_base(endpoint_url)
    factory = session_factory or SessionLocal
    db = factory()
    try:
        api_key = None
        for ep in db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all():  # noqa: E712
            if normalize_base(ep.base_url) == base:
                api_key = ep.api_key
                break
    finally:
        db.close()
    return (build_chat_url(base), model, build_headers(api_key, base))
