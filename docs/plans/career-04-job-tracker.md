# Career Hub 04 — Job Tracker Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn incoming email into a live application tracker: new inbox mail is prefiltered, classified by the configured LLM, matched to (or creates) a `JobApplication`, recorded as a `JobApplicationEvent`, and shown on a Tracker dashboard that updates within one inbox poll while Odysseus is open. Positive responses get a drafted reply when a reply is possible.

**Architecture:** A subscriber hook in `routes/email_routes.py:_record_email_received_events` (the only place new mail is detected today; the browser polls it every 60 s) hands new list dicts to `services/career/tracker.py`. The tracker runs a cheap regex prefilter, fetches the full message through the owner-scoped IMAP helpers, classifies it with a JSON-only prompt, matches by thread or company, records the event, and writes a suggested reply into both the event row and the email reader's `email_ai_replies` cache. `routes/career_tracker_routes.py` serves the dashboard summary and manual actions; `static/js/careerTracker.js` registers the Tracker tab with the hub shell from plan 00 and polls the summary every 20 s while visible.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (SQLite), `imaplib`/`email` stdlib, sqlite3 (`data/scheduled_emails.db`), pytest (`asyncio_mode=auto`), vanilla ES modules, Node for JS logic tests.

**Prerequisites:** `docs/plans/career-00-hub-foundation.md` complete (models, settings, `routes/career_helpers.py`, `career.js` plugin API). Plan 01 is **not** required; `services/career/llm.py` (`complete`, `complete_utility`) comes from plan 00.

## Global Constraints

- TDD: write the failing test, run it, watch it fail for the right reason, then implement. Commit after each task.
- Owner scoping on every route and every DB query: filter by `owner == user` only when `get_current_user(request)` is not `None`; foreign rows are 404.
- Server-side email access goes through `routes.email_helpers._imap(account_id, owner=owner)` and `_get_email_config(..., owner=)`. Never the MCP email server (unscoped).
- Nothing is ever sent. Replies are drafts stored as plain text (the email reader post-processes them with `_extract_reply` + `_apply_email_style_mechanics`).
- `email_ai_replies` is shared with the `draft_email_replies` action: write with `INSERT OR IGNORE`, never `REPLACE`.
- Graceful degradation: a failing IMAP fetch, LLM call or notification drops that one message with a logged warning; the inbox list route must never slow down or fail because of the tracker.
- The subscriber dispatch happens only inside the existing `if count and new_keys:` baseline branch — the first list after startup must not classify the whole inbox.
- Tests that touch SQLAlchemy use the `_import_real(...)` unstub helper copied verbatim from `tests/test_council_personas.py:16-32`.
- Keep each new module under ~200 lines (the split below is designed for that).

---

### Task 1: New-mail subscriber hook

**Files:**
- Modify: `routes/email_routes.py:93-145` (`_record_email_received_events`) and add two module-level definitions above it
- Test: `tests/test_email_new_mail_subscribers.py`

**Interfaces:**
- Produces: `routes.email_routes._NEW_MAIL_SUBSCRIBERS: list`, `register_new_mail_subscriber(fn)` where `fn` is `async (owner: str, account_id: str | None, fresh: list[dict]) -> None` and `fresh` contains only the list dicts whose key (`message_id` or `uid`) was not seen before.

- [x] **Step 1: Write the failing test**

```python
# tests/test_email_new_mail_subscribers.py
"""_record_email_received_events hands NEW list dicts to registered subscribers
(after the first baseline call) without blocking the list handler."""
import asyncio

import pytest

fastapi = pytest.importorskip("fastapi")

import routes.email_routes as er


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "SCHEDULED_DB", str(tmp_path / "sched.db"))
    monkeypatch.setattr(er, "fire_event", lambda *a, **k: None, raising=False)
    monkeypatch.setattr(er, "_NEW_MAIL_SUBSCRIBERS", [])
    calls = []

    async def sub(owner, account_id, fresh):
        calls.append((owner, account_id, [e["message_id"] for e in fresh]))

    er.register_new_mail_subscriber(sub)
    return calls


def _run(fn):
    async def main():
        fn()
        await asyncio.sleep(0)   # let create_task'd subscribers run
        await asyncio.sleep(0)
    asyncio.run(main())


def test_first_call_baselines_without_dispatch(env):
    calls = env
    first = [{"uid": "1", "message_id": "<a@x>", "subject": "hi"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", first))
    assert calls == []


def test_second_call_dispatches_only_new_messages(env):
    calls = env
    first = [{"uid": "1", "message_id": "<a@x>", "subject": "hi"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", first))
    second = first + [{"uid": "2", "message_id": "<b@x>", "subject": "Thanks for applying"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", second))
    assert calls == [("vanders", "acc1", ["<b@x>"])]


def test_non_inbox_and_anonymous_are_ignored(env):
    calls = env
    msgs = [{"uid": "1", "message_id": "<a@x>"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "Sent", msgs))
    _run(lambda: er._record_email_received_events("", "acc1", "INBOX", msgs))
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", msgs))
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", msgs + [{"uid": "9", "message_id": "<z@x>"}]))
    assert calls == [("vanders", "acc1", ["<z@x>"])]


def test_subscriber_exception_does_not_propagate(env):
    calls = env

    async def bad(owner, account_id, fresh):
        raise RuntimeError("boom")

    er.register_new_mail_subscriber(bad)
    msgs = [{"uid": "1", "message_id": "<a@x>"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", msgs))
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", msgs + [{"uid": "2", "message_id": "<b@x>"}]))
    assert calls == [("vanders", "acc1", ["<b@x>"])]
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_email_new_mail_subscribers.py -v`
Expected: FAIL with `AttributeError: module 'routes.email_routes' has no attribute 'register_new_mail_subscriber'`

- [x] **Step 3: Implement**

In `routes/email_routes.py`, insert directly above `def _record_email_received_events(...)` (line 93):

```python
# Server-side observers of NEW inbox mail. The only place new mail is detected
# is the list route below (the browser polls it every 60 s), so features that
# need per-message processing (Career tracker, plan 04) subscribe here rather
# than running their own IMAP watcher. Each subscriber is
# `async fn(owner, account_id, fresh_list_dicts)` and runs as a fire-and-forget
# task: the list response never waits on it.
_NEW_MAIL_SUBSCRIBERS: list = []


def register_new_mail_subscriber(fn) -> None:
    if fn not in _NEW_MAIL_SUBSCRIBERS:
        _NEW_MAIL_SUBSCRIBERS.append(fn)


def _dispatch_new_mail(owner: str, account_id: str | None, fresh: list[dict]) -> None:
    if not fresh or not _NEW_MAIL_SUBSCRIBERS:
        return
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.debug("new-mail subscribers skipped: no running loop")
        return

    def _log_failure(task: asyncio.Task):
        try:
            exc = task.exception()
        except asyncio.CancelledError:
            return
        if exc:
            logger.warning("new-mail subscriber failed: %s", exc)

    for fn in list(_NEW_MAIL_SUBSCRIBERS):
        try:
            loop.create_task(fn(owner, account_id, fresh)).add_done_callback(_log_failure)
        except Exception:
            logger.warning("new-mail subscriber could not be scheduled", exc_info=True)
```

Replace the whole `_record_email_received_events` function (lines 93-145) with:

```python
def _record_email_received_events(owner: str, account_id: str | None, folder: str, emails: list[dict]):
    """Baseline inbox messages, then fire `email_received` for new arrivals and
    hand the new list dicts to registered subscribers."""
    if not owner or (folder or "INBOX").upper() != "INBOX" or not emails:
        return
    try:
        from src.event_bus import fire_event
        account_key = (account_id or "default").strip() or "default"
        now = datetime.utcnow().isoformat() + "Z"
        keys = []
        for e in emails:
            key = (e.get("message_id") or e.get("uid") or "").strip()
            if key and key not in keys:
                keys.append(key)
        if not keys:
            return

        conn = _sql3.connect(SCHEDULED_DB)
        try:
            conn.execute(
                "CREATE TABLE IF NOT EXISTS email_event_seen ("
                "owner TEXT NOT NULL, account_key TEXT NOT NULL, folder TEXT NOT NULL, "
                "message_key TEXT NOT NULL, first_seen_at TEXT NOT NULL, "
                "PRIMARY KEY (owner, account_key, folder, message_key))"
            )
            count = conn.execute(
                "SELECT COUNT(*) FROM email_event_seen WHERE owner=? AND account_key=? AND folder=?",
                (owner, account_key, folder),
            ).fetchone()[0]
            existing = set()
            if count:
                placeholders = ",".join("?" * len(keys))
                rows = conn.execute(
                    f"SELECT message_key FROM email_event_seen "
                    f"WHERE owner=? AND account_key=? AND folder=? AND message_key IN ({placeholders})",
                    (owner, account_key, folder, *keys),
                ).fetchall()
                existing = {r[0] for r in rows}
            new_keys = [k for k in keys if k not in existing]
            conn.executemany(
                "INSERT OR IGNORE INTO email_event_seen "
                "(owner, account_key, folder, message_key, first_seen_at) VALUES (?, ?, ?, ?, ?)",
                [(owner, account_key, folder, k, now) for k in keys],
            )
            conn.commit()
        finally:
            conn.close()

        if count and new_keys:
            for _ in new_keys[:50]:
                fire_event("email_received", owner)
            logger.info("Fired email_received for %d new message(s)", min(len(new_keys), 50))
            new_set = set(new_keys)
            fresh = [e for e in emails if (e.get("message_id") or e.get("uid") or "").strip() in new_set]
            _dispatch_new_mail(owner, account_id, fresh)
    except Exception:
        logger.debug("email_received event detection skipped", exc_info=True)
```

