"""Council report routes — owner scoping + approval-flow status transitions.

Functional tests: real router on a bare FastAPI app with an in-memory DB and
the runner monkeypatched out (route tests don't run models). Uses the unstub
window from tests/test_project_routes_owner_scope.py.
"""

import importlib
import json
import sys
import types

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


_sa, _sa_orm, _sa_pool, _coredb, cr = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool",
    "core.database", "routes.council_routes",
    force=("core.database", "routes.council_routes",
           "services.council.runner"),
)
create_engine = _sa.create_engine
sessionmaker = _sa_orm.sessionmaker
StaticPool = _sa_pool.StaticPool
Base = _coredb.Base
Project = _coredb.Project
Report = _coredb.Report


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(cr, "SessionLocal", TestSession)

    current = {"user": "vanders"}
    monkeypatch.setattr(cr, "get_current_user", lambda request: current["user"])

    launched = []

    async def fake_run_report(report_id, **kw):
        launched.append(report_id)
        return {"status": "draft", "report_id": report_id}

    monkeypatch.setattr(cr, "run_report", fake_run_report)

    db = TestSession()
    db.add(Project(id="p1", owner="vanders", name="odysseus", path=str(tmp_path)))
    db.add(Project(id="p2", owner="other", name="theirs", path=str(tmp_path / "x")))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(cr.setup_council_routes())
    return TestClient(app), TestSession, current, launched


def _insert_report(TestSession, **overrides):
    fields = dict(id="r1", owner="vanders", project_id="p1",
                  title="Developer Review: odysseus", status="draft",
                  body="## Findings", scores='{"grounded": {}, "judged": {}}')
    fields.update(overrides)
    db = TestSession()
    db.add(Report(**fields))
    db.commit()
    db.close()


def test_create_report_queues_run(env):
    client, TestSession, _, launched = env
    res = client.post("/api/council/reports", json={"project_id": "p1"})
    assert res.status_code == 200
    rid = res.json()["report_id"]
    assert res.json()["status"] == "running"
    assert launched == [rid]

    db = TestSession()
    row = db.get(Report, rid)
    assert row.owner == "vanders"
    assert row.status == "running"
    assert row.report_type == "developer_review"
    assert "odysseus" in row.title
    db.close()


def test_create_report_404_for_foreign_project(env):
    client, _, _, launched = env
    res = client.post("/api/council/reports", json={"project_id": "p2"})
    assert res.status_code == 404
    assert launched == []


def test_create_report_rejects_unknown_report_type(env):
    client, _, _, launched = env
    res = client.post("/api/council/reports",
                      json={"project_id": "p1", "report_type": "../evil"})
    assert res.status_code == 400
    assert launched == []


def test_list_reports_owner_scoped_and_status_filtered(env):
    client, TestSession, _, _ = env
    _insert_report(TestSession, id="r1", status="draft")
    _insert_report(TestSession, id="r2", status="approved")
    _insert_report(TestSession, id="r3", owner="other", project_id="p2")

    res = client.get("/api/council/reports?status=draft")
    ids = [r["id"] for r in res.json()["reports"]]
    assert ids == ["r1"]

    res = client.get("/api/council/reports")
    ids = {r["id"] for r in res.json()["reports"]}
    assert ids == {"r1", "r2"}


def test_report_detail_includes_body_and_parsed_scores(env):
    client, TestSession, _, _ = env
    _insert_report(
        TestSession,
        scores='{"grounded": {"technical_readiness": 70}, "judged": {"marketability": 50}}',
    )
    res = client.get("/api/council/reports/r1")
    assert res.status_code == 200
    data = res.json()
    assert data["body"] == "## Findings"
    assert data["scores"]["grounded"]["technical_readiness"] == 70
    assert data["scores"]["judged"]["marketability"] == 50


def test_report_detail_tolerates_corrupt_scores_json(env):
    client, TestSession, _, _ = env
    _insert_report(TestSession, scores="{not-json")
    res = client.get("/api/council/reports/r1")
    assert res.status_code == 200
    assert res.json()["scores"] == {}


def test_report_detail_404_for_foreign_report(env):
    client, TestSession, _, _ = env
    _insert_report(TestSession, id="r3", owner="other", project_id="p2")
    assert client.get("/api/council/reports/r3").status_code == 404


def test_approve_flips_draft_to_approved(env):
    client, TestSession, _, _ = env
    _insert_report(TestSession)
    res = client.post("/api/council/reports/r1/approve")
    assert res.status_code == 200
    db = TestSession()
    assert db.get(Report, "r1").status == "approved"
    db.close()


def test_dismiss_flips_draft_to_dismissed(env):
    client, TestSession, _, _ = env
    _insert_report(TestSession)
    res = client.post("/api/council/reports/r1/dismiss")
    assert res.status_code == 200
    db = TestSession()
    assert db.get(Report, "r1").status == "dismissed"
    db.close()


def test_approve_rejected_unless_draft(env):
    client, TestSession, _, _ = env
    _insert_report(TestSession, id="rd", status="dismissed")
    _insert_report(TestSession, id="rr", status="running")
    assert client.post("/api/council/reports/rd/approve").status_code == 409
    assert client.post("/api/council/reports/rr/approve").status_code == 409


def test_approve_404_for_foreign_report(env):
    client, TestSession, _, _ = env
    _insert_report(TestSession, id="r3", owner="other", project_id="p2")
    assert client.post("/api/council/reports/r3/approve").status_code == 404
