"""Full-message fetch for the job tracker, through the owner-scoped IMAP helper."""
from __future__ import annotations

import asyncio
import email as email_mod
from email.utils import parseaddr
from typing import Any, Dict, Optional


async def fetch_full(owner: str, account_id: Optional[str], uid: str, folder: str = "INBOX") -> Dict[str, Any]:
    from routes.email_helpers import _decode_header, _extract_text, _imap, _q
    from routes.email_routes import _imap_uid_fetch

    def _sync():
        with _imap(account_id, owner=owner) as conn:
            conn.select(_q(folder), readonly=True)
            status, data = _imap_uid_fetch(conn, str(uid), "(RFC822)")
            if status != "OK" or not data or not isinstance(data[0], tuple):
                raise RuntimeError(f"UID {uid} not found in {folder}")
            msg = email_mod.message_from_bytes(data[0][1])
        headers = {
            "subject": _decode_header(msg.get("Subject")), "from": _decode_header(msg.get("From")),
            "from_address": parseaddr(msg.get("From") or "")[1], "reply_to": _decode_header(msg.get("Reply-To")),
            "date": msg.get("Date") or "", "message_id": (msg.get("Message-ID") or "").strip(),
            "in_reply_to": (msg.get("In-Reply-To") or "").strip(), "references": (msg.get("References") or "").strip(),
            "list_unsubscribe": msg.get("List-Unsubscribe") or "", "x_odysseus_origin": msg.get("X-Odysseus-Origin") or "",
        }
        return {"headers": headers, "body": _extract_text(msg) or ""}

    return await asyncio.to_thread(_sync)
