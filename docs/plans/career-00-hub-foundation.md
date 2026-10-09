# Career Hub 00 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ship the Career hub shell that every other career plan plugs into: four DB tables, per-owner settings, reviewer/skill seeding, the `/api/career` applications + CV + examples routes, the `#career-modal` UI with Applications and Setup tabs, and three small shared fixes (office-document upload text, a reusable library-document creator, GitHub/Trello integration presets).

**Architecture:** Mirrors the Agent Council feature exactly: SQLAlchemy models in `core/database.py` (new tables, `create_all` handles them), a `services/career/` package for logic, a `setup_career_routes(...)` router factory mounted in `app.py`, and a modal rendered by `static/js/career.js` with DOM-free helpers in `static/js/careerLogic.js` tested from Node. Design: `docs/plans/career-hub-design.md`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (SQLite), pytest (`asyncio_mode=auto`), vanilla ES modules, Node for JS logic tests.

## Global Constraints

- TDD: write the failing test, run it, watch it fail for the right reason, then implement. Commit after each task.
- Owner scoping on every route: filter by `owner == user` only when `get_current_user(request)` is not `None` (single-user mode sees everything). Foreign rows return 404.
- Graceful degradation: no RAG → routes that need it return 503 with a clear message; never crash startup.
- No inline event handlers in HTML (CSP). One delegated `click` listener per panel.
- Windows-native: paths via `os.path`, text files written with `encoding="utf-8"`.
- Tests that touch SQLAlchemy must use the `_import_real(...)` unstub helper (copied verbatim from `tests/test_council_personas.py:16-32`) because `tests/conftest.py` stubs `sqlalchemy` when it is missing.
- File size: keep each new module under ~200 lines; split when a file grows past that.

---

### Task 1: Career data models

**Files:**
- Modify: `core/database.py` (append after `class Report`, ~line 1422)
- Test: `tests/test_career_models.py`

**Interfaces:**
- Produces: `JobApplication`, `JobApplicationEvent`, `ApplicationReview`, `CareerPost` ORM classes with the columns below. Later plans import them from `core.database`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_models.py
"""Career hub tables (plan 00). Uses the unstub window from tests/test_council_personas.py."""
import importlib
import sys
import types
from datetime import datetime

import pytest


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
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    force=("core.database",),
)
Base = _coredb.Base
JobApplication = _coredb.JobApplication
JobApplicationEvent = _coredb.JobApplicationEvent
ApplicationReview = _coredb.ApplicationReview
CareerPost = _coredb.CareerPost


@pytest.fixture()
def db_factory():
    engine = _sa.create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=_sa_pool.StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return _sa_orm.sessionmaker(bind=engine)


def test_job_application_defaults_and_roundtrip(db_factory):
    db = db_factory()
    db.add(JobApplication(id="a1", owner="vanders", company="Acme", role="Backend Engineer"))
    db.commit()
    row = db.get(JobApplication, "a1")
    assert row.status == "drafting"
    assert row.source == "manual"
    assert row.archived is False
    assert row.cover_letter_doc_id is None
    assert row.created_at is not None
    db.close()


def test_event_can_exist_without_application_and_cascades_when_linked(db_factory):
    db = db_factory()
    db.add(JobApplication(id="a1", owner="vanders", company="Acme", role="Dev"))
    db.add(JobApplicationEvent(id="e1", owner="vanders", application_id="a1",
                               kind="confirmation", sentiment="neutral",
                               message_id="<m1@acme>", subject="Thanks for applying"))
    db.add(JobApplicationEvent(id="e2", owner="vanders", application_id=None,
                               kind="other", message_id="<m2@x>"))
    db.commit()
    assert db.get(JobApplicationEvent, "e1").can_reply is False
    assert db.get(JobApplicationEvent, "e1").confidence == 0
    assert db.get(JobApplicationEvent, "e2").application_id is None
    db.close()


def test_review_and_post_defaults(db_factory):
    db = db_factory()
    db.add(JobApplication(id="a1", owner="vanders", company="Acme", role="Dev"))
    db.add(ApplicationReview(id="r1", owner="vanders", application_id="a1"))
    db.add(CareerPost(id="p1", owner="vanders", post_type="progress", body="Shipped X"))
    db.commit()
    assert db.get(ApplicationReview, "r1").status == "running"
    post = db.get(CareerPost, "p1")
    assert post.status == "draft"
    assert post.window_days == 14
    db.close()
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_models.py -v`
Expected: FAIL with `AttributeError: module 'core.database' has no attribute 'JobApplication'`

- [x] **Step 3: Add the models**

Append to `core/database.py` directly after `class Report(...)` (before `class CalendarCal`):

```python
class JobApplication(TimestampMixin, Base):
    """A job application tracked by the Career hub (docs/plans/career-hub-design.md).

    One row is the spine for a cover letter document (plan 01), a reviewer
    panel verdict (plan 02) and the email events the tracker attaches (plan 04).
    """
    __tablename__ = "job_applications"

    id      = Column(String, primary_key=True, index=True)
    owner   = Column(String, nullable=True, index=True)
    company = Column(String, nullable=False)
    role    = Column(String, nullable=False)
    source  = Column(String, default="manual")     # manual | email | linkedin | board
    url     = Column(String, nullable=True)
    jd_text = Column(Text, nullable=True)

    applied_at    = Column(DateTime, nullable=True)
    status        = Column(String, default="drafting")
    last_event_at = Column(DateTime, nullable=True)
    notes         = Column(Text, nullable=True)

    cover_letter_doc_id = Column(String, ForeignKey("documents.id", ondelete="SET NULL"), nullable=True)
    latest_review_id    = Column(String, nullable=True)
    archived            = Column(Boolean, default=False)


class JobApplicationEvent(TimestampMixin, Base):
    """An email (or manual note) attached to an application by the tracker.

    ``application_id`` is nullable: low-confidence classifications land as
    "unsorted" events the user assigns or dismisses from the dashboard.
    """
    __tablename__ = "job_application_events"

    id             = Column(String, primary_key=True, index=True)
    owner          = Column(String, nullable=True, index=True)
    application_id = Column(String, ForeignKey("job_applications.id", ondelete="CASCADE"),
                            nullable=True, index=True)

    kind      = Column(String, default="other")     # confirmation | rejection | interview_invite | offer | info_request | recruiter_outreach | other
    sentiment = Column(String, default="neutral")   # positive | negative | neutral

    message_id   = Column(String, nullable=True, index=True)
    uid          = Column(String, nullable=True)
    folder       = Column(String, nullable=True)
    account_id   = Column(String, nullable=True)
    from_address = Column(String, nullable=True)
    reply_to     = Column(String, nullable=True)
    subject      = Column(String, nullable=True)
    received_at  = Column(DateTime, nullable=True)
    summary      = Column(Text, nullable=True)

    can_reply            = Column(Boolean, default=False)
    reply_blocked_reason = Column(String, nullable=True)
    suggested_reply      = Column(Text, nullable=True)
    confidence           = Column(Integer, default=0)   # 0-100


class ApplicationReview(TimestampMixin, Base):
    """One reviewer-panel run over an application's cover letter (plan 02)."""
    __tablename__ = "application_reviews"

    id             = Column(String, primary_key=True, index=True)
    owner          = Column(String, nullable=True, index=True)
    application_id = Column(String, ForeignKey("job_applications.id", ondelete="CASCADE"),
                            nullable=False, index=True)

    status        = Column(String, default="running")   # running | done | error
    verdicts      = Column(Text, nullable=True)         # JSON: {dept: {verdict, top_issues, line_edits, scores}}
    panel_summary = Column(Text, nullable=True)         # markdown synthesis
    scores        = Column(Text, nullable=True)         # JSON aggregate
    error         = Column(Text, nullable=True)
    started_at    = Column(DateTime, nullable=True)
    finished_at   = Column(DateTime, nullable=True)


class CareerPost(TimestampMixin, Base):
    """A LinkedIn post draft generated from project activity (plan 03)."""
    __tablename__ = "career_posts"

    id          = Column(String, primary_key=True, index=True)
    owner       = Column(String, nullable=True, index=True)
    post_type   = Column(String, default="progress")   # progress | technique | lesson | narrative
    body        = Column(Text, nullable=True)
    hooks       = Column(Text, nullable=True)          # JSON list of alternative opening lines
    sources     = Column(Text, nullable=True)          # JSON list of activity item refs
    window_days = Column(Integer, default=14)
    status      = Column(String, default="draft")      # draft | posted | discarded
    posted_at   = Column(DateTime, nullable=True)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_models.py -v`
Expected: 3 passed

- [x] **Step 5: Commit**

```bash
git add core/database.py tests/test_career_models.py
git commit -m "feat(career): add job application, event, review and post tables"
```

---

### Task 2: Per-owner career settings

**Files:**
- Create: `services/career/__init__.py` (empty)
- Create: `services/career/settings.py`
- Test: `tests/test_career_settings.py`

**Interfaces:**
- Produces: `DEFAULTS: dict`, `settings_path(owner) -> str`, `load_career_settings(owner) -> dict`, `save_career_settings(owner, updates) -> dict`. Every later plan reads settings through `load_career_settings(owner)`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_settings.py
"""Per-owner Career hub settings file (plan 00)."""
import json
import os

import pytest

from services.career import settings as cs


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path))
    return tmp_path


def test_defaults_when_no_file(data_dir):
    s = cs.load_career_settings("vanders")
    assert s["disclosure_default"] == "anonymous"
    assert s["nudge_after_days"] == 10
    assert s["ghosted_after_days"] == 21
    assert s["cv_filename"] == ""
    assert s["target_roles"] == []


def test_save_merges_known_keys_and_ignores_unknown(data_dir):
    out = cs.save_career_settings("vanders", {"voice_rules": "plain, first person", "bogus": 1})
    assert out["voice_rules"] == "plain, first person"
    assert "bogus" not in out
    on_disk = json.loads((data_dir / "career" / "vanders" / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["voice_rules"] == "plain, first person"
    assert cs.load_career_settings("vanders")["nudge_after_days"] == 10  # defaults still fill gaps


def test_settings_are_per_owner_and_anonymous_owner_maps_to_local(data_dir):
    cs.save_career_settings("vanders", {"cv_filename": "cv-luke.pdf"})
    assert cs.load_career_settings("other")["cv_filename"] == ""
    assert cs.settings_path(None).endswith(os.path.join("career", "local", "settings.json"))


def test_integer_fields_are_coerced_and_bounded(data_dir):
    out = cs.save_career_settings("vanders", {"nudge_after_days": "7", "ghosted_after_days": -3})
    assert out["nudge_after_days"] == 7
    assert out["ghosted_after_days"] == 1
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_settings.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career'`

- [x] **Step 3: Implement**

Create an empty `services/career/__init__.py`, then:

```python
# services/career/settings.py
"""Per-owner Career hub settings.

Stored as JSON at data/career/<owner>/settings.json (atomic writes). Unknown
keys are ignored on save; missing keys fall back to DEFAULTS on load.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Dict, Optional

from core.constants import DATA_DIR
from src.upload_handler import secure_filename

logger = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    "cv_filename": "",
    "voice_rules": "",
    "target_roles": [],
    "locations": [],
    "disclosure_default": "anonymous",      # anonymous | name_only | public
    "project_disclosure": {},               # project_id -> level
    "blocklist_terms": [],
    "scan_account_ids": [],
    "prefilter_terms": [],
    "nudge_after_days": 10,
    "ghosted_after_days": 21,
    "narrative_uses_tracker_stats": False,
}

_INT_FIELDS = ("nudge_after_days", "ghosted_after_days")
_LIST_FIELDS = ("target_roles", "locations", "blocklist_terms", "scan_account_ids", "prefilter_terms")


def settings_path(owner: Optional[str]) -> str:
    segment = secure_filename((owner or "local").strip())[:80] or "local"
    return os.path.join(DATA_DIR, "career", segment, "settings.json")


def load_career_settings(owner: Optional[str]) -> Dict[str, Any]:
    out = copy.deepcopy(DEFAULTS)
    path = settings_path(owner)
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if k in DEFAULTS})
    except Exception:
        logger.warning("career settings unreadable at %s; using defaults", path, exc_info=True)
    return out


def _coerce(key: str, value: Any) -> Any:
    if key in _INT_FIELDS:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return DEFAULTS[key]
    if key in _LIST_FIELDS:
        return [str(x) for x in value] if isinstance(value, list) else DEFAULTS[key]
    if key == "project_disclosure":
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    if key == "narrative_uses_tracker_stats":
        return bool(value)
    return value


def save_career_settings(owner: Optional[str], updates: Dict[str, Any]) -> Dict[str, Any]:
    current = load_career_settings(owner)
    for key, value in (updates or {}).items():
        if key in DEFAULTS:
            current[key] = _coerce(key, value)
    path = settings_path(owner)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from core.atomic_io import atomic_write_json
    atomic_write_json(path, current, indent=2)
    return current
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_settings.py -v`
Expected: 4 passed

