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
