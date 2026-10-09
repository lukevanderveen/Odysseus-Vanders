"""Can this job email be answered, and what should the answer say?

``reply_address`` is Reply-To, else From, blocked for no-reply senders, bulk
mail, and bodies that say not to reply. Drafts are plain text (no subject
line, no markdown) because the email reader post-processes
``email_ai_replies`` rows with ``_extract_reply`` / ``_apply_email_style_mechanics``.
"""
from __future__ import annotations

import logging
import re
import sqlite3
from datetime import datetime
from email.utils import parseaddr
from typing import Callable, Dict, Optional, Tuple

from routes.email_helpers import SCHEDULED_DB

logger = logging.getLogger(__name__)

# Factored from src/builtin_actions.py:1016-1020 (learn_sender_signatures).
NO_REPLY_LOCALS = (
    "noreply", "no-reply", "donotreply", "do-not-reply",
    "mailer-daemon", "notifications", "notification", "bounce",
)
_DO_NOT_REPLY_RE = re.compile(r"\b(do not|don't|please do not) reply\b", re.I)
_REPLY_KINDS = {"interview_invite", "info_request", "offer", "recruiter_outreach"}


def _is_no_reply(addr: str) -> bool:
    local = addr.split("@", 1)[0].lower()
    return any(local == p or local.startswith(p) for p in NO_REPLY_LOCALS) or "-noreply" in addr or "-bounces" in addr


def reply_address(headers: Dict[str, str], body: str = "") -> Tuple[Optional[str], Optional[str]]:
    candidates = [parseaddr(headers.get("reply_to") or "")[1], parseaddr(headers.get("from") or "")[1]]
    candidates = [c.strip().lower() for c in candidates if c and c.strip()]
    if not candidates:
        return None, "no sender address"
    if headers.get("list_unsubscribe"):
        return None, "bulk mail (List-Unsubscribe)"
    if _DO_NOT_REPLY_RE.search(body or ""):
        return None, "body says do not reply"
    for addr in candidates:
        if not _is_no_reply(addr):
            return addr, None
    return None, "no-reply sender"


def should_suggest_reply(kind: str, sentiment: str, can_reply: bool) -> bool:
    return bool(can_reply) and kind in _REPLY_KINDS


REPLY_PROMPT = (
    "You draft short email replies for a software engineer who is job hunting. Write ONLY the reply body: "
    "no subject line, no markdown, no placeholders in square brackets, 60-140 words. Confirm availability when "
    "asked, answer any direct question briefly, and if exactly one detail is missing ask for it. "
    "Sign off with the first name only.\nVoice rules from the candidate: {voice}"
)


async def suggest_reply(ctx: Dict[str, str], settings: Dict, complete: Callable, owner: Optional[str]) -> str:
    voice = (settings or {}).get("voice_rules") or "plain, friendly, concise"
    if ctx.get("kind") == "nudge":
        user = (f"Write a polite follow-up on my application for {ctx.get('role') or 'the role'} at "
                f"{ctx.get('company') or 'the company'}. It has been quiet since I applied; ask whether there is "
                f"any update and restate interest in one sentence.")
    else:
        user = (f"They wrote ({ctx.get('kind')}) about the {ctx.get('role') or 'role'} at {ctx.get('company') or 'the company'}.\n"
                f"Subject: {ctx.get('subject', '')}\nSummary: {ctx.get('summary', '')}\n"
                f"Suggested next step: {ctx.get('next_step', '')}\n\nTheir email:\n{(ctx.get('body') or '')[:4000]}")
    raw = await complete([{"role": "system", "content": REPLY_PROMPT.format(voice=voice)},
                          {"role": "user", "content": user}], owner=owner)
    lines = [ln for ln in (raw or "").strip().splitlines() if not ln.lower().startswith("subject:")]
    return "\n".join(lines).strip()


def write_cached_reply(message_id: str, reply: str, model: str = "", uid: str = "", folder: str = "INBOX") -> bool:
    """INSERT OR IGNORE so a draft from the draft_email_replies action is never overwritten."""
    if not message_id or not reply:
        return False
    conn = sqlite3.connect(str(SCHEDULED_DB))
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS email_ai_replies (
                message_id TEXT PRIMARY KEY, uid TEXT, folder TEXT, reply TEXT NOT NULL,
                model_used TEXT, created_at TEXT NOT NULL)""")
        cur = conn.execute(
            "INSERT OR IGNORE INTO email_ai_replies (message_id, uid, folder, reply, model_used, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (message_id, str(uid or ""), folder, reply, model or "career-tracker", datetime.utcnow().isoformat()),
        )
        conn.commit()
        return cur.rowcount > 0
    finally:
        conn.close()
