"""Activity sources: local git log parsing (real repo), GitHub/Trello via a fake execute."""
import os
import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import pytest

from services.career import activity_sources as acts

_HAS_GIT = shutil.which("git") is not None


@dataclass
class StubProject:
    id: str
    name: str
    path: str


def _git(cwd, *args):
    env = {**os.environ, "GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@x", "GIT_COMMITTER_NAME": "t",
           "GIT_COMMITTER_EMAIL": "t@x"}
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, env=env)


@pytest.fixture()
def repo(tmp_path):
    if not _HAS_GIT:
        pytest.skip("git not on PATH")
    _git(tmp_path, "init", "-q")
    (tmp_path / "a.py").write_text("print(1)\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "feat: add streaming parser\n\nUses an incremental tokenizer.")
    (tmp_path / "b.py").write_text("print(2)\n", encoding="utf-8")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "fix: retry on timeout")
    return tmp_path


def test_local_git_parses_subject_body_and_date(repo):
    items, warning = acts.fetch_local_git(StubProject("p1", "Odysseus", str(repo)),
                                          datetime.now(timezone.utc) - timedelta(days=1))
    assert warning is None
    assert [i.title for i in items] == ["fix: retry on timeout", "feat: add streaming parser"]
    assert items[1].body == "Uses an incremental tokenizer."
    assert items[0].source == "git" and items[0].kind == "commit"
    assert items[0].project_id == "p1" and items[0].project_name == "Odysseus"
    assert items[0].date.startswith(str(datetime.now().year))


def test_local_git_since_filters_old_commits(repo):
    items, _ = acts.fetch_local_git(StubProject("p1", "Odysseus", str(repo)),
                                    datetime.now(timezone.utc) + timedelta(days=1))
    assert items == []


def test_non_git_path_returns_warning(tmp_path):
    items, warning = acts.fetch_local_git(StubProject("p2", "Plain", str(tmp_path)), datetime.now(timezone.utc))
    assert items == [] and "not a git repository" in warning


def test_missing_git_binary_returns_warning(monkeypatch, tmp_path):
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(acts, "which_tool", lambda name: None)
    items, warning = acts.fetch_local_git(StubProject("p3", "X", str(tmp_path)), datetime.now(timezone.utc))
    assert items == [] and "git is not installed" in warning


def test_activity_item_roundtrip():
    item = acts.ActivityItem("git", "p1", "Odysseus", "t", "b", "2026-10-01T00:00:00+00:00", None, "commit")
    assert acts.item_from_dict(item.to_dict()) == item


# ── remote sources + gather (Task 3) ─────────────────────────────────────────
import json  # noqa: E402

from services.career import activity_remote as remote  # noqa: E402


def _ok(payload):
    return {"output": "HTTP 200\n" + json.dumps(payload), "exit_code": 0}


class FakeExecute:
    def __init__(self, routes):
        self.routes = routes
        self.calls = []

    async def __call__(self, integration_id, method, path, params=None, body=None, extra_headers=None):
        self.calls.append((integration_id, path, params or {}))
        for prefix, payload in self.routes.items():
            if path.startswith(prefix):
                return payload
        return {"error": f"HTTP 404\nno route {path}", "exit_code": 1}


def test_resolve_integration_by_preset_then_name(monkeypatch):
    monkeypatch.setattr(remote, "load_integrations", lambda: [
        {"id": "abc", "name": "My GH", "preset": "github"},
        {"id": "def", "name": "Trello", "preset": ""},
    ])
    assert remote.resolve_integration_id("github") == "abc"
    assert remote.resolve_integration_id("trello") == "def"
    assert remote.resolve_integration_id("gitea") is None


