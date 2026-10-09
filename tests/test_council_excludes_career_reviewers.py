"""Council status/members must not list career_* reviewers (they are CrewMember rows too)."""
import importlib
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
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database", "routes.council_routes",
    force=("core.database", "routes.council_routes", "services.council.runner"),
)
CrewMember = _coredb.CrewMember


@pytest.fixture()
def env(monkeypatch):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    TestSession = _sa_orm.sessionmaker(bind=engine)
    monkeypatch.setattr(cr, "SessionLocal", TestSession)
    monkeypatch.setattr(cr, "get_current_user", lambda request: "vanders")
    monkeypatch.setattr(cr, "queue_state", lambda: {"running": None, "queued": []})
    db = TestSession()
    db.add(CrewMember(id="rv1", owner="vanders", name="Recruiter", department="career_recruiter"))
    db.commit()
    db.close()
    app = FastAPI()
    app.include_router(cr.setup_council_routes())
    return TestClient(app), TestSession


def test_status_and_members_only_list_council_departments(env):
    client, _ = env
    status = client.get("/api/council/status").json()["members"]
    members = client.get("/api/council/members").json()["members"]
    assert len(status) == 5 and len(members) == 5
    assert all(not m["department"].startswith("career_") for m in status + members)


def test_council_member_edit_cannot_touch_a_career_reviewer(env):
    client, _ = env
    res = client.put("/api/council/members/rv1", json={"name": "Hacked"})
    assert res.status_code == 404
