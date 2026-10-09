"""Backfill: run the tracker pipeline over the last N days of INBOX headers.

Used by POST /api/career/tracker/scan (first-run import, catch-up after the
app was closed) and by the paused built-in action ``scan_job_emails``.
"""
from __future__ import annotations

import asyncio
import email as email_mod
import logging
import re
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Any, Callable, Dict, List, Optional

from services.career.email_classifier import prefilter

logger = logging.getLogger(__name__)

BATCH = 50
_UID_RE = re.compile(rb"UID (\d+)")


def owner_accounts(owner: str) -> List[Dict[str, str]]:
    """Enabled accounts for this owner, plus legacy unowned rows whose mailbox
    equals the owner (same rule as routes.email_helpers._get_email_config)."""
    from sqlalchemy import and_, or_
    from core.database import EmailAccount, SessionLocal
    db = SessionLocal()
    try:
        q = db.query(EmailAccount).filter(EmailAccount.enabled == True)  # noqa: E712
        if owner:
            unowned = or_(EmailAccount.owner == None, EmailAccount.owner == "")  # noqa: E711
            same = or_(EmailAccount.imap_user == owner, EmailAccount.from_address == owner)
            q = q.filter(or_(EmailAccount.owner == owner, and_(unowned, same)))
        return [{"id": r.id, "name": r.name or ""} for r in q.order_by(EmailAccount.created_at.asc()).all()]
    finally:
        db.close()


def list_dicts_since(conn, since: datetime, limit: int = 300) -> List[Dict[str, Any]]:
    from routes.email_helpers import _decode_header
    from routes.email_routes import _imap_uid_fetch
    conn.select("INBOX", readonly=True)
    status, data = conn.uid("SEARCH", None, f'(SINCE "{since.strftime("%d-%b-%Y")}")')
    if status != "OK" or not data or not data[0]:
        return []
    uids = data[0].decode().split() if isinstance(data[0], bytes) else str(data[0]).split()
    uids = uids[-limit:]
    out: List[Dict[str, Any]] = []
    for i in range(0, len(uids), BATCH):
        chunk = uids[i:i + BATCH]
        st, parts = _imap_uid_fetch(conn, ",".join(chunk), "(UID RFC822.HEADER)")
        if st != "OK":
            continue
        for part in parts:
            if not isinstance(part, tuple):
                continue
            meta, raw = part
            m = _UID_RE.search(meta)
            if not m:
                continue
            msg = email_mod.message_from_bytes(raw)
            out.append({
                "uid": m.group(1).decode(), "folder": "INBOX",
                "message_id": (msg.get("Message-ID") or "").strip(),
                "subject": _decode_header(msg.get("Subject")),
                "from_name": parseaddr(_decode_header(msg.get("From")))[0],
                "from_address": parseaddr(msg.get("From") or "")[1],
                "date": msg.get("Date") or "",
            })
    return out


async def scan_backfill(owner: str, days: int, *, session_factory: Optional[Callable] = None,
                        complete: Optional[Callable] = None, complete_utility: Optional[Callable] = None,
                        settings: Optional[Dict] = None, imap: Optional[Callable] = None,
                        process: Optional[Callable] = None) -> Dict[str, int]:
    if settings is None:
        from services.career.settings import load_career_settings
        settings = load_career_settings(owner)
    if imap is None:
        from routes.email_helpers import _imap as imap
    if process is None:
        from services.career.tracker import process_message as process
    wanted = set(settings.get("scan_account_ids") or [])
    accounts = [a for a in owner_accounts(owner) if not wanted or a["id"] in wanted]
    since = datetime.utcnow() - timedelta(days=max(1, int(days)))
    scanned = candidates = events = 0
    for acc in accounts:
        def _pull(account_id=acc["id"]):
            with imap(account_id, owner=owner) as conn:
                return list_dicts_since(conn, since)
        try:
            rows = await asyncio.to_thread(_pull)
        except Exception:
            logger.warning("DEGRADED: backfill could not read account %s", acc["id"], exc_info=True)
            continue
        scanned += len(rows)
        for ld in rows:
            if not prefilter(ld, extra_terms=settings.get("prefilter_terms") or ()):
                continue
            candidates += 1
            try:
                out = await process(owner, acc["id"], ld, complete=complete, complete_utility=complete_utility,
                                    session_factory=session_factory, settings=settings)
            except Exception:
                logger.warning("backfill: message %s skipped", ld.get("uid"), exc_info=True)
                continue
            if out:
                events += 1
    return {"scanned": scanned, "candidates": candidates, "events": events, "accounts": len(accounts)}


async def action_scan_job_emails(owner: str, **kwargs):
    """Built-in task action: catch-up scan of the last 7 days (paused by default)."""
    try:
        out = await scan_backfill(owner or "", int(kwargs.get("days") or 7))
        return (f"Scanned {out['scanned']} emails across {out['accounts']} account(s); "
                f"{out['candidates']} candidates, {out['events']} tracker events"), True
    except Exception as e:
        logger.error("scan_job_emails failed: %s", e)
        return str(e), False