async def test_github_commits_and_prs_since(monkeypatch):
    monkeypatch.setattr(remote, "resolve_integration_id", lambda p: "gh1")
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    execute = FakeExecute({
        "/user/repos": _ok([
            {"full_name": "luke/odysseus", "name": "odysseus", "pushed_at": "2026-10-05T10:00:00Z", "html_url": "https://github.com/luke/odysseus"},
            {"full_name": "luke/old", "name": "old", "pushed_at": "2025-01-01T00:00:00Z", "html_url": "https://github.com/luke/old"},
        ]),
        "/repos/luke/odysseus/commits": _ok([
            {"sha": "abc", "html_url": "https://github.com/luke/odysseus/commit/abc",
             "commit": {"message": "feat: cache tool index\n\nwarm at startup", "author": {"date": "2026-10-04T09:00:00Z"}}},
        ]),
        "/repos/luke/odysseus/pulls": _ok([
            {"title": "Add council", "body": "Phase 3", "html_url": "https://github.com/luke/odysseus/pull/9",
             "updated_at": "2026-10-03T00:00:00Z", "state": "closed", "merged_at": "2026-10-03T00:00:00Z"},
            {"title": "Ancient", "body": "", "html_url": "x", "updated_at": "2025-01-01T00:00:00Z", "state": "open", "merged_at": None},
        ]),
    })
    items, warning = await remote.fetch_github(since, execute=execute)
    assert warning is None
    assert [c[1] for c in execute.calls] == ["/user/repos", "/repos/luke/odysseus/commits", "/repos/luke/odysseus/pulls"]
    assert execute.calls[1][2]["since"] == "2026-10-01T00:00:00+00:00"
    kinds = sorted((i.kind, i.title) for i in items)
    assert kinds == [("commit", "feat: cache tool index"), ("pull_request", "Add council")]
    assert items[0].project_name == "odysseus" and items[0].project_id is None


async def test_github_not_registered_is_a_warning(monkeypatch):
    monkeypatch.setattr(remote, "resolve_integration_id", lambda p: None)
    items, warning = await remote.fetch_github(datetime.now(timezone.utc), execute=FakeExecute({}))
    assert items == [] and "GitHub integration" in warning


async def test_github_http_error_is_a_warning(monkeypatch):
    monkeypatch.setattr(remote, "resolve_integration_id", lambda p: "gh1")
    execute = FakeExecute({"/user/repos": {"error": "HTTP 401\nbad credentials", "exit_code": 1}})
    items, warning = await remote.fetch_github(datetime.now(timezone.utc), execute=execute)
    assert items == [] and "401" in warning


async def test_trello_actions_become_card_items(monkeypatch):
    monkeypatch.setattr(remote, "resolve_integration_id", lambda p: "tr1")
    since = datetime(2026, 10, 1, tzinfo=timezone.utc)
    execute = FakeExecute({
        "/1/members/me/boards": _ok([{"id": "b1", "name": "Odysseus", "url": "https://trello.com/b/b1"}]),
        "/1/boards/b1/actions": _ok([
            {"type": "createCard", "date": "2026-10-02T00:00:00.000Z",
             "data": {"card": {"name": "Job tracker dashboard", "shortLink": "c1"}, "list": {"name": "Doing"}}},
            {"type": "updateCard", "date": "2026-10-03T00:00:00.000Z",
             "data": {"card": {"name": "Cover letter skill", "shortLink": "c2"}, "listBefore": {"name": "Doing"}, "listAfter": {"name": "Done"}}},
            {"type": "commentCard", "date": "2026-10-04T00:00:00.000Z",
             "data": {"card": {"name": "Cover letter skill", "shortLink": "c2"}, "text": "shipped the rubric"}},
        ]),
    })
    items, warning = await remote.fetch_trello(since, execute=execute)
    assert warning is None
    assert execute.calls[1][2]["filter"] == "createCard,updateCard:idList,commentCard"
    assert [(i.kind, i.title) for i in items] == [
        ("card_created", "Job tracker dashboard"),
        ("card_moved", "Cover letter skill"),
        ("card_comment", "Cover letter skill"),
    ]
    assert items[1].body == "Moved from Doing to Done"
    assert items[2].url == "https://trello.com/c/c2"
    assert all(i.project_name == "Odysseus" for i in items)


async def test_gather_merges_sources_and_collects_warnings(monkeypatch, repo):
    import importlib, sys, types

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

    _sa, _sa_orm, _sa_pool, _coredb = _import_real(
        "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database", force=("core.database",))
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    factory = _sa_orm.sessionmaker(bind=engine)
    db = factory()
    db.add(_coredb.Project(id="p1", owner="vanders", name="Odysseus", path=str(repo)))
    db.add(_coredb.Project(id="p2", owner="other", name="Theirs", path=str(repo)))
    db.commit()
    db.close()
    monkeypatch.setattr(remote, "Project", _coredb.Project)

    async def failing_github(since, execute=None):
        return [], "GitHub integration not registered"

    monkeypatch.setattr(remote, "fetch_github", failing_github)
    items, warnings = await remote.gather_activity("vanders", 30, {"git", "github"}, None,
                                                   session_factory=factory, execute=FakeExecute({}))
    assert {i.project_id for i in items} == {"p1"}
    assert warnings == ["GitHub integration not registered"]
