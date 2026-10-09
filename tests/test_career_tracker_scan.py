"""Backfill scan over IMAP headers → process_message, with a fake connection."""
import email.message
from contextlib import contextmanager
from datetime import datetime

import pytest

from services.career import tracker_scan as ts


def _raw_header(subject, sender, mid):
    m = email.message.EmailMessage()
    m["Subject"] = subject
    m["From"] = sender
    m["Message-ID"] = mid
    m["Date"] = "Mon, 06 Oct 2026 10:00:00 +0000"
    return m.as_bytes()


class FakeConn:
    def __init__(self, messages):
        self.messages = messages    # uid -> (subject, sender, mid)
        self.searches = []

    def select(self, folder, readonly=False):
        return "OK", [b"3"]

    def uid(self, cmd, *args):
        if cmd == "SEARCH":
            self.searches.append(args)
            return "OK", [" ".join(self.messages).encode()]
        if cmd == "FETCH":
            uids = args[0].decode().split(",")
            out = []
            for u in uids:
                s, f, mid = self.messages[u]
                out.append((f"{u} (UID {u} RFC822.HEADER {{0}}".encode(), _raw_header(s, f, mid)))
                out.append(b")")
            return "OK", out
        raise AssertionError(cmd)


def test_list_dicts_since_parses_headers():
    conn = FakeConn({"1": ("Thanks for applying", "no-reply@greenhouse.io", "<a@x>"),
                     "2": ("Lunch", "bob@gmail.com", "<b@x>")})
    rows = ts.list_dicts_since(conn, datetime(2026, 9, 6))
    assert conn.searches[0][1] == b'(SINCE "06-Sep-2026")' or conn.searches[0][1] == '(SINCE "06-Sep-2026")'
    assert [r["uid"] for r in rows] == ["1", "2"]
    assert rows[0]["subject"] == "Thanks for applying" and rows[0]["from_address"] == "no-reply@greenhouse.io"
    assert rows[0]["message_id"] == "<a@x>" and rows[0]["folder"] == "INBOX"


async def test_scan_backfill_runs_prefiltered_messages_through_process(monkeypatch):
    conn = FakeConn({"1": ("Thanks for applying", "no-reply@greenhouse.io", "<a@x>"),
                     "2": ("Lunch", "bob@gmail.com", "<b@x>")})

    @contextmanager
    def fake_imap(account_id, owner=""):
        yield conn

    processed = []

    async def fake_process(owner, account_id, ld, **kw):
        processed.append(ld["uid"])
        return {"event_id": "e", "kind": "confirmation"}

    monkeypatch.setattr(ts, "owner_accounts", lambda owner: [{"id": "acc1", "name": "Main"}])
    out = await ts.scan_backfill("vanders", 30, settings={"scan_account_ids": [], "prefilter_terms": []},
                                 imap=fake_imap, process=fake_process)
    assert processed == ["1"]
    assert out == {"scanned": 2, "candidates": 1, "events": 1, "accounts": 1}


async def test_scan_backfill_respects_scan_account_ids(monkeypatch):
    seen = []

    @contextmanager
    def fake_imap(account_id, owner=""):
        seen.append(account_id)
        yield FakeConn({})

    monkeypatch.setattr(ts, "owner_accounts", lambda owner: [{"id": "acc1", "name": "A"}, {"id": "acc2", "name": "B"}])
    await ts.scan_backfill("vanders", 7, settings={"scan_account_ids": ["acc2"], "prefilter_terms": []},
                           imap=fake_imap, process=None)
    assert seen == ["acc2"]


def test_action_is_registered_and_shipped_paused():
    from src.builtin_actions import BUILTIN_ACTIONS, BUILTIN_ACTION_INFO
    from src.task_scheduler import HOUSEKEEPING_DEFAULTS, TaskScheduler
    assert "scan_job_emails" in BUILTIN_ACTIONS and "scan_job_emails" in BUILTIN_ACTION_INFO
    assert HOUSEKEEPING_DEFAULTS["scan_job_emails"]["ship_paused"] is True
    assert "scan_job_emails" in TaskScheduler._SILENT_ACTIONS
    assert "scan_job_emails" in TaskScheduler._MODEL_BACKED_ACTIONS


async def test_action_reports_counts_and_failure(monkeypatch):
    async def ok(owner, days):
        assert (owner, days) == ("vanders", 7)
        return {"scanned": 10, "candidates": 3, "events": 2, "accounts": 1}

    monkeypatch.setattr(ts, "scan_backfill", ok)
    msg, success = await ts.action_scan_job_emails("vanders")
    assert success is True and "Scanned 10 emails" in msg and "2 tracker events" in msg

    async def boom(owner, days):
        raise RuntimeError("imap down")

    monkeypatch.setattr(ts, "scan_backfill", boom)
    assert await ts.action_scan_job_emails("vanders") == ("imap down", False)