- [x] **Step 5: Commit**

```bash
git add services/career/__init__.py services/career/settings.py tests/test_career_settings.py
git commit -m "feat(career): per-owner settings file with defaults"
```

---

### Task 2b: Shared LLM helper and template renderer

**Files:**
- Create: `services/career/llm.py`
- Test: `tests/test_career_llm.py`

**Interfaces:**
- Consumes: `src.endpoint_resolver.resolve_endpoint(prefix, owner=None) -> (url, model, headers)`, `resolve_utility_fallback_candidates(owner=None) -> list`, `src.llm_core.llm_call_async_with_fallback(candidates, messages, **kw) -> str` (same calls as `services/council/runner.py:125-136`).
- Produces: `async complete(messages, owner=None) -> str` (Task endpoint, then utility fallbacks; timeout 900, max_tokens 4000), `async complete_utility(messages, owner=None, max_tokens=800) -> str` (Utility endpoint first, then Task, then fallbacks; timeout 120 — for cheap classification), `render_template(template, mapping) -> str` (replaces only known `{key}` tokens). Plans 01–04 all import these; none defines its own.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_llm.py
"""Career LLM helper: endpoint resolution order, call kwargs, template rendering."""
import pytest

from services.career import llm as cl


@pytest.fixture()
def fake_resolver(monkeypatch):
    seen = {}

    def resolve_endpoint(prefix, owner=None, **kw):
        seen.setdefault("prefixes", []).append((prefix, owner))
        return {"task": ("http://task", "task-model", {"h": "1"}),
                "utility": ("http://util", "util-model", {})}.get(prefix, (None, None, {}))

    async def call(candidates, messages, **kw):
        seen["candidates"] = candidates
        seen["messages"] = messages
        seen["kwargs"] = kw
        return "ok"

    monkeypatch.setattr(cl, "resolve_endpoint", resolve_endpoint)
    monkeypatch.setattr(cl, "resolve_utility_fallback_candidates", lambda owner=None: [("http://fb", "fb-model", {})])
    monkeypatch.setattr(cl, "llm_call_async_with_fallback", call)
    return seen


async def test_complete_prefers_task_then_utility_fallbacks(fake_resolver):
    assert await cl.complete([{"role": "user", "content": "hi"}], owner="vanders") == "ok"
    assert [c[1] for c in fake_resolver["candidates"]] == ["task-model", "fb-model"]
    assert fake_resolver["kwargs"] == {"timeout": 900, "max_tokens": 4000}
    assert fake_resolver["prefixes"][0] == ("task", "vanders")


async def test_complete_skips_task_when_unconfigured(monkeypatch, fake_resolver):
    monkeypatch.setattr(cl, "resolve_endpoint", lambda prefix, owner=None, **kw: (None, None, None))
    await cl.complete([], owner=None)
    assert [c[1] for c in fake_resolver["candidates"]] == ["fb-model"]


async def test_complete_utility_prefers_utility_then_task(fake_resolver):
    assert await cl.complete_utility([{"role": "user", "content": "classify"}], owner="vanders") == "ok"
    assert [c[1] for c in fake_resolver["candidates"]] == ["util-model", "task-model", "fb-model"]
    assert fake_resolver["kwargs"] == {"timeout": 120, "max_tokens": 800}


def test_render_template_replaces_only_known_keys():
    out = cl.render_template("Hi {name}, {\"json\": 1} {unknown} {name}", {"name": "Luke"})
    assert out == "Hi Luke, {\"json\": 1} {unknown} Luke"
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_llm.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.llm'`

- [x] **Step 3: Implement**

```python
# services/career/llm.py
"""LLM entry points and the prompt renderer shared by every Career feature.

``complete`` mirrors services/council/runner._default_complete: the Task
model chain (cloud model when configured) falling back through the utility
chain, so Cookbook / served-model settings apply unchanged.
``complete_utility`` is for cheap classification: Utility first, then Task,
then the utility fallbacks. Services take a ``complete`` callable as a
parameter so tests inject fakes instead of patching this module.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.endpoint_resolver import resolve_endpoint, resolve_utility_fallback_candidates
from src.llm_core import llm_call_async_with_fallback


def _candidate(prefix: str, owner: Optional[str]):
    url, model, headers = resolve_endpoint(prefix, owner=owner)
    return (url, model, headers) if url and model else None


async def complete(messages: List[Dict[str, str]], owner: Optional[str] = None) -> str:
    candidates = [c for c in (_candidate("task", owner),) if c]
    candidates += resolve_utility_fallback_candidates(owner)
    return await llm_call_async_with_fallback(candidates, messages, timeout=900, max_tokens=4000)


async def complete_utility(messages: List[Dict[str, str]], owner: Optional[str] = None,
                           max_tokens: int = 800) -> str:
    candidates = [c for c in (_candidate("utility", owner), _candidate("task", owner)) if c]
    candidates += resolve_utility_fallback_candidates(owner)
    return await llm_call_async_with_fallback(candidates, messages, timeout=120, max_tokens=max_tokens)


def render_template(template: str, mapping: Dict[str, str]) -> str:
    """Replace only known ``{key}`` tokens — prompts, JDs and letters contain
    braces, so str.format would explode."""
    out = template
    for key, val in mapping.items():
        out = out.replace("{" + key + "}", val)
    return out
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_llm.py -v`
Expected: 4 passed

- [x] **Step 5: Commit**

```bash
git add services/career/llm.py tests/test_career_llm.py
git commit -m "feat(career): shared LLM completion helper and template renderer"
```

---

### Task 3: Office-document text extraction for personal uploads

**Files:**
- Modify: `src/personal_docs.py` (add `extract_text_for_upload` after `extract_office_text`, ~line 37)
- Modify: `routes/personal_routes.py:216-221`
- Test: `tests/test_personal_upload_text.py`

**Interfaces:**
- Produces: `extract_text_for_upload(file_path: str, raw: bytes) -> str` — PDF via pypdf, markitdown formats via `extract_office_text`, everything else UTF-8 decoded. Task 7's CV route reuses it.

- [x] **Step 1: Write the failing test**

```python
# tests/test_personal_upload_text.py
"""Personal uploads must extract text from office formats, not decode the bytes."""
from src import personal_docs


def test_docx_goes_through_markitdown(monkeypatch, tmp_path):
    f = tmp_path / "cv-luke.docx"
    f.write_bytes(b"PK\x03\x04 not really a docx")
    monkeypatch.setattr(personal_docs, "extract_office_text", lambda p: "Luke — Software Engineer")
    assert personal_docs.extract_text_for_upload(str(f), f.read_bytes()) == "Luke — Software Engineer"


def test_pdf_goes_through_pypdf(monkeypatch, tmp_path):
    f = tmp_path / "cv.pdf"
    f.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(personal_docs, "extract_pdf_text", lambda p: "pdf text")
    assert personal_docs.extract_text_for_upload(str(f), f.read_bytes()) == "pdf text"


def test_plain_text_is_decoded(tmp_path):
    f = tmp_path / "notes.md"
    f.write_bytes("# Hello\n".encode("utf-8"))
    assert personal_docs.extract_text_for_upload(str(f), f.read_bytes()) == "# Hello\n"


def test_upload_route_uses_shared_extractor():
    src = open("routes/personal_routes.py", encoding="utf-8").read()
    assert "extract_text_for_upload(" in src
    assert 'if ext == ".pdf":' not in src
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_personal_upload_text.py -v`
Expected: FAIL with `AttributeError: module 'src.personal_docs' has no attribute 'extract_text_for_upload'`

- [x] **Step 3: Implement**

In `src/personal_docs.py`, after `extract_office_text`:

```python
def extract_text_for_upload(file_path: str, raw: bytes) -> str:
    """Text for an uploaded personal file: PDF via pypdf, office/epub via
    markitdown, anything else decoded as UTF-8. Returns "" on failure so the
    caller can count it as a failed upload instead of indexing garbage."""
    from src.markitdown_runtime import is_markitdown_format
    ext = os.path.splitext(file_path)[1].lower()
    if ext == ".pdf":
        return extract_pdf_text(file_path)
    if is_markitdown_format(file_path):
        return extract_office_text(file_path)
    return raw.decode("utf-8", errors="replace")
```

In `routes/personal_routes.py`, replace lines 216-221:

```python
                ext = os.path.splitext(safe_name)[1].lower()
                if ext == ".pdf":
                    from src.personal_docs import extract_pdf_text
                    text = extract_pdf_text(file_path)
                else:
                    text = content_bytes.decode("utf-8", errors="replace")
```

with:

```python
                ext = os.path.splitext(safe_name)[1].lower()
                from src.personal_docs import extract_text_for_upload
                text = extract_text_for_upload(file_path, content_bytes)
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_personal_upload_text.py -v`
Expected: 4 passed

- [x] **Step 5: Commit**

```bash
git add src/personal_docs.py routes/personal_routes.py tests/test_personal_upload_text.py
git commit -m "fix(personal): extract text from docx/pptx/xlsx uploads via markitdown"
```

---

### Task 4: Owner-stamped personal file indexing helper

**Files:**
- Create: `services/career/personal_files.py`
- Test: `tests/test_career_personal_files.py`

**Interfaces:**
- Consumes: `routes.personal_routes._personal_upload_dir_for_owner(owner)`, `_unique_personal_upload_path(upload_dir, original_name)`; `src.personal_docs.extract_text_for_upload`.
- Produces: `store_personal_file(rag, owner, original_name, raw: bytes) -> dict` returning `{"filename", "stored_filename", "path", "chunks"}`; `list_personal_files(owner, prefix) -> list[dict]`; `delete_personal_file(rag, owner, filename) -> bool`. Task 7 (CV + examples) and plan 01 (evidence) use the `filename` prefix convention: `cv-*` and `cover-letter-example-*`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_personal_files.py
"""Career personal files: stored under the owner's upload dir and indexed with owner metadata."""
import pytest

from services.career import personal_files as pf


class FakeRag:
    def __init__(self):
        self.docs = []
        self.deleted = []

    def _split_into_chunks(self, text, chunk_size=500):
        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] or []

    def add_document(self, text, metadata):
        self.docs.append((text, metadata))
        return True

    def delete_by_source(self, source):
        self.deleted.append(source)
        return 1


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    import routes.personal_routes as pr
    monkeypatch.setattr(pr, "UPLOADS_DIR", str(tmp_path))
    return tmp_path


def test_store_indexes_every_chunk_with_owner(uploads):
    rag = FakeRag()
    out = pf.store_personal_file(rag, "vanders", "cv-luke.md", b"# Luke\n" + b"x" * 900)
    assert out["filename"] == "cv-luke.md"
    assert out["chunks"] == 2
    assert (uploads / "vanders" / out["stored_filename"]).exists()
    assert all(m["owner"] == "vanders" and m["filename"] == "cv-luke.md" for _, m in rag.docs)


def test_store_rejects_empty_text(uploads):
    with pytest.raises(ValueError):
        pf.store_personal_file(FakeRag(), "vanders", "empty.md", b"   ")


def test_list_filters_by_prefix_and_owner(uploads):
    rag = FakeRag()
    pf.store_personal_file(rag, "vanders", "cover-letter-example-acme.md", b"JD...\nLetter...")
    pf.store_personal_file(rag, "vanders", "cv-luke.md", b"cv")
    pf.store_personal_file(rag, "other", "cover-letter-example-theirs.md", b"x")
    names = [f["filename"] for f in pf.list_personal_files("vanders", "cover-letter-example-")]
    assert names == ["cover-letter-example-acme.md"]


def test_delete_removes_file_and_chunks(uploads):
    rag = FakeRag()
    out = pf.store_personal_file(rag, "vanders", "cv-luke.md", b"cv text")
    assert pf.delete_personal_file(rag, "vanders", "cv-luke.md") is True
    assert not (uploads / "vanders" / out["stored_filename"]).exists()
    assert rag.deleted == [out["path"]]
    assert pf.delete_personal_file(rag, "vanders", "missing.md") is False
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_personal_files.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.personal_files'`

- [x] **Step 3: Implement**

