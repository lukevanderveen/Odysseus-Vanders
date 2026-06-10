"""Deep Index — context builder, runner, and background-route lifecycle.

Drives services/projects/deep_index.py + the deep-index routes (Agent Council
Phase 1). The LLM call is injected (`complete`) so no endpoint is needed; the
route runs the summary as a background task because local-model runs exceed
the app's 45s hard request timeout.
"""

import asyncio
import importlib
import sys
import time
import types

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient

from services.projects.deep_index import build_context, run_deep_index


def _import_real(*names, force=()):
    """Import real modules even when a sibling test stubbed their dep tree
    (see tests/test_project_model.py for the full rationale)."""
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


# ── build_context ────────────────────────────────────────────────────────────

@pytest.fixture()
def project(tmp_path):
    (tmp_path / "README.md").write_text("# Demo\nA flood mapper.", encoding="utf-8")
    (tmp_path / "pyproject.toml").write_text('[project]\nname = "demo"\n', encoding="utf-8")
    src = tmp_path / "src"
    src.mkdir()
    (src / "main.py").write_text("print('hi')\n", encoding="utf-8")
    vend = tmp_path / "node_modules" / "pkg"
    vend.mkdir(parents=True)
    (vend / "index.js").write_text("x", encoding="utf-8")
    return tmp_path


def test_build_context_has_tree_readme_and_configs(project):
    ctx = build_context(project)
    assert "src/main.py" in ctx
    assert "A flood mapper." in ctx        # README body
    assert 'name = "demo"' in ctx          # pyproject body
    assert "node_modules" not in ctx       # vendored dirs skipped


def test_build_context_respects_max_chars(project):
    (project / "BIG.md").write_text("z" * 100_000, encoding="utf-8")
    assert len(build_context(project, max_chars=5000)) <= 5000


# ── run_deep_index ───────────────────────────────────────────────────────────

def test_run_deep_index_returns_summary_and_ref(project):
    async def fake_complete(messages):
        assert any("src/main.py" in m["content"] for m in messages)
        return "## Purpose\nDemo project."

    result = asyncio.run(run_deep_index("p1", str(project), complete=fake_complete))
    assert result["status"] == "ok"
    assert result["summary"] == "## Purpose\nDemo project."
    assert result["ref"]


def test_run_deep_index_error_when_model_fails(project):
    async def broken(messages):
        raise RuntimeError("endpoint down")

    result = asyncio.run(run_deep_index("p1", str(project), complete=broken))
    assert result["status"] == "error"
    assert "endpoint down" in result["error"]


def test_run_deep_index_error_on_empty_summary(project):
    async def blank(messages):
        return "   "

    result = asyncio.run(run_deep_index("p1", str(project), complete=blank))
    assert result["status"] == "error"


# ── routes: background lifecycle ─────────────────────────────────────────────

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
    monkeypatch.setattr(pr, "get_current_user", lambda request: "vanders")
    pr._deep_runs.clear()

    proj_dir = tmp_path / "alpha"
    proj_dir.mkdir()
    db = TestSession()
    db.add(Project(id="p1", owner="vanders", name="alpha", path=str(proj_dir), stack="[]"))
    db.add(Project(id="theirs", owner="other", name="b", path=str(proj_dir), stack="[]"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(pr.setup_project_routes())
    # Context-managed client = one persistent event loop for the whole test;
    # without it each request gets its own loop and background tasks die with it.
    with TestClient(app) as client:
        yield client, TestSession


def _poll_status(client, pid, want, timeout_s=5.0):
    deadline = time.monotonic() + timeout_s
    status = None
    while time.monotonic() < deadline:
        status = client.get(f"/api/projects/{pid}/deep-index/status").json()
        if status["status"] in want:
            return status
        time.sleep(0.05)
    return status


def test_deep_index_404_for_foreign_project(env):
    client, _ = env
    assert client.post("/api/projects/theirs/deep-index").status_code == 404


def test_deep_index_completes_and_stamps_summary(env, monkeypatch):
    client, TestSession = env

    async def fake_run(pid, path, owner=None):
        return {"status": "ok", "summary": "## Purpose\nAlpha.", "ref": "r1"}

    monkeypatch.setattr(pr, "run_deep_index", fake_run)
    res = client.post("/api/projects/p1/deep-index")
    assert res.status_code == 200
    assert res.json()["status"] == "running"

    status = _poll_status(client, "p1", {"done", "error"})
    assert status["status"] == "done"

    detail = client.get("/api/projects/p1").json()
    assert detail["deep_summary"] == "## Purpose\nAlpha."
    assert detail["deep_indexed_at"] is not None


def test_deep_index_rejects_concurrent_run(env, monkeypatch):
    client, _ = env

    async def slow_run(pid, path, owner=None):
        await asyncio.sleep(0.8)
        return {"status": "ok", "summary": "s", "ref": "r"}

    monkeypatch.setattr(pr, "run_deep_index", slow_run)
    assert client.post("/api/projects/p1/deep-index").status_code == 200
    assert client.post("/api/projects/p1/deep-index").status_code == 409
    assert _poll_status(client, "p1", {"done"})["status"] == "done"


def test_deep_index_error_surfaces_in_status(env, monkeypatch):
    client, _ = env

    async def failing_run(pid, path, owner=None):
        return {"status": "error", "error": "model exploded"}

    monkeypatch.setattr(pr, "run_deep_index", failing_run)
    client.post("/api/projects/p1/deep-index")
    status = _poll_status(client, "p1", {"done", "error"})
    assert status["status"] == "error"
    assert "model exploded" in status["error"]

    detail = client.get("/api/projects/p1").json()
    assert detail["deep_summary"] is None
