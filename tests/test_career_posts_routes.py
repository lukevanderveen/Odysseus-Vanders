"""Studio routes for posts: owner scope, drafting, lifecycle, disclosure."""
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
                   "services.career.claims_store", "services.career.cover_letter", "services.career.posts"):
            sys.modules.pop(_k, None)

_sa, _sa_orm, _sa_pool, _coredb, sr, cs = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "routes.career_studio_routes", "services.career.settings",
    force=("core.database", "routes.career_studio_routes"),
)
CareerPost = _coredb.CareerPost
from services.career.activity_sources import ActivityItem  # noqa: E402


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    TestSession = _sa_orm.sessionmaker(bind=engine)
    monkeypatch.setattr(sr, "SessionLocal", TestSession)
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path))
    current = {"user": "vanders"}
    monkeypatch.setattr(sr, "get_current_user", lambda request: current["user"])

    items = [ActivityItem("git", "p1", "Odysseus", "feat: tool_index.py warmup", "warm cache",
                          "2026-10-03T00:00:00+00:00", None, "commit")]

    async def fake_gather(owner, days, sources, project_ids, session_factory=None, execute=None):
        fake_gather.calls.append((owner, days, sorted(sources), project_ids))
        return items, ["Trello integration not registered"]
    fake_gather.calls = []
    monkeypatch.setattr(sr, "gather_activity", fake_gather)

    async def fake_draft(items, post_type, settings, owner, complete=None, n_variants=2, tracker_stats=None):
        return [{"body": f"{post_type} {i}", "hooks": ["a", "b", "c"]} for i in range(n_variants)]
    monkeypatch.setattr(sr, "draft_post", fake_draft)

    db = TestSession()
    db.add(CareerPost(id="theirs", owner="other", post_type="progress", body="x"))
    db.commit()
    db.close()
    app = FastAPI()
    app.include_router(sr.setup_career_studio_routes(None))
    return TestClient(app), TestSession, current


def test_activity_preview_is_redacted_and_carries_warnings(env):
    client, _, _ = env
    res = client.get("/api/career/activity?days=7&sources=git,trello&project_ids=p1,p2")
    assert res.status_code == 200
    body = res.json()
    assert body["warnings"] == ["Trello integration not registered"]
    assert body["items"][0]["project_name"] == "a project I'm building"
    assert "tool_index.py" not in body["items"][0]["title"]


def test_draft_creates_rows_and_returns_them(env):
    client, TestSession, _ = env
    res = client.post("/api/career/posts/draft", json={"post_type": "technique", "days": 14,
                                                       "sources": ["git"], "n_variants": 2})
    assert res.status_code == 201
    posts = res.json()["posts"]
    assert [p["body"] for p in posts] == ["technique 0", "technique 1"]
    assert posts[0]["hooks"] == ["a", "b", "c"] and posts[0]["status"] == "draft"
    db = TestSession()
    rows = db.query(CareerPost).filter(CareerPost.owner == "vanders").all()
    assert len(rows) == 2 and json.loads(rows[0].sources)[0]["title"].count("[redacted]") >= 1
    db.close()


def test_draft_rejects_unknown_type_and_empty_activity(env, monkeypatch):
    client, _, _ = env
    assert client.post("/api/career/posts/draft", json={"post_type": "rant"}).status_code == 422

    async def empty(owner, days, sources, project_ids, session_factory=None, execute=None):
        return [], []
    monkeypatch.setattr(sr, "gather_activity", empty)
    assert client.post("/api/career/posts/draft", json={"post_type": "progress"}).status_code == 409


def test_posts_list_update_discard_owner_scoped(env):
    client, _, _ = env
    client.post("/api/career/posts/draft", json={"post_type": "lesson", "n_variants": 1})
    mine = client.get("/api/career/posts").json()["posts"]
    assert len(mine) == 1 and mine[0]["id"] != "theirs"
    pid = mine[0]["id"]
    res = client.put(f"/api/career/posts/{pid}", json={"status": "posted", "body": "edited"})
    assert res.json()["status"] == "posted" and res.json()["posted_at"] and res.json()["body"] == "edited"
    assert client.get("/api/career/posts?status=posted").json()["posts"][0]["id"] == pid
    assert client.delete(f"/api/career/posts/{pid}").json() == {"id": pid, "status": "discarded"}
    assert client.put("/api/career/posts/theirs", json={"status": "posted"}).status_code == 404
    assert client.delete("/api/career/posts/theirs").status_code == 404
    assert client.put(f"/api/career/posts/{pid}", json={"status": "weird"}).status_code == 422


def test_disclosure_saved_into_settings(env):
    client, _, _ = env
    res = client.put("/api/career/projects-disclosure", json={"project_id": "p1", "level": "public"})
    assert res.status_code == 200 and res.json()["project_disclosure"] == {"p1": "public"}
    assert client.put("/api/career/projects-disclosure", json={"project_id": "p1", "level": "nope"}).status_code == 422