```python
# services/career/personal_files.py
"""Career-owned personal files (CV, example cover letters).

Files live in the same per-owner upload directory the Personal Docs upload
route uses, and every chunk is stamped with ``owner`` so owner-scoped RAG
search can see them (the generic upload route only stamps owner when the
request resolved a user). The display ``filename`` carries a prefix
convention that plan 01 filters on: ``cv-*`` and ``cover-letter-example-*``.

A sidecar ``<stored_filename>.meta.json`` records the display name so listing
does not depend on RAG being up.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from src.personal_docs import extract_text_for_upload

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500


def _upload_dir(owner: Optional[str]) -> str:
    from routes.personal_routes import _personal_upload_dir_for_owner
    return _personal_upload_dir_for_owner(owner)


def _meta_path(file_path: str) -> str:
    return file_path + ".meta.json"


def store_personal_file(rag, owner: Optional[str], original_name: str, raw: bytes) -> Dict[str, Any]:
    """Write the file, extract text, index every chunk with owner metadata."""
    from routes.personal_routes import _unique_personal_upload_path
    upload_dir = _upload_dir(owner)
    file_path, stored_name, safe_name = _unique_personal_upload_path(upload_dir, original_name)
    with open(file_path, "wb") as f:
        f.write(raw)
    text = extract_text_for_upload(file_path, raw)
    if not text or not text.strip():
        os.remove(file_path)
        raise ValueError(f"No text could be extracted from {safe_name}")

    chunks = rag._split_into_chunks(text, chunk_size=CHUNK_SIZE) if rag else []
    indexed = 0
    for i, chunk in enumerate(chunks):
        metadata = {
            "source": file_path,
            "filename": safe_name,
            "stored_filename": stored_name,
            "directory": upload_dir,
            "type": os.path.splitext(safe_name)[1].lower(),
            "chunk_id": i,
            "owner": owner or "",
        }
        if rag.add_document(chunk, metadata):
            indexed += 1
    with open(_meta_path(file_path), "w", encoding="utf-8") as f:
        json.dump({"filename": safe_name, "stored_filename": stored_name, "chunks": indexed}, f)
    return {"filename": safe_name, "stored_filename": stored_name, "path": file_path, "chunks": indexed}


def list_personal_files(owner: Optional[str], prefix: str = "") -> List[Dict[str, Any]]:
    upload_dir = _upload_dir(owner)
    out = []
    for name in sorted(os.listdir(upload_dir)):
        if not name.endswith(".meta.json"):
            continue
        try:
            with open(os.path.join(upload_dir, name), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        if meta.get("filename", "").startswith(prefix):
            meta["path"] = os.path.join(upload_dir, meta["stored_filename"])
            out.append(meta)
    return out


def delete_personal_file(rag, owner: Optional[str], filename: str) -> bool:
    for meta in list_personal_files(owner):
        if meta["filename"] != filename:
            continue
        path = meta["path"]
        if rag and hasattr(rag, "delete_by_source"):
            try:
                rag.delete_by_source(path)
            except Exception:
                logger.warning("DEGRADED: could not remove RAG chunks for %s", path, exc_info=True)
        for p in (path, _meta_path(path)):
            if os.path.exists(p):
                os.remove(p)
        return True
    return False
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_personal_files.py -v`
Expected: 4 passed

- [x] **Step 5: Commit**

```bash
git add services/career/personal_files.py tests/test_career_personal_files.py
git commit -m "feat(career): owner-stamped personal file storage and indexing"
```

---

### Task 5: Reusable library-document creator

**Files:**
- Modify: `routes/document_helpers.py` (append `create_library_document`)
- Modify: `routes/document_routes.py:69-111`
- Test: `tests/test_document_library_create.py`

**Interfaces:**
- Produces: `create_library_document(db, *, owner: str | None, title: str, content: str, language: str | None = None, session_id: str | None = None, source: str = "user") -> Document` (adds + commits, fires `document_created`). Plan 01 uses it for cover letters.

- [x] **Step 1: Write the failing test**

```python
# tests/test_document_library_create.py
"""create_library_document: session-less doc + first version, owner-stamped."""
import importlib
import sys
import types

import pytest


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


_sa, _sa_orm, _sa_pool, _coredb, dh = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database", "routes.document_helpers",
    force=("core.database", "routes.document_helpers"),
)


@pytest.fixture()
def db():
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    session = _sa_orm.sessionmaker(bind=engine)()
    yield session
    session.close()


def test_creates_doc_and_initial_version(db):
    doc = dh.create_library_document(db, owner="vanders", title="Cover letter — Acme",
                                     content="Dear team,", language="markdown")
    assert doc.owner == "vanders"
    assert doc.session_id is None
    assert doc.language == "markdown"
    assert doc.version_count == 1
    versions = db.query(_coredb.DocumentVersion).filter_by(document_id=doc.id).all()
    assert len(versions) == 1 and versions[0].content == "Dear team," and versions[0].source == "user"


def test_language_is_sniffed_when_missing(db):
    doc = dh.create_library_document(db, owner=None, title="Note", content="# Heading\n\nprose")
    assert doc.language == "markdown"


def test_post_document_route_uses_helper():
    src = open("routes/document_routes.py", encoding="utf-8").read()
    assert "create_library_document(" in src
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_document_library_create.py -v`
Expected: FAIL with `AttributeError: module 'routes.document_helpers' has no attribute 'create_library_document'`

- [x] **Step 3: Implement**

Append to `routes/document_helpers.py`:

```python
def create_library_document(db, *, owner, title: str, content: str,
                            language: Optional[str] = None, session_id: Optional[str] = None,
                            source: str = "user") -> Document:
    """Create a Document plus its first DocumentVersion and commit.

    Shared by POST /api/document and server-side features (Career cover
    letters) that need a session-less "library" document. Language is sniffed
    from content when not supplied; email-shaped content is always "email".
    """
    import uuid as _uuid
    from src.tool_implementations import _looks_like_email_document, _sniff_doc_language

    lang = language or _sniff_doc_language(content)
    if _looks_like_email_document(content, title):
        lang = "email"
    doc = Document(
        id=str(_uuid.uuid4()), session_id=session_id, title=title, language=lang,
        current_content=content, version_count=1, is_active=True, owner=owner,
    )
    ver = DocumentVersion(
        id=str(_uuid.uuid4()), document_id=doc.id, version_number=1,
        content=content, summary="Initial version", source=source,
    )
    db.add(doc)
    db.add(ver)
    db.commit()
    db.refresh(doc)
    try:
        from src.event_bus import fire_event
        fire_event("document_created", doc.owner)
    except Exception:
        logger.debug("document_created event dispatch failed", exc_info=True)
    return doc
```

Check the top of `routes/document_helpers.py` imports `Document`, `DocumentVersion`, `Optional` and has a module `logger`; add any that are missing:

```python
import logging
from typing import Optional
from core.database import Document, DocumentVersion
logger = logging.getLogger(__name__)
```

In `routes/document_routes.py`, replace lines 69-117 (from `doc_id = str(uuid.uuid4())` through `return _doc_to_dict(doc)`) with:

```python
            _assert_pdf_marker_upload_owned(request, req.content, user, upload_handler)
            from routes.document_helpers import create_library_document
            doc = create_library_document(
                db, owner=user or (session.owner if session else None),
                title=req.title, content=req.content, language=req.language,
                session_id=req.session_id,
            )
            return _doc_to_dict(doc)
```

- [x] **Step 4: Run tests to verify they pass and nothing regressed**

Run: `python -m pytest tests/test_document_library_create.py tests/test_document_tool_owner_scope.py tests/test_document_deeplink.py -v`
Expected: all passed

- [x] **Step 5: Commit**

```bash
git add routes/document_helpers.py routes/document_routes.py tests/test_document_library_create.py
git commit -m "refactor(documents): factor create_library_document out of POST /api/document"
```

---

### Task 6: GitHub and Trello integration presets

**Files:**
- Modify: `src/integrations.py:22-139` (add two presets to `INTEGRATION_PRESETS`)
- Test: `tests/test_integration_presets_career.py`

**Interfaces:**
- Produces: presets `github` (bearer) and `trello` (query `token` + `default_params.key`). Plan 03 calls `execute_api_call("github", ...)` / `execute_api_call("trello", ...)` once the user registers them in Settings → Integrations.

- [x] **Step 1: Write the failing test**

```python
# tests/test_integration_presets_career.py
from src.integrations import INTEGRATION_PRESETS


def test_github_preset_is_bearer_with_api_base():
    p = INTEGRATION_PRESETS["github"]
    assert p["auth_type"] == "bearer"
    assert p["base_url"] == "https://api.github.com"
    assert "/repos/{owner}/{repo}/commits" in p["description"]


def test_trello_preset_uses_query_token_and_default_key_param():
    p = INTEGRATION_PRESETS["trello"]
    assert p["auth_type"] == "query"
    assert p["auth_param"] == "token"
    assert p["base_url"] == "https://api.trello.com"
    assert "default_params" in p["description"] and "key" in p["description"]
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_integration_presets_career.py -v`
Expected: FAIL with `KeyError: 'github'`

- [x] **Step 3: Implement**

Add before the closing `}` of `INTEGRATION_PRESETS` in `src/integrations.py`:

```python
    "github": {
        "name": "GitHub",
        "auth_type": "bearer",
        "base_url": "https://api.github.com",
        "description": (
            "GitHub REST API (v3). api_key is a fine-grained personal access token with repo read access. Key endpoints:\n"
            "  GET /user/repos?sort=pushed&per_page=50 — your repositories\n"
            "  GET /repos/{owner}/{repo}/commits?since=ISO8601 — commits since a date\n"
            "  GET /repos/{owner}/{repo}/pulls?state=all&sort=updated&direction=desc — pull requests\n"
            "  GET /repos/{owner}/{repo}/commits/{sha} — one commit with files"
        ),
    },
    "trello": {
        "name": "Trello",
        "auth_type": "query",
        "auth_param": "token",
        "base_url": "https://api.trello.com",
        "description": (
            "Trello REST API. api_key is your Trello *token*; put your API key in default_params as {\"key\": \"...\"} "
            "so every call carries both. Key endpoints:\n"
            "  GET /1/members/me/boards?fields=name,url — your boards\n"
            "  GET /1/boards/{id}/lists — lists on a board\n"
            "  GET /1/boards/{id}/actions?since=ISO8601&filter=createCard,updateCard:idList,commentCard&limit=200 — recent card activity\n"
            "  GET /1/cards/{id} — one card"
        ),
    },
```

Check whether the presets dict already carries `base_url` on other entries; if the Settings UI reads `base_url` from a preset to pre-fill the form, nothing else is needed. If it does not, the user types the base URL when registering — the description tells them which.

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_integration_presets_career.py -v`
Expected: 2 passed

- [x] **Step 5: Commit**

```bash
git add src/integrations.py tests/test_integration_presets_career.py
git commit -m "feat(integrations): GitHub and Trello presets for career activity sources"
```

---

### Task 7: Reviewer personas, seeding, and council exclusion

**Files:**
- Create: `services/career/reviewers/recruiter.md`, `hiring_manager.md`, `engineer.md`, `hr.md`
- Create: `services/career/seeds.py`
- Modify: `services/council/personas.py:75`
- Modify: `routes/council_routes.py:229`, `:260`, `:273`
- Test: `tests/test_career_seeds.py`, `tests/test_council_excludes_career_reviewers.py`

**Interfaces:**
- Produces: `REVIEWER_DEPARTMENTS = ("career_recruiter", "career_hiring_manager", "career_engineer", "career_hr")`, `REVIEWER_NAMES: dict`, `load_persona(department) -> str`, `seed_reviewers(owner, session_factory=None) -> int`. Plan 02 reads reviewers as `CrewMember` rows whose `department` is in `REVIEWER_DEPARTMENTS`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_career_seeds.py
"""Reviewer persona seeding (plan 00). Reviewers are CrewMember rows with a career_* department."""
import importlib
import sys
import types

import pytest


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


_sa, _sa_orm, _sa_pool, _coredb, seeds, personas = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "services.career.seeds", "services.council.personas",
    force=("core.database", "services.career.seeds", "services.council.personas"),
)
CrewMember = _coredb.CrewMember


@pytest.fixture()
def db_factory():
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    return _sa_orm.sessionmaker(bind=engine)


def test_seed_creates_four_reviewers_with_personas(db_factory):
    assert seeds.seed_reviewers("vanders", session_factory=db_factory) == 4
    db = db_factory()
    rows = db.query(CrewMember).filter(CrewMember.owner == "vanders").all()
    assert sorted(r.department for r in rows) == sorted(seeds.REVIEWER_DEPARTMENTS)
    assert all(len(r.personality or "") > 80 for r in rows)
    db.close()


def test_seed_is_idempotent_and_keeps_edits(db_factory):
    seeds.seed_reviewers("vanders", session_factory=db_factory)
    db = db_factory()
    hr = db.query(CrewMember).filter(CrewMember.department == "career_hr").first()
    hr.personality = "Edited."
    db.commit()
    db.close()
    assert seeds.seed_reviewers("vanders", session_factory=db_factory) == 0
    db = db_factory()
    assert db.query(CrewMember).filter(CrewMember.department == "career_hr").first().personality == "Edited."
    db.close()


def test_council_seed_ignores_career_reviewers(db_factory):
    seeds.seed_reviewers("vanders", session_factory=db_factory)
    assert personas.seed_council_members("vanders", session_factory=db_factory) == 5
    db = db_factory()
    assert db.query(CrewMember).count() == 9
    db.close()


def test_persona_files_exist_for_every_department():
    for dept in seeds.REVIEWER_DEPARTMENTS:
        assert "reviewer" in seeds.load_persona(dept).lower() or "you are" in seeds.load_persona(dept).lower()
```