Note for the test's `fire_event` monkeypatch: the function imports `fire_event` locally, so the patch with `raising=False` is a no-op and the real `fire_event` runs; it is safe without a scheduler (it only queries `ScheduledTask` rows and swallows errors). If the real import fails in the test environment, add `monkeypatch.setitem(sys.modules, "src.event_bus", types.SimpleNamespace(fire_event=lambda *a, **k: None))` to the fixture.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_email_new_mail_subscribers.py tests/test_security_regressions.py -v -k "subscriber or email"`
Expected: the four new tests pass; existing email security tests unchanged.

- [x] **Step 5: Commit**

```bash
git add routes/email_routes.py tests/test_email_new_mail_subscribers.py
git commit -m "feat(email): subscriber hook for newly detected inbox mail"
```

---

### Task 2: Career LLM helper — provided by plan 00

No work in this plan. `services/career/llm.py` ships in `career-00-hub-foundation.md` Task 2b with `complete` (Task chain, for reply drafts) and `complete_utility` (Utility first, for classification). Later tasks import both from `services.career.llm`.

---

### Task 3: Email classifier (prefilter + JSON verdict)

**Files:**
- Create: `services/career/email_classifier.py`
- Test: `tests/test_career_email_classifier.py`

**Interfaces:**
- Produces: `PREFILTER_SUBJECT_RE`, `PREFILTER_SENDER_RE`, `ATS_SENDER_RE`, `prefilter(list_dict, extra_terms=()) -> bool`, `is_ats_sender(from_address) -> bool`, `KINDS`, `SENTIMENTS`, `CLASSIFY_PROMPT`, `parse_verdict(text) -> dict`, `async classify(headers: dict, body: str, complete, owner) -> dict` where the verdict dict is `{is_job_related: bool, kind: str, sentiment: str, company: str, role: str, confidence: int (0-100), summary: str, next_step: str}`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_email_classifier.py
"""Prefilter table and robust verdict parsing for the job tracker."""
import pytest

from services.career import email_classifier as ec


@pytest.mark.parametrize("subject,sender,expected", [
    ("Thank you for applying to Acme", "no-reply@greenhouse.io", True),
    ("Your application to Beta Ltd", "jobs@beta.com", True),
    ("Interview availability — Backend Engineer", "sarah@gamma.io", True),
    ("Unfortunately we will not be moving forward", "talent@delta.co", True),
    ("Weekly digest", "newsletter@shop.com", False),
    ("Your Amazon order has shipped", "ship-confirm@amazon.co.uk", False),
    ("Lunch?", "friend@gmail.com", False),
])
def test_prefilter_table(subject, sender, expected):
    assert ec.prefilter({"subject": subject, "from_address": sender, "from_name": ""}) is expected


def test_prefilter_honours_extra_terms():
    e = {"subject": "Hackajob update", "from_address": "x@y.com", "from_name": ""}
    assert ec.prefilter(e) is False
    assert ec.prefilter(e, extra_terms=("hackajob",)) is True


def test_is_ats_sender():
    assert ec.is_ats_sender("no-reply@lever.co")
    assert ec.is_ats_sender("jobs-noreply@linkedin.com")
    assert not ec.is_ats_sender("sarah@gamma.io")


def test_parse_verdict_takes_last_json_object_and_clamps():
    text = ('Sure. {"draft": true}\n```json\n{"is_job_related": true, "kind": "interview_invite", '
            '"sentiment": "positive", "company": "Acme Ltd", "role": "Backend Engineer", '
            '"confidence": 1.7, "summary": "Invite to a 45 min call", "next_step": "pick a slot"}\n```')
    v = ec.parse_verdict(text)
    assert v["kind"] == "interview_invite" and v["sentiment"] == "positive"
    assert v["confidence"] == 100
    assert v["company"] == "Acme Ltd"


def test_parse_verdict_defaults_on_garbage():
    v = ec.parse_verdict("no json here")
    assert v == {"is_job_related": False, "kind": "other", "sentiment": "neutral", "company": "",
                 "role": "", "confidence": 0, "summary": "", "next_step": ""}
    v2 = ec.parse_verdict('{"is_job_related": "yes", "kind": "party", "sentiment": "meh", "confidence": "0.4"}')
    assert v2["is_job_related"] is True and v2["kind"] == "other" and v2["sentiment"] == "neutral"
    assert v2["confidence"] == 40


async def test_classify_builds_prompt_and_parses():
    seen = {}

    async def complete(messages, owner=None, **kw):
        seen["messages"] = messages
        return '{"is_job_related": true, "kind": "rejection", "sentiment": "negative", "company": "Beta", "role": "Dev", "confidence": 0.9, "summary": "No", "next_step": ""}'

    headers = {"subject": "Update on your application", "from": "talent@beta.com", "date": "Mon"}
    v = await ec.classify(headers, "Unfortunately...", complete, "vanders")
    assert v["kind"] == "rejection" and v["confidence"] == 90
    assert "Update on your application" in seen["messages"][-1]["content"]
    assert seen["messages"][0]["role"] == "system"
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_email_classifier.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.email_classifier'`

- [x] **Step 3: Implement**

```python
# services/career/email_classifier.py
"""Two-stage job-email classification: a free regex prefilter on the list
dict (subject + sender), then a JSON-only LLM verdict on the full message.
"""
from __future__ import annotations

import json
import re
from typing import Any, Callable, Dict, Iterable, Optional

KINDS = ("confirmation", "rejection", "interview_invite", "offer", "info_request", "recruiter_outreach", "other")
SENTIMENTS = ("positive", "negative", "neutral")

PREFILTER_SUBJECT_RE = re.compile(
    r"\b(application|applied|applying|interview|offer|unfortunately|position|role|candidate|"
    r"recruit\w*|talent|assessment|next steps?|your application|thank you for applying|"
    r"we(?:'d| would) like to)\b", re.I)
PREFILTER_SENDER_RE = re.compile(
    r"(greenhouse|lever\.co|ashby|workable|smartrecruiters|workday|myworkdayjobs|icims|bamboohr|"
    r"linkedin|indeed|otta|wellfound|teamtailor|personio|careers@|jobs@|recruit|talent|\bhr@)", re.I)
ATS_SENDER_RE = re.compile(
    r"(greenhouse|lever\.co|ashby|workable|smartrecruiters|workday|myworkdayjobs|icims|bamboohr|"
    r"linkedin|indeed|otta|wellfound|teamtailor|personio)", re.I)


def prefilter(list_dict: Dict[str, Any], extra_terms: Iterable[str] = ()) -> bool:
    subject = str(list_dict.get("subject") or "")
    sender = f"{list_dict.get('from_name') or ''} {list_dict.get('from_address') or ''}"
    if PREFILTER_SUBJECT_RE.search(subject) or PREFILTER_SENDER_RE.search(sender):
        return True
    hay = f"{subject} {sender}".lower()
    return any(t and t.lower() in hay for t in extra_terms)


def is_ats_sender(from_address: Optional[str]) -> bool:
    return bool(ATS_SENDER_RE.search(from_address or ""))


CLASSIFY_PROMPT = (
    "You classify emails for a software engineer's job search. Reply with ONE JSON object and nothing else:\n"
    '{"is_job_related": true|false, '
    '"kind": "confirmation|rejection|interview_invite|offer|info_request|recruiter_outreach|other", '
    '"sentiment": "positive|negative|neutral", '
    '"company": "<employer name or empty>", "role": "<job title or empty>", '
    '"confidence": <0.0-1.0>, "summary": "<=200 chars, what this email says>", '
    '"next_step": "<=120 chars, what the candidate should do, or empty"}\n'
    "Rules: confirmation = an application was received; rejection = not moving forward; "
    "interview_invite = any request to schedule a call/interview/assessment; offer = a job offer; "
    "info_request = they need something from the candidate; recruiter_outreach = unsolicited approach. "
    "Job alerts, newsletters and marketing are NOT job related. Never invent a company."
)

_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)
_DEFAULT = {"is_job_related": False, "kind": "other", "sentiment": "neutral", "company": "",
            "role": "", "confidence": 0, "summary": "", "next_step": ""}


def _as_bool(v: Any) -> bool:
    if isinstance(v, bool):
        return v
    return str(v).strip().lower() in ("true", "yes", "1")


def _as_pct(v: Any) -> int:
    try:
        f = float(v)
    except (TypeError, ValueError):
        return 0
    if f <= 1.0:
        f *= 100
    return int(round(max(0.0, min(100.0, f))))


def parse_verdict(text: str) -> Dict[str, Any]:
    out = dict(_DEFAULT)
    for candidate in reversed(_JSON_RE.findall(text or "")):
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict) or "is_job_related" not in data and "kind" not in data:
            continue
        out["is_job_related"] = _as_bool(data.get("is_job_related", False))
        out["kind"] = data.get("kind") if data.get("kind") in KINDS else "other"
        out["sentiment"] = data.get("sentiment") if data.get("sentiment") in SENTIMENTS else "neutral"
        out["company"] = str(data.get("company") or "").strip()[:120]
        out["role"] = str(data.get("role") or "").strip()[:120]
        out["confidence"] = _as_pct(data.get("confidence"))
        out["summary"] = str(data.get("summary") or "").strip()[:200]
        out["next_step"] = str(data.get("next_step") or "").strip()[:120]
        return out
    return out


async def classify(headers: Dict[str, str], body: str, complete: Callable, owner: Optional[str]) -> Dict[str, Any]:
    user = (
        f"From: {headers.get('from', '')}\nSubject: {headers.get('subject', '')}\n"
        f"Date: {headers.get('date', '')}\n\n{(body or '')[:6000]}"
    )
    raw = await complete([{"role": "system", "content": CLASSIFY_PROMPT},
                          {"role": "user", "content": user}], owner=owner)
    return parse_verdict(raw or "")
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_email_classifier.py -v`
Expected: 12 passed

- [x] **Step 5: Commit**

```bash
git add services/career/email_classifier.py tests/test_career_email_classifier.py
git commit -m "feat(career): job-email prefilter and JSON classifier"
```

---

### Task 4: Reply address, reply suggestion, cached-reply write

