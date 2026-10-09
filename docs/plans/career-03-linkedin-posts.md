# Career Hub 03 — LinkedIn Posts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Draft LinkedIn posts (progress, technique, lesson, job-search narrative) from real project activity — local git repos, GitHub, Trello — after a deterministic privacy scrub and per-project disclosure level, with a Posts tab in the Career hub and a seeded `linkedin-post-writer` v2 skill for the agent.

**Architecture:** Three fetchers return normalised `ActivityItem`s plus a warning string (never raise). `disclosure.py` redacts text and applies the project's disclosure level before any model sees it. `posts.py` renders a prompt from packaged templates and parses one JSON block per variant into `CareerPost` draft rows. Routes live in `routes/career_studio_routes.py`; the Posts tab registers itself with `career.js` through `registerTab`/`onPanelClick`. Design: `docs/plans/career-hub-design.md`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (SQLite), httpx via `src.integrations.execute_api_call`, `git` CLI via subprocess, pytest, vanilla ES modules, Node for JS tests.

**Prerequisite:** `docs/plans/career-00-hub-foundation.md` is fully landed (`CareerPost` model, `services/career/settings.py`, `services/career/seeds.py`, `github`/`trello` integration presets, `static/js/career.js` with `registerTab`/`onPanelClick`/`refresh`/`getState`). Plan 01 may or may not be landed; Task 6 gives both variants for `routes/career_studio_routes.py`. `services/career/llm.py` comes from plan 00.

## Global Constraints

- TDD: failing test first, watch it fail for the expected reason, minimal code, commit per task.
- Owner scoping on every route (filter by owner only when `get_current_user(request)` is not `None`; foreign rows 404). `Project` queries exclude `archived == True`.
- Nothing publishes. Posts are drafts; the only outbound calls are reads against GitHub/Trello.
- A failing source never fails the request: fetchers return `([], "warning text")`.
- The model never sees unredacted text: `apply_disclosure` runs before prompt construction and before the `/activity` preview is returned.
- `execute_api_call` returns `{"output": "HTTP <status>\n<body>", "exit_code": 0}` on success and `{"error": ..., "exit_code": 1}` on failure; the body is truncated at 12 000 characters. Always request small pages (`per_page`/`limit` ≤ 50) and treat a JSON decode failure as a warning.
- Integration lookup: `src.integrations._find_integration` matches by id or display name only. The fetchers resolve the integration by its `preset` field (`"github"` / `"trello"`), falling back to a name match, so the user's chosen display name does not matter.
- Tests touching SQLAlchemy use the `_import_real` helper copied verbatim from `tests/test_career_models.py`.
- Keep each new module under ~200 lines.

---

### Task 1: Shared LLM completion helper — provided by plan 00

No work in this plan. `services/career/llm.py` (`complete`, `complete_utility`, `render_template`) ships in `career-00-hub-foundation.md` Task 2b. Tasks 5 and 6 import `complete` from `services.career.llm` as their default completion callable.

---

### Task 2: Local git activity source

**Files:**
- Create: `services/career/activity_sources.py` (dataclass + git fetcher)
- Test: `tests/test_career_activity_sources.py`

**Interfaces:**
- Produces: `ActivityItem` dataclass (`source, project_id, project_name, title, body, date, url, kind`; `to_dict()`), `item_from_dict(d)`, `fetch_local_git(project, since: datetime) -> tuple[list[ActivityItem], str | None]`. `project` is anything with `.id`, `.name`, `.path` (the `Project` ORM row or a test stub).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_activity_sources.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_activity_sources.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.activity_sources'`

- [ ] **Step 3: Implement**

```python
# services/career/activity_sources.py
"""Normalised project activity for LinkedIn post drafting (plan 03).

Every fetcher returns ``(items, warning)`` and never raises: a missing tool,
an unregistered integration or an HTTP error becomes a warning string the UI
shows next to the preview. Remote sources live in activity_remote.py.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.platform_compat import which_tool

logger = logging.getLogger(__name__)

GIT_LOG_TIMEOUT = 10
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"


@dataclass(frozen=True)
class ActivityItem:
    source: str            # git | github | trello
    project_id: Optional[str]
    project_name: str
    title: str
    body: str
    date: str              # ISO 8601
    url: Optional[str]
    kind: str              # commit | pull_request | card_created | card_moved | card_comment

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def item_from_dict(d: Dict[str, Any]) -> ActivityItem:
    return ActivityItem(
        source=str(d.get("source", "")), project_id=d.get("project_id"),
        project_name=str(d.get("project_name", "")), title=str(d.get("title", "")),
        body=str(d.get("body", "")), date=str(d.get("date", "")),
        url=d.get("url"), kind=str(d.get("kind", "")),
    )


def _parse_git_log(raw: str, project) -> List[ActivityItem]:
    items: List[ActivityItem] = []
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n\r ")
        if not record:
            continue
        parts = record.split(_FIELD_SEP)
        if len(parts) < 3:
            continue
        sha, date, subject = parts[0].strip(), parts[1].strip(), parts[2].strip()
        body = parts[3].strip() if len(parts) > 3 else ""
        items.append(ActivityItem(
            source="git", project_id=getattr(project, "id", None), project_name=project.name,
            title=subject, body=body, date=date, url=None, kind="commit",
        ))
    return items


def fetch_local_git(project, since: datetime) -> Tuple[List[ActivityItem], Optional[str]]:
    """Commits since ``since`` from a project folder on disk."""
    path = getattr(project, "path", "") or ""
    if not os.path.isdir(os.path.join(path, ".git")):
        return [], f"{project.name}: not a git repository"
    git = which_tool("git")
    if not git:
        return [], "git is not installed or not on PATH"
    cmd = [git, "log", f"--since={since.isoformat()}", "--no-merges",
           f"--pretty=format:%H{_FIELD_SEP}%aI{_FIELD_SEP}%s{_FIELD_SEP}%b{_RECORD_SEP}"]
    try:
        res = subprocess.run(cmd, cwd=path, timeout=GIT_LOG_TIMEOUT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return [], f"{project.name}: git log timed out"
    except OSError as e:
        return [], f"{project.name}: git failed ({e})"
    if res.returncode != 0:
        return [], f"{project.name}: git log failed ({(res.stderr or '').strip()[:120]})"
    return _parse_git_log(res.stdout, project), None
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_activity_sources.py -v`
Expected: 5 passed (or skipped where git is missing)

- [ ] **Step 5: Commit**

```bash
git add services/career/activity_sources.py tests/test_career_activity_sources.py
git commit -m "feat(career): ActivityItem and local git activity source"
```

---

### Task 3: GitHub and Trello activity sources + gather

**Files:**
- Create: `services/career/activity_remote.py`
- Modify: `tests/test_career_activity_sources.py` (append tests)

**Interfaces:**
- Consumes: `src.integrations.execute_api_call`, `load_integrations`; `core.database.Project`, `SessionLocal`.
- Produces: `resolve_integration_id(preset: str) -> str | None`, `async fetch_github(since, execute=execute_api_call)`, `async fetch_trello(since, execute=execute_api_call)`, `async gather_activity(owner, days, sources, project_ids, session_factory=None, execute=execute_api_call) -> tuple[list[ActivityItem], list[str]]`. `sources` ⊆ `{"git","github","trello"}`.

- [ ] **Step 1: Append the failing tests**

```python
# append to tests/test_career_activity_sources.py
import json

from services.career import activity_remote as remote


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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_career_activity_sources.py -v`
Expected: new tests FAIL with `ModuleNotFoundError: No module named 'services.career.activity_remote'`

- [ ] **Step 3: Implement**