```python
# tests/test_council_excludes_career_reviewers.py
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
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_career_seeds.py tests/test_council_excludes_career_reviewers.py -v`
Expected: `test_career_seeds.py` fails with `ModuleNotFoundError: No module named 'services.career.seeds'`; the council exclusion tests fail with `assert 6 == 5` and `assert 200 == 404`.

- [x] **Step 3: Write the persona files**

`services/career/reviewers/recruiter.md`:

```markdown
You are a technical recruiter doing a 30-second first screen of a cover letter and CV for a software engineering role. You skim the way an ATS and a busy recruiter actually do: you look for the role title echoed back, the two or three hard requirements from the job description named explicitly, years and stack matching, location and work-authorisation signals, and a reason this person applied to *this* company. You are polite but you do not grade on effort. You reject anything generic, anything that could have been sent to ten companies, and anything that buries the match in paragraph three. You write short, concrete feedback with the exact sentence you would change.
```

`services/career/reviewers/hiring_manager.md`:

```markdown
You are the engineering manager who owns this open role and will interview the candidate. You read the cover letter for evidence of impact, not duties: what the person built, what changed because of it, and whether they understand the problems your team has. You are sceptical of claims without a number, a scale, or a concrete artefact behind them. You value curiosity about the company's actual product and a sentence that shows they read the job description. You say plainly whether you would spend an hour interviewing this person and what single addition would move you from maybe to yes.
```

`services/career/reviewers/engineer.md`:

```markdown
You are a blunt senior software engineer on the hiring panel. You ignore tone and read for technical credibility: are the technologies named used correctly, do the described systems make sense, is there any buzzword padding, and is anything claimed that a five-minute technical conversation would expose as thin? You prefer one specific, well-explained piece of work over a list of twelve technologies. You call out vague verbs ("worked on", "helped with", "exposure to") and suggest the precise, honest replacement. You are direct, not cruel, and every criticism comes with the fix.
```

`services/career/reviewers/hr.md`:

```markdown
You are the HR / People partner supporting this hire. You check the cover letter for consistency with the CV (dates, titles, employers, education), professionalism of tone, anything that would be flagged in an HR screen (over-sharing, negativity about past employers, unsupported salary or title demands, discriminatory or risky statements), and logistics the hiring team needs stated or omitted deliberately: location, right to work, notice period, availability. You also judge whether the letter reads as written by the candidate rather than generated. You are measured and specific, and you distinguish "must fix" from "nice to tidy".
```

- [x] **Step 4: Implement the seeder**

```python
# services/career/seeds.py
"""Idempotent seeding for the Career hub: reviewer personas (CrewMember rows
with a ``career_*`` department) and packaged skills (plans 01/03 add the
templates under services/career/skills/).

Mirrors services/council/personas.seed_council_members: never modifies a row
that already exists, so user edits survive every bootstrap.
"""
from __future__ import annotations

import logging
import os
import shutil
import uuid
from pathlib import Path
from typing import Callable, Dict, List, Optional

from core.database import CrewMember, SessionLocal

logger = logging.getLogger(__name__)

REVIEWER_DEPARTMENTS = ("career_recruiter", "career_hiring_manager", "career_engineer", "career_hr")

REVIEWER_NAMES: Dict[str, str] = {
    "career_recruiter": "Recruiter screener",
    "career_hiring_manager": "Hiring manager",
    "career_engineer": "Senior engineer",
    "career_hr": "HR / People partner",
}

_PERSONA_FILES = {
    "career_recruiter": "recruiter.md",
    "career_hiring_manager": "hiring_manager.md",
    "career_engineer": "engineer.md",
    "career_hr": "hr.md",
}

_PACKAGED_REVIEWERS = Path(__file__).parent / "reviewers"
_PACKAGED_SKILLS = Path(__file__).parent / "skills"
SKILL_CATEGORY = "career"
RETIRED_SKILL_DIRS = (("general", "linkedin-post-writer", "1.0.0"),)   # (category, name, version to retire)


def _persona_dirs():
    from core.constants import DATA_DIR
    return (Path(DATA_DIR) / "career" / "reviewers", _PACKAGED_REVIEWERS)


def load_persona(department: str) -> str:
    """User override in data/career/reviewers/<file> wins over the packaged text."""
    fname = _PERSONA_FILES[department]
    for d in _persona_dirs():
        f = d / fname
        if f.is_file():
            return f.read_text(encoding="utf-8").strip()
    raise FileNotFoundError(f"No persona file for {department}")


def seed_reviewers(owner: Optional[str], session_factory: Callable = None) -> int:
    factory = session_factory or SessionLocal
    db = factory()
    try:
        q = db.query(CrewMember).filter(CrewMember.department.in_(REVIEWER_DEPARTMENTS))
        if owner is not None:
            q = q.filter(CrewMember.owner == owner)
        covered = {m.department for m in q.all()}
        created = 0
        for dept in REVIEWER_DEPARTMENTS:
            if dept in covered:
                continue
            db.add(CrewMember(
                id=uuid.uuid4().hex[:12], owner=owner, name=REVIEWER_NAMES[dept],
                personality=load_persona(dept), department=dept,
            ))
            created += 1
        if created:
            db.commit()
        return created
    finally:
        db.close()


def seed_skills(owner: Optional[str], skills_manager, templates_dir: Optional[str] = None) -> List[str]:
    """Copy each packaged skill folder into data/skills/career/<name>/ unless a
    skill with that name already exists anywhere. Retires the listed legacy
    skill folders when their frontmatter version matches. Returns names seeded."""
    src_root = Path(templates_dir) if templates_dir else _PACKAGED_SKILLS
    if not src_root.is_dir():
        return []
    _retire_legacy_skills(skills_manager)
    existing = {s.get("name") for s in skills_manager.load_all()}
    seeded: List[str] = []
    for skill_dir in sorted(p for p in src_root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file()):
        name = skill_dir.name
        if name in existing:
            continue
        dest = Path(skills_manager._skill_dir(SKILL_CATEGORY, name))
        shutil.copytree(skill_dir, dest, dirs_exist_ok=True)
        text = (dest / "SKILL.md").read_text(encoding="utf-8")
        if owner and "\nowner:" not in text:
            text = text.replace("\n---\n", f"\nowner: {owner}\n---\n", 1)
        (dest / "SKILL.md").write_text(text, encoding="utf-8")
        seeded.append(name)
    return seeded


def _retire_legacy_skills(skills_manager) -> None:
    for category, name, version in RETIRED_SKILL_DIRS:
        path = skills_manager._skill_file(category, name)
        if not os.path.isfile(path):
            continue
        text = open(path, encoding="utf-8").read()
        if f"version: {version}" not in text:
            logger.info("Keeping %s/%s (version differs from %s)", category, name, version)
            continue
        shutil.rmtree(os.path.dirname(path), ignore_errors=True)
        logger.info("Retired legacy skill %s/%s v%s in favour of the career seed", category, name, version)
```

- [x] **Step 5: Exclude career reviewers from the council**

`services/council/personas.py` line 75 — change

```python
        q = db.query(CrewMember).filter(CrewMember.department.isnot(None))
```
to
```python
        q = db.query(CrewMember).filter(CrewMember.department.in_(DEPARTMENTS))
```

`routes/council_routes.py` — import `DEPARTMENTS`:

```python
from services.council.personas import DEPARTMENTS, seed_council_members
```

line 229 (`/status`) and line 260 (`/members`) — change `CrewMember.department.isnot(None)` to `CrewMember.department.in_(DEPARTMENTS)`.

line 273 (`update_member`) — change

```python
            q = db.query(CrewMember).filter(CrewMember.id == mid)
```
to
```python
            q = db.query(CrewMember).filter(CrewMember.id == mid,
                                            CrewMember.department.in_(DEPARTMENTS))
```

- [x] **Step 6: Run tests to verify they pass, including the existing council suites**

Run: `python -m pytest tests/test_career_seeds.py tests/test_council_excludes_career_reviewers.py tests/test_council_personas.py tests/test_council_routes_owner_scope.py -v`
Expected: all passed

- [x] **Step 7: Commit**

```bash
git add services/career/reviewers services/career/seeds.py services/council/personas.py routes/council_routes.py tests/test_career_seeds.py tests/test_council_excludes_career_reviewers.py
git commit -m "feat(career): seed reviewer personas; council ignores career_* departments"
```

---

### Task 8: Skill seeding test

**Files:**
- Test: `tests/test_career_skill_seeding.py`

**Interfaces:**
- Consumes: `seed_skills(owner, skills_manager, templates_dir=None)` from Task 7.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_skill_seeding.py
"""Packaged career skills are copied into data/skills/career/ once; the legacy
general/linkedin-post-writer v1.0.0 is retired."""
import os
from pathlib import Path

from services.career import seeds
from services.memory.skills import SkillsManager

_V1 = """---
name: linkedin-post-writer
description: old
version: 1.0.0
category: general
status: published
source: user
---

## Procedure

1. old
"""

_TEMPLATE = """---
name: {name}
description: {name} seeded
version: 2.0.0
category: career
status: published
source: user
---

## When to Use

Testing.

## Procedure

1. Call the hub.
"""


def _templates(tmp_path):
    root = tmp_path / "templates"
    for name in ("cover-letter-writer", "linkedin-post-writer"):
        d = root / name
        d.mkdir(parents=True)
        (d / "SKILL.md").write_text(_TEMPLATE.format(name=name), encoding="utf-8")
    (root / "linkedin-post-writer" / "examples").mkdir()
    (root / "linkedin-post-writer" / "examples" / "one.md").write_text("example", encoding="utf-8")
    return str(root)


def test_seeds_each_template_once_with_owner(tmp_path):
    sm = SkillsManager(str(tmp_path / "data"))
    seeded = seeds.seed_skills("vanders", sm, templates_dir=_templates(tmp_path))
    assert seeded == ["cover-letter-writer", "linkedin-post-writer"]
    text = Path(sm._skill_file("career", "linkedin-post-writer")).read_text(encoding="utf-8")
    assert "owner: vanders" in text
    assert os.path.isfile(os.path.join(sm._skill_dir("career", "linkedin-post-writer"), "examples", "one.md"))
    assert seeds.seed_skills("vanders", sm, templates_dir=_templates(tmp_path / "again")) == []


def test_retires_legacy_v1_but_keeps_other_versions(tmp_path):
    sm = SkillsManager(str(tmp_path / "data"))
    legacy = Path(sm._skill_file("general", "linkedin-post-writer"))
    legacy.parent.mkdir(parents=True)
    legacy.write_text(_V1, encoding="utf-8")
    seeded = seeds.seed_skills("vanders", sm, templates_dir=_templates(tmp_path))
    assert "linkedin-post-writer" in seeded
    assert not legacy.exists()

    sm2 = SkillsManager(str(tmp_path / "data2"))
    legacy2 = Path(sm2._skill_file("general", "linkedin-post-writer"))
    legacy2.parent.mkdir(parents=True)
    legacy2.write_text(_V1.replace("1.0.0", "1.5.0"), encoding="utf-8")
    seeded2 = seeds.seed_skills("vanders", sm2, templates_dir=_templates(tmp_path / "t2"))
    assert legacy2.exists()
    assert "linkedin-post-writer" not in seeded2   # name taken by the user's own edited skill
