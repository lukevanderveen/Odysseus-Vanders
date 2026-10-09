"""Review + reviewer endpoints on the career studio router (plan 02)."""
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


for _name in ("src.endpoint_resolver", "src.llm_core", "core.atomic_io"):
    # Collection-time stubs from other test modules; evict so the studio router's
    # career imports bind the real modules.
    _mod = sys.modules.get(_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(_name, None)
        for _k in ("services.career.llm", "services.career.templates", "services.career.settings",
                   "services.career.claims_store", "services.career.cover_letter"):
            sys.modules.pop(_k, None)

_sa, _sa_orm, _sa_pool, _coredb, crs = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database", "routes.career_studio_routes",
    force=("core.database", "routes.career_studio_routes", "services.career.review_runner"),
)
JobApplication = _coredb.JobApplication
ApplicationReview = _coredb.ApplicationReview
Document = _coredb.Document
CrewMember = _coredb.CrewMember


@pytest.fixture()
def env(monkeypatch):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    TestSession = _sa_orm.sessionmaker(bind=engine)
    monkeypatch.setattr(crs, "SessionLocal", TestSession)
    current = {"user": "vanders"}
    monkeypatch.setattr(crs, "get_current_user", lambda request: current["user"])
    launched = []

    async def fake_run_review(review_id, **kw):
        launched.append(review_id)
        return {"status": "done", "review_id": review_id}

    monkeypatch.setattr(crs, "run_review", fake_run_review)
    queue = {"running": None, "queued": []}
    monkeypatch.setattr(crs, "queue_state", lambda: queue)

    db = TestSession()
    db.add(Document(id="d1", owner="vanders", title="CL", current_content="Dear Acme"))
    db.add(JobApplication(id="a1", owner="vanders", company="Acme", role="Dev", cover_letter_doc_id="d1"))
    db.add(JobApplication(id="a2", owner="vanders", company="Beta", role="Dev"))
    db.add(JobApplication(id="a3", owner="other", company="Theirs", role="Dev", cover_letter_doc_id="d1"))
    db.add(CrewMember(id="rv1", owner="vanders", name="Recruiter screener", department="career_recruiter",
                      personality="p"))
    db.add(CrewMember(id="rv2", owner="other", name="Theirs", department="career_hr"))
    db.add(CrewMember(id="cm1", owner="vanders", name="Dev dept", department="developer"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(crs.setup_career_studio_routes(None))
    return TestClient(app), TestSession, current, launched, queue


def test_create_review_launches_run_and_409_without_letter(env):
    client, TestSession, _, launched, _ = env
    res = client.post("/api/career/applications/a1/reviews")
    assert res.status_code == 200 and res.json()["status"] == "running"
    assert launched == [res.json()["review_id"]]
    db = TestSession()
    row = db.get(ApplicationReview, res.json()["review_id"])
    assert row.owner == "vanders" and row.application_id == "a1"
    db.close()
    assert client.post("/api/career/applications/a2/reviews").status_code == 409
    assert client.post("/api/career/applications/a3/reviews").status_code == 404


def test_list_and_get_reviews_are_owner_scoped(env):
    client, TestSession, _, _, _ = env
    db = TestSession()
    db.add(ApplicationReview(id="r1", owner="vanders", application_id="a1", status="done",
                             verdicts=json.dumps({"career_hr": {"name": "HR", "verdict": "maybe", "body": "b", "scores": {}}}),
                             panel_summary="## Verdict\nrevise", scores=json.dumps({"overall": 60, "panel_verdict": "revise"})))
    db.add(ApplicationReview(id="r2", owner="other", application_id="a3", status="done"))
    db.commit()
    db.close()
    listed = client.get("/api/career/applications/a1/reviews").json()["reviews"]
    assert [r["id"] for r in listed] == ["r1"] and "verdicts" not in listed[0]
    assert listed[0]["scores"]["panel_verdict"] == "revise"
    full = client.get("/api/career/reviews/r1").json()
    assert full["verdicts"]["career_hr"]["verdict"] == "maybe" and full["panel_summary"].startswith("## Verdict")
    assert client.get("/api/career/reviews/r2").status_code == 404
    assert client.get("/api/career/applications/a3/reviews").status_code == 404


def test_reviewers_list_seeds_missing_and_excludes_council(env):
    client, _, _, _, _ = env
    reviewers = client.get("/api/career/reviewers").json()["reviewers"]
    assert len(reviewers) == 4
    assert all(r["department"].startswith("career_") for r in reviewers)
    assert any(r["id"] == "rv1" for r in reviewers)   # existing row kept, not duplicated


def test_reviewer_update_scope(env):
    client, TestSession, _, _, _ = env
    res = client.put("/api/career/reviewers/rv1", json={"personality": "Edited.", "model": "qwen"})
    assert res.status_code == 200 and res.json()["personality"] == "Edited." and res.json()["model"] == "qwen"
    assert client.put("/api/career/reviewers/rv2", json={"name": "x"}).status_code == 404
    assert client.put("/api/career/reviewers/cm1", json={"name": "x"}).status_code == 404
    db = TestSession()
    assert db.get(CrewMember, "cm1").name == "Dev dept"
    db.close()


def test_reviewers_status_reflects_queue(env):
    client, TestSession, _, _, queue = env
    db = TestSession()
    db.add(ApplicationReview(id="r1", owner="vanders", application_id="a1"))
    db.commit()
    db.close()
    queue["running"] = "r1"
    status = client.get("/api/career/reviewers/status").json()
    assert status["running"] == "r1"
    assert all(r["status"] == "Busy" for r in status["reviewers"])
    queue["running"] = None
    status = client.get("/api/career/reviewers/status").json()
    assert all(r["status"] == "Ready" for r in status["reviewers"])