```python
# services/career/activity_remote.py
"""GitHub and Trello activity through the integrations registry, plus the
gather step that merges every selected source for an owner.

``execute_api_call`` returns ``{"output": "HTTP <status>\\n<body>"}`` on
success; ``_parse`` strips that first line and decodes the JSON body.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, List, Optional, Set, Tuple

from core.database import Project, SessionLocal
from services.career.activity_sources import ActivityItem, fetch_local_git
from src.integrations import execute_api_call, load_integrations

logger = logging.getLogger(__name__)

SOURCES = ("git", "github", "trello")
TRELLO_FILTER = "createCard,updateCard:idList,commentCard"


def resolve_integration_id(preset: str) -> Optional[str]:
    """Prefer the integration whose ``preset`` is ``preset``; fall back to a
    case-insensitive display-name match so a renamed integration still works."""
    items = load_integrations()
    for item in items:
        if (item.get("preset") or "").lower() == preset:
            return item.get("id")
    for item in items:
        if (item.get("name") or "").lower() == preset:
            return item.get("id")
    return None


def _parse(result: dict) -> Tuple[Any, Optional[str]]:
    if not result or result.get("exit_code", 0) != 0 or "error" in result:
        return None, str((result or {}).get("error") or "integration call failed")[:200]
    text = str(result.get("output", ""))
    body = text.split("\n", 1)[1] if "\n" in text else text
    try:
        return json.loads(body), None
    except (json.JSONDecodeError, ValueError):
        return None, "integration returned non-JSON or truncated output"


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _is_since(value: str, since: datetime) -> bool:
    dt = _parse_dt(value)
    return bool(dt) and dt >= since


async def fetch_github(since: datetime, execute: Callable = execute_api_call) -> Tuple[List[ActivityItem], Optional[str]]:
    iid = resolve_integration_id("github")
    if not iid:
        return [], "GitHub integration not registered (Settings → Integrations → preset GitHub)"
    repos, err = _parse(await execute(iid, "GET", "/user/repos", params={"sort": "pushed", "per_page": 50}))
    if err:
        return [], f"GitHub: {err}"
    items: List[ActivityItem] = []
    for repo in repos or []:
        if not _is_since(repo.get("pushed_at", ""), since):
            continue
        full = repo.get("full_name") or ""
        name = repo.get("name") or full
        commits, err = _parse(await execute(iid, "GET", f"/repos/{full}/commits",
                                            params={"since": since.isoformat(), "per_page": 50}))
        if err:
            return items, f"GitHub {full}: {err}"
        for c in commits or []:
            message = (c.get("commit") or {}).get("message") or ""
            subject, _, body = message.partition("\n")
            items.append(ActivityItem("github", None, name, subject.strip(), body.strip(),
                                      ((c.get("commit") or {}).get("author") or {}).get("date") or "",
                                      c.get("html_url"), "commit"))
        pulls, err = _parse(await execute(iid, "GET", f"/repos/{full}/pulls",
                                          params={"state": "all", "sort": "updated", "direction": "desc", "per_page": 20}))
        if err:
            return items, f"GitHub {full}: {err}"
        for p in pulls or []:
            if not _is_since(p.get("updated_at", ""), since):
                continue
            items.append(ActivityItem("github", None, name, p.get("title") or "", p.get("body") or "",
                                      p.get("updated_at") or "", p.get("html_url"), "pull_request"))
    return items, None


def _trello_item(action: dict, board_name: str) -> Optional[ActivityItem]:
    data = action.get("data") or {}
    card = data.get("card") or {}
    title = card.get("name") or ""
    url = f"https://trello.com/c/{card['shortLink']}" if card.get("shortLink") else None
    kind = action.get("type")
    if kind == "createCard":
        return ActivityItem("trello", None, board_name, title, f"Created in {(data.get('list') or {}).get('name', '')}".strip(),
                            action.get("date") or "", url, "card_created")
    if kind == "updateCard" and data.get("listAfter"):
        return ActivityItem("trello", None, board_name, title,
                            f"Moved from {(data.get('listBefore') or {}).get('name', '')} to {data['listAfter'].get('name', '')}",
                            action.get("date") or "", url, "card_moved")
    if kind == "commentCard":
        return ActivityItem("trello", None, board_name, title, data.get("text") or "", action.get("date") or "", url, "card_comment")
    return None


async def fetch_trello(since: datetime, execute: Callable = execute_api_call) -> Tuple[List[ActivityItem], Optional[str]]:
    iid = resolve_integration_id("trello")
    if not iid:
        return [], "Trello integration not registered (Settings → Integrations → preset Trello)"
    boards, err = _parse(await execute(iid, "GET", "/1/members/me/boards", params={"fields": "name,url"}))
    if err:
        return [], f"Trello: {err}"
    items: List[ActivityItem] = []
    for board in boards or []:
        actions, err = _parse(await execute(iid, "GET", f"/1/boards/{board.get('id')}/actions",
                                            params={"since": since.isoformat(), "filter": TRELLO_FILTER, "limit": 50}))
        if err:
            return items, f"Trello {board.get('name')}: {err}"
        for action in reversed(actions or []):   # API returns newest first; keep chronological
            item = _trello_item(action, board.get("name") or "")
            if item:
                items.append(item)
    return items, None


def _owner_projects(owner: Optional[str], project_ids: Optional[Iterable[str]], session_factory) -> List[Project]:
    db = (session_factory or SessionLocal)()
    try:
        q = db.query(Project).filter(Project.archived == False)  # noqa: E712
        if owner is not None:
            q = q.filter(Project.owner == owner)
        if project_ids:
            q = q.filter(Project.id.in_(list(project_ids)))
        return [type("P", (), {"id": p.id, "name": p.name, "path": p.path})() for p in q.all()]
    finally:
        db.close()


async def gather_activity(owner: Optional[str], days: int, sources: Set[str], project_ids: Optional[Iterable[str]],
                          session_factory=None, execute: Callable = execute_api_call
                          ) -> Tuple[List[ActivityItem], List[str]]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    items: List[ActivityItem] = []
    warnings: List[str] = []
    if "git" in sources:
        for project in _owner_projects(owner, project_ids, session_factory):
            got, warn = await asyncio.to_thread(fetch_local_git, project, since)
            items.extend(got)
            if warn:
                warnings.append(warn)
    if "github" in sources:
        got, warn = await fetch_github(since, execute=execute)
        items.extend(got)
        if warn:
            warnings.append(warn)
    if "trello" in sources:
        got, warn = await fetch_trello(since, execute=execute)
        items.extend(got)
        if warn:
            warnings.append(warn)
    items.sort(key=lambda i: i.date, reverse=True)
    return items, warnings
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_career_activity_sources.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add services/career/activity_remote.py tests/test_career_activity_sources.py
git commit -m "feat(career): GitHub and Trello activity sources with gather"
```

---

### Task 4: Disclosure and redaction

**Files:**
- Create: `services/career/disclosure.py`
- Test: `tests/test_career_disclosure.py`

**Interfaces:**
- Consumes: `ActivityItem`, settings keys `disclosure_default`, `project_disclosure`, `blocklist_terms`.
- Produces: `LEVELS`, `level_for(project_id, settings) -> str`, `redact_text(text, blocklist=()) -> str`, `apply_disclosure(items, settings) -> list[ActivityItem]`, `ANONYMOUS_NAME = "a project I'm building"`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_disclosure.py
"""Deterministic scrub + per-project disclosure levels. The model never sees raw text."""
import pytest

from services.career import disclosure as d
from services.career.activity_sources import ActivityItem


@pytest.mark.parametrize("raw,expected_absent", [
    ("edited D:\\Code\\Odysseus\\src\\agent_loop.py today", "D:\\Code"),
    ("see /home/luke/app/routes/x.py", "/home/luke"),
    ("deployed to https://api.internal.example.com/v1", "internal.example.com"),
    ("bind 127.0.0.1:7000 and host 10.0.0.5", "7000"),
    ("set CHROMADB_HOST=chroma and $SEARXNG_URL", "CHROMADB_HOST"),
    ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_"),
    ("key sk-abcdef1234567890abcdef", "sk-"),
    ("aws AKIAIOSFODNN7EXAMPLE", "AKIA"),
    ("hash 0123456789abcdef0123456789abcdef", "0123456789abcdef"),
    ("bumped fastapi to v0.115.2", "0.115.2"),
    ("mail luke@example.com", "luke@example.com"),
])
def test_redact_text_removes_identifying_fragments(raw, expected_absent):
    out = d.redact_text(raw)
    assert expected_absent not in out
    assert "[redacted]" in out


