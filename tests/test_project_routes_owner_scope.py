"""Project routes — owner scoping, scan upsert, index stamping, staleness.

Functional tests: real router on a bare FastAPI app with an in-memory DB.
Workspace roots come ONLY from admin settings (monkeypatched here) — the scan
endpoint must not accept caller-supplied paths.
"""

import importlib
import sys
import types

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.projects.scanner import current_index_ref


def _import_real(*names, force=()):
    """Import real modules even when a sibling test stubbed their dep tree
    (test_agent_loop.py stubs sqlalchemy/core.database at import time);
    restores the popped entries afterwards so later modules are unaffected.

    ``force`` modules are popped even when cached as real file-backed modules —
    a project module imported earlier UNDER stubs is real but holds mock class
    bindings. Never force third-party packages (re-importing sqlalchemy beside
    its cached submodules produces a second, inconsistent package instance).
    """
    def _is_stub(m):
        # A real file-backed module has a str __file__. Sibling stubs are
        # MagicMocks (not ModuleType) or ModuleType subclasses whose
        # __getattr__ fabricates attributes (returns a MagicMock, not a str).
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


_sa, _sa_orm, _sa_pool, _coredb, pr = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool",
    "core.database", "routes.project_routes",
    force=("core.database", "routes.project_routes"),
)
create_engine = _sa.create_engine
sessionmaker = _sa_orm.sessionmaker
StaticPool = _sa_pool.StaticPool
Base = _coredb.Base
Project = _coredb.Project


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    monkeypatch.setattr(pr, "SessionLocal", TestSession)

    current = {"user": "vanders"}
    monkeypatch.setattr(pr, "get_current_user", lambda request: current["user"])

    workspace = tmp_path / "ws"
    proj = workspace / "alpha"
    (proj / ".git").mkdir(parents=True)
    (proj / ".git" / "HEAD").write_text("a" * 40 + "\n", encoding="utf-8")
    (proj / "pyproject.toml").write_text("", encoding="utf-8")
    monkeypatch.setattr(pr, "get_setting", lambda key, default=None: [str(workspace)])

    app = FastAPI()
    app.include_router(pr.setup_project_routes())
    return TestClient(app), TestSession, current, proj


def _insert(TestSession, **overrides):
    fields = dict(id="px", owner="vanders", name="x", path=r"D:\x", stack="[]")
    fields.update(overrides)
    db = TestSession()
    db.add(Project(**fields))
    db.commit()
    db.close()


def test_scan_discovers_and_stamps_owner(env):
    client, TestSession, _, proj = env
    res = client.post("/api/projects/scan")
    assert res.status_code == 200
    projects = res.json()["projects"]
    assert len(projects) == 1
    assert projects[0]["name"] == "alpha"
    assert projects[0]["owner"] == "vanders"
    assert "python" in projects[0]["stack"]
    assert projects[0]["stale"] is True  # never indexed


def test_scan_is_idempotent(env):
    client, TestSession, _, _ = env
    client.post("/api/projects/scan")
    client.post("/api/projects/scan")
    db = TestSession()
    assert db.query(Project).count() == 1
    db.close()


def test_list_excludes_other_owners(env):
    client, TestSession, _, _ = env
    _insert(TestSession, id="mine", name="mine")
    _insert(TestSession, id="theirs", owner="other", name="theirs", path=r"D:\y")
    res = client.get("/api/projects")
    names = [p["name"] for p in res.json()["projects"]]
    assert names == ["mine"]


def test_detail_404_for_foreign_project(env):
    client, TestSession, _, _ = env
    _insert(TestSession, id="theirs", owner="other")
    assert client.get("/api/projects/theirs").status_code == 404


def test_index_stamps_ref_and_clears_staleness(env, monkeypatch):
    client, TestSession, _, proj = env
    client.post("/api/projects/scan")
    db = TestSession()
    pid = db.query(Project).one().id
    db.close()

    real_ref = current_index_ref(proj)
    monkeypatch.setattr(
        pr, "index_project",
        lambda project_id, path, owner=None: {
            "status": "ok", "files": 1, "chunks": 2, "ref": real_ref,
        },
    )
    res = client.post(f"/api/projects/{pid}/index")
    assert res.status_code == 200
    assert res.json()["status"] == "ok"

    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["stale"] is False
    assert detail["indexed_at"] is not None


def test_index_degraded_does_not_stamp(env, monkeypatch):
    client, TestSession, _, _ = env
    client.post("/api/projects/scan")
    db = TestSession()
    pid = db.query(Project).one().id
    db.close()

    monkeypatch.setattr(
        pr, "index_project",
        lambda project_id, path, owner=None: {"status": "degraded", "error": "down"},
    )
    res = client.post(f"/api/projects/{pid}/index")
    assert res.status_code == 503
    detail = client.get(f"/api/projects/{pid}").json()
    assert detail["indexed_at"] is None
    assert detail["stale"] is True


def test_index_404_for_foreign_project(env):
    client, TestSession, _, _ = env
    _insert(TestSession, id="theirs", owner="other")
    assert client.post("/api/projects/theirs/index").status_code == 404


def test_archive_is_owner_scoped_and_hides_from_list(env):
    client, TestSession, _, _ = env
    _insert(TestSession, id="mine")
    _insert(TestSession, id="theirs", owner="other", path=r"D:\y")

    assert client.delete("/api/projects/theirs").status_code == 404
    assert client.delete("/api/projects/mine").status_code == 200
    assert client.get("/api/projects").json()["projects"] == []