**Files:**
- Create: `services/career/tracker_replies.py`
- Test: `tests/test_career_tracker_replies.py`

**Interfaces:**
- Produces: `NO_REPLY_LOCALS`, `reply_address(headers: dict, body: str = "") -> tuple[str | None, str | None]`, `should_suggest_reply(kind, sentiment, can_reply) -> bool`, `REPLY_PROMPT`, `async suggest_reply(ctx: dict, settings: dict, complete, owner) -> str` (ctx keys: `kind, company, role, subject, summary, next_step, body`; kind `"nudge"` produces a polite follow-up), `write_cached_reply(message_id, reply, model="", uid="", folder="INBOX") -> bool` (True when a row was inserted).

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_tracker_replies.py
"""Reply-address resolution, suggestion gating, and the INSERT OR IGNORE cache write."""
import sqlite3

import pytest

from services.career import tracker_replies as tr


@pytest.mark.parametrize("headers,body,addr,reason", [
    ({"from": "Sarah <sarah@gamma.io>", "reply_to": ""}, "", "sarah@gamma.io", None),
    ({"from": "no-reply@greenhouse.io", "reply_to": "recruiting@acme.com"}, "", "recruiting@acme.com", None),
    ({"from": "no-reply@greenhouse.io", "reply_to": ""}, "", None, "no-reply sender"),
    ({"from": "donotreply@acme.com", "reply_to": "noreply@acme.com"}, "", None, "no-reply sender"),
    ({"from": "talent@beta.com", "reply_to": "", "list_unsubscribe": "<https://x>"}, "", None, "bulk mail (List-Unsubscribe)"),
    ({"from": "talent@beta.com", "reply_to": ""}, "Please do not reply to this email.", None, "body says do not reply"),
    ({"from": "", "reply_to": ""}, "", None, "no sender address"),
])
def test_reply_address(headers, body, addr, reason):
    assert tr.reply_address(headers, body) == (addr, reason)


@pytest.mark.parametrize("kind,sentiment,can_reply,expected", [
    ("interview_invite", "positive", True, True),
    ("info_request", "neutral", True, True),
    ("offer", "positive", True, True),
    ("confirmation", "neutral", True, False),
    ("rejection", "negative", True, False),
    ("interview_invite", "positive", False, False),
    ("recruiter_outreach", "positive", True, True),
])
def test_should_suggest_reply(kind, sentiment, can_reply, expected):
    assert tr.should_suggest_reply(kind, sentiment, can_reply) is expected


async def test_suggest_reply_uses_voice_rules_and_strips_subject_lines():
    seen = {}

    async def complete(messages, owner=None, **kw):
        seen["messages"] = messages
        return "Subject: Re: Interview\n\nHi Sarah,\n\nThursday 10:00 works for me.\n\nThanks,\nLuke"

    ctx = {"kind": "interview_invite", "company": "Gamma", "role": "Backend", "subject": "Interview",
           "summary": "Invite", "next_step": "pick a slot", "body": "Can you do Thursday?"}
    out = await tr.suggest_reply(ctx, {"voice_rules": "short, warm, no exclamation marks"}, complete, "vanders")
    assert out.startswith("Hi Sarah")
    assert "Subject:" not in out
    assert "short, warm, no exclamation marks" in seen["messages"][0]["content"]


async def test_nudge_prompt_mentions_follow_up():
    seen = {}

    async def complete(messages, owner=None, **kw):
        seen["messages"] = messages
        return "Hi, just following up on my application."

    await tr.suggest_reply({"kind": "nudge", "company": "Acme", "role": "Dev", "subject": "", "summary": "",
                            "next_step": "", "body": ""}, {}, complete, "vanders")
    assert "follow" in seen["messages"][-1]["content"].lower()


def test_write_cached_reply_inserts_once(tmp_path, monkeypatch):
    db = tmp_path / "sched.db"
    monkeypatch.setattr(tr, "SCHEDULED_DB", str(db))
    assert tr.write_cached_reply("<m1@x>", "first draft", model="m", uid="7") is True
    assert tr.write_cached_reply("<m1@x>", "second draft", model="m", uid="7") is False
    row = sqlite3.connect(db).execute("SELECT reply, uid, folder FROM email_ai_replies WHERE message_id=?", ("<m1@x>",)).fetchone()
    assert row == ("first draft", "7", "INBOX")
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_tracker_replies.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.tracker_replies'`

- [x] **Step 3: Implement**

```python
# services/career/tracker_replies.py
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_tracker_replies.py -v`
Expected: 17 passed

- [x] **Step 5: Commit**

```bash
git add services/career/tracker_replies.py tests/test_career_tracker_replies.py
git commit -m "feat(career): reply address resolution and reply drafts for tracker events"
```

---

### Task 5: Tracker core — matching, recording, full fetch, message pipeline

**Files:**
- Create: `services/career/tracker.py`
- Test: `tests/test_career_tracker.py`

**Interfaces:**
- Consumes: Task 3 `prefilter/is_ats_sender/classify`, Task 4 `reply_address/should_suggest_reply/suggest_reply/write_cached_reply`, plan 00 `services.career.llm.complete/complete_utility`, plan 00 models + `load_career_settings`.
- Produces: `normalise_company(name) -> str`, `STATUS_FOR_KIND`, `CREATE_KINDS`, `match_application(db, owner, company, role, message_refs) -> JobApplication | None`, `record_event(db, owner, app, verdict, headers, *, uid, folder, account_id, can_reply, reply_blocked_reason) -> JobApplicationEvent | None` (None when message_id already recorded), `async fetch_full(owner, account_id, uid, folder="INBOX") -> dict`, `async process_message(owner, account_id, list_dict, *, complete=None, complete_utility=None, session_factory=None, fetch=None, settings=None, notify=None) -> dict | None`, `async on_new_inbox_messages(owner, account_id, fresh)`, `register()`.
- The `fetch_full` result shape: `{"headers": {"subject", "from", "from_address", "reply_to", "date", "message_id", "in_reply_to", "references", "list_unsubscribe", "x_odysseus_origin"}, "body": str}`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_tracker.py
"""Tracker core: company normalisation, matching, event recording, message pipeline."""
import importlib
import sys
import types
from datetime import datetime, timedelta

import pytest


def _import_real(*names, force=()):
    def _is_stub(m):
        return (not isinstance(m, types.ModuleType)
                or not isinstance(getattr(m, "__file__", None), str))

    saved = {}
    for key in list(sys.modules):
        top = key.split(".")[0]
        is_dep_stub = (
            top == "sqlalchemy" or key in ("core.database", "core.models", "src.database")
        ) and _is_stub(sys.modules[key])
        if is_dep_stub or key in force:
            saved[key] = sys.modules.pop(key)
    try:
        return [importlib.import_module(n) for n in names]
    finally:
        sys.modules.update(saved)


_sa, _sa_orm, _sa_pool, _coredb, tracker, tr = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "services.career.tracker", "services.career.tracker_replies",
    force=("core.database", "services.career.tracker"),
)
JobApplication = _coredb.JobApplication
JobApplicationEvent = _coredb.JobApplicationEvent


@pytest.fixture()
def db_factory(monkeypatch, tmp_path):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(tr, "SCHEDULED_DB", str(tmp_path / "sched.db"))
    return _sa_orm.sessionmaker(bind=engine)


def _seed(db_factory, **kw):
    fields = dict(id="a1", owner="vanders", company="Acme Ltd", role="Backend Engineer", status="applied",
                  applied_at=datetime.utcnow() - timedelta(days=3))
    fields.update(kw)
    db = db_factory()
    db.add(JobApplication(**fields))
    db.commit()
    db.close()


@pytest.mark.parametrize("raw,expected", [
    ("Acme Ltd", "acme"), ("ACME, Inc.", "acme"), ("Beta GmbH", "beta"), ("Gamma plc", "gamma"),
    ("Delta Technologies LLC", "delta technologies"), ("", ""),
])
def test_normalise_company(raw, expected):
    assert tracker.normalise_company(raw) == expected


def test_match_by_thread_then_company(db_factory):
    _seed(db_factory)
    db = db_factory()
    db.add(JobApplicationEvent(id="e0", owner="vanders", application_id="a1", kind="confirmation",
                               message_id="<conf@acme>"))
    db.commit()
    assert tracker.match_application(db, "vanders", "Totally Different", "", ["<conf@acme>"]).id == "a1"
    assert tracker.match_application(db, "vanders", "ACME Inc", "Backend", []).id == "a1"
    assert tracker.match_application(db, "vanders", "Nope", "Backend", []) is None
    assert tracker.match_application(db, "other", "Acme", "Backend", []) is None
    db.close()


def test_match_prefers_closest_role_when_company_has_two_openings(db_factory):
    _seed(db_factory)
    _seed(db_factory, id="a2", role="Data Engineer")
    db = db_factory()
    assert tracker.match_application(db, "vanders", "Acme", "Data Eng", []).id == "a2"
    db.close()


def test_record_event_updates_status_and_dedupes(db_factory):
    _seed(db_factory)
    db = db_factory()
    app = db.get(JobApplication, "a1")
    verdict = {"kind": "interview_invite", "sentiment": "positive", "confidence": 90, "summary": "Call Thu"}
    headers = {"message_id": "<i1@acme>", "subject": "Interview", "from_address": "sarah@acme.com", "reply_to": "", "date": ""}
    ev = tracker.record_event(db, "vanders", app, verdict, headers, uid="5", folder="INBOX", account_id="acc",
                              can_reply=True, reply_blocked_reason=None)
    assert ev.kind == "interview_invite" and app.status == "interview" and app.last_event_at is not None
    assert tracker.record_event(db, "vanders", app, verdict, headers, uid="5", folder="INBOX", account_id="acc",
                                can_reply=True, reply_blocked_reason=None) is None
    rej = {"kind": "rejection", "sentiment": "negative", "confidence": 95, "summary": "No"}
    tracker.record_event(db, "vanders", app, rej, {**headers, "message_id": "<r1@acme>"}, uid="6", folder="INBOX",
                         account_id="acc", can_reply=False, reply_blocked_reason="no-reply sender")
    assert app.status == "rejected"
    db.close()