```

- [x] **Step 2: Run test to verify it passes (the seeder already exists from Task 7)**

Run: `python -m pytest tests/test_career_skill_seeding.py -v`
Expected: 2 passed. If `SkillsManager.load_all()` returns dicts without `name`, read `services/memory/skills.py:load_all` and adapt the `existing` set in `seed_skills` to the key it uses.

- [x] **Step 3: Commit**

```bash
git add tests/test_career_skill_seeding.py services/career/seeds.py
git commit -m "test(career): skill seeding copies templates once and retires v1 linkedin skill"
```

---

### Task 9: Career routes — bootstrap, settings, applications

**Files:**
- Create: `routes/career_routes.py`
- Create: `routes/career_helpers.py`
- Modify: `app.py` (mount after the council router, ~line 693)
- Test: `tests/test_career_routes_owner_scope.py`

**Interfaces:**
- Consumes: Task 1 models, Task 2 settings, Task 7 seeds, `src.search.content.fetch_webpage_content`.
- Produces: `setup_career_routes(skills_manager, rag_manager) -> APIRouter` (prefix `/api/career`); `routes/career_helpers.py` with `application_to_dict(app, settings, now=None) -> dict`, `get_owned_application(db, aid, user) -> JobApplication` (404 if missing), `APPLICATION_STATUSES`, `days_since_event(app, now=None) -> int | None`, `computed_status(app, settings, now=None) -> str`. Plans 01/02/04 import these helpers.

Routes:

| Method | Path | Body / query | Returns |
|---|---|---|---|
| GET | `/bootstrap` | — | `{settings, counts: {applications}, has_cv, reviewers: [{id, department, name}], seeded_skills: [...]}`; seeds reviewers + skills |
| GET | `/settings` | — | settings dict |
| PUT | `/settings` | partial settings | settings dict |
| GET | `/applications` | `include_archived=0` | `{applications: [dict]}` newest first |
| POST | `/applications` | `{company, role, url?, jd_text?, jd_url?, applied_at?, status?, notes?}` | dict (201) |
| GET | `/applications/{id}` | — | dict |
| PUT | `/applications/{id}` | any of company, role, url, jd_text, applied_at, status, notes, archived | dict |
| DELETE | `/applications/{id}` | — | `{id, archived: true}` |

`application_to_dict` adds `computed_status` = `"ghosted"` when status is in `("applied","acknowledged","screening")` and `last_event_at or applied_at or created_at` is older than `settings["ghosted_after_days"]`, else the stored status; and `days_since_event`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_routes_owner_scope.py
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
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_routes_owner_scope.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'routes.career_routes'`

- [x] **Step 3: Implement the helpers**

```python
# routes/career_helpers.py
"""Shared serialisers and lookups for the /api/career routers."""
from __future__ import annotations

from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import HTTPException

from core.database import JobApplication

APPLICATION_STATUSES = (
    "drafting", "applied", "acknowledged", "screening", "interview",
    "offer", "rejected", "withdrawn", "ghosted",
)
_WAITING_STATUSES = ("applied", "acknowledged", "screening")


def _iso(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None


def days_since_event(app: JobApplication, now: Optional[datetime] = None) -> Optional[int]:
    anchor = app.last_event_at or app.applied_at or app.created_at
    if not anchor:
        return None
    return max(0, ((now or datetime.utcnow()) - anchor).days)


def computed_status(app: JobApplication, settings: Dict[str, Any], now: Optional[datetime] = None) -> str:
    if app.status in _WAITING_STATUSES:
        days = days_since_event(app, now)
        if days is not None and days >= int(settings.get("ghosted_after_days", 21)):
            return "ghosted"
    return app.status or "drafting"


def application_to_dict(app: JobApplication, settings: Dict[str, Any],
                        now: Optional[datetime] = None) -> Dict[str, Any]:
    return {
        "id": app.id,
        "company": app.company,
        "role": app.role,
        "source": app.source,
        "url": app.url,
        "jd_text": app.jd_text,
        "applied_at": _iso(app.applied_at),
        "status": app.status,
        "computed_status": computed_status(app, settings, now),
        "days_since_event": days_since_event(app, now),
        "last_event_at": _iso(app.last_event_at),
        "notes": app.notes,
        "cover_letter_doc_id": app.cover_letter_doc_id,
        "latest_review_id": app.latest_review_id,
        "archived": bool(app.archived),
        "created_at": _iso(app.created_at),
        "updated_at": _iso(app.updated_at),
    }


def get_owned_application(db, aid: str, user: Optional[str]) -> JobApplication:
    q = db.query(JobApplication).filter(JobApplication.id == aid)
    if user is not None:
        q = q.filter(JobApplication.owner == user)
    app = q.first()
    if not app:
        raise HTTPException(status_code=404, detail="Application not found")
    return app
```

- [x] **Step 4: Implement the router**

```python
# routes/career_routes.py
"""Career hub core API — bootstrap, settings, applications.

docs/plans/career-hub-design.md. Owner-scoped like council/projects: filter by
owner only when a user is resolved; foreign rows are 404.
"""
from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Query, Request
from pydantic import BaseModel, field_validator

from core.database import CrewMember, JobApplication, SessionLocal
from routes.career_helpers import APPLICATION_STATUSES, application_to_dict, get_owned_application
from services.career.seeds import REVIEWER_DEPARTMENTS, seed_reviewers, seed_skills
from services.career.settings import load_career_settings, save_career_settings
from src.auth_helpers import get_current_user
from src.search.content import fetch_webpage_content

logger = logging.getLogger(__name__)


class ApplicationCreate(BaseModel):
    company: str
    role: str
    url: Optional[str] = None
    jd_text: Optional[str] = None
    jd_url: Optional[str] = None
    applied_at: Optional[datetime] = None
    status: str = "drafting"
    notes: Optional[str] = None

    @field_validator("company", "role")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: str) -> str:
        if v not in APPLICATION_STATUSES:
            raise ValueError(f"status must be one of {APPLICATION_STATUSES}")
        return v


class ApplicationUpdate(BaseModel):
    company: Optional[str] = None
    role: Optional[str] = None
    url: Optional[str] = None
    jd_text: Optional[str] = None
    applied_at: Optional[datetime] = None
    status: Optional[str] = None
    notes: Optional[str] = None
    archived: Optional[bool] = None

    @field_validator("status")
    @classmethod
    def _known_status(cls, v: Optional[str]) -> Optional[str]:
        if v is not None and v not in APPLICATION_STATUSES:
            raise ValueError(f"status must be one of {APPLICATION_STATUSES}")
        return v


async def _jd_from_url(url: str) -> str:
    try:
        data = await asyncio.to_thread(fetch_webpage_content, url, 8)
    except Exception:
        logger.warning("JD fetch failed for %s", url, exc_info=True)
        return ""
    return (data or {}).get("content") or ""


def setup_career_routes(skills_manager, rag_manager) -> APIRouter:
    router = APIRouter(prefix="/api/career", tags=["career"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _has_cv(user: Optional[str], settings: Dict[str, Any]) -> bool:
        return bool(settings.get("cv_filename"))

    @router.get("/bootstrap")
    def bootstrap(request: Request):
        user = _owner(request)
        seed_reviewers(user, session_factory=SessionLocal)
        try:
            seeded = seed_skills(user, skills_manager) if skills_manager else []
        except Exception:
            logger.warning("DEGRADED: career skill seeding failed", exc_info=True)
            seeded = []
        settings = load_career_settings(user)
        db = SessionLocal()
        try:
            aq = db.query(JobApplication).filter(JobApplication.archived == False)  # noqa: E712
            rq = db.query(CrewMember).filter(CrewMember.department.in_(REVIEWER_DEPARTMENTS))
            if user is not None:
                aq = aq.filter(JobApplication.owner == user)
                rq = rq.filter(CrewMember.owner == user)
            reviewers = [{"id": m.id, "department": m.department, "name": m.name}
                         for m in rq.order_by(CrewMember.name).all()]
            return {
                "settings": settings,
                "counts": {"applications": aq.count()},
                "has_cv": _has_cv(user, settings),
                "reviewers": reviewers,
                "seeded_skills": seeded,
            }
        finally:
            db.close()

    @router.get("/settings")
    def get_settings(request: Request):
        return load_career_settings(_owner(request))

    @router.put("/settings")
    def put_settings(body: Dict[str, Any], request: Request):
        return save_career_settings(_owner(request), body or {})

    @router.get("/applications")
    def list_applications(request: Request, include_archived: int = Query(0)):
        user = _owner(request)
        settings = load_career_settings(user)
        db = SessionLocal()
        try:
            q = db.query(JobApplication)
            if user is not None:
                q = q.filter(JobApplication.owner == user)
            if not include_archived:
                q = q.filter(JobApplication.archived == False)  # noqa: E712
            rows = q.order_by(JobApplication.created_at.desc()).all()
            return {"applications": [application_to_dict(a, settings) for a in rows]}
        finally:
            db.close()

    @router.post("/applications", status_code=201)
    async def create_application(body: ApplicationCreate, request: Request):
        user = _owner(request)
        jd_text = (body.jd_text or "").strip()
        if not jd_text and body.jd_url:
            jd_text = await _jd_from_url(body.jd_url)
        db = SessionLocal()
        try:
            app = JobApplication(
                id=uuid.uuid4().hex[:12], owner=user, company=body.company, role=body.role,
                url=body.url or body.jd_url, jd_text=jd_text or None, applied_at=body.applied_at,
                status=body.status, notes=body.notes, source="manual",
            )
            db.add(app)
            db.commit()
            db.refresh(app)
            return application_to_dict(app, load_career_settings(user))
        finally:
            db.close()

    @router.get("/applications/{aid}")
    def get_application(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            return application_to_dict(get_owned_application(db, aid, user), load_career_settings(user))
        finally:
            db.close()

    @router.put("/applications/{aid}")
    def update_application(aid: str, body: ApplicationUpdate, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            for field in ("company", "role", "url", "jd_text", "applied_at", "status", "notes", "archived"):
                val = getattr(body, field)
                if val is not None:
                    setattr(app, field, val.strip() if isinstance(val, str) and field in ("company", "role") else val)
            db.commit()
            db.refresh(app)
            return application_to_dict(app, load_career_settings(user))
        finally:
            db.close()

    @router.delete("/applications/{aid}")
    def archive_application(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            app.archived = True
            db.commit()
            return {"id": aid, "archived": True}
        finally:
            db.close()

    return router
```

Mount in `app.py` after the council router:

```python
# Career hub (cover letters, reviewer panel, posts, job tracker)
from routes.career_routes import setup_career_routes
app.include_router(setup_career_routes(skills_manager, rag_manager))
```

- [x] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_career_routes_owner_scope.py -v`
Expected: 7 passed. If pydantic in this repo is v1 (`field_validator` import fails), use `@validator("company", "role")` from `pydantic` with the same bodies.

- [x] **Step 6: Commit**

```bash
git add routes/career_routes.py routes/career_helpers.py app.py tests/test_career_routes_owner_scope.py
git commit -m "feat(career): bootstrap, settings and applications API"
```

---

### Task 10: CV and example-pair routes

**Files:**
- Modify: `routes/career_routes.py` (add three endpoints + one upload endpoint)
- Test: `tests/test_career_files_routes.py`

**Interfaces:**
- Consumes: Task 4 `store_personal_file`, `list_personal_files`, `delete_personal_file`.
- Produces routes:

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/cv` | multipart `file` | `{filename, chunks}`; saves `cv_filename` in settings; 503 if no RAG |
| GET | `/examples` | — | `{examples: [{filename, chunks}]}` |
| POST | `/examples` | `{title, jd_text, letter_text}` | `{filename, chunks}` (201) |
| DELETE | `/examples/{filename}` | — | `{deleted: true}` or 404 |

The example file is written as markdown: `# <title>\n\n## Job description\n\n<jd>\n\n## Cover letter\n\n<letter>\n` with filename `cover-letter-example-<slug>.md`; the CV is stored as `cv-<original name>`.

- [x] **Step 1: Write the failing test**