def test_blocklist_terms_are_case_insensitive():
    assert "Odysseus" not in d.redact_text("Odysseus now streams", ["odysseus"])


def test_plain_prose_is_untouched():
    text = "Rewrote the retry loop so timeouts back off exponentially."
    assert d.redact_text(text) == text


def test_level_for_uses_project_override_then_default():
    settings = {"disclosure_default": "name_only", "project_disclosure": {"p1": "public", "bad": "nonsense"}}
    assert d.level_for("p1", settings) == "public"
    assert d.level_for("p2", settings) == "name_only"
    assert d.level_for("bad", settings) == "name_only"
    assert d.level_for(None, {}) == "anonymous"


def _item(pid, name, title, body):
    return ActivityItem("git", pid, name, title, body, "2026-10-01T00:00:00+00:00", None, "commit")


def test_apply_disclosure_three_levels():
    settings = {"disclosure_default": "anonymous", "project_disclosure": {"n": "name_only", "pub": "public"},
                "blocklist_terms": ["vanders"]}
    items = d.apply_disclosure([
        _item("a", "Odysseus", "feat: tool_index.py warmup", "warm vanders cache in app.py"),
        _item("n", "Odysseus", "feat: tool_index.py warmup", "warm cache"),
        _item("pub", "Odysseus", "feat: tool_index.py warmup", "warm cache ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ], settings)
    anon, named, public = items
    assert anon.project_name == d.ANONYMOUS_NAME
    assert "tool_index.py" not in anon.title and "app.py" not in anon.body and "vanders" not in anon.body
    assert named.project_name == "Odysseus" and "tool_index.py" not in named.title
    assert public.project_name == "Odysseus" and "tool_index.py" in public.title and "ghp_" not in public.body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_disclosure.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.disclosure'`

- [ ] **Step 3: Implement**

```python
# services/career/disclosure.py
"""Privacy scrub for project activity before it reaches a model or the UI.

Two layers: ``redact_text`` is a table of regexes that always run (paths,
hosts, ports, env vars, secrets, versions, emails, IPs, the user's blocklist),
and ``apply_disclosure`` adds the per-project level — anonymous projects lose
their name and any module-ish identifiers; name_only keeps the name; public
keeps module names but still never leaks secrets or blocklisted terms.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Optional

from services.career.activity_sources import ActivityItem

LEVELS = ("anonymous", "name_only", "public")
ANONYMOUS_NAME = "a project I'm building"
REDACTED = "[redacted]"

# Order matters: secrets before hex-ish tokens, URLs before host:port.
_RULES = [
    ("secret_prefixed", re.compile(r"\b(?:ghp|gho|ghs|github_pat|sk|xox[bap]|AKIA)[A-Za-z0-9_\-]{8,}\b")),
    ("hex_token", re.compile(r"\b[0-9a-fA-F]{32,}\b")),
    ("url", re.compile(r"\bhttps?://[^\s)\]]+")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("win_path", re.compile(r"\b[A-Za-z]:\\[^\s'\"`]+")),
    ("posix_path", re.compile(r"(?<![\w/])/(?:[\w.-]+/)+[\w.-]+")),
    ("ip", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{2,5})?\b")),
    ("host_port", re.compile(r"\b[\w.-]+:\d{2,5}\b")),
    ("env_assign", re.compile(r"\b[A-Z][A-Z0-9_]{2,}=\S*")),
    ("env_ref", re.compile(r"\$\{?[A-Z][A-Z0-9_]{2,}\}?")),
    ("semver", re.compile(r"\bv?\d+\.\d+\.\d+(?:[-+][\w.]+)?\b")),
]
_MODULE_RE = re.compile(r"\b[\w\-]+\.(?:py|js|ts|tsx|jsx|css|html|sql|go|rs|java|cs)\b")


def redact_text(text: str, blocklist: Iterable[str] = ()) -> str:
    out = text or ""
    for _name, rx in _RULES:
        out = rx.sub(REDACTED, out)
    for term in blocklist or ():
        term = (term or "").strip()
        if term:
            out = re.sub(re.escape(term), REDACTED, out, flags=re.IGNORECASE)
    return out


def level_for(project_id: Optional[str], settings: Dict[str, Any]) -> str:
    default = settings.get("disclosure_default") or "anonymous"
    if default not in LEVELS:
        default = "anonymous"
    level = (settings.get("project_disclosure") or {}).get(project_id or "", default)
    return level if level in LEVELS else default


def _scrub(text: str, level: str, blocklist: Iterable[str]) -> str:
    if level == "public":
        # Secrets, emails and the blocklist still go; paths and versions may stay.
        out = text or ""
        for name, rx in _RULES:
            if name in ("secret_prefixed", "hex_token", "email"):
                out = rx.sub(REDACTED, out)
        for term in blocklist or ():
            if term.strip():
                out = re.sub(re.escape(term.strip()), REDACTED, out, flags=re.IGNORECASE)
        return out
    out = redact_text(text, blocklist)
    return _MODULE_RE.sub(REDACTED, out)


def apply_disclosure(items: Iterable[ActivityItem], settings: Dict[str, Any]) -> List[ActivityItem]:
    blocklist = settings.get("blocklist_terms") or []
    out: List[ActivityItem] = []
    for item in items:
        level = level_for(item.project_id, settings)
        name = ANONYMOUS_NAME if level == "anonymous" else item.project_name
        out.append(replace(
            item, project_name=name,
            title=_scrub(item.title, level, blocklist),
            body=_scrub(item.body, level, blocklist),
            url=item.url if level == "public" else None,
        ))
    return out
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_disclosure.py -v`
Expected: all passed. If `host_port` eats a time like `10:30`, that is acceptable (it is redacted, not leaked).

- [ ] **Step 5: Commit**

```bash
git add services/career/disclosure.py tests/test_career_disclosure.py
git commit -m "feat(career): activity redaction and per-project disclosure levels"
```

---

### Task 5: Post drafting service and prompt templates

**Files:**
- Create: `services/career/prompts/post_rules.md`, `post_progress.md`, `post_technique.md`, `post_lesson.md`, `post_narrative.md`
- Create: `services/career/posts.py`
- Test: `tests/test_career_posts.py`

**Interfaces:**
- Consumes: `ActivityItem`, `services.career.llm.complete`, settings keys `voice_rules`, `target_roles`, `narrative_uses_tracker_stats`.
- Produces: `POST_TYPES`, `build_post_messages(items, post_type, settings, tracker_stats=None) -> list[dict]`, `parse_post_output(raw) -> {"body", "hooks"}`, `async draft_post(items, post_type, settings, owner, complete=complete, n_variants=2, tracker_stats=None) -> list[{"body","hooks"}]`, `post_to_dict(post: CareerPost) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_posts.py
"""Post drafting: prompt assembly, JSON parsing with fallback, N variants."""
import pytest

from services.career import posts
from services.career.activity_sources import ActivityItem


def _items():
    return [ActivityItem("git", "p1", "a project I'm building", "feat: incremental tokenizer",
                         "stream tokens as they arrive", "2026-10-03T00:00:00+00:00", None, "commit")]


def test_messages_include_rules_type_template_voice_and_items():
    msgs = posts.build_post_messages(_items(), "technique", {"voice_rules": "no exclamation marks", "target_roles": []})
    assert msgs[0]["role"] == "system" and "140" in msgs[0]["content"] and "never name internal modules" in msgs[0]["content"].lower()
    user = msgs[1]["content"]
    assert "incremental tokenizer" in user and "no exclamation marks" in user
    assert "technique" in user.lower()


def test_narrative_adds_target_roles_and_optional_stats():
    msgs = posts.build_post_messages(_items(), "narrative", {"target_roles": ["backend engineer"]},
                                     tracker_stats={"interviews": 2})
    assert "backend engineer" in msgs[1]["content"] and "2 interviews" in msgs[1]["content"]


def test_unknown_type_raises():
    with pytest.raises(ValueError):
        posts.build_post_messages(_items(), "rant", {})


def test_parse_fenced_json_and_fallback():
    out = posts.parse_post_output('Here you go:\n```json\n{"post": "Body text", "hooks": ["a", "b", "c", "d"]}\n```')
    assert out == {"body": "Body text", "hooks": ["a", "b", "c"]}
    raw = posts.parse_post_output("Just prose, no json.")
    assert raw == {"body": "Just prose, no json.", "hooks": []}


async def test_draft_post_calls_complete_per_variant():
    seen = []

    async def fake_complete(messages, owner=None):
        seen.append((messages[1]["content"], owner))
        return '{"post": "variant %d", "hooks": ["h1","h2","h3"]}' % len(seen)

    out = await posts.draft_post(_items(), "progress", {}, "vanders", complete=fake_complete, n_variants=3)
    assert [v["body"] for v in out] == ["variant 1", "variant 2", "variant 3"]
    assert all(o == "vanders" for _, o in seen)
    assert "Variant 2 of 3" in seen[1][0]


async def test_draft_post_requires_items():
    with pytest.raises(ValueError):
        await posts.draft_post([], "progress", {}, "vanders", complete=None)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_posts.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.posts'`

- [ ] **Step 3: Write the prompt templates**

`services/career/prompts/post_rules.md`:

```markdown
You draft LinkedIn posts for a software engineer who is job hunting. Rules that always apply:

- First person, plain-spoken, specific. No "thrilled to announce", no "excited to share", no buzzword lists.
- The first line is the hook and must be 140 characters or fewer; LinkedIn truncates the preview there.
- 100 to 200 words in total. Short paragraphs, one idea each. At most 4 hashtags, on the last line.
- Describe the technique or the outcome, never the implementation: never name internal modules, file names,
  vendors, infrastructure, hosts, credentials or customers. Anything marked [redacted] stays out entirely.
- Respect the project name exactly as given. "a project I'm building" means the project must stay unnamed.
- One soft call to action at most (a question or an invitation to compare notes). No links.
- Output exactly one fenced JSON block: {"post": "<the post>", "hooks": ["<alt hook 1>", "<alt hook 2>", "<alt hook 3>"]}
```

`services/career/prompts/post_progress.md`:

```markdown
Post type: progress update. Tell what shipped recently and why it mattered, built from the activity below.
Pick the two or three most interesting items; ignore chores. End with what is next in one sentence.
```

`services/career/prompts/post_technique.md`:

```markdown
Post type: technique / TIL. Choose one technique visible in the activity below and explain it generically so an
engineer on any stack could reuse it: the problem, the idea, the trade-off. No project internals at all.
```

`services/career/prompts/post_lesson.md`:

```markdown
Post type: lesson learned. Find a mistake, reversal or trade-off in the activity below. Say what was tried, what
went wrong or surprised, and what the author would do differently. Honest, not self-flagellating.
```

`services/career/prompts/post_narrative.md`:

```markdown
Post type: job-search narrative. Open-to-work style. Say what roles the author is looking for, what they have been
building recently (from the activity below) and what kind of team they want. Confident, not pleading.
```

- [ ] **Step 4: Implement**

```python
# services/career/posts.py
"""LinkedIn post drafting (plan 03): prompt from packaged templates, one
completion per variant, JSON-block parsing with a plain-text fallback.
"""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Callable, Dict, Iterable, List, Optional

from services.career.activity_sources import ActivityItem
from services.career.llm import complete as default_complete

POST_TYPES = ("progress", "technique", "lesson", "narrative")
MAX_ITEMS_IN_PROMPT = 40
_PROMPTS = Path(__file__).parent / "prompts"
_JSON_RE = re.compile(r"\{[^{}]*\}", re.S)


def _read(name: str) -> str:
    return (_PROMPTS / name).read_text(encoding="utf-8").strip()


def _items_block(items: Iterable[ActivityItem]) -> str:
    lines = []
    for i in list(items)[:MAX_ITEMS_IN_PROMPT]:
        body = f" — {i.body}" if i.body else ""
        lines.append(f"- [{i.source}/{i.kind}] {i.date[:10]} {i.project_name}: {i.title}{body}")
    return "\n".join(lines)


def build_post_messages(items: Iterable[ActivityItem], post_type: str, settings: Dict[str, Any],
                        tracker_stats: Optional[Dict[str, int]] = None, variant: int = 1,
                        n_variants: int = 1) -> List[Dict[str, str]]:
    if post_type not in POST_TYPES:
        raise ValueError(f"post_type must be one of {POST_TYPES}")
    parts = [_read(f"post_{post_type}.md")]
    voice = (settings.get("voice_rules") or "").strip()
    if voice:
        parts.append(f"Author's voice rules:\n{voice}")
    if post_type == "narrative":
        roles = ", ".join(settings.get("target_roles") or []) or "software engineering roles"
        parts.append(f"Roles sought: {roles}.")
        if tracker_stats and settings.get("narrative_uses_tracker_stats", False) is not False:
            parts.append(f"Search so far: {tracker_stats.get('applications', 0)} applications, "
                         f"{tracker_stats.get('interviews', 0)} interviews.")
    if n_variants > 1:
        parts.append(f"Variant {variant} of {n_variants}: take a different angle from the other variants.")
    parts.append("Activity (already privacy-scrubbed):\n" + _items_block(items))
    return [{"role": "system", "content": _read("post_rules.md")},
            {"role": "user", "content": "\n\n".join(parts)}]


def parse_post_output(raw: str) -> Dict[str, Any]:
    text = (raw or "").strip()
    for candidate in _JSON_RE.findall(text):
        try:
            data = json.loads(candidate)
        except (json.JSONDecodeError, ValueError):
            continue
        if isinstance(data, dict) and data.get("post"):
            hooks = [str(h).strip() for h in (data.get("hooks") or []) if str(h).strip()][:3]
            return {"body": str(data["post"]).strip(), "hooks": hooks}
    cleaned = re.sub(r"```(?:json)?", "", text).strip()
    return {"body": cleaned, "hooks": []}


async def draft_post(items: List[ActivityItem], post_type: str, settings: Dict[str, Any], owner: Optional[str],
                     complete: Callable = default_complete, n_variants: int = 2,
                     tracker_stats: Optional[Dict[str, int]] = None) -> List[Dict[str, Any]]:
    if not items:
        raise ValueError("No activity in the selected window — widen the window or add a source")
    n = max(1, min(int(n_variants), 5))
    out = []
    for v in range(1, n + 1):
        messages = build_post_messages(items, post_type, settings, tracker_stats, variant=v, n_variants=n)
        out.append(parse_post_output(await complete(messages, owner)))
    return out


def post_to_dict(post) -> Dict[str, Any]:
    def _json(s):
        try:
            return json.loads(s) if s else []
        except (json.JSONDecodeError, TypeError):
            return []
    return {
        "id": post.id, "post_type": post.post_type, "body": post.body, "hooks": _json(post.hooks),
        "sources": _json(post.sources), "window_days": post.window_days, "status": post.status,
        "posted_at": post.posted_at.isoformat() if post.posted_at else None,
        "created_at": post.created_at.isoformat() if post.created_at else None,
    }
```

Note on `narrative_uses_tracker_stats`: the test passes `tracker_stats` with settings lacking the key, and expects the stats line. The intent from the design is "only when the user opts in"; the route (Task 6) is where the gate lives — it only passes `tracker_stats` when the setting is true. Keep `build_post_messages` simple: if stats are given, they are used. Replace the `if tracker_stats and ...` line above with `if tracker_stats:`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_career_posts.py -v`
Expected: 6 passed

- [ ] **Step 6: Commit**

```bash
git add services/career/prompts services/career/posts.py tests/test_career_posts.py
git commit -m "feat(career): LinkedIn post drafting with packaged prompt templates"
```

---

### Task 6: Studio routes — activity preview, drafts, posts, disclosure

**Files:**
- Create or modify: `routes/career_studio_routes.py`
- Modify: `app.py` (mount only if the router is new)
- Test: `tests/test_career_posts_routes.py`

**Interfaces:**
- Consumes: Tasks 3–5; `core.database.CareerPost`, `Project`, `SessionLocal`; `services.career.settings`.
- Produces: `setup_career_studio_routes(rag_manager) -> APIRouter` (prefix `/api/career`) with:

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/activity` | `days=14`, `sources=git,github,trello`, `project_ids=a,b` | `{items: [redacted item dicts], warnings: [str]}` |
| POST | `/posts/draft` | `{post_type, days=14, sources=[...], project_ids=[...], n_variants=2}` | `{posts: [post dicts]}` (201); 422 unknown type; 409 no activity |
| GET | `/posts` | `status=` | `{posts: [...]}` newest first |
| PUT | `/posts/{id}` | `{status?, body?}` | post dict; `status: posted` stamps `posted_at` |
| DELETE | `/posts/{id}` | — | `{id, status: "discarded"}` |
| PUT | `/projects-disclosure` | `{project_id, level}` | settings dict; 422 unknown level |

**If `routes/career_studio_routes.py` already exists (plan 01)**: skip the "create" variant, add the imports and request models at module level, and paste the endpoints block before the final `return router`. Otherwise create the file as shown and mount it in `app.py` after the career router.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_posts_routes.py
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


_sa, _sa_orm, _sa_pool, _coredb, sr, cs = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "routes.career_studio_routes", "services.career.settings",
    force=("core.database", "routes.career_studio_routes"),
)
CareerPost = _coredb.CareerPost
from services.career.activity_sources import ActivityItem


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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_posts_routes.py -v`
Expected: FAIL with `ModuleNotFoundError` (new file) or `AttributeError: ... has no attribute 'gather_activity'` (file exists from plan 01)

- [ ] **Step 3a: Create the router (only if the file does not exist)**

```python
# routes/career_studio_routes.py
"""Career studio API — cover letters (plan 01), reviewer panel (plan 02),
activity + LinkedIn posts (plan 03). Owner-scoped like routes/career_routes.py.
"""
from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from core.database import CareerPost, SessionLocal
from services.career.activity_remote import SOURCES, gather_activity
from services.career.disclosure import LEVELS, apply_disclosure
from services.career.posts import POST_TYPES, draft_post, post_to_dict
from services.career.settings import load_career_settings, save_career_settings
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def setup_career_studio_routes(rag_manager) -> APIRouter:
    router = APIRouter(prefix="/api/career", tags=["career"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    # --- plan 03 endpoints are pasted here (Step 3b) ---

    return router
```

Mount in `app.py` after the career router:

```python
from routes.career_studio_routes import setup_career_studio_routes
app.include_router(setup_career_studio_routes(rag_manager))
```

- [ ] **Step 3b: Add the plan-03 models and endpoints**

Module-level (after the imports; if the file came from plan 01, also add the four `services.career.*` imports and `CareerPost` above):

```python
POST_STATUSES = ("draft", "posted", "discarded")


class DraftRequest(BaseModel):
    post_type: str
    days: int = 14
    sources: List[str] = ["git", "github", "trello"]
    project_ids: List[str] = []
    n_variants: int = 2

    @field_validator("post_type")
    @classmethod
    def _type(cls, v: str) -> str:
        if v not in POST_TYPES:
            raise ValueError(f"post_type must be one of {POST_TYPES}")
        return v


class PostUpdate(BaseModel):
    status: Optional[str] = None
    body: Optional[str] = None

    @field_validator("status")
    @classmethod
    def _status(cls, v):
        if v is not None and v not in POST_STATUSES:
            raise ValueError(f"status must be one of {POST_STATUSES}")
        return v


class DisclosureUpdate(BaseModel):
    project_id: str
    level: str

    @field_validator("level")
    @classmethod
    def _level(cls, v: str) -> str:
        if v not in LEVELS:
            raise ValueError(f"level must be one of {LEVELS}")
        return v


def _sources(raw: List[str]) -> set:
    chosen = {s.strip() for s in raw if s and s.strip() in SOURCES}
    return chosen or set(SOURCES)
```

Inside `setup_career_studio_routes`, before `return router`:

```python
    def _owned_post(db, pid: str, user: Optional[str]) -> CareerPost:
        q = db.query(CareerPost).filter(CareerPost.id == pid)
        if user is not None:
            q = q.filter(CareerPost.owner == user)
        post = q.first()
        if not post:
            raise HTTPException(404, "Post not found")
        return post

    @router.get("/activity")
    async def activity_preview(request: Request, days: int = Query(14), sources: str = Query("git,github,trello"),
                               project_ids: str = Query("")):
        user = _owner(request)
        settings = load_career_settings(user)
        ids = [p for p in project_ids.split(",") if p.strip()] or None
        items, warnings = await gather_activity(user, days, _sources(sources.split(",")), ids)
        return {"items": [i.to_dict() for i in apply_disclosure(items, settings)], "warnings": warnings}

    @router.post("/posts/draft", status_code=201)
    async def draft_posts(body: DraftRequest, request: Request):
        user = _owner(request)
        settings = load_career_settings(user)
        items, warnings = await gather_activity(user, body.days, _sources(body.sources), body.project_ids or None)
        safe = apply_disclosure(items, settings)
        if not safe:
            raise HTTPException(409, "No activity in that window. " + " ".join(warnings))
        tracker_stats = None
        if body.post_type == "narrative" and settings.get("narrative_uses_tracker_stats"):
            tracker_stats = _tracker_stats(user)
        variants = await draft_post(safe, body.post_type, settings, user, n_variants=body.n_variants,
                                    tracker_stats=tracker_stats)
        db = SessionLocal()
        try:
            rows = []
            for v in variants:
                post = CareerPost(
                    id=uuid.uuid4().hex[:12], owner=user, post_type=body.post_type, body=v["body"],
                    hooks=json.dumps(v["hooks"]), sources=json.dumps([i.to_dict() for i in safe[:20]]),
                    window_days=body.days, status="draft",
                )
                db.add(post)
                rows.append(post)
            db.commit()
            for r in rows:
                db.refresh(r)
            return {"posts": [post_to_dict(r) for r in rows], "warnings": warnings}
        finally:
            db.close()

    def _tracker_stats(user: Optional[str]) -> Dict[str, int]:
        """Counts for narrative posts; the tracker (plan 04) owns the data, so
        this stays a two-line query on JobApplication and degrades to zeros."""
        try:
            from core.database import JobApplication
            db = SessionLocal()
            try:
                q = db.query(JobApplication).filter(JobApplication.archived == False)  # noqa: E712
                if user is not None:
                    q = q.filter(JobApplication.owner == user)
                apps = q.all()
            finally:
                db.close()
            return {"applications": len(apps),
                    "interviews": sum(1 for a in apps if a.status in ("interview", "offer"))}
        except Exception:
            return {"applications": 0, "interviews": 0}

    @router.get("/posts")
    def list_posts(request: Request, status: str = Query("")):
        user = _owner(request)
        db = SessionLocal()
        try:
            q = db.query(CareerPost)
            if user is not None:
                q = q.filter(CareerPost.owner == user)
            if status:
                q = q.filter(CareerPost.status == status)
            else:
                q = q.filter(CareerPost.status != "discarded")
            return {"posts": [post_to_dict(p) for p in q.order_by(CareerPost.created_at.desc()).all()]}
        finally:
            db.close()

    @router.put("/posts/{pid}")
    def update_post(pid: str, body: PostUpdate, request: Request):
        db = SessionLocal()
        try:
            post = _owned_post(db, pid, _owner(request))
            if body.body is not None:
                post.body = body.body
            if body.status is not None:
                post.status = body.status
                if body.status == "posted":
                    post.posted_at = datetime.utcnow()
            db.commit()
            db.refresh(post)
            return post_to_dict(post)
        finally:
            db.close()

    @router.delete("/posts/{pid}")
    def discard_post(pid: str, request: Request):
        db = SessionLocal()
        try:
            post = _owned_post(db, pid, _owner(request))
            post.status = "discarded"
            db.commit()
            return {"id": pid, "status": "discarded"}
        finally:
            db.close()

    @router.put("/projects-disclosure")
    def set_disclosure(body: DisclosureUpdate, request: Request):
        user = _owner(request)
        current = load_career_settings(user).get("project_disclosure") or {}
        current[body.project_id] = body.level
        return save_career_settings(user, {"project_disclosure": current})
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_posts_routes.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add routes/career_studio_routes.py app.py tests/test_career_posts_routes.py
git commit -m "feat(career): activity preview, post drafting and lifecycle routes"
```

---

### Task 7: Seeded skill `linkedin-post-writer` v2

**Files:**
- Create: `services/career/skills/linkedin-post-writer/SKILL.md`
- Test: `tests/test_career_posts_skill.py`

**Interfaces:**
- Consumes: `services.career.seeds.seed_skills` (copies this folder into `data/skills/career/linkedin-post-writer/` on first `GET /api/career/bootstrap`, retiring the v1 `general/` skill).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_posts_skill.py
from pathlib import Path

from services.memory.skill_format import Skill

_SKILL = Path("services/career/skills/linkedin-post-writer/SKILL.md")


def test_skill_template_parses_with_expected_frontmatter():
    sk = Skill.from_markdown(_SKILL.read_text(encoding="utf-8"))
    assert sk.name == "linkedin-post-writer"
    assert sk.version == "2.0.0" and sk.category == "career"
    assert sk.source == "user" and sk.status == "published"
    assert "/api/career/activity" in " ".join(sk.procedure)
    assert "/api/career/posts/draft" in " ".join(sk.procedure)
    assert any("never publish" in p.lower() for p in sk.procedure + sk.pitfalls)
    assert any("app_api" in p for p in sk.pitfalls)
    assert any("140" in v for v in sk.verification)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_posts_skill.py -v`
Expected: FAIL with `FileNotFoundError`

- [ ] **Step 3: Write the skill**

```markdown
---
name: linkedin-post-writer
description: "Draft a ready-to-paste LinkedIn post in my voice from my recent project activity (git, GitHub, Trello), privacy-scrubbed. Never publishes."
version: 2.0.0
category: career
tags: [linkedin, posts, writing, career, social]
status: published
confidence: 0.9
source: user
---

## When to Use

When I ask to write or draft a LinkedIn post about a project, an update, something I learned, or an open-to-work post. Also triggered by `/linkedin-post-writer <what it's about>`.

## Procedure

1. Decide the post type with me if it is not obvious: progress (what shipped), technique (one reusable idea, no internals), lesson (a mistake or trade-off), narrative (open-to-work).
2. Fetch the privacy-scrubbed activity with the app_api tool: GET /api/career/activity?days=14&sources=git,github,trello (use days=7 or 30 if I ask). Show me the item list and any warnings (an unregistered source is a warning, not an error).
3. If the preview is empty, say so and ask whether to widen the window or register GitHub/Trello under Settings → Integrations. Do not invent activity.
4. Draft with app_api: POST /api/career/posts/draft with {"post_type": "<type>", "days": <n>, "sources": [...], "n_variants": 2}. The server applies the disclosure rules and stores the drafts.
5. Present each variant as one clean copy-paste block followed by its three alternative hooks. Point out which variant you would post and why in one sentence.
6. Offer one round of edits; apply them with PUT /api/career/posts/{id} {"body": "..."} so the hub keeps the final text.
7. Remind me it is a draft to review in the Career hub → Posts tab, and that this skill never publishes anywhere.

## Pitfalls

- The activity is already privacy-scrubbed; never re-add project names, file names, vendors, hosts or anything that appeared as [redacted]. "a project I'm building" must stay unnamed.
- app_api is an admin-only tool. If it is refused, tell me to run the draft from the Career hub → Posts tab instead.
- My voice: first-person, plain-spoken, authentic. No buzzword salad, no "thrilled to announce", no "excited to share".
- Open with a strong hook in the first line (LinkedIn truncates the preview around 140 characters). 100-200 words, 2-4 relevant hashtags, at most one soft call to action.
- Never publish, never post, never offer to post. Drafts only.

## Verification

- The first line of each variant is 140 characters or fewer and would make sense on its own.
- Each variant is 100-200 words with at most 4 hashtags and no links.
- No [redacted] markers, file names, hostnames, credentials or internal module names appear in the post.
- The drafts exist in the Career hub (GET /api/career/posts returns them with status draft).
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_posts_skill.py tests/test_career_skill_seeding.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add services/career/skills/linkedin-post-writer/SKILL.md tests/test_career_posts_skill.py
git commit -m "feat(career): seeded linkedin-post-writer v2 skill"
```

---

### Task 8: Posts tab front-end

**Files:**
- Create: `static/js/careerPostsLogic.js`, `static/js/careerPosts.js`
- Modify: `static/js/career.js` (one import line + one install line)
- Modify: `static/style.css` (append)
- Test: `tests/test_career_posts_js.py`

**Interfaces:**
- Consumes: the plan 00 `career.js` plugin API object (`registerTab`, `onPanelClick`, `refresh`, `render`, `getState`) passed to `installPosts(api)`; `uiModule.esc/showToast/showError/styledConfirm` from `static/js/ui.js`; `GET /api/projects` (`{projects: [{id, name, exists, ...}]}`).
- Produces: `careerPostsLogic.js` exports `cadenceLabel(posts, now)`, `postCardHtml(post, esc)`, `activityItemHtml(item, esc)`, `SOURCE_LABELS`, `POST_TYPE_LABELS`, `DISCLOSURE_LABELS`; `careerPosts.js` exports `installPosts(api)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_posts_js.py
"""careerPostsLogic.js helpers under Node + syntax checks + wiring."""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def node_available():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(["node", "--input-type=module", "-e", script], cwd=_REPO,
                         capture_output=True, timeout=15, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    return json.loads([ln for ln in res.stdout.splitlines() if ln.strip()][-1])


def test_cadence_label(node_available):
    out = _run_node(textwrap.dedent("""
        const { cadenceLabel } = await import('./static/js/careerPostsLogic.js');
        const now = new Date('2026-10-10T00:00:00Z');
        console.log(JSON.stringify({
          none: cadenceLabel([], now),
          nine: cadenceLabel([{ status: 'posted', posted_at: '2026-10-01T00:00:00' },
                              { status: 'draft', posted_at: null }], now),
          today: cadenceLabel([{ status: 'posted', posted_at: '2026-10-10T08:00:00' }], now),
        }));
    """))
    assert out["none"] == "Nothing posted yet"
    assert out["nine"] == "Last posted 9 days ago"
    assert out["today"] == "Last posted today"


def test_post_card_and_activity_item_html(node_available):
    out = _run_node(textwrap.dedent("""
        const { postCardHtml, activityItemHtml } = await import('./static/js/careerPostsLogic.js');
        const esc = (s) => String(s ?? '').replace(/</g, '&lt;');
        console.log(JSON.stringify({
          card: postCardHtml({ id: 'p1', post_type: 'technique', body: 'Body <b>', hooks: ['h1', 'h2'], status: 'draft' }, esc),
          posted: postCardHtml({ id: 'p2', post_type: 'progress', body: 'x', hooks: [], status: 'posted' }, esc),
          item: activityItemHtml({ source: 'git', kind: 'commit', date: '2026-10-03T00:00:00+00:00', project_name: 'a project I\\'m building', title: 'feat: x', body: '' }, esc),
        }));
    """))
    assert 'data-post-id="p1"' in out["card"] and "&lt;b>" in out["card"]
    assert 'data-action="copy-post"' in out["card"] and 'data-action="mark-posted"' in out["card"] and 'data-action="discard-post"' in out["card"]
    assert "h1" in out["card"] and "Technique" in out["card"]
    assert 'data-action="mark-posted"' not in out["posted"]
    assert "2026-10-03" in out["item"] and "feat: x" in out["item"] and "git" in out["item"]


def test_posts_module_wired_and_parses(node_available):
    career = (_REPO / "static" / "js" / "career.js").read_text(encoding="utf-8")
    assert "import { installPosts } from './careerPosts.js';" in career
    assert "installPosts(careerApi);" in career
    for f in ("careerPosts.js", "careerPostsLogic.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
    posts = (_REPO / "static" / "js" / "careerPosts.js").read_text(encoding="utf-8")
    assert "export const installPosts" in posts
    assert "api.registerTab('posts'" in posts and "api.onPanelClick(" in posts
    assert "from './career.js'" not in posts
    assert "navigator.clipboard.writeText" in posts
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_posts_js.py -v`
Expected: FAIL with `Cannot find module` / missing import assertion

- [ ] **Step 3: `static/js/careerPostsLogic.js`**

```javascript
// ============================================
// Career hub — Posts tab pure helpers (no DOM, Node-importable)
// ============================================

export const SOURCE_LABELS = { git: 'Local git', github: 'GitHub', trello: 'Trello' };
export const POST_TYPE_LABELS = {
  progress: 'Progress update',
  technique: 'Technique / TIL',
  lesson: 'Lesson learned',
  narrative: 'Job-search narrative',
};
export const DISCLOSURE_LABELS = { anonymous: 'Anonymous', name_only: 'Name only', public: 'Public' };

export const cadenceLabel = (posts, now = new Date()) => {
  const stamps = (posts || [])
    .filter((p) => p.status === 'posted' && p.posted_at)
    .map((p) => new Date(p.posted_at.endsWith('Z') ? p.posted_at : `${p.posted_at}Z`).getTime());
  if (!stamps.length) return 'Nothing posted yet';
  const days = Math.floor((now.getTime() - Math.max(...stamps)) / 86400000);
  if (days <= 0) return 'Last posted today';
  return `Last posted ${days} day${days === 1 ? '' : 's'} ago`;
};

export const activityItemHtml = (item, esc) => `
  <li class="career-activity-item">
    <span class="proj-chip">${esc(item.source)}/${esc(item.kind)}</span>
    <span class="career-activity-date">${esc((item.date || '').slice(0, 10))}</span>
    <span class="career-activity-project">${esc(item.project_name)}</span>
    <span class="career-activity-title">${esc(item.title)}</span>
    ${item.body ? `<div class="memory-desc career-activity-body">${esc(item.body)}</div>` : ''}
  </li>`;

export const postCardHtml = (post, esc) => {
  const hooks = (post.hooks || []).map((h) => `<li>${esc(h)}</li>`).join('');
  const actions = post.status === 'draft'
    ? `<button class="memory-toolbar-btn" data-action="mark-posted">Mark posted</button>
       <button class="memory-toolbar-btn" data-action="discard-post">Discard</button>`
    : '';
  return `
    <div class="proj-card admin-card career-post" data-post-id="${esc(post.id)}">
      <div class="proj-card-head">
        <span class="proj-name">${esc(POST_TYPE_LABELS[post.post_type] || post.post_type)}</span>
        <span class="proj-badge ${post.status === 'posted' ? 'career-chip-positive' : 'career-chip-waiting'}">${esc(post.status)}</span>
      </div>
      <pre class="career-post-body">${esc(post.body)}</pre>
      ${hooks ? `<div class="memory-desc">Alternative hooks</div><ul class="career-hooks">${hooks}</ul>` : ''}
      <div class="proj-actions">
        <button class="memory-toolbar-btn" data-action="copy-post">Copy</button>
        ${actions}
      </div>
    </div>`;
};
```

- [ ] **Step 4: `static/js/careerPosts.js`**

```javascript
// ============================================
// Career hub — Posts tab (plan 03). Plugs in through installPosts(api); never imports career.js.
// ============================================
import uiModule from './ui.js';
import {
  activityItemHtml, cadenceLabel, DISCLOSURE_LABELS, POST_TYPE_LABELS, postCardHtml, SOURCE_LABELS,
} from './careerPostsLogic.js';

const esc = uiModule.esc;
let _api = null;

const local = {
  sources: new Set(['git', 'github', 'trello']),
  days: 14,
  postType: 'technique',
  variants: 2,
  projects: [],
  selectedProjects: new Set(),
  activity: null,       // { items, warnings } after a preview
  posts: [],
  busy: false,
  loaded: false,
};

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || `${url} failed (${res.status})`);
  return data;
};

const _load = async () => {
  const [projects, posts] = await Promise.all([
    _json('/api/projects').catch(() => ({ projects: [] })),
    _json('/api/career/posts'),
  ]);
  local.projects = (projects.projects || []).filter((p) => p.exists !== false);
  local.posts = posts.posts || [];
  local.loaded = true;
};

const _controls = (settings) => {
  const sources = Object.entries(SOURCE_LABELS).map(([k, label]) => `
    <label class="career-check"><input type="checkbox" data-source="${k}" ${local.sources.has(k) ? 'checked' : ''}> ${esc(label)}</label>`).join('');
  const days = [7, 14, 30].map((d) => `<option value="${d}" ${d === local.days ? 'selected' : ''}>${d} days</option>`).join('');
  const types = Object.entries(POST_TYPE_LABELS).map(([k, label]) => `<option value="${k}" ${k === local.postType ? 'selected' : ''}>${esc(label)}</option>`).join('');
  const disclosure = settings.project_disclosure || {};
  const projects = local.projects.map((p) => {
    const level = disclosure[p.id] || settings.disclosure_default || 'anonymous';
    const opts = Object.entries(DISCLOSURE_LABELS).map(([k, label]) => `<option value="${k}" ${k === level ? 'selected' : ''}>${esc(label)}</option>`).join('');
    return `<li class="career-project-row" data-project-id="${esc(p.id)}">
      <label class="career-check"><input type="checkbox" data-project="${esc(p.id)}" ${local.selectedProjects.has(p.id) ? 'checked' : ''}> ${esc(p.name)}</label>
      <select class="settings-input career-disclosure" data-disclosure-for="${esc(p.id)}">${opts}</select></li>`;
  }).join('');
  return `
    <div class="council-run-form">${sources}<select id="career-posts-days" class="settings-input">${days}</select></div>
    <details class="proj-summary" ${local.projects.length ? '' : 'hidden'}><summary>Local projects (${local.projects.length}) — tick to limit, set disclosure per project</summary>
      <ul class="career-project-list">${projects}</ul></details>
    <div class="council-run-form">
      <button id="career-posts-preview" class="memory-toolbar-btn" ${local.busy ? 'disabled' : ''}>Preview activity</button>
      <select id="career-posts-type" class="settings-input">${types}</select>
      <input id="career-posts-variants" class="settings-input" type="number" min="1" max="5" value="${local.variants}" style="width:60px">
      <button id="career-posts-draft" class="memory-toolbar-btn" ${local.busy ? 'disabled' : ''}>${local.busy ? 'Working…' : 'Draft posts'}</button>
    </div>`;
};

const _activity = () => {
  if (!local.activity) return '';
  const { items, warnings } = local.activity;
  const warn = (warnings || []).map((w) => `<div class="memory-desc cc-warn">${esc(w)}</div>`).join('');
  const list = items.length
    ? `<ul class="career-activity-list">${items.map((i) => activityItemHtml(i, esc)).join('')}</ul>`
    : '<p class="memory-desc">No activity in this window.</p>';
  return `<h3 class="cc-section">What the model will see (${items.length} items, redacted)</h3>${warn}${list}`;
};

const render = (state) => {
  if (!local.loaded) { _load().then(() => _api.render()).catch((e) => uiModule.showError(`Posts: ${e.message}`)); }
  const drafts = local.posts.filter((p) => p.status === 'draft');
  const posted = local.posts.filter((p) => p.status === 'posted');
  return `
    <div class="career-posts">
      <div class="memory-desc report-scores">${esc(cadenceLabel(local.posts))}</div>
      ${_controls(state.settings || {})}
      ${_activity()}
      <h3 class="cc-section">Drafts (${drafts.length})</h3>
      ${drafts.length ? `<div class="projects-grid">${drafts.map((p) => postCardHtml(p, esc)).join('')}</div>` : '<p class="memory-desc">No drafts yet.</p>'}
      ${posted.length ? `<h3 class="cc-section">Posted (${posted.length})</h3><div class="projects-grid">${posted.map((p) => postCardHtml(p, esc)).join('')}</div>` : ''}
    </div>`;
};

const _readControls = () => {
  local.days = Number(document.getElementById('career-posts-days')?.value || 14);
  local.postType = document.getElementById('career-posts-type')?.value || 'technique';
  local.variants = Number(document.getElementById('career-posts-variants')?.value || 2);
};

const _query = () => {
  const ids = [...local.selectedProjects].join(',');
  return `days=${local.days}&sources=${[...local.sources].join(',')}${ids ? `&project_ids=${encodeURIComponent(ids)}` : ''}`;
};

const _preview = async () => {
  _readControls();
  local.busy = true; _api.render();
  try {
    local.activity = await _json(`/api/career/activity?${_query()}`);
  } catch (e) {
    uiModule.showError(`Preview: ${e.message}`);
  } finally {
    local.busy = false; _api.render();
  }
};

const _draft = async () => {
  _readControls();
  local.busy = true; _api.render();
  try {
    const out = await _json('/api/career/posts/draft', {
      method: 'POST',
      body: JSON.stringify({ post_type: local.postType, days: local.days, sources: [...local.sources],
        project_ids: [...local.selectedProjects], n_variants: local.variants }),
    });
    local.posts = [...out.posts, ...local.posts];
    (out.warnings || []).forEach((w) => uiModule.showToast(w));
    uiModule.showToast(`${out.posts.length} draft${out.posts.length === 1 ? '' : 's'} ready`);
  } catch (e) {
    uiModule.showError(`Draft posts: ${e.message}`);
  } finally {
    local.busy = false; _api.render();
  }
};

const _update = async (id, patch) => {
  try {
    const post = await _json(`/api/career/posts/${id}`, { method: 'PUT', body: JSON.stringify(patch) });
    local.posts = local.posts.map((p) => (p.id === id ? post : p));
    _api.render();
  } catch (e) {
    uiModule.showError(`Update post: ${e.message}`);
  }
};

const _discard = async (id) => {
  if (!(await uiModule.styledConfirm('Discard this draft?'))) return;
  try {
    await _json(`/api/career/posts/${id}`, { method: 'DELETE' });
    local.posts = local.posts.filter((p) => p.id !== id);
    _api.render();
  } catch (e) {
    uiModule.showError(`Discard: ${e.message}`);
  }
};

const _copy = async (id) => {
  const post = local.posts.find((p) => p.id === id);
  if (!post) return;
  try {
    await navigator.clipboard.writeText(post.body);
    uiModule.showToast('Post copied');
  } catch (_) {
    uiModule.showError('Clipboard unavailable — select the text and copy manually');
  }
};

const _setDisclosure = async (projectId, level) => {
  try {
    await _json('/api/career/projects-disclosure', { method: 'PUT', body: JSON.stringify({ project_id: projectId, level }) });
    await _api.refresh();
  } catch (e) {
    uiModule.showError(`Disclosure: ${e.message}`);
  }
};

const onClick = (ev, state) => {
  if (state.tab !== 'posts') return false;
  if (ev.target.closest('#career-posts-preview')) { _preview(); return true; }
  if (ev.target.closest('#career-posts-draft')) { _draft(); return true; }
  const src = ev.target.closest('input[data-source]');
  if (src) { src.checked ? local.sources.add(src.dataset.source) : local.sources.delete(src.dataset.source); return true; }
  const proj = ev.target.closest('input[data-project]');
  if (proj) { proj.checked ? local.selectedProjects.add(proj.dataset.project) : local.selectedProjects.delete(proj.dataset.project); return true; }
  const btn = ev.target.closest('button[data-action]');
  const id = btn?.closest('[data-post-id]')?.dataset.postId;
  if (!btn || !id) return false;
  if (btn.dataset.action === 'copy-post') { _copy(id); return true; }
  if (btn.dataset.action === 'mark-posted') { _update(id, { status: 'posted' }); return true; }
  if (btn.dataset.action === 'discard-post') { _discard(id); return true; }
  return false;
};

export const installPosts = (api) => {
  _api = api;
  api.registerTab('posts', render);
  api.onPanelClick(onClick);
  document.addEventListener('change', (ev) => {
    const sel = ev.target.closest?.('select[data-disclosure-for]');
    if (sel && api.getState().tab === 'posts') _setDisclosure(sel.dataset.disclosureFor, sel.value);
  });
};
```

- [ ] **Step 5: Wire into `career.js` and style**

In `static/js/career.js`, add one import line after `import { readSettingsForm, setupHtml } from './careerSetup.js';` and one install line inside the `// ── plugins ──` block (after `const careerApi = ...`, before `export default careerApi;`):

```javascript
import { installPosts } from './careerPosts.js';
```

```javascript
installPosts(careerApi);
```

`careerPosts.js` never imports `career.js`, so there is no module cycle.

Append to `static/style.css` after the Career hub block:

```css
.career-posts { display: flex; flex-direction: column; gap: 6px; padding: 8px 2px; }
.career-check { display: inline-flex; align-items: center; gap: 4px; font-size: 12px; }
.career-project-list, .career-activity-list, .career-hooks { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.career-project-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; }
.career-disclosure { max-width: 140px; }
.career-activity-item { display: flex; flex-wrap: wrap; gap: 6px; align-items: baseline; font-size: 12px; }
.career-activity-date, .career-activity-project { opacity: 0.6; }
.career-activity-body { flex-basis: 100%; }
.career-post-body { white-space: pre-wrap; font-family: inherit; font-size: 12px; margin: 0; max-height: 320px; overflow-y: auto; }
.career-hooks li { font-size: 12px; opacity: 0.85; }
```

- [ ] **Step 6: Run tests**

Run: `python -m pytest tests/test_career_posts_js.py tests/test_career_js.py -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add static/js/careerPosts.js static/js/careerPostsLogic.js static/js/career.js static/style.css tests/test_career_posts_js.py
git commit -m "feat(career): Posts tab with activity preview, drafting and disclosure controls"
```

---

### Task 9: Full verification and smoke test

- [ ] **Step 1: Suites and syntax**

```bash
python -m py_compile routes/career_studio_routes.py services/career/*.py
node --check static/js/careerPosts.js && node --check static/js/careerPostsLogic.js && node --check static/js/career.js
python -m pytest tests/test_career_*.py -v
python -m pytest
```
Expected: all career suites green; the full run has no new failures.

- [ ] **Step 2: Manual smoke (server running, logged in as admin)**

1. Settings → Integrations → add preset **GitHub** (base `https://api.github.com`, api_key = a read-only PAT) and preset **Trello** (base `https://api.trello.com`, api_key = token, default_params `{"key": "<api key>"}`).
2. Open `/career` → Posts. Untick GitHub and Trello, keep Local git, 14 days → **Preview activity**: commits from this repo appear with `a project I'm building` and `[redacted]` where file names were.
3. In the projects list set this repo to **Name only** → preview again: the project name shows, module names still redacted.
4. Tick GitHub + Trello → preview: items from both, or a warning naming the unregistered one.
5. Post type **Technique**, variants 2 → **Draft posts**: two cards with bodies and three hooks each. **Copy** puts the body on the clipboard. **Mark posted** moves the card to Posted and the cadence line reads "Last posted today".
6. In chat with the agent: `/linkedin-post-writer progress update on what I built this fortnight` → the agent calls the activity route, shows the preview, drafts, and never offers to publish.

- [ ] **Step 3: Final commit if smoke needed fixes**

```bash
git add -A
git commit -m "chore(career): posts smoke fixes"
```

---

## Done when

- [ ] `tests/test_career_activity_sources.py`, `test_career_disclosure.py`, `test_career_posts.py`, `test_career_posts_routes.py`, `test_career_posts_skill.py`, `test_career_posts_js.py` pass; full `python -m pytest` has no new failures.
- [ ] `GET /api/career/activity` returns redacted items from local git, GitHub and Trello, with per-source warnings instead of errors.
- [ ] Per-project disclosure levels persist in settings and change the preview immediately.
- [ ] `POST /api/career/posts/draft` stores N variants as `CareerPost` drafts; copy / mark posted / discard work from the Posts tab.
- [ ] `data/skills/career/linkedin-post-writer/SKILL.md` exists at v2.0.0 after the first `/career` open and the v1 `general/` folder is gone.
- [ ] Nothing in the codebase can publish to LinkedIn.