def test_record_event_never_downgrades_offer_or_withdrawn(db_factory):
    _seed(db_factory, status="offer")
    db = db_factory()
    app = db.get(JobApplication, "a1")
    tracker.record_event(db, "vanders", app, {"kind": "confirmation", "sentiment": "neutral", "confidence": 80, "summary": ""},
                         {"message_id": "<c@x>", "subject": "", "from_address": "", "reply_to": "", "date": ""},
                         uid="1", folder="INBOX", account_id="acc", can_reply=False, reply_blocked_reason=None)
    assert app.status == "offer"
    db.close()


def _fake_fetch(headers_extra=None, body="Thanks for applying to Acme. We'd like to invite you to interview on Thursday."):
    async def fetch(owner, account_id, uid, folder="INBOX"):
        headers = {"subject": "Interview invitation — Backend Engineer", "from": "Sarah <sarah@acme.com>",
                   "from_address": "sarah@acme.com", "reply_to": "", "date": "Mon, 6 Oct 2026 10:00:00 +0000",
                   "message_id": "<i1@acme>", "in_reply_to": "", "references": "", "list_unsubscribe": "",
                   "x_odysseus_origin": ""}
        headers.update(headers_extra or {})
        return {"headers": headers, "body": body}
    return fetch


def _complete(verdict_json):
    async def complete(messages, owner=None, **kw):
        if "classify emails" in messages[0]["content"]:
            return verdict_json
        return "Hi Sarah, Thursday works. Thanks, Luke"
    return complete


_INVITE = ('{"is_job_related": true, "kind": "interview_invite", "sentiment": "positive", "company": "Acme Ltd", '
           '"role": "Backend Engineer", "confidence": 0.92, "summary": "Interview Thursday", "next_step": "confirm"}')


async def test_process_message_creates_app_event_reply_and_notification(db_factory):
    notes = []
    list_dict = {"uid": "5", "message_id": "<i1@acme>", "subject": "Interview invitation — Backend Engineer",
                 "from_name": "Sarah", "from_address": "sarah@acme.com"}
    out = await tracker.process_message(
        "vanders", "acc", list_dict, complete=_complete(_INVITE), complete_utility=_complete(_INVITE),
        session_factory=db_factory, fetch=_fake_fetch(), settings={"voice_rules": ""},
        notify=lambda **kw: notes.append(kw))
    assert out["created_application"] is True and out["kind"] == "interview_invite"
    db = db_factory()
    app = db.query(JobApplication).one()
    assert app.company == "Acme Ltd" and app.status == "interview" and app.source == "email"
    ev = db.query(JobApplicationEvent).one()
    assert ev.application_id == app.id and ev.can_reply is True and ev.suggested_reply.startswith("Hi Sarah")
    db.close()
    assert notes and notes[0]["owner"] == "vanders" and "Acme" in notes[0]["body"]


async def test_process_message_matches_existing_application(db_factory):
    _seed(db_factory)
    out = await tracker.process_message(
        "vanders", "acc", {"uid": "5", "message_id": "<i1@acme>", "subject": "Interview", "from_name": "", "from_address": "sarah@acme.com"},
        complete=_complete(_INVITE), complete_utility=_complete(_INVITE), session_factory=db_factory,
        fetch=_fake_fetch(), settings={}, notify=lambda **kw: None)
    assert out["created_application"] is False and out["application_id"] == "a1"


async def test_low_confidence_lands_unsorted(db_factory):
    weak = _INVITE.replace("0.92", "0.4")
    out = await tracker.process_message(
        "vanders", "acc", {"uid": "5", "message_id": "<i1@acme>", "subject": "Interview", "from_name": "", "from_address": "x@acme.com"},
        complete=_complete(weak), complete_utility=_complete(weak), session_factory=db_factory,
        fetch=_fake_fetch(), settings={}, notify=lambda **kw: None)
    assert out["application_id"] is None and out["kind"] == "other"
    db = db_factory()
    assert db.query(JobApplication).count() == 0
    assert db.query(JobApplicationEvent).one().application_id is None
    db.close()


async def test_prefilter_miss_self_sent_and_not_job_related_return_none(db_factory):
    calls = []

    async def counting_fetch(owner, account_id, uid, folder="INBOX"):
        calls.append(uid)
        return await _fake_fetch({"x_odysseus_origin": "odysseus-ui"})(owner, account_id, uid, folder)

    base = dict(complete=_complete(_INVITE), complete_utility=_complete(_INVITE), session_factory=db_factory,
                settings={}, notify=lambda **kw: None)
    assert await tracker.process_message("vanders", "acc", {"uid": "1", "message_id": "<x>", "subject": "Lunch?", "from_name": "", "from_address": "bob@gmail.com"},
                                         fetch=counting_fetch, **base) is None
    assert calls == []
    assert await tracker.process_message("vanders", "acc", {"uid": "2", "message_id": "<y>", "subject": "Interview", "from_name": "", "from_address": "me@me.com"},
                                         fetch=counting_fetch, **base) is None
    not_job = _INVITE.replace('"is_job_related": true', '"is_job_related": false')
    assert await tracker.process_message("vanders", "acc", {"uid": "3", "message_id": "<z>", "subject": "Interview", "from_name": "", "from_address": "x@acme.com"},
                                         fetch=_fake_fetch(), **{**base, "complete_utility": _complete(not_job)}) is None