```python
# tests/test_career_files_routes.py
"""CV upload and example cover-letter pairs through /api/career."""
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


_sa, _sa_orm, _sa_pool, _coredb, crr, cs, pr = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "routes.career_routes", "services.career.settings", "routes.personal_routes",
    force=("core.database", "routes.career_routes"),
)


class FakeRag:
    def __init__(self):
        self.docs = []

    def _split_into_chunks(self, text, chunk_size=500):
        return [text]

    def add_document(self, text, metadata):
        self.docs.append(metadata)
        return True

    def delete_by_source(self, source):
        return 1


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(crr, "SessionLocal", _sa_orm.sessionmaker(bind=engine))
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(pr, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(crr, "get_current_user", lambda request: "vanders")
    rag = FakeRag()
    app = FastAPI()
    app.include_router(crr.setup_career_routes(None, rag))
    return TestClient(app), rag


def test_cv_upload_sets_setting_and_prefixes_filename(env):
    client, rag = env
    res = client.post("/api/career/cv", files={"file": ("Luke CV.md", b"# Luke\nEngineer", "text/markdown")})
    assert res.status_code == 200
    assert res.json()["filename"].startswith("cv-")
    assert cs.load_career_settings("vanders")["cv_filename"] == res.json()["filename"]
    assert rag.docs[0]["owner"] == "vanders"
    assert client.get("/api/career/bootstrap").json()["has_cv"] is True


def test_cv_upload_without_rag_is_503(monkeypatch, tmp_path):
    monkeypatch.setattr(crr, "get_current_user", lambda request: "vanders")
    app = FastAPI()
    app.include_router(crr.setup_career_routes(None, None))
    res = TestClient(app).post("/api/career/cv", files={"file": ("cv.md", b"x", "text/markdown")})
    assert res.status_code == 503


def test_example_pair_lifecycle(env):
    client, _ = env
    res = client.post("/api/career/examples",
                      json={"title": "Acme backend", "jd_text": "Build APIs", "letter_text": "Dear Acme"})
    assert res.status_code == 201
    fname = res.json()["filename"]
    assert fname == "cover-letter-example-acme-backend.md"
    listed = client.get("/api/career/examples").json()["examples"]
    assert [e["filename"] for e in listed] == [fname]
    assert client.delete(f"/api/career/examples/{fname}").json() == {"deleted": True}
    assert client.get("/api/career/examples").json()["examples"] == []
    assert client.delete(f"/api/career/examples/{fname}").status_code == 404


def test_example_pair_requires_both_texts(env):
    client, _ = env
    assert client.post("/api/career/examples", json={"title": "x", "jd_text": "", "letter_text": "y"}).status_code == 422
```

- [x] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_files_routes.py -v`
Expected: FAIL with 404s (routes do not exist yet)

- [x] **Step 3: Implement**

Add to `routes/career_routes.py` imports:

```python
from fastapi import File, UploadFile
from services.career.personal_files import delete_personal_file, list_personal_files, store_personal_file
from services.memory.skill_format import slugify
```

Add a request model:

```python
class ExamplePair(BaseModel):
    title: str
    jd_text: str
    letter_text: str

    @field_validator("title", "jd_text", "letter_text")
    @classmethod
    def _not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("must not be blank")
        return v.strip()
```

Add these endpoints inside `setup_career_routes` before `return router`:

```python
    def _require_rag():
        if rag_manager is None:
            raise HTTPException(503, "RAG is not available — the embedding service is required to index files")
        return rag_manager

    @router.post("/cv")
    async def upload_cv(request: Request, file: UploadFile = File(...)):
        user = _owner(request)
        rag = _require_rag()
        raw = await file.read()
        name = file.filename or "cv.txt"
        if not name.startswith("cv-"):
            name = f"cv-{name}"
        try:
            out = store_personal_file(rag, user, name, raw)
        except ValueError as e:
            raise HTTPException(422, str(e))
        save_career_settings(user, {"cv_filename": out["filename"]})
        return {"filename": out["filename"], "chunks": out["chunks"]}

    @router.get("/examples")
    def list_examples(request: Request):
        files = list_personal_files(_owner(request), "cover-letter-example-")
        return {"examples": [{"filename": f["filename"], "chunks": f.get("chunks", 0)} for f in files]}

    @router.post("/examples", status_code=201)
    def add_example(body: ExamplePair, request: Request):
        user = _owner(request)
        rag = _require_rag()
        text = (f"# {body.title}\n\n## Job description\n\n{body.jd_text}\n\n"
                f"## Cover letter\n\n{body.letter_text}\n")
        name = f"cover-letter-example-{slugify(body.title)}.md"
        out = store_personal_file(rag, user, name, text.encode("utf-8"))
        return {"filename": out["filename"], "chunks": out["chunks"]}

    @router.delete("/examples/{filename}")
    def delete_example(filename: str, request: Request):
        if not filename.startswith("cover-letter-example-"):
            raise HTTPException(404, "Example not found")
        if not delete_personal_file(rag_manager, _owner(request), filename):
            raise HTTPException(404, "Example not found")
        return {"deleted": True}
```

- [x] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_files_routes.py tests/test_career_routes_owner_scope.py -v`
Expected: all passed. `python-multipart` is already a dependency (email attachments use `UploadFile`); if the upload test errors with "Form data requires python-multipart", add it to `requirements.txt`.

- [x] **Step 5: Commit**

```bash
git add routes/career_routes.py tests/test_career_files_routes.py
git commit -m "feat(career): CV upload and cover-letter example pairs"
```

---

### Task 11: UI shell — deep link, modal, sidebar, module wiring

**Files:**
- Modify: `app.py` (after `serve_council`, ~line 755)
- Modify: `static/index.html` (favicon SHAPES ~line 143, titles ~line 170, modal after `#council-modal` ~line 295, sidebar after `#tool-council-btn` ~line 931)
- Modify: `static/app.js` (import ~line 40, click handler after council ~line 831, `_routeOpen` ~line 1031)
- Create: `static/js/careerLogic.js`, `static/js/career.js`, `static/js/careerApplications.js`, `static/js/careerSetup.js`
- Modify: `static/style.css` (append a `/* ── Career hub ── */` section)
- Test: `tests/test_career_js.py`, `tests/test_career_ui_wiring.py`

**Interfaces:**
- Produces: `careerLogic.js` exports `TABS`, `STATUS_LABELS`, `statusChip(status)`, `daysLabel(days)`, `applicationBuckets(apps)`, `placeholderHtml(tab)`, `REVIEWER_COLORS`. `career.js` exports `open`, `close`, `isOpen`, `refresh`, `render`, `getState`, a registry `registerTab(name, renderFn)` later plans use to plug in Tracker/Reviewers/Posts renderers (each renderer is `(state) => html` where `state = {tab, apps, settings, bootstrap, examples, view, openId}`), and `onPanelClick(handler)` where `handler(ev, state)` returns `true` when it handled the click. The default export is a `careerApi` object with all of those. **Plugin contract for plans 01–04:** each feature module exports `install(api)` and never imports `career.js`; `career.js` gets two lines per plugin at the marked plugin block (`import { install as installX } from './careerX.js';` and `installX(careerApi);`). Detail-view sections are added by pushing `(app, state) => html` onto `detailSections` exported from `careerApplications.js`.

- [x] **Step 1: Write the failing tests**

```python
# tests/test_career_ui_wiring.py
"""Career hub shell is wired like the council: deep link, modal, sidebar, route opener."""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_INDEX = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_APP_JS = (_REPO / "static" / "app.js").read_text(encoding="utf-8")
_APP_PY = (_REPO / "app.py").read_text(encoding="utf-8")


def test_career_deep_link_route_exists():
    assert '@app.get("/career")' in _APP_PY


def test_modal_and_sidebar_markup():
    assert 'id="career-modal"' in _INDEX
    assert 'role="dialog" aria-label="Career"' in _INDEX
    assert 'id="close-career-modal"' in _INDEX and 'aria-label="Close career modal"' in _INDEX
    for tab in ("tracker", "applications", "reviewers", "posts", "setup"):
        assert f'data-career-tab="{tab}"' in _INDEX
    assert 'id="career-panel"' in _INDEX
    assert 'id="tool-career-btn"' in _INDEX
    assert "'/career':" in _INDEX and "'Career — Odysseus'" in _INDEX


def test_app_js_imports_and_routes_career():
    assert "import careerModule from './js/career.js';" in _APP_JS
    assert "Modals.toggle('career-modal')" in _APP_JS
    assert "'/career': () => document.getElementById('tool-career-btn')?.click()" in _APP_JS
```

```python
# tests/test_career_js.py
"""careerLogic.js pure helpers, run under Node (pattern: tests/test_council_js.py)."""
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
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_status_chip_covers_every_status(node_available):
    out = _run_node(textwrap.dedent("""
        const { statusChip, STATUS_LABELS } = await import('./static/js/careerLogic.js');
        console.log(JSON.stringify({
          keys: Object.keys(STATUS_LABELS),
          offer: statusChip('offer'), rejected: statusChip('rejected'),
          ghosted: statusChip('ghosted'), unknown: statusChip('???'),
        }));
    """))
    assert sorted(out["keys"]) == sorted(["drafting", "applied", "acknowledged", "screening", "interview",
                                          "offer", "rejected", "withdrawn", "ghosted"])
    assert out["offer"]["cls"] == "career-chip-positive"
    assert out["rejected"]["cls"] == "career-chip-negative"
    assert out["ghosted"]["cls"] == "career-chip-muted"
    assert out["unknown"]["label"] == "Drafting"


def test_application_buckets(node_available):
    out = _run_node(textwrap.dedent("""
        const { applicationBuckets } = await import('./static/js/careerLogic.js');
        const apps = [
          { id: 'd', computed_status: 'drafting' },
          { id: 'w', computed_status: 'applied' },
          { id: 'i', computed_status: 'interview' },
          { id: 'g', computed_status: 'ghosted' },
          { id: 'r', computed_status: 'rejected' },
          { id: 'o', computed_status: 'offer' },
        ];
        const b = applicationBuckets(apps);
        console.log(JSON.stringify({ drafting: b.drafting.map(a => a.id), active: b.active.map(a => a.id),
          closed: b.closed.map(a => a.id) }));
    """))
    assert out == {"drafting": ["d"], "active": ["w", "i", "o"], "closed": ["g", "r"]}


def test_days_label_and_placeholder(node_available):
    out = _run_node(textwrap.dedent("""
        const { daysLabel, placeholderHtml } = await import('./static/js/careerLogic.js');
        console.log(JSON.stringify({ zero: daysLabel(0), one: daysLabel(1), many: daysLabel(12),
          none: daysLabel(null), ph: placeholderHtml('tracker') }));
    """))
    assert out["zero"] == "today" and out["one"] == "1 day ago" and out["many"] == "12 days ago"
    assert out["none"] == ""
    assert "career-placeholder" in out["ph"] and "tracker" in out["ph"].lower()


def test_career_module_sources_parse(node_available):
    for f in ("career.js", "careerLogic.js", "careerApplications.js", "careerSetup.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
```

- [x] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_career_ui_wiring.py tests/test_career_js.py -v`
Expected: wiring tests fail on the first assertion; JS tests fail with `Cannot find module`.

- [x] **Step 3: Server deep link (`app.py`)**

After `serve_council`:

```python
@app.get("/career")
async def serve_career(request: Request):
    return await serve_index(request)
```

- [x] **Step 4: `static/index.html`**

In the inline favicon script, add to `SHAPES` after the `'/council'` entry:

```javascript
        '/career':
          "<rect x='5' y='10' width='22' height='15' rx='2' fill='none' stroke='" + ac + "' stroke-width='2.5'/>" +
          "<path d='M12 10 V7 a2 2 0 0 1 2 -2 h4 a2 2 0 0 1 2 2 v3' fill='none' stroke='" + ac + "' stroke-width='2.5'/>" +
          "<path d='M5 16 h22' fill='none' stroke='" + ac + "' stroke-width='2.5'/>",
```

Add to `titles`: `'/career': 'Career — Odysseus',`

After the `#council-modal` block (before `<!-- Project Command Centre Modal -->`):

```html
  <!-- Career Modal -->
  <div id="career-modal" class="modal hidden">
    <div class="modal-content projects-modal-content" role="dialog" aria-label="Career" style="background:var(--bg)">
      <div class="modal-header">
        <h4><svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round" style="vertical-align:-2px;margin-right:6px"><rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M3 12h18"/></svg>Career</h4>
        <button class="close-btn" id="close-career-modal" aria-label="Close career modal">✖</button>
      </div>
      <div class="modal-body projects-modal-body">
        <div class="admin-card" style="display:flex;flex-direction:column;overflow:hidden;flex:1;min-height:0;">
          <div id="career-tabs" class="council-tabs">
            <button class="memory-toolbar-btn" data-career-tab="tracker">Tracker</button>
            <button class="memory-toolbar-btn active" data-career-tab="applications">Applications</button>
            <button class="memory-toolbar-btn" data-career-tab="reviewers">Reviewers</button>
            <button class="memory-toolbar-btn" data-career-tab="posts">Posts</button>
            <button class="memory-toolbar-btn" data-career-tab="setup">Setup</button>
          </div>
          <p class="memory-desc doclib-desc" style="margin-top:6px;">Cover letters, a reviewer panel, LinkedIn drafts and a live application tracker — nothing is sent or published without you.</p>
          <div class="projects-scroll"><div id="career-panel"></div></div>
        </div>
      </div>
    </div>
  </div>
```

After the `#tool-council-btn` list item:

```html
        <div class="list-item" id="tool-career-btn">
          <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor"
            stroke-width="2" stroke-linecap="round" stroke-linejoin="round"
            style="flex-shrink:0;opacity:0.5;">
            <rect x="3" y="7" width="18" height="13" rx="2"/><path d="M8 7V5a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2"/><path d="M3 12h18"/>
          </svg>
          <span class="grow">Career</span>
        </div>
```

- [x] **Step 5: `static/app.js`**

Import after the council import:

```javascript
import careerModule from './js/career.js';
```

Click handler after the council block:

```javascript
  // Career modal toggle
  const toolCareerBtn = el('tool-career-btn');
  if (toolCareerBtn) {
    toolCareerBtn.addEventListener('click', async () => {
      if (!careerModule) return;
      const Modals = await import('./js/modalManager.js');
      if (!Modals.toggle('career-modal')) {
        careerModule.isOpen() ? careerModule.close() : careerModule.open();
      }
    });
  }
```

`_routeOpen` entry after `/council`:

```javascript
    '/career': () => document.getElementById('tool-career-btn')?.click(),
```

- [x] **Step 6: `static/js/careerLogic.js`**

```javascript
// ============================================
// Career hub — pure logic (no DOM, Node-importable for tests)
// ============================================

export const TABS = ['tracker', 'applications', 'reviewers', 'posts', 'setup'];

export const STATUS_LABELS = {
  drafting: 'Drafting',
  applied: 'Applied',
  acknowledged: 'Acknowledged',
  screening: 'Screening',
  interview: 'Interview',
  offer: 'Offer',
  rejected: 'Rejected',
  withdrawn: 'Withdrawn',
  ghosted: 'Ghosted',
};

const POSITIVE = new Set(['interview', 'offer']);
const NEGATIVE = new Set(['rejected']);
const MUTED = new Set(['ghosted', 'withdrawn', 'drafting']);

export const statusChip = (status) => {
  const key = STATUS_LABELS[status] ? status : 'drafting';
  const cls = POSITIVE.has(key) ? 'career-chip-positive'
    : NEGATIVE.has(key) ? 'career-chip-negative'
      : MUTED.has(key) ? 'career-chip-muted'
        : 'career-chip-waiting';
  return { label: STATUS_LABELS[key], cls };
};

export const REVIEWER_COLORS = {
  career_recruiter: '#d9a13b',
  career_hiring_manager: '#5b8abf',
  career_engineer: '#5dbf6e',
  career_hr: '#d873a8',
};

export const daysLabel = (days) => {
  if (days === null || days === undefined) return '';
  if (days === 0) return 'today';
  return `${days} day${days === 1 ? '' : 's'} ago`;
};

// drafting = not yet sent; active = in flight; closed = settled (ghosted counts as closed).
export const applicationBuckets = (apps) => {
  const out = { drafting: [], active: [], closed: [] };
  for (const a of apps || []) {
    const s = a.computed_status || a.status || 'drafting';
    if (s === 'drafting') out.drafting.push(a);
    else if (s === 'rejected' || s === 'withdrawn' || s === 'ghosted') out.closed.push(a);
    else out.active.push(a);
  }
  return out;
};

const PLACEHOLDER_COPY = {
  tracker: 'The live job tracker (plan 04) lands here: responses from your inbox, who is waiting, what to nudge.',
  reviewers: 'The reviewer panel (plan 02) lands here: edit the four reviewer personas and run them on a cover letter.',
  posts: 'LinkedIn post drafting (plan 03) lands here: pick sources, a window, and a post type.',
};

export const placeholderHtml = (tab) =>
  `<div class="career-placeholder memory-desc">${PLACEHOLDER_COPY[tab] || `${tab} is not built yet.`}</div>`;
```

- [x] **Step 7: `static/js/careerApplications.js`**

```javascript
// ============================================
// Career hub — Applications tab (list, add form, detail)
// Later plans extend the detail view: cover letter (01), review panel (02),
// email events (04) through the `detailSections` registry below.
// ============================================
import uiModule from './ui.js';
import { applicationBuckets, daysLabel, statusChip, STATUS_LABELS } from './careerLogic.js';

const esc = uiModule.esc;

// Later plans push `(app, state) => html` here; sections render in push order.
export const detailSections = [];

const _card = (a) => {
  const chip = statusChip(a.computed_status);
  const when = a.days_since_event === null || a.days_since_event === undefined
    ? '' : ` · ${daysLabel(a.days_since_event)}`;
  return `
    <div class="proj-card admin-card career-app" data-application-id="${esc(a.id)}">
      <div class="proj-card-head">
        <span class="proj-name">${esc(a.company)} — ${esc(a.role)}</span>
        <span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>
      </div>
      <div class="memory-desc report-scores">${esc(a.source)}${when}</div>
      <div class="proj-actions">
        <button class="memory-toolbar-btn" data-action="open-application">Open</button>
        <button class="memory-toolbar-btn" data-action="archive-application">Archive</button>
      </div>
    </div>`;
};

const _group = (title, apps) => apps.length
  ? `<h3 class="cc-section">${esc(title)} (${apps.length})</h3><div class="projects-grid">${apps.map(_card).join('')}</div>`
  : '';

export const addFormHtml = () => `
  <div class="council-edit-form career-add-form">
    <h2 class="council-reports-title">New application</h2>
    <div class="assistant-field-row">
      <label class="assistant-field" style="flex:1;"><span>Company</span><input id="career-add-company" class="settings-input" type="text"></label>
      <label class="assistant-field" style="flex:1;"><span>Role</span><input id="career-add-role" class="settings-input" type="text"></label>
    </div>
    <label class="assistant-field"><span>Posting URL (fetched for the job description if you leave the text empty)</span>
      <input id="career-add-url" class="settings-input" type="url"></label>
    <label class="assistant-field"><span>Job description (paste)</span>
      <textarea id="career-add-jd" class="settings-input" rows="8"></textarea></label>
    <div class="council-run-form">
      <button id="career-add-save" class="memory-toolbar-btn">Save application</button>
      <button id="career-add-cancel" class="memory-toolbar-btn">Cancel</button>
    </div>
  </div>`;

export const listHtml = (state) => {
  const b = applicationBuckets(state.apps);
  const body = state.apps.length
    ? _group('Drafting', b.drafting) + _group('Active', b.active) + _group('Closed', b.closed)
    : '<p class="memory-desc">No applications yet. Add one, or let the tracker create them from confirmation emails.</p>';
  return `
    <div class="council-run-form">
      <button id="career-add-btn" class="memory-toolbar-btn">Add application</button>
    </div>${body}`;
};

const _statusOptions = (current) => Object.entries(STATUS_LABELS)
  .filter(([v]) => v !== 'ghosted')
  .map(([v, label]) => `<option value="${esc(v)}"${v === current ? ' selected' : ''}>${esc(label)}</option>`).join('');

export const detailHtml = (app, state) => `
  <div class="career-detail" data-application-id="${esc(app.id)}">
    <div class="council-run-form">
      <button id="career-back" class="memory-toolbar-btn">← All applications</button>
      <select id="career-detail-status" class="settings-input">${_statusOptions(app.status)}</select>
    </div>
    <h2 class="cc-title">${esc(app.company)} — ${esc(app.role)}</h2>
    <div class="memory-desc report-scores">${app.url ? `<a href="${esc(app.url)}" target="_blank" rel="noopener">${esc(app.url)}</a>` : 'No posting URL'}</div>
    ${detailSections.map((fn) => fn(app, state)).join('')}
    <details class="proj-summary"><summary>Job description</summary>
      <div class="proj-summary-body">${esc(app.jd_text || '(none)')}</div></details>
    <label class="assistant-field"><span>Notes</span>
      <textarea id="career-detail-notes" class="settings-input" rows="4">${esc(app.notes || '')}</textarea></label>
    <div class="council-run-form"><button id="career-detail-save-notes" class="memory-toolbar-btn">Save notes</button></div>
  </div>`;
```

- [x] **Step 8: `static/js/careerSetup.js`**

```javascript
// ============================================
// Career hub — Setup tab (CV, voice rules, targets, example pairs, timings)
// ============================================
import uiModule from './ui.js';

const esc = uiModule.esc;

export const setupHtml = (state) => {
  const s = state.settings || {};
  const examples = (state.examples || []).map((e) => `
    <li class="career-example-row" data-example="${esc(e.filename)}">
      <span>${esc(e.filename)}</span>
      <button class="memory-toolbar-btn" data-action="delete-example">Delete</button>
    </li>`).join('');
  return `
    <div class="career-setup">
      <h3 class="cc-section">CV</h3>
      <p class="memory-desc">${s.cv_filename ? `Current: <strong>${esc(s.cv_filename)}</strong>` : 'No CV yet — upload a PDF, DOCX or Markdown file.'}</p>
      <div class="council-run-form">
        <input id="career-cv-file" type="file" accept=".pdf,.docx,.md,.txt" class="settings-input">
        <button id="career-cv-upload" class="memory-toolbar-btn">Upload CV</button>
      </div>

      <h3 class="cc-section">Voice and targets</h3>
      <label class="assistant-field"><span>Voice rules (how you write: tone, phrases to avoid, length)</span>
        <textarea id="career-voice" class="settings-input" rows="4">${esc(s.voice_rules || '')}</textarea></label>
      <label class="assistant-field"><span>Target roles (comma separated)</span>
        <input id="career-roles" class="settings-input" type="text" value="${esc((s.target_roles || []).join(', '))}"></label>
      <label class="assistant-field"><span>Locations (comma separated)</span>
        <input id="career-locations" class="settings-input" type="text" value="${esc((s.locations || []).join(', '))}"></label>

      <h3 class="cc-section">Tracker timing</h3>
      <div class="assistant-field-row">
        <label class="assistant-field"><span>Suggest a nudge after (days)</span>
          <input id="career-nudge" class="settings-input" type="number" min="1" value="${esc(String(s.nudge_after_days ?? 10))}"></label>
        <label class="assistant-field"><span>Mark ghosted after (days)</span>
          <input id="career-ghosted" class="settings-input" type="number" min="1" value="${esc(String(s.ghosted_after_days ?? 21))}"></label>
      </div>
      <div class="council-run-form"><button id="career-settings-save" class="memory-toolbar-btn">Save settings</button></div>

      <h3 class="cc-section">Example cover letters (job description + the letter you sent)</h3>
      <ul class="career-example-list">${examples || '<li class="memory-desc">None yet. The closest examples are used as style references when drafting.</li>'}</ul>
      <div class="council-edit-form">
        <label class="assistant-field"><span>Title</span><input id="career-ex-title" class="settings-input" type="text"></label>
        <label class="assistant-field"><span>Job description</span><textarea id="career-ex-jd" class="settings-input" rows="5"></textarea></label>
        <label class="assistant-field"><span>Cover letter</span><textarea id="career-ex-letter" class="settings-input" rows="6"></textarea></label>
        <div class="council-run-form"><button id="career-ex-save" class="memory-toolbar-btn">Add example</button></div>
      </div>

      <h3 class="cc-section">Post sources</h3>
      <p class="memory-desc">Register <strong>GitHub</strong> and <strong>Trello</strong> under Settings → Integrations (presets are provided). Local repositories come from the Projects tool.</p>
    </div>`;
};

export const readSettingsForm = () => ({
  voice_rules: document.getElementById('career-voice')?.value || '',
  target_roles: (document.getElementById('career-roles')?.value || '').split(',').map((s) => s.trim()).filter(Boolean),
  locations: (document.getElementById('career-locations')?.value || '').split(',').map((s) => s.trim()).filter(Boolean),
  nudge_after_days: Number(document.getElementById('career-nudge')?.value || 10),
  ghosted_after_days: Number(document.getElementById('career-ghosted')?.value || 21),
});
```

- [x] **Step 9: `static/js/career.js`**

