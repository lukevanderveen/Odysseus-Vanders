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


for _name in ("src.endpoint_resolver", "src.llm_core", "core.atomic_io"):
    # Collection-time stubs from other test modules; evict so the tracker
    # router's career imports bind the real modules.
    _mod = sys.modules.get(_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(_name, None)
        for _k in ("services.career.llm", "services.career.settings"):
            sys.modules.pop(_k, None)

_sa, _sa_orm, _sa_pool, _coredb, ctr, cs = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "routes.career_tracker_routes", "services.career.settings",
    force=("core.database", "routes.career_tracker_routes", "routes.career_helpers",
           "services.career.tracker"),
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


def _fake_fetch_full(message_id):
    async def fetch(owner, account_id, uid, folder="INBOX"):
        return {"headers": {"message_id": message_id, "subject": "Re: application", "from": "hr@wait.co",
                            "from_address": "hr@wait.co", "reply_to": "", "date": ""}, "body": ""}
    return fetch


def test_link_email_records_event_once(env, monkeypatch):
    client, _ = env
    monkeypatch.setattr(ctr, "fetch_full", _fake_fetch_full("<link@wait>"))
    body = {"application_id": "w", "uid": "42", "folder": "INBOX", "account_id": "acc"}
    res = client.post("/api/career/tracker/link-email", json=body)
    assert res.status_code == 200 and res.json()["application_id"] == "w" and res.json()["uid"] == "42"
    assert client.post("/api/career/tracker/link-email", json=body).status_code == 409
    assert client.post("/api/career/tracker/link-email", json={**body, "application_id": "x"}).status_code == 404


def test_link_email_unknown_uid_is_404(env, monkeypatch):
    client, _ = env

    async def missing(owner, account_id, uid, folder="INBOX"):
        raise RuntimeError(f"UID {uid} not found in {folder}")

    monkeypatch.setattr(ctr, "fetch_full", missing)
    res = client.post("/api/career/tracker/link-email", json={"application_id": "w", "uid": "999"})
    assert res.status_code == 404