async def test_on_new_inbox_messages_isolates_failures(db_factory, monkeypatch):
    seen = []

    async def fake_process(owner, account_id, ld, **kw):
        seen.append(ld["uid"])
        if ld["uid"] == "1":
            raise RuntimeError("boom")
        return {"kind": "other"}

    monkeypatch.setattr(tracker, "process_message", fake_process)
    await tracker.on_new_inbox_messages("vanders", "acc", [{"uid": "1"}, {"uid": "2"}])
    assert seen == ["1", "2"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_tracker.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.tracker'`

- [x] **Step 3: Implement**

```python
# services/career/tracker.py
"""Job tracker pipeline: new inbox mail → prefilter → full fetch → classify →
match/create application → event → reply draft → notification.

Everything external is injectable (fetch, complete, session_factory, notify)
so the pipeline is unit-testable without IMAP or a model.
"""
from __future__ import annotations

import asyncio
import difflib
import email as email_mod
import logging
import re
import uuid
from datetime import datetime
from email.utils import parseaddr, parsedate_to_datetime
from typing import Any, Callable, Dict, List, Optional

from core.database import JobApplication, JobApplicationEvent, SessionLocal
from services.career.email_classifier import classify, is_ats_sender, prefilter
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


def _default_notify(**kw):
    from src.event_bus import get_task_scheduler
    scheduler = get_task_scheduler()
    if scheduler is None:
        logger.info("Tracker notification dropped (no scheduler): %s", kw.get("body"))
        return
    scheduler.add_notification(**kw)


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
    fetch = fetch or fetch_full
    notify = notify or _default_notify
    factory = session_factory or SessionLocal

    full = await fetch(owner, account_id, str(list_dict.get("uid") or ""), list_dict.get("folder") or "INBOX")
    headers, body = full["headers"], full["body"]
    if headers.get("x_odysseus_origin"):
        return None
    if headers.get("list_unsubscribe") and not is_ats_sender(headers.get("from_address")):
        return None
    verdict = await classify(headers, body, complete_utility, owner)
    if not verdict["is_job_related"]:
        return None

    addr, blocked = reply_address(headers, body)
    refs = [headers.get("in_reply_to") or "", *(headers.get("references") or "").split()]
    db = factory()
    try:
        app = match_application(db, owner, verdict["company"], verdict["role"], refs)
        created = False
        confident = verdict["confidence"] >= MIN_CONFIDENCE
        if app is None and confident and verdict["kind"] in CREATE_KINDS and verdict["company"]:
            app = JobApplication(id=uuid.uuid4().hex[:12], owner=owner, company=verdict["company"],
                                 role=verdict["role"] or "Unknown role", source="email", status="applied",
                                 applied_at=_parse_date(headers.get("date") or "") or datetime.utcnow())
            db.add(app)
            db.flush()
            created = True
        if not confident:
            verdict = {**verdict, "kind": "other"}
            app = app if app and not created else None
        ev = record_event(db, owner, app, verdict, headers, uid=list_dict.get("uid") or "", folder=list_dict.get("folder") or "INBOX",
                          account_id=account_id, can_reply=addr is not None, reply_blocked_reason=blocked)
        if ev is None:
            return None
        if app and should_suggest_reply(ev.kind, ev.sentiment, ev.can_reply):
            try:
                ctx = {"kind": ev.kind, "company": app.company, "role": app.role, "subject": ev.subject or "",
                       "summary": ev.summary or "", "next_step": verdict.get("next_step", ""), "body": body}
                ev.suggested_reply = await suggest_reply(ctx, settings, complete, owner)
                db.commit()
                write_cached_reply(ev.message_id or "", ev.suggested_reply, uid=ev.uid or "", folder=ev.folder or "INBOX")
            except Exception:
                logger.warning("DEGRADED: reply suggestion failed for %s", ev.message_id, exc_info=True)
        if app and ev.sentiment == "positive":
            try:
                notify(task_name="Career tracker", status="success", owner=owner,
                       body=f"{app.company} — {app.role}: {ev.kind.replace('_', ' ')}. {ev.summary or ''}".strip())
            except Exception:
                logger.debug("tracker notification failed", exc_info=True)
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
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_tracker.py -v`
Expected: 15 passed

- [x] **Step 5: Commit**

```bash
git add services/career/tracker.py tests/test_career_tracker.py
git commit -m "feat(career): tracker pipeline — match, record, reply draft, notify"
```

---

### Task 6: Backfill scan and built-in action

**Files:**
- Create: `services/career/tracker_scan.py`
- Modify: `src/builtin_actions.py:2190-2231` (register `scan_job_emails`), `src/task_scheduler.py:186-198` (seed), `src/task_scheduler.py:870-897` (silent + model-backed sets)
- Test: `tests/test_career_tracker_scan.py`

**Interfaces:**
- Produces: `owner_accounts(owner) -> list[dict]` (each `{"id", "name"}`), `list_dicts_since(conn, since: datetime, limit=300) -> list[dict]` (sync, given an open IMAP connection), `async scan_backfill(owner, days, *, session_factory=None, complete=None, complete_utility=None, settings=None, imap=None, process=None) -> dict` returning `{"scanned", "candidates", "events", "accounts"}`, and the built-in action `action_scan_job_emails(owner, **kw) -> (str, bool)`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_tracker_scan.py
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
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_tracker_scan.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.tracker_scan'`

- [x] **Step 3: Implement the scanner**

```python
# services/career/tracker_scan.py
"""Backfill: run the tracker pipeline over the last N days of INBOX headers.

Used by POST /api/career/tracker/scan (first-run import, catch-up after the
app was closed) and by the paused built-in action ``scan_job_emails``.
"""
from __future__ import annotations

import asyncio
import email as email_mod
import logging
from datetime import datetime, timedelta
from email.utils import parseaddr
from typing import Any, Callable, Dict, List, Optional

from services.career.email_classifier import prefilter

logger = logging.getLogger(__name__)

BATCH = 50


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
            import re as _re
            m = _re.search(rb"UID (\d+)", meta)
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
```

Note: `list_dicts_since` calls `conn.uid("SEARCH", None, ...)`, so the fake in the test receives `(None, '(SINCE "06-Sep-2026")')` — the assertion checks `args[1]`.

- [x] **Step 4: Register the action and seed**

`src/builtin_actions.py` — add inside `BUILTIN_ACTIONS` after `"check_email_urgency": action_check_email_urgency,`:

```python
    "scan_job_emails": _lazy_scan_job_emails,
```

and define above the dict (keeps the import lazy so builtin_actions never imports SQLAlchemy models at module load):

```python
async def _lazy_scan_job_emails(owner: str, **kwargs):
    from services.career.tracker_scan import action_scan_job_emails
    return await action_scan_job_emails(owner, **kwargs)
```

Add to `BUILTIN_ACTION_INFO`:

```python
    "scan_job_emails": "Career tracker catch-up: classify the last 7 days of inbox mail into job application events (the live tracker already runs whenever Odysseus is open).",
```

`src/task_scheduler.py` — add to `HOUSEKEEPING_DEFAULTS` after the `check_email_urgency` line:

```python
    "scan_job_emails":       {"name": "Career Tracker Catch-up",  "schedule": "cron",  "scheduled_time": None,    "cron_expression": "0 */6 * * *", "ship_paused": True, "legacy_names": []},
```

Add `"scan_job_emails",` to both `_SILENT_ACTIONS` and `_MODEL_BACKED_ACTIONS`.

- [x] **Step 5: Run tests**

Run: `python -m pytest tests/test_career_tracker_scan.py tests/test_scheduler_restart_doublefire.py -v`
Expected: all passed

- [x] **Step 6: Commit**

```bash
git add services/career/tracker_scan.py src/builtin_actions.py src/task_scheduler.py tests/test_career_tracker_scan.py
git commit -m "feat(career): backfill scan and paused scan_job_emails action"
```

---

### Task 7: Tracker routes

**Files:**
- Create: `routes/career_tracker_routes.py`
- Modify: `app.py` (mount after `setup_career_routes`)
- Test: `tests/test_career_tracker_routes.py`

**Interfaces:**
- Consumes: plan 00 `routes.career_helpers.application_to_dict/get_owned_application/computed_status`, `load_career_settings`; Task 5 `register`, `fetch_full`, `record_event`; Task 4 `suggest_reply`; Task 6 `scan_backfill`.
- Produces: `setup_career_tracker_routes() -> APIRouter` (prefix `/api/career/tracker`), `event_to_dict(ev, app=None) -> dict`, `build_summary(db, user, settings, now=None) -> dict`.

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/summary` | — | `{tiles: {active, awaiting, interviews, offers, rejected, response_rate}, recent: [event+company/role, newest 20], awaiting: [app dicts + nudge_due], closed: [app dicts], unsorted: [event dicts], updated_at}` |
| GET | `/events` | `application_id` | `{events: [...]}` newest first |
| POST | `/scan` | `{days: 30}` | backfill result |
| POST | `/events/{eid}/assign` | `{application_id}` | event dict |
| POST | `/events/{eid}/dismiss` | — | `{id, dismissed: true}` (row deleted) |
| POST | `/events/{eid}/nudge` | — | `{reply}` for an awaiting application's latest event (or app-level when no event) |
| POST | `/applications/{aid}/nudge` | — | `{reply}` |
| POST | `/link-email` | `{application_id, uid, folder, account_id}` | event dict |

Tiles: `active` = apps whose computed status ∈ {applied, acknowledged, screening, interview, offer}; `awaiting` = ∈ {applied, acknowledged, screening}; `interviews`/`offers`/`rejected` = counts of those computed statuses; `response_rate` = round(100 × apps with ≥1 event / apps with status ≠ drafting) or 0. `nudge_due` = awaiting and `days_since_event >= nudge_after_days`. `updated_at` = max of event `created_at` and application `updated_at`, ISO string or `""`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_tracker_routes.py
"""Tracker API: owner-scoped summary math, assign/dismiss, scan delegation."""
import importlib
import sys
import types
from datetime import datetime, timedelta

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient


def _import_real(*names, force=()):
    def _is_stub(m):
        return (not isinstance(m, types.ModuleType)
                or not isinstance(getattr(m, "__file__", None), str))

    saved = {}
    for key in list(sys.modules):
        top = key.split(".")[0]
        is_dep_stub = (
            top == "sqlalchemy" or key in ("core.database", "core.models", "src.database")
        ) and _is_stub(sys.modules[key])
        if is_dep_stub or key in force:
            saved[key] = sys.modules.pop(key)
    try:
        return [importlib.import_module(n) for n in names]
    finally:
        sys.modules.update(saved)


_sa, _sa_orm, _sa_pool, _coredb, ctr, cs = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "routes.career_tracker_routes", "services.career.settings",
    force=("core.database", "routes.career_tracker_routes", "routes.career_helpers"),
)
JobApplication = _coredb.JobApplication
JobApplicationEvent = _coredb.JobApplicationEvent


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    TestSession = _sa_orm.sessionmaker(bind=engine)
    monkeypatch.setattr(ctr, "SessionLocal", TestSession)
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(ctr, "get_current_user", lambda request: "vanders")
    monkeypatch.setattr(ctr, "register", lambda: None)
    now = datetime.utcnow()
    db = TestSession()
    db.add(JobApplication(id="d", owner="vanders", company="Draft Co", role="Dev", status="drafting"))
    db.add(JobApplication(id="w", owner="vanders", company="Wait Co", role="Dev", status="applied", applied_at=now - timedelta(days=12)))
    db.add(JobApplication(id="i", owner="vanders", company="Int Co", role="Dev", status="interview", last_event_at=now - timedelta(days=1)))
    db.add(JobApplication(id="r", owner="vanders", company="Rej Co", role="Dev", status="rejected", last_event_at=now))
    db.add(JobApplication(id="x", owner="other", company="Theirs", role="Dev", status="offer"))
    db.add(JobApplicationEvent(id="e1", owner="vanders", application_id="i", kind="interview_invite", sentiment="positive",
                               message_id="<i@x>", uid="5", subject="Interview", received_at=now - timedelta(days=1), can_reply=True,
                               suggested_reply="Hi, Thursday works."))
    db.add(JobApplicationEvent(id="e2", owner="vanders", application_id="r", kind="rejection", sentiment="negative",
                               message_id="<r@x>", uid="6", received_at=now))
    db.add(JobApplicationEvent(id="u1", owner="vanders", application_id=None, kind="other", sentiment="neutral",
                               message_id="<u@x>", uid="7", subject="Maybe a job?", received_at=now))
    db.add(JobApplicationEvent(id="ux", owner="other", application_id=None, kind="other", message_id="<ux@x>"))
    db.commit()
    db.close()
    app = FastAPI()
    app.include_router(ctr.setup_career_tracker_routes())
    return TestClient(app), TestSession


def test_summary_tiles_lists_and_owner_scope(env):
    client, _ = env
    s = client.get("/api/career/tracker/summary").json()
    assert s["tiles"] == {"active": 2, "awaiting": 1, "interviews": 1, "offers": 0, "rejected": 1, "response_rate": 67}
    assert [e["id"] for e in s["recent"]] == ["e2", "e1"]
    assert s["recent"][1]["company"] == "Int Co" and s["recent"][1]["suggested_reply"].startswith("Hi")
    assert [a["id"] for a in s["awaiting"]] == ["w"] and s["awaiting"][0]["nudge_due"] is True
    assert [a["id"] for a in s["closed"]] == ["r"]
    assert [e["id"] for e in s["unsorted"]] == ["u1"]
    assert s["updated_at"]


def test_events_for_application(env):
    client, _ = env
    assert [e["id"] for e in client.get("/api/career/tracker/events?application_id=i").json()["events"]] == ["e1"]
    assert client.get("/api/career/tracker/events?application_id=x").status_code == 404


def test_assign_and_dismiss_unsorted(env):
    client, TestSession = env
    res = client.post("/api/career/tracker/events/u1/assign", json={"application_id": "w"})
    assert res.status_code == 200 and res.json()["application_id"] == "w"
    db = TestSession()
    assert db.get(JobApplication, "w").last_event_at is not None
    db.close()
    assert client.post("/api/career/tracker/events/ux/dismiss").status_code == 404
    assert client.post("/api/career/tracker/events/e2/dismiss").json() == {"id": "e2", "dismissed": True}
    assert client.post("/api/career/tracker/events/u1/assign", json={"application_id": "x"}).status_code == 404


def test_scan_delegates_to_backfill(env, monkeypatch):
    client, _ = env
    seen = {}

    async def fake_scan(owner, days, **kw):
        seen["args"] = (owner, days)
        return {"scanned": 3, "candidates": 1, "events": 1, "accounts": 1}

    monkeypatch.setattr(ctr, "scan_backfill", fake_scan)
    assert client.post("/api/career/tracker/scan", json={"days": 14}).json()["events"] == 1
    assert seen["args"] == ("vanders", 14)
    assert client.post("/api/career/tracker/scan", json={"days": 999}).status_code == 422


def test_nudge_returns_draft(env, monkeypatch):
    client, _ = env

    async def fake_suggest(ctx, settings, complete, owner):
        return f"Following up on {ctx['company']}"

    monkeypatch.setattr(ctr, "suggest_reply", fake_suggest)
    assert client.post("/api/career/tracker/applications/w/nudge").json() == {"reply": "Following up on Wait Co"}
    assert client.post("/api/career/tracker/applications/x/nudge").status_code == 404
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_tracker_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'routes.career_tracker_routes'`

- [x] **Step 3: Implement**

```python
# routes/career_tracker_routes.py
"""Career tracker API — dashboard summary, unsorted triage, backfill, nudges.

Registers the tracker as a new-mail subscriber at setup so the live path is
active as soon as the router is mounted.
"""
from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, Field

from core.database import JobApplication, JobApplicationEvent, SessionLocal
from routes.career_helpers import application_to_dict, computed_status, days_since_event, get_owned_application
from services.career.settings import load_career_settings
from services.career.tracker import fetch_full, record_event, register
from services.career.tracker_replies import suggest_reply
from services.career.tracker_scan import scan_backfill
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)

_ACTIVE = {"applied", "acknowledged", "screening", "interview", "offer"}
_AWAITING = {"applied", "acknowledged", "screening"}
_CLOSED = {"rejected", "withdrawn", "ghosted"}


class ScanRequest(BaseModel):
    days: int = Field(30, ge=1, le=365)


class AssignRequest(BaseModel):
    application_id: str


class LinkEmailRequest(BaseModel):
    application_id: str
    uid: str
    folder: str = "INBOX"
    account_id: Optional[str] = None


def _iso(dt: Optional[datetime]) -> str:
    return dt.isoformat() if dt else ""


def event_to_dict(ev: JobApplicationEvent, app: Optional[JobApplication] = None) -> Dict[str, Any]:
    return {
        "id": ev.id, "application_id": ev.application_id, "kind": ev.kind, "sentiment": ev.sentiment,
        "message_id": ev.message_id, "uid": ev.uid, "folder": ev.folder or "INBOX", "account_id": ev.account_id,
        "from_address": ev.from_address, "reply_to": ev.reply_to, "subject": ev.subject,
        "received_at": _iso(ev.received_at), "summary": ev.summary, "can_reply": bool(ev.can_reply),
        "reply_blocked_reason": ev.reply_blocked_reason, "suggested_reply": ev.suggested_reply,
        "confidence": ev.confidence or 0, "company": app.company if app else None, "role": app.role if app else None,
    }


def build_summary(db, user: Optional[str], settings: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    now = now or datetime.utcnow()
    aq = db.query(JobApplication).filter(JobApplication.archived == False)  # noqa: E712
    eq = db.query(JobApplicationEvent)
    if user is not None:
        aq = aq.filter(JobApplication.owner == user)
        eq = eq.filter(JobApplicationEvent.owner == user)
    apps = aq.all()
    events = eq.order_by(JobApplicationEvent.received_at.desc()).all()
    by_id = {a.id: a for a in apps}
    with_events = {e.application_id for e in events if e.application_id}

    tiles = {"active": 0, "awaiting": 0, "interviews": 0, "offers": 0, "rejected": 0}
    awaiting, closed = [], []
    nudge_days = int(settings.get("nudge_after_days", 10))
    for a in apps:
        st = computed_status(a, settings, now)
        tiles["active"] += st in _ACTIVE
        tiles["awaiting"] += st in _AWAITING
        tiles["interviews"] += st == "interview"
        tiles["offers"] += st == "offer"
        tiles["rejected"] += st == "rejected"
        d = application_to_dict(a, settings, now)
        if st in _AWAITING:
            days = days_since_event(a, now)
            d["nudge_due"] = days is not None and days >= nudge_days
            awaiting.append(d)
        elif st in _CLOSED:
            closed.append(d)
    sent = [a for a in apps if a.status != "drafting"]
    tiles["response_rate"] = round(100 * len([a for a in sent if a.id in with_events]) / len(sent)) if sent else 0
    awaiting.sort(key=lambda d: -(d["days_since_event"] or 0))

    stamps = [e.created_at for e in events if e.created_at] + [a.updated_at for a in apps if a.updated_at]
    return {
        "tiles": tiles,
        "recent": [event_to_dict(e, by_id.get(e.application_id)) for e in events if e.application_id][:20],
        "awaiting": awaiting, "closed": closed,
        "unsorted": [event_to_dict(e) for e in events if not e.application_id],
        "updated_at": _iso(max(stamps)) if stamps else "",
    }


def setup_career_tracker_routes() -> APIRouter:
    router = APIRouter(prefix="/api/career/tracker", tags=["career"])
    register()

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _owned_event(db, eid: str, user: Optional[str]) -> JobApplicationEvent:
        q = db.query(JobApplicationEvent).filter(JobApplicationEvent.id == eid)
        if user is not None:
            q = q.filter(JobApplicationEvent.owner == user)
        ev = q.first()
        if not ev:
            raise HTTPException(404, "Event not found")
        return ev

    @router.get("/summary")
    def summary(request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            return build_summary(db, user, load_career_settings(user))
        finally:
            db.close()

    @router.get("/events")
    def events(request: Request, application_id: str = Query(...)):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, application_id, user)
            rows = (db.query(JobApplicationEvent).filter(JobApplicationEvent.application_id == app.id)
                    .order_by(JobApplicationEvent.received_at.desc()).all())
            return {"events": [event_to_dict(e, app) for e in rows]}
        finally:
            db.close()

    @router.post("/scan")
    async def scan(body: ScanRequest, request: Request):
        user = _owner(request)
        return await scan_backfill(user or "", body.days)

    @router.post("/events/{eid}/assign")
    def assign(eid: str, body: AssignRequest, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            ev = _owned_event(db, eid, user)
            app = get_owned_application(db, body.application_id, user)
            ev.application_id = app.id
            app.last_event_at = ev.received_at or datetime.utcnow()
            db.commit()
            db.refresh(ev)
            return event_to_dict(ev, app)
        finally:
            db.close()

    @router.post("/events/{eid}/dismiss")
    def dismiss(eid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            ev = _owned_event(db, eid, user)
            db.delete(ev)
            db.commit()
            return {"id": eid, "dismissed": True}
        finally:
            db.close()

    @router.post("/applications/{aid}/nudge")
    async def nudge(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            ctx = {"kind": "nudge", "company": app.company, "role": app.role, "subject": "", "summary": "",
                   "next_step": "", "body": ""}
        finally:
            db.close()
        from services.career.llm import complete
        return {"reply": await suggest_reply(ctx, load_career_settings(user), complete, user)}

    @router.post("/link-email")
    async def link_email(body: LinkEmailRequest, request: Request):
        user = _owner(request)
        full = await fetch_full(user or "", body.account_id, body.uid, body.folder)
        db = SessionLocal()
        try:
            app = get_owned_application(db, body.application_id, user)
            verdict = {"kind": "other", "sentiment": "neutral", "confidence": 100, "summary": "Linked manually"}
            ev = record_event(db, user, app, verdict, full["headers"], uid=body.uid, folder=body.folder,
                              account_id=body.account_id, can_reply=False, reply_blocked_reason=None)
            if ev is None:
                raise HTTPException(409, "That email is already recorded")
            return event_to_dict(ev, app)
        finally:
            db.close()

    return router
```

Mount in `app.py` directly after `app.include_router(setup_career_routes(skills_manager, rag_manager))`:

```python
from routes.career_tracker_routes import setup_career_tracker_routes
app.include_router(setup_career_tracker_routes())
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_tracker_routes.py -v`
Expected: 5 passed. If `routes/career_helpers.py` from plan 00 does not export `days_since_event`, it does define it at module level (used by `application_to_dict`); import it as shown.

- [x] **Step 5: Commit**

```bash
git add routes/career_tracker_routes.py app.py tests/test_career_tracker_routes.py
git commit -m "feat(career): tracker summary, triage, scan and nudge API"
```

---

### Task 8: Tracker tab UI

**Files:**
- Create: `static/js/careerTrackerLogic.js`, `static/js/careerTracker.js`
- Modify: `static/js/career.js` (one import line + one install line)
- Modify: `static/style.css` (append tracker rules)
- Test: `tests/test_career_tracker_js.py`

**Interfaces:**
- Consumes: the plan 00 `career.js` plugin API object (`registerTab`, `onPanelClick`, `refresh`, `render`, `getState`, `isOpen`, `close`) passed to `installTracker(api)`; `careerApplications.detailSections`; `careerLogic.statusChip/daysLabel`; `uiModule.esc/showToast/showError/copyToClipboard`.
- Produces: `careerTrackerLogic.js` exports `tilesHtml(tiles, esc)`, `sentimentChip(kind, sentiment)`, `eventRowHtml(ev, esc, {showApp})`, `nudgeDue(app, settings, now)`, `summaryChanged(prev, next)`; `careerTracker.js` exports `installTracker(api)`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_tracker_js.py
"""careerTrackerLogic.js helpers under Node, plus wiring assertions."""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def node_available():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(["node", "--input-type=module", "-e", script], cwd=_REPO,
                         capture_output=True, timeout=15, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads([ln for ln in res.stdout.splitlines() if ln.strip()][-1])


def test_tiles_and_chips(node_available):
    out = _run_node(textwrap.dedent("""
        const { tilesHtml, sentimentChip } = await import('./static/js/careerTrackerLogic.js');
        const esc = (s) => String(s);
        console.log(JSON.stringify({
          tiles: tilesHtml({ active: 3, awaiting: 1, interviews: 1, offers: 0, rejected: 2, response_rate: 40 }, esc),
          pos: sentimentChip('interview_invite', 'positive'), neg: sentimentChip('rejection', 'negative'),
          neu: sentimentChip('confirmation', 'neutral'), other: sentimentChip('other', 'neutral'),
        }));
    """))
    assert out["tiles"].count('class="career-tile admin-card"') == 6
    assert "40%" in out["tiles"] and "Response rate" in out["tiles"]
    assert out["pos"] == {"label": "Interview invite", "cls": "career-chip-positive"}
    assert out["neg"] == {"label": "Rejection", "cls": "career-chip-negative"}
    assert out["neu"] == {"label": "Confirmation", "cls": "career-chip-waiting"}
    assert out["other"] == {"label": "Other", "cls": "career-chip-muted"}


def test_event_row_buttons_and_nudge_due(node_available):
    out = _run_node(textwrap.dedent("""
        const { eventRowHtml, nudgeDue, summaryChanged } = await import('./static/js/careerTrackerLogic.js');
        const esc = (s) => String(s ?? '');
        const ev = { id: 'e1', kind: 'interview_invite', sentiment: 'positive', company: 'Acme', role: 'Dev',
                     subject: 'Interview', summary: 'Thu 10:00', uid: '5', folder: 'INBOX', can_reply: true,
                     suggested_reply: 'Hi Sarah' };
        const noReply = { ...ev, id: 'e2', can_reply: false, suggested_reply: null, reply_blocked_reason: 'no-reply sender' };
        console.log(JSON.stringify({
          withReply: eventRowHtml(ev, esc, { showApp: true }),
          blocked: eventRowHtml(noReply, esc, { showApp: false }),
          due: nudgeDue({ computed_status: 'applied', days_since_event: 11 }, { nudge_after_days: 10 }),
          notDue: nudgeDue({ computed_status: 'applied', days_since_event: 3 }, { nudge_after_days: 10 }),
          closed: nudgeDue({ computed_status: 'rejected', days_since_event: 40 }, { nudge_after_days: 10 }),
          changed: summaryChanged({ updated_at: 'a' }, { updated_at: 'b' }),
          same: summaryChanged({ updated_at: 'a' }, { updated_at: 'a' }),
        }));
    """))
    assert 'data-action="open-email"' in out["withReply"] and 'data-uid="5"' in out["withReply"]
    assert 'data-action="copy-reply"' in out["withReply"] and "Acme — Dev" in out["withReply"]
    assert 'data-action="copy-reply"' not in out["blocked"] and "no-reply sender" in out["blocked"]
    assert "Acme — Dev" not in out["blocked"]
    assert out["due"] is True and out["notDue"] is False and out["closed"] is False
    assert out["changed"] is True and out["same"] is False


def test_tracker_wired_into_hub():
    career = (_REPO / "static" / "js" / "career.js").read_text(encoding="utf-8")
    tracker = (_REPO / "static" / "js" / "careerTracker.js").read_text(encoding="utf-8")
    assert "import { installTracker } from './careerTracker.js';" in career
    assert "installTracker(careerApi);" in career
    assert "export const installTracker" in tracker
    assert "api.registerTab('tracker'" in tracker and "api.onPanelClick(" in tracker
    assert "from './career.js'" not in tracker
    assert "detailSections.push(" in tracker
    assert "/api/career/tracker/summary" in tracker
    assert "#email=" in tracker
    for f in ("careerTracker.js", "careerTrackerLogic.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_tracker_js.py -v`
Expected: FAIL with `Cannot find module` / assertion on missing import.

- [x] **Step 3: `static/js/careerTrackerLogic.js`**

```javascript
// ============================================
// Career tracker — pure logic (no DOM, Node-importable for tests)
// ============================================

const KIND_LABELS = {
  confirmation: 'Confirmation',
  rejection: 'Rejection',
  interview_invite: 'Interview invite',
  offer: 'Offer',
  info_request: 'Info request',
  recruiter_outreach: 'Recruiter outreach',
  other: 'Other',
};

export const sentimentChip = (kind, sentiment) => {
  const label = KIND_LABELS[kind] || 'Other';
  if (kind === 'other') return { label, cls: 'career-chip-muted' };
  const cls = sentiment === 'positive' ? 'career-chip-positive'
    : sentiment === 'negative' ? 'career-chip-negative'
      : 'career-chip-waiting';
  return { label, cls };
};

const TILES = [
  ['active', 'Active'], ['awaiting', 'Awaiting response'], ['interviews', 'Interviews'],
  ['offers', 'Offers'], ['rejected', 'Rejected'], ['response_rate', 'Response rate'],
];

export const tilesHtml = (tiles, esc) => `<div class="career-tiles">${TILES.map(([key, label]) => {
  const raw = (tiles || {})[key] ?? 0;
  const value = key === 'response_rate' ? `${raw}%` : String(raw);
  return `<div class="career-tile admin-card"><span class="career-tile-num">${esc(value)}</span><span class="career-tile-label">${esc(label)}</span></div>`;
}).join('')}</div>`;

export const eventRowHtml = (ev, esc, { showApp = true } = {}) => {
  const chip = sentimentChip(ev.kind, ev.sentiment);
  const who = showApp && ev.company ? `<span class="proj-name">${esc(ev.company)} — ${esc(ev.role || '')}</span>` : '';
  const reply = ev.suggested_reply
    ? `<button class="memory-toolbar-btn" data-action="copy-reply">Copy reply</button>`
    : ev.reply_blocked_reason ? `<span class="memory-desc">No reply: ${esc(ev.reply_blocked_reason)}</span>` : '';
  return `
    <div class="report-row admin-card career-event" data-event-id="${esc(ev.id)}" data-uid="${esc(ev.uid || '')}" data-folder="${esc(ev.folder || 'INBOX')}">
      <div class="proj-card-head">${who}<span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>
        <span class="memory-desc">${esc((ev.received_at || '').slice(0, 10))}</span></div>
      <div class="memory-desc report-scores">${esc(ev.subject || '(no subject)')}</div>
      ${ev.summary ? `<div class="memory-desc">${esc(ev.summary)}</div>` : ''}
      ${ev.suggested_reply ? `<pre class="career-reply">${esc(ev.suggested_reply)}</pre>` : ''}
      <div class="proj-actions">
        ${ev.uid ? '<button class="memory-toolbar-btn" data-action="open-email">Open email</button>' : ''}
        ${reply}
      </div>
    </div>`;
};

const AWAITING = new Set(['applied', 'acknowledged', 'screening']);

export const nudgeDue = (app, settings, now) => {
  if (!AWAITING.has(app.computed_status)) return false;
  const days = app.days_since_event;
  return Number.isFinite(days) && days >= Number((settings || {}).nudge_after_days ?? 10);
};

export const summaryChanged = (prev, next) => (prev || {}).updated_at !== (next || {}).updated_at;
```

- [x] **Step 4: `static/js/careerTracker.js`**

```javascript
// ============================================
// Career hub — Tracker tab. Plugs in through installTracker(api) (plan 00
// plugin contract; never imports career.js) and polls the summary every 20 s
// while the tab is visible.
// ============================================
import uiModule from './ui.js';
import { detailSections } from './careerApplications.js';
import { daysLabel, statusChip } from './careerLogic.js';
import { eventRowHtml, nudgeDue, summaryChanged, tilesHtml } from './careerTrackerLogic.js';

const esc = uiModule.esc;
const POLL_MS = 20000;
let _api = null;
let _summary = null;
let _events = {};        // application id -> events (detail view)
let _pollTimer = null;
let _busy = false;

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${url} failed (${res.status})`);
  return data;
};

const _appRow = (a, settings, awaiting) => {
  const chip = statusChip(a.computed_status);
  return `
    <div class="report-row admin-card career-app" data-application-id="${esc(a.id)}">
      <div class="proj-card-head"><span class="proj-name">${esc(a.company)} — ${esc(a.role)}</span>
        <span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>
        <span class="memory-desc">${esc(daysLabel(a.days_since_event))}</span></div>
      ${awaiting && nudgeDue(a, settings) ? '<div class="proj-actions"><button class="memory-toolbar-btn" data-action="suggest-nudge">Suggest nudge</button></div>' : ''}
    </div>`;
};

const _unsortedRow = (ev, apps) => {
  const opts = apps.map((a) => `<option value="${esc(a.id)}">${esc(a.company)} — ${esc(a.role)}</option>`).join('');
  return `
    <div class="report-row admin-card career-event" data-event-id="${esc(ev.id)}" data-uid="${esc(ev.uid || '')}" data-folder="${esc(ev.folder || 'INBOX')}">
      <div class="memory-desc report-scores">${esc(ev.subject || '(no subject)')} — ${esc(ev.from_address || '')}</div>
      ${ev.summary ? `<div class="memory-desc">${esc(ev.summary)}</div>` : ''}
      <div class="council-run-form">
        <select class="settings-input" data-role="assign-target"><option value="">Assign to…</option>${opts}</select>
        <button class="memory-toolbar-btn" data-action="assign-event">Assign</button>
        <button class="memory-toolbar-btn" data-action="dismiss-event">Dismiss</button>
        ${ev.uid ? '<button class="memory-toolbar-btn" data-action="open-email">Open email</button>' : ''}
      </div>
    </div>`;
};

const _trackerHtml = (state) => {
  if (!_summary) return '<p class="memory-desc">Loading tracker…</p>';
  const s = _summary;
  const settings = state.settings || {};
  return `
    ${tilesHtml(s.tiles, esc)}
    <div class="council-run-form">
      <button id="career-scan-btn" class="memory-toolbar-btn" ${_busy ? 'disabled' : ''}>${_busy ? 'Scanning…' : 'Scan last 30 days'}</button>
      <span class="memory-desc">Live: new inbox mail is classified automatically while Odysseus is open.</span>
    </div>
    <h3 class="cc-section">Recent responses</h3>
    ${s.recent.length ? s.recent.map((e) => eventRowHtml(e, esc, { showApp: true })).join('') : '<p class="memory-desc">No responses yet.</p>'}
    <h3 class="cc-section">Awaiting response (${s.awaiting.length})</h3>
    ${s.awaiting.length ? s.awaiting.map((a) => _appRow(a, settings, true)).join('') : '<p class="memory-desc">Nothing in flight.</p>'}
    ${s.unsorted.length ? `<h3 class="cc-section">Unsorted (${s.unsorted.length})</h3>${s.unsorted.map((e) => _unsortedRow(e, state.apps)).join('')}` : ''}
    <details class="proj-summary"><summary>Closed (${s.closed.length})</summary>${s.closed.map((a) => _appRow(a, settings, false)).join('')}</details>`;
};

const _loadSummary = async () => {
  const next = await _json('/api/career/tracker/summary');
  const changed = summaryChanged(_summary, next);
  _summary = next;
  return changed;
};

const _schedulePoll = () => {
  clearTimeout(_pollTimer);
  if (!_api.isOpen() || _api.getState().tab !== 'tracker') return;
  _pollTimer = setTimeout(async () => {
    try {
      if (await _loadSummary()) _api.render();
    } catch (e) {
      console.warn('tracker poll', e);
    } finally {
      _schedulePoll();
    }
  }, POLL_MS);
};

const renderTracker = (state) => {
  if (!_summary) {
    _loadSummary().then(() => _api.render()).catch((e) => uiModule.showError(`Tracker: ${e.message}`));
  }
  _schedulePoll();
  return _trackerHtml(state);
};

// Application detail: events timeline (loaded lazily, cached per application).
const eventsSection = (app) => {
  const evs = _events[app.id];
  if (!evs) {
    _json(`/api/career/tracker/events?application_id=${encodeURIComponent(app.id)}`)
      .then((d) => { _events[app.id] = d.events || []; _api.render(); })
      .catch(() => { _events[app.id] = []; });
    return '<h3 class="cc-section">Email events</h3><p class="memory-desc">Loading…</p>';
  }
  return `<h3 class="cc-section">Email events (${evs.length})</h3>${evs.length
    ? evs.map((e) => eventRowHtml(e, esc, { showApp: false })).join('')
    : '<p class="memory-desc">No emails linked yet.</p>'}`;
};

const _openEmail = (row) => {
  const uid = row?.dataset.uid;
  if (!uid) return;
  const folder = row.dataset.folder || 'INBOX';
  _api.close();
  window.location.hash = `#email=${encodeURIComponent(folder)}:${uid}`;
  const hdr = document.querySelector('#email-section .section-header-flex');
  if (hdr && !document.getElementById('email-lib-modal')) hdr.click();
};

const _invalidate = async () => { _summary = null; _events = {}; await _api.refresh(); };

const handleClick = (ev, state) => {
  const row = ev.target.closest('[data-event-id]');
  if (ev.target.closest('#career-scan-btn')) {
    if (_busy) return true;
    _busy = true; _api.render();
    _json('/api/career/tracker/scan', { method: 'POST', body: JSON.stringify({ days: 30 }) })
      .then((r) => uiModule.showToast(`Scanned ${r.scanned} emails, ${r.events} tracker events`))
      .catch((e) => uiModule.showError(`Scan: ${e.message}`))
      .finally(async () => { _busy = false; await _invalidate(); });
    return true;
  }
  if (ev.target.closest('[data-action="open-email"]')) { _openEmail(row); return true; }
  if (ev.target.closest('[data-action="copy-reply"]')) {
    const text = row?.querySelector('.career-reply')?.textContent || '';
    uiModule.copyToClipboard(text);
    return true;
  }
  if (ev.target.closest('[data-action="assign-event"]')) {
    const target = row?.querySelector('[data-role="assign-target"]')?.value;
    if (!target) { uiModule.showError('Pick an application first'); return true; }
    _json(`/api/career/tracker/events/${row.dataset.eventId}/assign`, { method: 'POST', body: JSON.stringify({ application_id: target }) })
      .then(_invalidate).catch((e) => uiModule.showError(`Assign: ${e.message}`));
    return true;
  }
  if (ev.target.closest('[data-action="dismiss-event"]')) {
    _json(`/api/career/tracker/events/${row.dataset.eventId}/dismiss`, { method: 'POST' })
      .then(_invalidate).catch((e) => uiModule.showError(`Dismiss: ${e.message}`));
    return true;
  }
  if (ev.target.closest('[data-action="suggest-nudge"]')) {
    const id = ev.target.closest('[data-application-id]')?.dataset.applicationId;
    if (!id) return true;
    _json(`/api/career/tracker/applications/${id}/nudge`, { method: 'POST' })
      .then((r) => { uiModule.copyToClipboard(r.reply); uiModule.showToast('Nudge draft copied to clipboard'); })
      .catch((e) => uiModule.showError(`Nudge: ${e.message}`));
    return true;
  }
  return false;
};

export const installTracker = (api) => {
  _api = api;
  api.registerTab('tracker', renderTracker);
  detailSections.push(eventsSection);
  api.onPanelClick(handleClick);
};
```

In `static/js/career.js`, add one import line after the `careerSetup.js` import and one install line inside the `// ── plugins ──` block (after `const careerApi = ...`, before `export default careerApi;`):

```javascript
import { installTracker } from './careerTracker.js';
```

```javascript
installTracker(careerApi);
```

`careerTracker.js` never imports `career.js`, so there is no module cycle.

- [x] **Step 5: `static/style.css`**

Append after the `.career-tile-label` rule from plan 00:

```css
.career-event .proj-card-head { gap: 8px; flex-wrap: wrap; }
.career-reply { white-space: pre-wrap; font-size: 12px; padding: 8px; margin: 0; border-left: 3px solid color-mix(in srgb, var(--fg) 30%, transparent); opacity: 0.9; }
.career-app .proj-card-head { gap: 8px; }
```

- [x] **Step 6: Run tests**

Run: `python -m pytest tests/test_career_tracker_js.py tests/test_career_js.py tests/test_career_ui_wiring.py -v`
Expected: all passed

- [x] **Step 7: Commit**

```bash
git add static/js/careerTracker.js static/js/careerTrackerLogic.js static/js/career.js static/style.css tests/test_career_tracker_js.py
git commit -m "feat(career): live Tracker tab with tiles, responses, awaiting and unsorted triage"
```

---

### Task 9: Full verification and smoke test

- [x] **Step 1: Syntax and suites**

```bash
python -m py_compile app.py routes/*.py src/*.py services/career/*.py
node --check static/js/careerTracker.js && node --check static/js/careerTrackerLogic.js && node --check static/js/career.js
python -m pytest tests/test_career_*.py tests/test_email_new_mail_subscribers.py tests/test_security_regressions.py tests/test_auth_regressions.py -v
python -m pytest
```
Expected: every suite green; the full run shows no new failures versus the pre-plan tree.

- [ ] **Step 2: Manual smoke (server running, one email account configured, a Task or Utility model reachable)**

1. Open `http://127.0.0.1:7000/career` → Tracker tab shows six tiles (all zero on a fresh install) and the scan button.
2. Click **Scan last 30 days** → toast reports scanned/events counts; any real confirmations/rejections from the last month appear under Recent responses and the matching applications under Awaiting/Closed.
3. From another mailbox send yourself: subject `Thank you for applying to Acme — Backend Engineer`, body "We have received your application." Keep Odysseus open. Within one inbox poll (≤60 s) the **Awaiting response** tile increments, a toast is not expected (neutral), and Applications lists "Acme — Backend Engineer" with status Acknowledged.
4. Send `We'd like to invite you to interview` from a normal (non no-reply) address, body "Are you free Thursday at 10:00?" → within ≤60 s: a browser toast/notification "Career tracker", Recent responses shows the positive chip, the application status flips to Interview, and **Copy reply** is present with the draft.
5. Click **Open email** → the email library opens on that message and the reader's AI reply button reads "AI Reply (cached draft ready)".
6. Send a job-shaped email with an ambiguous body (e.g. subject "Your application" from a personal address, body "hey") → it appears under **Unsorted**; assign it to an application, then dismiss another.
7. Set "Suggest a nudge after" to 1 in Setup, reload Tracker → the Acknowledged application shows **Suggest nudge**; click it → a follow-up draft is copied to the clipboard.
8. Close the modal and reopen → tiles are correct; leave the tab open for a minute and confirm the panel does not flicker (poll re-renders only on change).

- [ ] **Step 3: Final commit (if anything was touched during smoke)**

```bash
git add -A
git commit -m "chore(career): tracker smoke fixes"
```

---

## Done when

- [x] `tests/test_email_new_mail_subscribers.py` and all `tests/test_career_*.py` pass; the full `python -m pytest` has no new failures.
- [ ] A confirmation email creates an application and an event within one inbox poll while the app is open; an interview invite flips status, drafts a reply, and triggers a notification.
- [ ] The reply draft is visible both in the Tracker row and in the email reader's cached AI reply.
- [x] No-reply / bulk senders never get a draft and the row explains why.
- [x] Low-confidence classifications land in Unsorted and can be assigned or dismissed.
- [x] `POST /api/career/tracker/scan` imports history; the `scan_job_emails` housekeeping task exists and ships paused.
- [x] The inbox list route is unchanged in latency: subscriber work runs in background tasks and the first list after startup only baselines.