```javascript
// ============================================
// Career hub modal (docs/plans/career-hub-design.md)
// Tabs: Tracker (04), Applications (00/01/02), Reviewers (02), Posts (03), Setup (00).
// Later plans plug in with registerTab(name, renderFn) and onPanelClick(handler)
// instead of editing this file.
// ============================================
import uiModule from './ui.js';
import { placeholderHtml, TABS } from './careerLogic.js';
import { addFormHtml, detailHtml, listHtml } from './careerApplications.js';
import { readSettingsForm, setupHtml } from './careerSetup.js';

const _tabRenderers = {};
const _clickHandlers = [];

export const registerTab = (name, render) => { _tabRenderers[name] = render; };
export const onPanelClick = (handler) => { _clickHandlers.push(handler); };

const state = { tab: 'applications', apps: [], settings: {}, bootstrap: {}, examples: [], view: 'list', openId: null };
export const getState = () => state;

const _modal = () => document.getElementById('career-modal');
const _panel = () => document.getElementById('career-panel');

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail?.[0]?.msg || data.detail || `${url} failed (${res.status})`);
  return data;
};

const _fetchAll = async () => {
  const [boot, apps, examples] = await Promise.all([
    _json('/api/career/bootstrap'),
    _json('/api/career/applications'),
    _json('/api/career/examples').catch(() => ({ examples: [] })),
  ]);
  state.bootstrap = boot;
  state.settings = boot.settings || {};
  state.apps = apps.applications || [];
  state.examples = examples.examples || [];
};

const _renderApplications = () => {
  if (state.view === 'add') return addFormHtml();
  if (state.view === 'detail') {
    const app = state.apps.find((a) => a.id === state.openId);
    if (app) return detailHtml(app, state);
    state.view = 'list';
  }
  return listHtml(state);
};

export const render = () => {
  const panel = _panel();
  if (!panel) return;
  document.querySelectorAll('#career-tabs [data-career-tab]').forEach((b) => {
    b.classList.toggle('active', b.dataset.careerTab === state.tab);
  });
  const custom = _tabRenderers[state.tab];
  panel.innerHTML = state.tab === 'applications' ? _renderApplications()
    : state.tab === 'setup' ? setupHtml(state)
      : custom ? custom(state)
        : placeholderHtml(state.tab);
};

export const refresh = async () => {
  try {
    await _fetchAll();
    render();
  } catch (e) {
    uiModule.showError(`Career: ${e.message}`);
  }
};

// ── actions ──────────────────────────────────────────────────────────────────

const _saveApplication = async () => {
  const body = {
    company: document.getElementById('career-add-company')?.value || '',
    role: document.getElementById('career-add-role')?.value || '',
    jd_url: document.getElementById('career-add-url')?.value || null,
    jd_text: document.getElementById('career-add-jd')?.value || null,
  };
  try {
    const app = await _json('/api/career/applications', { method: 'POST', body: JSON.stringify(body) });
    state.view = 'detail';
    state.openId = app.id;
    await refresh();
    uiModule.showToast('Application saved');
  } catch (e) {
    uiModule.showError(`Save application: ${e.message}`);
  }
};

const _updateApplication = async (id, patch) => {
  try {
    await _json(`/api/career/applications/${id}`, { method: 'PUT', body: JSON.stringify(patch) });
    await refresh();
  } catch (e) {
    uiModule.showError(`Update application: ${e.message}`);
  }
};

const _archiveApplication = async (id) => {
  if (!(await uiModule.styledConfirm('Archive this application?'))) return;
  try {
    await _json(`/api/career/applications/${id}`, { method: 'DELETE' });
    if (state.openId === id) state.view = 'list';
    await refresh();
  } catch (e) {
    uiModule.showError(`Archive: ${e.message}`);
  }
};

const _saveSettings = async () => {
  try {
    await _json('/api/career/settings', { method: 'PUT', body: JSON.stringify(readSettingsForm()) });
    await refresh();
    uiModule.showToast('Career settings saved');
  } catch (e) {
    uiModule.showError(`Save settings: ${e.message}`);
  }
};

const _uploadCv = async () => {
  const input = document.getElementById('career-cv-file');
  const file = input?.files?.[0];
  if (!file) return uiModule.showError('Choose a CV file first');
  const form = new FormData();
  form.append('file', file);
  try {
    const res = await fetch('/api/career/cv', { method: 'POST', body: form });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `upload failed (${res.status})`);
    await refresh();
    uiModule.showToast(`CV indexed (${data.chunks} chunks)`);
  } catch (e) {
    uiModule.showError(`Upload CV: ${e.message}`);
  }
};

const _addExample = async () => {
  const body = {
    title: document.getElementById('career-ex-title')?.value || '',
    jd_text: document.getElementById('career-ex-jd')?.value || '',
    letter_text: document.getElementById('career-ex-letter')?.value || '',
  };
  try {
    await _json('/api/career/examples', { method: 'POST', body: JSON.stringify(body) });
    await refresh();
    uiModule.showToast('Example added');
  } catch (e) {
    uiModule.showError(`Add example: ${e.message}`);
  }
};

const _deleteExample = async (filename) => {
  try {
    await _json(`/api/career/examples/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    await refresh();
  } catch (e) {
    uiModule.showError(`Delete example: ${e.message}`);
  }
};

// ── wiring ───────────────────────────────────────────────────────────────────

let _wired = false;
const _wire = () => {
  if (_wired) return;
  _wired = true;
  document.getElementById('close-career-modal')?.addEventListener('click', close);
  document.getElementById('career-tabs')?.addEventListener('click', (ev) => {
    const btn = ev.target.closest('[data-career-tab]');
    if (!btn || !TABS.includes(btn.dataset.careerTab)) return;
    state.tab = btn.dataset.careerTab;
    render();
  });
  _panel()?.addEventListener('click', (ev) => {
    for (const handler of _clickHandlers) {
      if (handler(ev, state) === true) return;   // a plugin handled it
    }
    if (ev.target.closest('#career-add-btn')) { state.view = 'add'; return render(); }
    if (ev.target.closest('#career-add-cancel')) { state.view = 'list'; return render(); }
    if (ev.target.closest('#career-add-save')) return _saveApplication();
    if (ev.target.closest('#career-back')) { state.view = 'list'; return render(); }
    if (ev.target.closest('#career-settings-save')) return _saveSettings();
    if (ev.target.closest('#career-cv-upload')) return _uploadCv();
    if (ev.target.closest('#career-ex-save')) return _addExample();
    if (ev.target.closest('#career-detail-save-notes')) {
      return _updateApplication(state.openId, { notes: document.getElementById('career-detail-notes')?.value || '' });
    }
    const delEx = ev.target.closest('[data-action="delete-example"]');
    if (delEx) return _deleteExample(delEx.closest('[data-example]')?.dataset.example);
    const btn = ev.target.closest('button[data-action]');
    const id = btn?.closest('[data-application-id]')?.dataset.applicationId;
    if (!btn || !id) return;
    if (btn.dataset.action === 'open-application') { state.view = 'detail'; state.openId = id; return render(); }
    if (btn.dataset.action === 'archive-application') return _archiveApplication(id);
  });
  _panel()?.addEventListener('change', (ev) => {
    if (ev.target.id === 'career-detail-status' && state.openId) {
      _updateApplication(state.openId, { status: ev.target.value });
    }
  });
};

export const open = () => {
  const modal = _modal();
  if (!modal) return;
  _wire();
  modal.classList.remove('hidden');
  refresh();
};

export const close = () => { _modal()?.classList.add('hidden'); };
export const isOpen = () => !!_modal() && !_modal().classList.contains('hidden');

const careerApi = { open, close, isOpen, refresh, render, registerTab, onPanelClick, getState };

// ── plugins ──────────────────────────────────────────────────────────────────
// Later plans add exactly two lines here per feature module: an import of its
// `install` function and a call `installX(careerApi);`. Plugin modules never
// import career.js (that would be an ES-module cycle: this file's consts are
// in the temporal dead zone while a dependency evaluates). Each `install(api)`
// calls api.registerTab / api.onPanelClick and may import `detailSections`
// from careerApplications.js; it must not fetch or render at load.

export default careerApi;
```

Check `uiModule.styledConfirm` exists in `static/js/ui.js` (it is referenced by `tests/test_dialog_aria.py`); if its name differs, use the exported confirm helper from that file.

- [x] **Step 10: `static/style.css`**

Append at the end of the council section (after `.score-ai-tag`):

```css
/* ── Career hub (docs/plans/career-hub-design.md) ─────────────────────── */
.career-chip-positive { color: #5dbf6e; border-color: color-mix(in srgb, #5dbf6e 50%, transparent); }
.career-chip-negative { color: #c75450; border-color: color-mix(in srgb, #c75450 50%, transparent); }
.career-chip-waiting  { color: #d9a13b; border-color: color-mix(in srgb, #d9a13b 50%, transparent); }
.career-chip-muted    { color: color-mix(in srgb, var(--fg) 50%, transparent); }
.career-placeholder { padding: 16px 2px; }
.career-detail { display: flex; flex-direction: column; gap: 8px; padding: 8px 2px; }
.career-setup { display: flex; flex-direction: column; gap: 6px; padding: 8px 2px; }
.career-example-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.career-example-row { display: flex; align-items: center; justify-content: space-between; gap: 8px; font-size: 12px; }
/* Stat tiles — shared by Tracker (04) and later dashboards. */
.career-tiles { display: grid; grid-template-columns: repeat(auto-fill, minmax(140px, 1fr)); gap: 8px; padding: 8px 2px; }
.career-tile { padding: 10px 12px; display: flex; flex-direction: column; gap: 2px; }
.career-tile-num { font-size: 22px; font-weight: 700; font-variant-numeric: tabular-nums; }
.career-tile-label { font-size: 11px; opacity: 0.7; text-transform: uppercase; letter-spacing: 0.06em; }
```

- [x] **Step 11: Run the tests**

Run: `python -m pytest tests/test_career_ui_wiring.py tests/test_career_js.py tests/test_dialog_aria.py -v`
Expected: all passed

- [x] **Step 12: Commit**

```bash
git add app.py static/index.html static/app.js static/style.css static/js/career.js static/js/careerLogic.js static/js/careerApplications.js static/js/careerSetup.js tests/test_career_ui_wiring.py tests/test_career_js.py
git commit -m "feat(career): Career hub modal with Applications and Setup tabs"
```

---

### Task 12: Full verification and smoke test

- [x] **Step 1: Syntax and suites**

```bash
python -m py_compile app.py routes/*.py src/*.py services/career/*.py core/database.py
node --check static/js/career.js && node --check static/js/careerLogic.js && node --check static/js/careerApplications.js && node --check static/js/careerSetup.js
python -m pytest tests/test_career_*.py tests/test_council_*.py tests/test_document_*.py tests/test_personal_upload_text.py tests/test_integration_presets_career.py -v
python -m pytest
```
Expected: every suite green; the full run shows no new failures compared with `git stash && python -m pytest && git stash pop` on the pre-plan tree.

- [x] **Step 2: Manual smoke (server running)**

1. Open `http://127.0.0.1:7000/career` — the Career modal opens on the Applications tab with "No applications yet".
2. Setup tab → upload a `.docx` CV → toast reports chunk count; the tab shows the `cv-…` filename.
3. Setup tab → add an example pair → it appears in the list; delete it.
4. Applications → Add application with a posting URL and no JD text → detail view shows the fetched job description.
5. Change the status select to Interview → chip updates after refresh. Archive → it disappears from the list.
6. Open `/council` → Members shows five council members only; the four reviewers are absent.
7. `GET /api/career/bootstrap` (browser) → `reviewers` lists four `career_*` entries.

- [x] **Step 3: Final commit (if anything was touched during smoke)**

```bash
git add -A
git commit -m "chore(career): foundation smoke fixes"
```

---

## Done when

- [x] `tests/test_career_*.py`, council, document and personal-upload suites pass; full `python -m pytest` has no new failures.
- [x] `/career` deep link opens the hub; Applications and Setup tabs work end to end; Tracker/Reviewers/Posts show their placeholders.
- [x] Reviewers exist as `career_*` crew members and never appear in the council office.
- [x] A `.docx` CV uploads with real text; `GET /api/career/bootstrap` reports `has_cv: true`.
- [x] `create_library_document` is the only place a session-less document is created.
- [x] `services/career/llm.py` exposes `complete`, `complete_utility` and `render_template`; later plans import them rather than redefining.
- [x] GitHub and Trello presets appear in the Integrations preset list.
