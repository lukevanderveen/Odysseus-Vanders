"""Career applications routes: owner scoping, CRUD, bootstrap seeding."""
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


_sa, _sa_orm, _sa_pool, _coredb, crr, helpers, cs = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "routes.career_routes", "routes.career_helpers", "services.career.settings",
    force=("core.database", "routes.career_routes", "routes.career_helpers",
           "services.career.seeds"),
)
JobApplication = _coredb.JobApplication
CrewMember = _coredb.CrewMember


class FakeSkills:
    def load_all(self):
        return []


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    TestSession = _sa_orm.sessionmaker(bind=engine)
    monkeypatch.setattr(crr, "SessionLocal", TestSession)
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path))
    current = {"user": "vanders"}
    monkeypatch.setattr(crr, "get_current_user", lambda request: current["user"])
    monkeypatch.setattr(crr, "seed_skills", lambda owner, sm: [])

    db = TestSession()
    db.add(JobApplication(id="a1", owner="vanders", company="Acme", role="Dev", status="applied",
                          applied_at=datetime.utcnow() - timedelta(days=30)))
    db.add(JobApplication(id="a2", owner="other", company="Theirs", role="Dev"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(crr.setup_career_routes(FakeSkills(), None))
    return TestClient(app), TestSession, current


def test_bootstrap_seeds_reviewers_and_returns_settings(env):
    client, TestSession, _ = env
    res = client.get("/api/career/bootstrap")
    assert res.status_code == 200
    body = res.json()
    assert body["settings"]["nudge_after_days"] == 10
    assert body["counts"]["applications"] == 1
    assert sorted(r["department"] for r in body["reviewers"]) == [
        "career_engineer", "career_hiring_manager", "career_hr", "career_recruiter"]
    db = TestSession()
    assert db.query(CrewMember).filter(CrewMember.owner == "vanders").count() == 4
    db.close()


def test_list_is_owner_scoped_and_computes_ghosted(env):
    client, _, _ = env
    apps = client.get("/api/career/applications").json()["applications"]
    assert [a["id"] for a in apps] == ["a1"]
    assert apps[0]["computed_status"] == "ghosted"
    assert apps[0]["days_since_event"] >= 30


def test_create_fetches_jd_from_url(env, monkeypatch):
    client, TestSession, _ = env
    monkeypatch.setattr(crr, "fetch_webpage_content",
                        lambda url, timeout=5: {"title": "Backend Engineer", "content": "Build APIs"})
    res = client.post("/api/career/applications",
                      json={"company": "Beta", "role": "Backend", "jd_url": "https://jobs.example/1"})
    assert res.status_code == 201
    body = res.json()
    assert body["jd_text"] == "Build APIs"
    assert body["url"] == "https://jobs.example/1"
    assert body["status"] == "drafting"
    db = TestSession()
    assert db.get(JobApplication, body["id"]).owner == "vanders"
    db.close()


def test_create_rejects_missing_company(env):
    client, _, _ = env
    assert client.post("/api/career/applications", json={"company": " ", "role": "Dev"}).status_code == 422


def test_get_update_delete_foreign_application_404(env):
    client, _, _ = env
    assert client.get("/api/career/applications/a2").status_code == 404
    assert client.put("/api/career/applications/a2", json={"status": "offer"}).status_code == 404
    assert client.delete("/api/career/applications/a2").status_code == 404


def test_update_validates_status_and_delete_archives(env):
    client, TestSession, _ = env
    assert client.put("/api/career/applications/a1", json={"status": "nonsense"}).status_code == 422
    res = client.put("/api/career/applications/a1", json={"status": "interview", "notes": "call Tue"})
    assert res.json()["status"] == "interview" and res.json()["computed_status"] == "interview"
    assert client.delete("/api/career/applications/a1").json() == {"id": "a1", "archived": True}
    assert client.get("/api/career/applications").json()["applications"] == []
    assert len(client.get("/api/career/applications?include_archived=1").json()["applications"]) == 1


def test_settings_roundtrip(env):
    client, _, _ = env
    res = client.put("/api/career/settings", json={"voice_rules": "plain", "bogus": 1})
    assert res.json()["voice_rules"] == "plain" and "bogus" not in res.json()
    assert client.get("/api/career/settings").json()["voice_rules"] == "plain"
