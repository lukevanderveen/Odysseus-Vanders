"""LinkedIn post drafting (plan 03): prompt from packaged templates, one
completion per variant, JSON-block parsing with a plain-text fallback.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from services.career.activity_sources import ActivityItem
from services.career.llm import complete as default_complete

POST_TYPES = ("progress", "technique", "lesson", "narrative")
MAX_ITEMS_IN_PROMPT = 40
_PROMPTS = Path(__file__).parent / "prompts"
_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)


def _read(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8").strip()


def _items_block(items: Iterable[ActivityItem]) -> str:
    lines = []
    for i in list(items)[:MAX_ITEMS_IN_PROMPT]:
        body = f" — {i.body}" if i.body else ""
        lines.append(f"- [{i.source}/{i.kind}] {i.date[:10]} {i.project_name}: {i.title}{body}")
    return "\n".join(lines)


def build_post_messages(items: Iterable[ActivityItem], post_type: str, settings: Dict[str, Any],
                        tracker_stats: Optional[Dict[str, int]] = None, variant: int = 1,
                        n_variants: int = 1) -> List[Dict[str, str]]:
    if post_type not in POST_TYPES:
        raise ValueError(f"post_type must be one of {POST_TYPES}")
    parts = [_read(f"post_{post_type}.md")]
    voice = (settings.get("voice_rules") or "").strip()
    if voice:
        parts.append(f"Author's voice rules:\n{voice}")
    if post_type == "narrative":
        roles = ", ".join(settings.get("target_roles") or []) or "software engineering roles"
        parts.append(f"Roles sought: {roles}.")
        # The route only passes stats when narrative_uses_tracker_stats is on.
        if tracker_stats:
            parts.append(f"Search so far: {tracker_stats.get('applications', 0)} applications, "
                         f"{tracker_stats.get('interviews', 0)} interviews.")
    if n_variants > 1:
        parts.append(f"Variant {variant} of {n_variants}: take a different angle from the other variants.")
    parts.append("Activity (already privacy-scrubbed):\n" + _items_block(items))
    return [{"role": "system", "content": _read("post_rules.md")},
            {"role": "user", "content": "\n\n".join(parts)}]


def parse_post_output(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    for candidate in _JSON_RE.findall(text):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("post"):
            hooks = [str(h).strip() for h in (data.get("hooks") or []) if str(h).strip()][:3]
            return {"body": str(data["post"]).strip(), "hooks": hooks}
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    return {"body": cleaned, "hooks": []}


async def draft_post(items: List[ActivityItem], post_type: str, settings: Dict[str, Any], owner: Optional[str],
                     complete: Callable = default_complete, n_variants: int = 2,
                     tracker_stats: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    if not items:
        raise ValueError("No activity in the selected window — widen the window or add a source")
    n = max(1, min(int(n_variants), 5))
    out = []
    for v in range(1, n + 1):
        messages = build_post_messages(items, post_type, settings, tracker_stats, variant=v, n_variants=n)
        out.append(parse_post_output(await complete(messages, owner)))
    return out


def post_to_dict(post) -> Dict[str, Any]:
    def _json(s):
        try:
            return json.loads(s) if s else []
        except (json.JSONDecodeError, TypeError):
            return []
    return {
        "id": post.id, "post_type": post.post_type, "body": post.body, "hooks": _json(post.hooks),
        "sources": _json(post.sources), "window_days": post.window_days, "status": post.status,
        "posted_at": post.posted_at.isoformat() if post.posted_at else None,
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }
