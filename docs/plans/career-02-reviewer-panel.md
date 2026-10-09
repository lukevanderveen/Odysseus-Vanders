# Career Hub 02 — Reviewer Panel Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run a four-persona hiring panel (Recruiter screener, Hiring manager, Senior engineer, HR/People partner) over an application's cover letter and job description, persist each reviewer's verdict, line edits and scores plus a chaired panel synthesis, and show it all on the application detail view with an editable Reviewers tab.

**Architecture:** A sibling of the council runner under `services/career/`: `review_scoring.py` (pure parsing/aggregation), `templates.py` (packaged prompt templates with a user override dir), `review_runner.py` (one review at a time on its own `asyncio.Semaphore(1)`, four reviewer completions then a panel completion, persisted on `ApplicationReview`). Routes are added to the existing `routes/career_studio_routes.py`. The front-end plugs into the hub through `registerTab` / `onPanelClick` / `detailSections` from plan 00 and never edits `career.js` beyond one import and one install line per module. Design: `docs/plans/career-hub-design.md`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (SQLite), pytest (`asyncio_mode=auto`), vanilla ES modules, Node for JS logic tests.

## Global Constraints

- **Prerequisites:** `docs/plans/career-00-hub-foundation.md` (models, seeds, hub shell) **and** `docs/plans/career-01-cover-letter.md` (CV evidence, `career_studio_routes.py`, cover letter document on `JobApplication.cover_letter_doc_id`) are merged. The LLM wrapper `services/career/llm.py` comes from plan 00.
- TDD: failing test first, watch it fail for the right reason, minimal code, commit per task.
- Owner scoping on every route: filter by `owner == user` only when `get_current_user(request)` is not `None`; foreign rows are 404.
- Reviewers are `CrewMember` rows whose `department` is in `services.career.seeds.REVIEWER_DEPARTMENTS`; the council exclusion is already in place from plan 00. Never widen a reviewer query beyond those departments.
- Graceful degradation: a review that cannot run (no cover letter, LLM failure) lands as `status="error"` with a message; `run_review` never raises into the caller.
- No inline HTML event handlers (CSP). Delegated click handling through `onPanelClick`.
- Tests touching SQLAlchemy use the `_import_real(...)` unstub helper copied verbatim from `tests/test_council_personas.py:16-32`.
- Keep each new Python module under ~200 lines.

### Interfaces consumed (exact names)

From plan 00:
- `core.database.ApplicationReview(id, owner, application_id, status="running", verdicts, panel_summary, scores, error, started_at, finished_at)`; `core.database.JobApplication.latest_review_id`, `.cover_letter_doc_id`, `.company`, `.role`, `.jd_text`, `.owner`; `core.database.CrewMember(id, owner, name, avatar, personality, model, endpoint_url, department)`; `core.database.Document.current_content`.
- `services.career.seeds.REVIEWER_DEPARTMENTS`, `REVIEWER_NAMES`, `seed_reviewers(owner, session_factory=None)`.
- `routes.career_helpers.get_owned_application(db, aid, user)`.
- `static/js/career.js` default export `{ open, close, isOpen, refresh, render, registerTab, onPanelClick, getState }`; `static/js/careerApplications.js` export `detailSections` (array of `(app, state) => html`); `static/js/careerLogic.js` export `REVIEWER_COLORS`.
- `src.event_bus.get_task_scheduler()` → `.add_notification(task_name, status, task_id=None, owner=None, body=None)`.

From plan 01:
- `services.career.llm.complete(messages: list[dict], owner: str | None) -> str` (async; plan 00).
- `services.career.evidence.cv_chunks(owner, query, k=8) -> list[str]`.
- `routes/career_studio_routes.py` exposing `setup_career_studio_routes(rag_manager) -> APIRouter` with prefix `/api/career` and a module-level `logger`; this plan inserts endpoints before its `return router`.

---

### Task 1: Review scoring — parse reviewer and panel output

**Files:**
- Create: `services/career/review_scoring.py`
- Test: `tests/test_career_review_scoring.py`

**Interfaces:**
- Produces: `REVIEW_DIMS`, `DIM_LABELS`, `VERDICTS = ("advance", "maybe", "reject")`, `PANEL_VERDICTS = ("advance", "revise", "rewrite")`, `dims_json_example(department) -> str`, `parse_review_block(text, department) -> dict`, `parse_panel_verdict(text) -> str`, `strip_json_fence(text) -> str`, `aggregate_panel(verdicts) -> dict`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_review_scoring.py
"""Pure parsing/aggregation for the reviewer panel (plan 02)."""
from services.career import review_scoring as rs

REVIEW = (
    "## Verdict\nmaybe — decent but generic.\n\n## Top 3 issues\n1. x\n\n"
    "```json\n{\"verdict\": \"advance\", \"ats_match\": 140, \"clarity\": 62.4, \"role_fit\": 90, \"bogus\": 1}\n```\n"
)


def test_dims_registry_covers_four_reviewers():
    assert set(rs.REVIEW_DIMS) == {"career_recruiter", "career_hiring_manager", "career_engineer", "career_hr"}
    assert rs.REVIEW_DIMS["career_engineer"] == ("technical_credibility", "specificity")
    assert all(d in rs.DIM_LABELS for dims in rs.REVIEW_DIMS.values() for d in dims)


def test_parse_review_block_clamps_filters_and_reads_verdict():
    out = rs.parse_review_block(REVIEW, "career_recruiter")
    assert out == {"verdict": "advance", "scores": {"ats_match": 100, "clarity": 62}}


def test_parse_review_block_uses_last_fence_and_defaults_verdict():
    text = "```json\n{\"verdict\": \"reject\", \"ats_match\": 1}\n```\nlater\n```json\n{\"ats_match\": 55}\n```"
    out = rs.parse_review_block(text, "career_recruiter")
    assert out == {"verdict": "maybe", "scores": {"ats_match": 55}}
    assert rs.parse_review_block("no json here", "career_hr") == {"verdict": "maybe", "scores": {}}


def test_strip_json_fence_removes_only_trailing_fence():
    assert rs.strip_json_fence(REVIEW).endswith("1. x")
    assert rs.strip_json_fence("plain") == "plain"


def test_parse_panel_verdict():
    assert rs.parse_panel_verdict("## Verdict\nrevise\n```json\n{\"panel_verdict\": \"rewrite\"}\n```") == "rewrite"
    assert rs.parse_panel_verdict("```json\n{\"panel_verdict\": \"nonsense\"}\n```") == "revise"
    assert rs.parse_panel_verdict("") == "revise"


def test_dims_json_example_lists_the_reviewers_dims():
    ex = rs.dims_json_example("career_hr")
    assert '"verdict": "maybe"' in ex and '"consistency_with_cv": 70' in ex and '"professionalism": 70' in ex


def test_aggregate_panel_averages_per_reviewer_then_overall():
    verdicts = {
        "career_recruiter": {"verdict": "advance", "scores": {"ats_match": 80, "clarity": 60}},
        "career_engineer": {"verdict": "reject", "scores": {"technical_credibility": 40}},
        "career_hr": {"verdict": "maybe", "scores": {}},
    }
    agg = rs.aggregate_panel(verdicts)
    assert agg["by_reviewer"] == {"career_recruiter": 70, "career_engineer": 40}
    assert agg["overall"] == 55
    assert agg["verdict_counts"] == {"advance": 1, "maybe": 1, "reject": 1}
    assert rs.aggregate_panel({}) == {"overall": None, "by_reviewer": {}, "verdict_counts": {}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_review_scoring.py -v`
Expected: FAIL with `ImportError: cannot import name 'review_scoring' from 'services.career'`

- [ ] **Step 3: Implement**

```python
# services/career/review_scoring.py
"""Reviewer-panel scoring: a fixed per-reviewer dimension registry, parsing of
the JSON fence each reviewer ends with, and the panel aggregate.

Mirrors services/council/scoring.py in spirit (clamp 0-100, drop unknown keys,
never raise) but every reviewer owns its own two dimensions. The JS mirror is
static/js/careerReviewLogic.js — keep REVIEW_DIMS and DIM_LABELS in sync.
"""
from __future__ import annotations

import json
import re
from typing import Dict, Tuple

REVIEW_DIMS: Dict[str, Tuple[str, str]] = {
    "career_recruiter": ("ats_match", "clarity"),
    "career_hiring_manager": ("role_fit", "impact_evidence"),
    "career_engineer": ("technical_credibility", "specificity"),
    "career_hr": ("consistency_with_cv", "professionalism"),
}

DIM_LABELS = {
    "ats_match": "ATS keyword match",
    "clarity": "Clarity",
    "role_fit": "Role fit",
    "impact_evidence": "Impact evidence",
    "technical_credibility": "Technical credibility",
    "specificity": "Specificity",
    "consistency_with_cv": "Consistency with CV",
    "professionalism": "Professionalism",
}

VERDICTS = ("advance", "maybe", "reject")
PANEL_VERDICTS = ("advance", "revise", "rewrite")

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_TRAILING_FENCE_RE = re.compile(r"```(?:json)?\s*\{[^`]*\}\s*```\s*$", re.S)


def dims_json_example(department: str) -> str:
    body = {"verdict": "maybe"}
    body.update({d: 70 for d in REVIEW_DIMS.get(department, ())})
    return json.dumps(body)


def _last_fence(text: str) -> dict:
    for raw in reversed(_FENCE_RE.findall(text or "")):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _clamp(v) -> int:
    return int(round(max(0, min(100, float(v)))))


def parse_review_block(text: str, department: str) -> dict:
    """``{"verdict": advance|maybe|reject, "scores": {dim: 0-100}}`` from the
    last JSON fence; unknown dims dropped, non-numbers dropped, bools excluded."""
    data = _last_fence(text)
    verdict = str(data.get("verdict", "")).strip().lower()
    scores = {}
    for dim in REVIEW_DIMS.get(department, ()):
        v = data.get(dim)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            scores[dim] = _clamp(v)
    return {"verdict": verdict if verdict in VERDICTS else "maybe", "scores": scores}


def parse_panel_verdict(text: str) -> str:
    v = str(_last_fence(text).get("panel_verdict", "")).strip().lower()
    return v if v in PANEL_VERDICTS else "revise"


def strip_json_fence(text: str) -> str:
    return _TRAILING_FENCE_RE.sub("", text or "").strip()


def aggregate_panel(verdicts: dict) -> dict:
    """Per-reviewer mean of its scores, overall mean of those, verdict tally."""
    by_reviewer: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    for dept, v in (verdicts or {}).items():
        scores = [s for s in (v.get("scores") or {}).values() if isinstance(s, (int, float))]
        if scores:
            by_reviewer[dept] = int(round(sum(scores) / len(scores)))
        verdict = v.get("verdict")
        if verdict:
            counts[verdict] = counts.get(verdict, 0) + 1
    overall = int(round(sum(by_reviewer.values()) / len(by_reviewer))) if by_reviewer else None
    return {"overall": overall, "by_reviewer": by_reviewer, "verdict_counts": counts}
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_review_scoring.py -v`
Expected: 7 passed

- [ ] **Step 5: Commit**

```bash
git add services/career/review_scoring.py tests/test_career_review_scoring.py
git commit -m "feat(career): reviewer panel score parsing and aggregation"
```

---

### Task 2: Prompt templates with user override

**Files:**
- Create: `services/career/templates.py`
- Create: `services/career/reviewers/prompts/reviewer.md`, `services/career/reviewers/prompts/panel.md`
- Test: `tests/test_career_templates.py`

**Interfaces:**
- Consumes: `services.career.llm.render_template` (plan 00).
- Produces: `load_template(subdir: str, name: str) -> str` (checks `data/career/<subdir>/<name>.md`, then `services/career/<subdir>/<name>.md`; raises `FileNotFoundError`) and re-exports `render_template` so callers import both from one place.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_templates.py
"""Packaged prompt templates with a data/career/ override layer (plan 02)."""
import pytest

from services.career import templates as t


def test_packaged_reviewer_and_panel_templates_have_placeholders():
    r = t.load_template("reviewers/prompts", "reviewer")
    for key in ("{reviewer_name}", "{company}", "{role}", "{jd}", "{cover_letter}", "{cv_excerpt}", "{dims_json_example}"):
        assert key in r
    p = t.load_template("reviewers/prompts", "panel")
    for key in ("{company}", "{role}", "{reviews_markdown}"):
        assert key in p
    assert "panel_verdict" in p


def test_user_override_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "DATA_DIR", str(tmp_path))
    d = tmp_path / "career" / "reviewers" / "prompts"
    d.mkdir(parents=True)
    (d / "panel.md").write_text("custom {company}", encoding="utf-8")
    assert t.load_template("reviewers/prompts", "panel") == "custom {company}"


def test_missing_template_raises():
    with pytest.raises(FileNotFoundError):
        t.load_template("reviewers/prompts", "nope")


def test_render_template_replaces_only_known_keys():
    out = t.render_template("A {company} {\"json\": 1} {unknown}", {"company": "Acme"})
    assert out == "A Acme {\"json\": 1} {unknown}"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_templates.py -v`
Expected: FAIL with `ImportError: cannot import name 'templates' from 'services.career'`

- [ ] **Step 3: Implement the module**

```python
# services/career/templates.py
"""Prompt templates for Career hub features.

Lookup order: data/career/<subdir>/<name>.md (user override, gitignored) then
the packaged services/career/<subdir>/<name>.md. Same pattern as
services/council/runner.load_template.
"""
from __future__ import annotations

from pathlib import Path

from core.constants import DATA_DIR
from services.career.llm import render_template  # re-exported for callers

__all__ = ["load_template", "render_template"]

_PACKAGED_ROOT = Path(__file__).parent


def load_template(subdir: str, name: str) -> str:
    for root in (Path(DATA_DIR) / "career", _PACKAGED_ROOT):
        f = root / subdir / f"{name}.md"
        if f.is_file():
            try:
                return f.read_text(encoding="utf-8")
            except OSError:
                continue
    raise FileNotFoundError(f"No template {subdir}/{name}.md")
```

- [ ] **Step 4: Write the templates**

`services/career/reviewers/prompts/reviewer.md`:

```markdown
You are {reviewer_name} on the hiring panel for the {role} role at {company}. Stay fully in the character described in your system prompt and judge only from the material below.

Write your review as markdown with exactly these sections:

## Verdict
One line: advance, maybe, or reject — then one sentence on why.

## Top 3 issues
A numbered list. Each issue quotes the exact phrase from the cover letter it concerns.

## Line edits
At most five pairs in the form `replace: "<original>" → with: "<rewrite>"`. Rewrites must stay true to the CV excerpt; do not invent experience.

## What would make me say yes
Two or three sentences naming the single most valuable addition or cut.

Then output exactly one fenced json block and nothing after it. Use integers 0-100 for the scores and one of advance, maybe, reject for the verdict:

```json
{dims_json_example}
```

Ground every point in the job description, the cover letter, or the CV excerpt. If the CV excerpt is missing or does not cover a claim, say "unverified" for that claim instead of guessing.

## Job description
{jd}

## Cover letter under review
{cover_letter}

## CV excerpt (retrieved; may be partial)
{cv_excerpt}
```

`services/career/reviewers/prompts/panel.md`:

```markdown
You chair the hiring panel for the {role} role at {company}. Four reviewers have each reviewed the candidate's cover letter. Synthesise their reviews for the candidate, who will revise the letter before sending it.

Write markdown with exactly these sections:

## Verdict
One of advance, revise, or rewrite, followed by two sentences that reconcile any disagreement between reviewers (name the reviewers when they disagree).

## Consolidated edits
A numbered list that merges every reviewer's line edits, duplicates removed, most important first. Attribute each item to the reviewer(s) who raised it, like "(Recruiter screener, HR)".

## Send-ready checklist
Exactly five `- [ ]` items the candidate ticks before sending, each checkable in under a minute.

Then output exactly one fenced json block and nothing after it, where panel_verdict is one of advance, revise, rewrite:

```json
{"panel_verdict": "revise"}
```

## Reviews
{reviews_markdown}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_career_templates.py -v`
Expected: 4 passed

- [ ] **Step 6: Commit**

```bash
git add services/career/templates.py services/career/reviewers/prompts tests/test_career_templates.py
git commit -m "feat(career): reviewer and panel prompt templates with override dir"
```

---

### Task 3: Review runner

**Files:**
- Create: `services/career/review_prompts.py` (message building + reviewer snapshot + member model override)
- Create: `services/career/review_runner.py` (queue, orchestration, persistence, notify)
- Test: `tests/test_career_review_runner.py`

**Interfaces:**
- Consumes: Task 1 and Task 2; plan 00 `services.career.llm.complete`; plan 01 `services.career.evidence.cv_chunks`; plan 00 models and `seed_reviewers`.
- Produces: `review_runner.run_review(review_id, complete=None, session_factory=None, evidence=None, notify=None) -> dict` (`{"status": "done"|"error", "review_id": ..., "error"?: ...}`), `review_runner.queue_state() -> {"running": id|None, "queued": [ids]}`, `review_prompts.ReviewContext`, `review_prompts.build_reviewer_messages(member, ctx)`, `review_prompts.build_panel_messages(ctx, verdicts)`, `review_prompts.member_candidate(member, owner, session_factory)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_review_runner.py
"""Reviewer panel runner — four persona completions, a panel synthesis, queue,
persistence and notification. Uses the unstub window from tests/test_council_runner.py."""
import asyncio
import importlib
import json
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


_sa, _sa_orm, _sa_pool, _coredb, seeds, runner, prompts = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "services.career.seeds", "services.career.review_runner", "services.career.review_prompts",
    force=("core.database", "services.career.seeds", "services.career.review_runner",
           "services.career.review_prompts"),
)
Base = _coredb.Base
JobApplication = _coredb.JobApplication
ApplicationReview = _coredb.ApplicationReview
Document = _coredb.Document
CrewMember = _coredb.CrewMember


@pytest.fixture()
def db_factory():
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    Base.metadata.create_all(bind=engine)
    factory = _sa_orm.sessionmaker(bind=engine)
    seeds.seed_reviewers("vanders", session_factory=factory)
    db = factory()
    db.add(Document(id="d1", owner="vanders", title="Cover letter — Acme", language="markdown",
                    current_content="Dear Acme,\nI built the billing pipeline at Beta."))
    db.add(JobApplication(id="a1", owner="vanders", company="Acme", role="Backend Engineer",
                          jd_text="Python, Postgres, 3 years", cover_letter_doc_id="d1"))
    db.add(JobApplication(id="a2", owner="vanders", company="NoLetter", role="Dev"))
    db.add(ApplicationReview(id="rv1", owner="vanders", application_id="a1"))
    db.add(ApplicationReview(id="rv2", owner="vanders", application_id="a2"))
    db.commit()
    db.close()
    return factory


def _review_output(dept):
    dims = runner.REVIEW_DIMS[dept]
    return (f"## Verdict\nmaybe for {dept}\n\n## Line edits\nreplace: \"built\" → with: \"designed\"\n\n"
            f"```json\n{{\"verdict\": \"advance\", \"{dims[0]}\": 80, \"{dims[1]}\": 60}}\n```")


PANEL_OUTPUT = "## Verdict\nrevise\n\n## Consolidated edits\n1. x\n\n```json\n{\"panel_verdict\": \"revise\"}\n```"


def _fake_complete(log):
    async def complete(messages):
        log.append(messages)
        if messages[0]["role"] == "system":
            dept = next(d for d, name in seeds.REVIEWER_NAMES.items() if name in messages[-1]["content"])
            return _review_output(dept)
        return PANEL_OUTPUT
    return complete


def fake_evidence(owner, query, k=8):
    return ["Beta — built billing pipeline (2023)"]


async def test_run_review_happy_path(db_factory):
    log, notes = [], []
    result = await runner.run_review("rv1", complete=_fake_complete(log), session_factory=db_factory,
                                     evidence=fake_evidence, notify=lambda **kw: notes.append(kw))
    assert result["status"] == "done"
    assert len(log) == 5
    systems = [m[0]["content"] for m in log[:4]]
    assert len(set(systems)) == 4 and all("hiring" in s.lower() or "recruit" in s.lower()
                                           or "engineer" in s.lower() or "hr" in s.lower() for s in systems)
    for m in log[:4]:
        user = m[-1]["content"]
        assert "Dear Acme" in user and "Python, Postgres" in user and "billing pipeline (2023)" in user
    assert log[4][0]["role"] == "user" and log[4][0]["content"].count("## ") >= 4
    assert "Recruiter screener" in log[4][0]["content"]

    db = db_factory()
    row = db.get(ApplicationReview, "rv1")
    assert row.status == "done" and row.started_at and row.finished_at
    verdicts = json.loads(row.verdicts)
    assert set(verdicts) == set(seeds.REVIEWER_DEPARTMENTS)
    assert verdicts["career_engineer"]["scores"] == {"technical_credibility": 80, "specificity": 60}
    assert verdicts["career_engineer"]["verdict"] == "advance"
    assert "```json" not in verdicts["career_engineer"]["body"]
    scores = json.loads(row.scores)
    assert scores["overall"] == 70 and scores["panel_verdict"] == "revise"
    assert row.panel_summary.startswith("## Verdict") and "```json" not in row.panel_summary
    assert db.get(JobApplication, "a1").latest_review_id == "rv1"
    db.close()
    assert notes and notes[0]["owner"] == "vanders" and notes[0]["status"] == "success"


async def test_run_review_without_cover_letter_is_error(db_factory):
    log = []
    result = await runner.run_review("rv2", complete=_fake_complete(log), session_factory=db_factory,
                                     evidence=fake_evidence, notify=lambda **kw: None)
    assert result["status"] == "error" and "cover letter" in result["error"].lower()
    assert log == []
    db = db_factory()
    assert db.get(ApplicationReview, "rv2").status == "error"
    db.close()


async def test_llm_failure_lands_as_error_and_frees_queue(db_factory):
    async def boom(messages):
        raise RuntimeError("endpoint down")
    result = await runner.run_review("rv1", complete=boom, session_factory=db_factory,
                                     evidence=fake_evidence, notify=lambda **kw: None)
    assert result["status"] == "error" and "endpoint down" in result["error"]
    assert runner.queue_state() == {"running": None, "queued": []}


async def test_queue_serialises_reviews(db_factory):
    db = db_factory()
    db.add(ApplicationReview(id="rv3", owner="vanders", application_id="a1"))
    db.commit()
    db.close()
    release = asyncio.Event()
    observed = {}
    log = []
    inner = _fake_complete(log)

    async def gated(messages):
        observed.setdefault("mid_run", runner.queue_state())
        await release.wait()
        return await inner(messages)

    t1 = asyncio.create_task(runner.run_review("rv1", complete=gated, session_factory=db_factory,
                                               evidence=fake_evidence, notify=lambda **kw: None))
    t2 = asyncio.create_task(runner.run_review("rv3", complete=gated, session_factory=db_factory,
                                               evidence=fake_evidence, notify=lambda **kw: None))
    while not observed.get("mid_run"):
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)
    state = runner.queue_state()
    assert state["running"] in ("rv1", "rv3")
    assert state["queued"] == [({"rv1", "rv3"} - {state["running"]}).pop()]
    release.set()
    await asyncio.gather(t1, t2)
    assert runner.queue_state() == {"running": None, "queued": []}


def test_member_candidate_uses_override_model(db_factory, monkeypatch):
    monkeypatch.setattr(prompts, "resolve_task_endpoint",
                        lambda owner: ("http://local/v1/chat/completions", "default-m", {"X": "1"}))
    member = {"model": "qwen-big", "endpoint_url": ""}
    assert prompts.member_candidate(member, "vanders", db_factory) == (
        "http://local/v1/chat/completions", "qwen-big", {"X": "1"})
    assert prompts.member_candidate({"model": "", "endpoint_url": ""}, "vanders", db_factory) is None
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_review_runner.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.review_runner'`

- [ ] **Step 3: Implement `review_prompts.py`**

```python
# services/career/review_prompts.py
"""Message building for the reviewer panel: a detached snapshot of each
reviewer, the per-reviewer prompt, the panel synthesis prompt, and the
optional per-member model override (CrewMember.model / endpoint_url)."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Dict, List, Optional, Tuple

from core.database import CrewMember, SessionLocal
from services.career.review_scoring import dims_json_example
from services.career.seeds import REVIEWER_DEPARTMENTS, REVIEWER_NAMES
from services.career.templates import load_template, render_template

PROMPT_DIR = "reviewers/prompts"
CV_QUERY_CHARS = 400
MAX_CHARS = {"jd": 6000, "cover_letter": 6000, "cv_excerpt": 5000}


@dataclass
class ReviewContext:
    owner: Optional[str]
    company: str
    role: str
    jd: str
    cover_letter: str
    cv_excerpt: str
    members: List[Dict] = field(default_factory=list)   # snapshots, in REVIEWER_DEPARTMENTS order


def _clip(text: str, key: str) -> str:
    text = (text or "").strip()
    return text[: MAX_CHARS[key]] if len(text) > MAX_CHARS[key] else text


def snapshot_reviewers(owner: Optional[str], session_factory: Callable = None) -> List[Dict]:
    """Plain dicts so the runner can close its DB session before the long LLM calls."""
    factory = session_factory or SessionLocal
    db = factory()
    try:
        q = db.query(CrewMember).filter(CrewMember.department.in_(REVIEWER_DEPARTMENTS))
        if owner is not None:
            q = q.filter(CrewMember.owner == owner)
        rows = {m.department: m for m in q.all()}
        return [{
            "id": rows[d].id, "department": d,
            "name": rows[d].name or REVIEWER_NAMES[d],
            "personality": rows[d].personality or "",
            "model": rows[d].model or "", "endpoint_url": rows[d].endpoint_url or "",
        } for d in REVIEWER_DEPARTMENTS if d in rows]
    finally:
        db.close()


def build_context(owner, company, role, jd, cover_letter, cv_chunks: List[str], members) -> ReviewContext:
    return ReviewContext(
        owner=owner, company=company or "?", role=role or "?",
        jd=_clip(jd, "jd") or "(no job description on file)",
        cover_letter=_clip(cover_letter, "cover_letter"),
        cv_excerpt=_clip("\n\n".join(c for c in cv_chunks if c), "cv_excerpt") or "(no CV excerpt available)",
        members=list(members),
    )


def build_reviewer_messages(member: Dict, ctx: ReviewContext) -> list:
    prompt = render_template(load_template(PROMPT_DIR, "reviewer"), {
        "reviewer_name": member["name"], "company": ctx.company, "role": ctx.role,
        "jd": ctx.jd, "cover_letter": ctx.cover_letter, "cv_excerpt": ctx.cv_excerpt,
        "dims_json_example": dims_json_example(member["department"]),
    })
    system = member["personality"] or f"You are {member['name']} on a hiring panel."
    return [{"role": "system", "content": system}, {"role": "user", "content": prompt}]


def build_panel_messages(ctx: ReviewContext, verdicts: Dict[str, Dict]) -> list:
    blocks = []
    for dept in REVIEWER_DEPARTMENTS:
        v = verdicts.get(dept)
        if not v:
            continue
        blocks.append(f"## {v['name']} — verdict: {v['verdict']}\n{v['body']}")
    prompt = render_template(load_template(PROMPT_DIR, "panel"), {
        "company": ctx.company, "role": ctx.role,
        "reviews_markdown": "\n\n---\n\n".join(blocks) or "(no reviews completed)",
    })
    return [{"role": "user", "content": prompt}]


def resolve_task_endpoint(owner: Optional[str]) -> Tuple[Optional[str], Optional[str], Optional[Dict]]:
    """Module-level so tests can monkeypatch it (endpoint_resolver touches settings + DB)."""
    from src.endpoint_resolver import resolve_endpoint
    return resolve_endpoint("task", owner=owner)


def member_candidate(member: Dict, owner: Optional[str],
                     session_factory: Callable = None) -> Optional[Tuple[str, str, Dict]]:
    """(chat_url, model, headers) when the reviewer pins a model, else None.

    A pinned endpoint_url is a base URL (the Reviewers editor stores
    ModelEndpoint.base_url); its API key comes from the matching ModelEndpoint
    row. A pinned model without an endpoint runs on the Task endpoint."""
    model = (member.get("model") or "").strip()
    if not model:
        return None
    endpoint_url = (member.get("endpoint_url") or "").strip()
    if not endpoint_url:
        url, _default_model, headers = resolve_task_endpoint(owner)
        return (url, model, headers or {}) if url else None
    from core.database import ModelEndpoint
    from src.endpoint_resolver import build_chat_url, build_headers, normalize_base
    base = normalize_base(endpoint_url)
    factory = session_factory or SessionLocal
    db = factory()
    try:
        api_key = None
        for ep in db.query(ModelEndpoint).filter(ModelEndpoint.is_enabled == True).all():  # noqa: E712
            if normalize_base(ep.base_url) == base:
                api_key = ep.api_key
                break
    finally:
        db.close()
    return (build_chat_url(base), model, build_headers(api_key, base))
```

- [ ] **Step 4: Implement `review_runner.py`**

```python
# services/career/review_runner.py
"""Reviewer panel runner (plan 02).

One review at a time on its own semaphore (independent of the council queue):
four reviewer completions (each with its persona as the system prompt) then a
chaired panel synthesis. Results land on ApplicationReview; the application's
latest_review_id is updated; a notification is queued. Never raises into the
caller — failures land as status="error".
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime
from typing import Callable, Dict, Optional

from core.database import ApplicationReview, Document, JobApplication, SessionLocal
from services.career.review_prompts import (
    build_context, build_panel_messages, build_reviewer_messages, member_candidate, snapshot_reviewers,
)
from services.career.review_scoring import (
    REVIEW_DIMS, aggregate_panel, parse_panel_verdict, parse_review_block, strip_json_fence,
)
from services.career.seeds import seed_reviewers

logger = logging.getLogger(__name__)

_run_semaphore = asyncio.Semaphore(1)
_queued: list = []
_current: Optional[str] = None

LLM_TIMEOUT = 900
MAX_TOKENS = 3000


def queue_state() -> Dict:
    return {"running": _current, "queued": list(_queued)}


async def _default_complete(messages, owner):
    from services.career.llm import complete
    return await complete(messages, owner)


def _default_evidence(owner, query, k=8):
    from services.career.evidence import cv_chunks
    return cv_chunks(owner, query, k)


def _default_notify(**kw):
    from src.event_bus import get_task_scheduler
    scheduler = get_task_scheduler()
    if scheduler is None:
        logger.info("Review notification dropped (no scheduler): %s", kw.get("task_name"))
        return
    scheduler.add_notification(**kw)


async def _call(member: Optional[Dict], messages: list, complete, owner, session_factory) -> str:
    """Reviewer with a pinned model → direct call; otherwise the injected/default completer."""
    candidate = member_candidate(member, owner, session_factory) if member else None
    if candidate and complete is None:
        from src.llm_core import llm_call_async_with_fallback
        return await llm_call_async_with_fallback([candidate], messages, timeout=LLM_TIMEOUT, max_tokens=MAX_TOKENS)
    if complete is None:
        return await _default_complete(messages, owner)
    return await complete(messages)


def _finish(db, review: ApplicationReview, *, status: str, verdicts=None, panel_summary=None,
            scores=None, error=None):
    review.status = status
    review.verdicts = json.dumps(verdicts) if verdicts is not None else None
    review.panel_summary = panel_summary
    review.scores = json.dumps(scores) if scores is not None else None
    review.error = error
    review.finished_at = datetime.utcnow()
    db.commit()


def _load_context(db, review: ApplicationReview, evidence, session_factory):
    app = db.get(JobApplication, review.application_id)
    if not app:
        raise ValueError("Application not found")
    doc = db.get(Document, app.cover_letter_doc_id) if app.cover_letter_doc_id else None
    if not doc or not (doc.current_content or "").strip():
        raise ValueError("No cover letter to review — draft one first")
    seed_reviewers(app.owner, session_factory=session_factory)
    members = snapshot_reviewers(app.owner, session_factory)
    if not members:
        raise ValueError("No reviewers configured")
    query = f"{app.role} at {app.company}: {(app.jd_text or '')[:400]}"
    try:
        chunks = evidence(app.owner, query, 8) or []
    except Exception:
        logger.warning("DEGRADED: CV evidence unavailable for review %s", review.id, exc_info=True)
        chunks = []
    return app.owner, build_context(app.owner, app.company, app.role, app.jd_text, doc.current_content, chunks, members)


async def _run_panel(ctx, complete, owner, session_factory) -> tuple:
    verdicts: Dict[str, Dict] = {}
    for member in ctx.members:
        raw = await _call(member, build_reviewer_messages(member, ctx), complete, owner, session_factory)
        parsed = parse_review_block(raw, member["department"])
        verdicts[member["department"]] = {
            "name": member["name"], "verdict": parsed["verdict"],
            "body": strip_json_fence(raw), "scores": parsed["scores"],
        }
    panel_raw = await _call(None, build_panel_messages(ctx, verdicts), complete, owner, session_factory)
    scores = aggregate_panel(verdicts)
    scores["panel_verdict"] = parse_panel_verdict(panel_raw)
    return verdicts, strip_json_fence(panel_raw), scores


async def _under_queue(review_id: str, work):
    global _current
    _queued.append(review_id)
    try:
        async with _run_semaphore:
            _queued.remove(review_id)
            _current = review_id
            try:
                return await work()
            finally:
                _current = None
    except Exception:
        if review_id in _queued:
            _queued.remove(review_id)
        raise


async def run_review(review_id: str, complete: Optional[Callable] = None, session_factory: Callable = None,
                     evidence: Optional[Callable] = None, notify: Optional[Callable] = None) -> Dict:
    factory = session_factory or SessionLocal
    evidence = evidence or _default_evidence
    notify = notify or _default_notify
    db = factory()
    try:
        review = db.get(ApplicationReview, review_id)
        if not review:
            return {"status": "error", "review_id": review_id, "error": "Review not found"}
        review.started_at = datetime.utcnow()
        db.commit()
        try:
            owner, ctx = _load_context(db, review, evidence, factory)
        except ValueError as e:
            _finish(db, review, status="error", error=str(e))
            return {"status": "error", "review_id": review_id, "error": str(e)}
    finally:
        db.close()

    try:
        verdicts, panel_summary, scores = await _under_queue(
            review_id, lambda: _run_panel(ctx, complete, owner, factory))
    except Exception as e:
        logger.warning("Review %s failed: %s", review_id, e)
        db = factory()
        try:
            _finish(db, db.get(ApplicationReview, review_id), status="error", error=str(e)[:500])
        finally:
            db.close()
        return {"status": "error", "review_id": review_id, "error": str(e)}

    db = factory()
    try:
        review = db.get(ApplicationReview, review_id)
        _finish(db, review, status="done", verdicts=verdicts, panel_summary=panel_summary, scores=scores)
        app = db.get(JobApplication, review.application_id)
        app.latest_review_id = review_id
        db.commit()
        company, role = app.company, app.role
    finally:
        db.close()
    try:
        notify(task_name="Career review", status="success", task_id=review_id, owner=owner,
               body=f"Panel verdict for {company} — {role}: {scores['panel_verdict']} (overall {scores['overall']})")
    except Exception:
        logger.debug("review notify failed", exc_info=True)
    return {"status": "done", "review_id": review_id}
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_career_review_runner.py -v`
Expected: 5 passed

- [ ] **Step 6: Commit**

```bash
git add services/career/review_prompts.py services/career/review_runner.py tests/test_career_review_runner.py
git commit -m "feat(career): reviewer panel runner with queue, persistence and notification"
```

---

### Task 4: Review and reviewer routes

**Files:**
- Modify: `routes/career_studio_routes.py` (imports at top, helpers after the request models, endpoints before `return router`)
- Test: `tests/test_career_review_routes.py`

**Interfaces:**
- Consumes: Task 3 `run_review`, `queue_state`; plan 00 `get_owned_application`, `REVIEWER_DEPARTMENTS`, `seed_reviewers`.
- Produces routes (all under the existing `/api/career` prefix):

| Method | Path | Body | Returns |
|---|---|---|---|
| POST | `/applications/{id}/reviews` | — | `{review_id, status: "running"}`; 409 if no cover letter |
| GET | `/applications/{id}/reviews` | — | `{reviews: [dict without bodies]}` newest first |
| GET | `/reviews/{rid}` | — | review dict with `verdicts` and `panel_summary` |
| GET | `/reviewers` | — | `{reviewers: [{id, department, name, avatar, personality, model, endpoint_url}]}` |
| GET | `/reviewers/status` | — | `{reviewers: [{id, department, name, status: Busy\|Ready}], running: id\|null, queued: [ids]}` |
| PUT | `/reviewers/{mid}` | `{name?, avatar?, personality?, model?, endpoint_url?}` | reviewer dict; 404 for council members or foreign rows |

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_review_routes.py
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
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_review_routes.py -v`
Expected: FAIL with `AttributeError: module 'routes.career_studio_routes' has no attribute 'run_review'`

- [ ] **Step 3: Add imports and helpers to `routes/career_studio_routes.py`**

Add to the import block at the top of the file (keep whatever plan 01 already imports; add only the missing names):

```python
import asyncio
import json
import uuid
from typing import Any, Dict, Optional

from fastapi import HTTPException, Request
from pydantic import BaseModel

from core.database import ApplicationReview, CrewMember, Document, JobApplication, SessionLocal
from routes.career_helpers import get_owned_application
from services.career.review_runner import queue_state, run_review
from services.career.seeds import REVIEWER_DEPARTMENTS, seed_reviewers
from src.auth_helpers import get_current_user
```

Add after the request models (module level):

```python
_review_tasks: set = set()


class ReviewerUpdate(BaseModel):
    name: Optional[str] = None
    avatar: Optional[str] = None
    personality: Optional[str] = None
    model: Optional[str] = None
    endpoint_url: Optional[str] = None


def _review_to_dict(r: ApplicationReview, include_body: bool = False) -> Dict[str, Any]:
    def _json(s):
        try:
            return json.loads(s) if s else {}
        except (json.JSONDecodeError, TypeError):
            return {}
    out = {
        "id": r.id, "application_id": r.application_id, "status": r.status, "error": r.error,
        "scores": _json(r.scores),
        "started_at": r.started_at.isoformat() if r.started_at else None,
        "finished_at": r.finished_at.isoformat() if r.finished_at else None,
        "created_at": r.created_at.isoformat() if r.created_at else None,
    }
    if include_body:
        out["verdicts"] = _json(r.verdicts)
        out["panel_summary"] = r.panel_summary
    return out


def _reviewer_to_dict(m: CrewMember) -> Dict[str, Any]:
    return {"id": m.id, "department": m.department, "name": m.name, "avatar": m.avatar,
            "personality": m.personality, "model": m.model, "endpoint_url": m.endpoint_url}


def _reviewer_query(db, user: Optional[str]):
    q = db.query(CrewMember).filter(CrewMember.department.in_(REVIEWER_DEPARTMENTS))
    if user is not None:
        q = q.filter(CrewMember.owner == user)
    return q
```

- [ ] **Step 4: Add the endpoints**

Insert inside `setup_career_studio_routes(...)` immediately before `return router`:

```python
    # ── Reviewer panel (plan 02) ────────────────────────────────────────────

    @router.post("/applications/{aid}/reviews")
    async def create_review(aid: str, request: Request):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            doc = db.get(Document, app.cover_letter_doc_id) if app.cover_letter_doc_id else None
            if not doc or not (doc.current_content or "").strip():
                raise HTTPException(409, "Draft a cover letter before running the panel")
            rid = uuid.uuid4().hex[:12]
            db.add(ApplicationReview(id=rid, owner=user, application_id=app.id, status="running"))
            db.commit()
        finally:
            db.close()
        task = asyncio.create_task(run_review(rid))
        _review_tasks.add(task)
        task.add_done_callback(_review_tasks.discard)
        return {"review_id": rid, "status": "running"}

    @router.get("/applications/{aid}/reviews")
    def list_reviews(aid: str, request: Request):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            rows = (db.query(ApplicationReview).filter(ApplicationReview.application_id == app.id)
                    .order_by(ApplicationReview.created_at.desc()).all())
            return {"reviews": [_review_to_dict(r) for r in rows]}
        finally:
            db.close()

    @router.get("/reviews/{rid}")
    def get_review(rid: str, request: Request):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            q = db.query(ApplicationReview).filter(ApplicationReview.id == rid)
            if user is not None:
                q = q.filter(ApplicationReview.owner == user)
            review = q.first()
            if not review:
                raise HTTPException(404, "Review not found")
            return _review_to_dict(review, include_body=True)
        finally:
            db.close()

    @router.get("/reviewers")
    def list_reviewers(request: Request):
        user = get_current_user(request)
        seed_reviewers(user, session_factory=SessionLocal)
        db = SessionLocal()
        try:
            rows = _reviewer_query(db, user).order_by(CrewMember.department).all()
            return {"reviewers": [_reviewer_to_dict(m) for m in rows]}
        finally:
            db.close()

    @router.get("/reviewers/status")
    def reviewers_status(request: Request):
        user = get_current_user(request)
        state = queue_state()
        db = SessionLocal()
        try:
            running = None
            if state["running"]:
                q = db.query(ApplicationReview).filter(ApplicationReview.id == state["running"])
                if user is not None:
                    q = q.filter(ApplicationReview.owner == user)
                running = state["running"] if q.first() else None
            rows = _reviewer_query(db, user).order_by(CrewMember.department).all()
            return {
                "running": running,
                "queued": list(state["queued"]),
                "reviewers": [{"id": m.id, "department": m.department, "name": m.name,
                               "status": "Busy" if running else "Ready"} for m in rows],
            }
        finally:
            db.close()

    @router.put("/reviewers/{mid}")
    def update_reviewer(mid: str, body: ReviewerUpdate, request: Request):
        user = get_current_user(request)
        db = SessionLocal()
        try:
            member = _reviewer_query(db, user).filter(CrewMember.id == mid).first()
            if not member:
                raise HTTPException(404, "Reviewer not found")
            for field in ("name", "avatar", "personality", "model", "endpoint_url"):
                val = getattr(body, field)
                if val is not None:
                    setattr(member, field, val)
            db.commit()
            return _reviewer_to_dict(member)
        finally:
            db.close()
```

- [ ] **Step 5: Run tests to verify they pass, plus plan 01's studio route tests**

Run: `python -m pytest tests/test_career_review_routes.py tests/test_career_cover_letter_routes.py -v`
Expected: all passed (if plan 01 named its route test file differently, run `python -m pytest tests/test_career_*.py`).

- [ ] **Step 6: Commit**

```bash
git add routes/career_studio_routes.py tests/test_career_review_routes.py
git commit -m "feat(career): review and reviewer endpoints"
```

---

### Task 5: Front-end — Reviewers tab and the review section on an application

**Files:**
- Create: `static/js/careerReviewLogic.js` (pure, Node-testable)
- Create: `static/js/careerReviewers.js` (Reviewers tab: cards + persona editor)
- Create: `static/js/careerReview.js` (application detail section: run panel, reviewer cards, panel summary, poll)
- Modify: `static/js/career.js` (two import lines + two install lines in the plugin block)
- Modify: `static/style.css` (append to the Career hub section)
- Test: `tests/test_career_review_js.py`

**Interfaces:**
- Consumes: plan 00 `career.js` API object, `careerApplications.detailSections`, `careerLogic.REVIEWER_COLORS`; `councilLogic.memberStatusChip`, `modelSort.sortModelIds`; `uiModule.esc/showToast/showError`.
- Produces: `careerReviewLogic.js` exports `REVIEW_DIMS`, `DIM_LABELS`, `reviewScoreEntries(verdict)`, `reviewScoreBarsHtml(verdict, esc)`, `panelVerdictChip(verdict)`, `reviewerLabel(department)`. `careerReviewers.js` exports `installReviewers(api)`. `careerReview.js` exports `installReview(api)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_review_js.py
"""careerReviewLogic.js pure helpers under Node + source wiring guards (plan 02)."""
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


def test_review_score_entries_follow_registry_order(node_available):
    out = _run_node(textwrap.dedent("""
        const { reviewScoreEntries, REVIEW_DIMS } = await import('./static/js/careerReviewLogic.js');
        const v = { scores: { specificity: 40, technical_credibility: 88, bogus: 1 } };
        console.log(JSON.stringify({ e: reviewScoreEntries('career_engineer', v), keys: Object.keys(REVIEW_DIMS) }));
    """))
    assert out["e"] == [
        {"key": "technical_credibility", "label": "Technical credibility", "value": 88},
        {"key": "specificity", "label": "Specificity", "value": 40},
    ]
    assert sorted(out["keys"]) == ["career_engineer", "career_hiring_manager", "career_hr", "career_recruiter"]


def test_score_bars_are_hatched_ai_bars_and_chips(node_available):
    out = _run_node(textwrap.dedent("""
        const { reviewScoreBarsHtml, panelVerdictChip, reviewerLabel } = await import('./static/js/careerReviewLogic.js');
        const esc = (s) => String(s).replace(/</g, '&lt;');
        console.log(JSON.stringify({
          html: reviewScoreBarsHtml('career_hr', { scores: { professionalism: 72 } }, esc),
          empty: reviewScoreBarsHtml('career_hr', { scores: {} }, esc),
          advance: panelVerdictChip('advance'), rewrite: panelVerdictChip('rewrite'), none: panelVerdictChip(undefined),
          label: reviewerLabel('career_hiring_manager'),
        }));
    """))
    assert 'score-bar-fill judged' in out["html"] and 'width:72%' in out["html"] and 'score-ai-tag' in out["html"]
    assert out["empty"] == ""
    assert out["advance"] == {"label": "Advance", "cls": "career-chip-positive"}
    assert out["rewrite"] == {"label": "Rewrite", "cls": "career-chip-negative"}
    assert out["none"] == {"label": "Pending", "cls": "career-chip-muted"}
    assert out["label"] == "Hiring manager"


def test_modules_parse_and_career_js_installs_them(node_available):
    for f in ("careerReviewLogic.js", "careerReviewers.js", "careerReview.js", "career.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
    src = (_REPO / "static" / "js" / "career.js").read_text(encoding="utf-8")
    assert "import { installReviewers } from './careerReviewers.js';" in src
    assert "import { installReview } from './careerReview.js';" in src
    assert "installReviewers(careerApi);" in src and "installReview(careerApi);" in src
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_review_js.py -v`
Expected: FAIL with `Cannot find module` for `careerReviewLogic.js`

- [ ] **Step 3: `static/js/careerReviewLogic.js`**

```javascript
// ============================================
// Career hub — reviewer panel pure logic (no DOM, Node-importable)
// Mirror of services/career/review_scoring.py: keep REVIEW_DIMS/DIM_LABELS in sync.
// ============================================

export const REVIEW_DIMS = {
  career_recruiter: ['ats_match', 'clarity'],
  career_hiring_manager: ['role_fit', 'impact_evidence'],
  career_engineer: ['technical_credibility', 'specificity'],
  career_hr: ['consistency_with_cv', 'professionalism'],
};

export const DIM_LABELS = {
  ats_match: 'ATS keyword match',
  clarity: 'Clarity',
  role_fit: 'Role fit',
  impact_evidence: 'Impact evidence',
  technical_credibility: 'Technical credibility',
  specificity: 'Specificity',
  consistency_with_cv: 'Consistency with CV',
  professionalism: 'Professionalism',
};

const REVIEWER_LABELS = {
  career_recruiter: 'Recruiter screener',
  career_hiring_manager: 'Hiring manager',
  career_engineer: 'Senior engineer',
  career_hr: 'HR / People partner',
};

export const reviewerLabel = (department) => REVIEWER_LABELS[department] || department;

// verdict = {name, verdict, body, scores} as persisted by the runner.
export const reviewScoreEntries = (department, verdict) => {
  const scores = (verdict || {}).scores || {};
  const out = [];
  for (const key of REVIEW_DIMS[department] || []) {
    if (Number.isFinite(scores[key])) out.push({ key, label: DIM_LABELS[key], value: scores[key] });
  }
  return out;
};

// Every panel score is an LLM judgement → hatched bar + AI tag (council convention).
export const reviewScoreBarsHtml = (department, verdict, esc) => {
  const entries = reviewScoreEntries(department, verdict);
  if (!entries.length) return '';
  const rows = entries.map((e) => `
    <div class="score-bar-row">
      <span class="score-bar-label">${esc(e.label)}<span class="score-ai-tag">AI</span></span>
      <span class="score-bar-track"><span class="score-bar-fill judged" style="width:${Math.max(0, Math.min(100, e.value))}%"></span></span>
      <span class="score-bar-val">${esc(String(e.value))}</span>
    </div>`).join('');
  return `<div class="score-bars">${rows}</div>`;
};

const VERDICT_CHIPS = {
  advance: { label: 'Advance', cls: 'career-chip-positive' },
  maybe: { label: 'Maybe', cls: 'career-chip-waiting' },
  revise: { label: 'Revise', cls: 'career-chip-waiting' },
  reject: { label: 'Reject', cls: 'career-chip-negative' },
  rewrite: { label: 'Rewrite', cls: 'career-chip-negative' },
};

export const panelVerdictChip = (verdict) => VERDICT_CHIPS[verdict] || { label: 'Pending', cls: 'career-chip-muted' };
```

- [ ] **Step 4: `static/js/careerReviewers.js`**

```javascript
// ============================================
// Career hub — Reviewers tab: four persona cards + editor (name, personality,
// model/endpoint override). Plugs into career.js through installReviewers(api).
// ============================================
import uiModule from './ui.js';
import { memberStatusChip, memberEditPayload } from './councilLogic.js';
import { sortModelIds } from './modelSort.js';
import { REVIEWER_COLORS } from './careerLogic.js';
import { reviewerLabel } from './careerReviewLogic.js';

const esc = uiModule.esc;

let _api = null;
let _reviewers = null;     // null = not loaded yet
let _status = {};          // department -> Busy|Ready
let _editing = null;

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${url} failed (${res.status})`);
  return data;
};

const _load = async () => {
  try {
    const [list, status] = await Promise.all([_json('/api/career/reviewers'), _json('/api/career/reviewers/status')]);
    _reviewers = list.reviewers || [];
    _status = Object.fromEntries((status.reviewers || []).map((r) => [r.department, r.status]));
  } catch (e) {
    _reviewers = [];
    uiModule.showError(`Reviewers: ${e.message}`);
  }
  _api.render();
};

const _card = (m) => {
  const chip = memberStatusChip(_status[m.department] || 'Ready');
  const color = REVIEWER_COLORS[m.department] || 'var(--fg)';
  return `
    <div class="proj-card admin-card council-member" data-reviewer-id="${esc(m.id)}" style="--dept-color:${color}">
      <div class="proj-card-head">
        <span class="council-avatar">${esc((m.name || '?')[0].toUpperCase())}</span>
        <span class="proj-name">${esc(m.name)}</span>
        <span class="proj-badge ${chip.cls}${chip.label === 'Busy' ? ' report-chip-running' : ''}">${esc(chip.label)}</span>
      </div>
      <div class="council-dept">${esc(reviewerLabel(m.department))}</div>
      <div class="memory-desc report-scores">${esc((m.personality || '').slice(0, 140))}${(m.personality || '').length > 140 ? '…' : ''}</div>
      <div class="memory-desc report-scores">${m.model ? `Model: ${esc(m.model)}` : 'Model: task default'}</div>
      <div class="proj-actions"><button class="memory-toolbar-btn" data-action="edit-reviewer">Edit persona</button></div>
    </div>`;
};

const _form = (m) => `
  <div class="council-edit-form" data-reviewer-id="${esc(m.id)}" style="--dept-color:${REVIEWER_COLORS[m.department] || 'var(--fg)'}">
    <h2 class="council-reports-title">Edit ${esc(reviewerLabel(m.department))}</h2>
    <label class="assistant-field"><span>Name</span>
      <input type="text" id="career-rv-name" class="settings-input" value="${esc(m.name || '')}"></label>
    <label class="assistant-field"><span>Personality (system prompt)</span>
      <textarea id="career-rv-personality" class="settings-input" rows="8">${esc(m.personality || '')}</textarea></label>
    <div class="assistant-field-row">
      <label class="assistant-field" style="flex:1;"><span>Model endpoint</span>
        <select id="career-rv-endpoint" class="settings-input"></select></label>
      <label class="assistant-field" style="flex:1;"><span>Model</span>
        <select id="career-rv-model" class="settings-input"></select></label>
    </div>
    <div class="council-run-form">
      <button id="career-rv-save" class="memory-toolbar-btn">Save</button>
      <button id="career-rv-cancel" class="memory-toolbar-btn">Cancel</button>
    </div>
  </div>`;

// Same endpoint/model population as the council member editor.
const _populateEndpointModel = (member) => {
  const epSelect = document.getElementById('career-rv-endpoint');
  const modelSelect = document.getElementById('career-rv-model');
  if (!epSelect || !modelSelect) return;
  fetch('/api/model-endpoints').then((r) => (r.ok ? r.json() : [])).then((eps) => {
    const endpoints = Array.isArray(eps) ? eps : [];
    let html = '<option value="">(use task default)</option>';
    for (const ep of endpoints) {
      if (!ep.is_enabled) continue;
      const url = ep.base_url || '';
      const sel = member.endpoint_url && url
        && member.endpoint_url.includes(url.replace('/v1', '').replace(/\/$/, '')) ? ' selected' : '';
      html += `<option value="${esc(url)}"${sel}>${esc(ep.name || url)}</option>`;
    }
    epSelect.innerHTML = html;
    epSelect.onchange = async () => {
      const url = epSelect.value;
      if (!url) { modelSelect.innerHTML = '<option value="">(default)</option>'; return; }
      const ep = endpoints.find((e) => e.base_url === url);
      if (!ep) return;
      modelSelect.innerHTML = '<option value="">loading…</option>';
      try {
        const res = await fetch(`/api/model-endpoints/${ep.id}/models`);
        const models = await res.json();
        const ids = (models.models || models || [])
          .map((m) => (typeof m === 'string' ? m : (m.id || m.name || ''))).filter(Boolean);
        let mh = '<option value="">(default)</option>';
        for (const mid of sortModelIds(ids)) {
          mh += `<option value="${esc(mid)}"${mid === member.model ? ' selected' : ''}>${esc(mid.split('/').pop())}</option>`;
        }
        modelSelect.innerHTML = mh;
      } catch { modelSelect.innerHTML = '<option value="">(failed)</option>'; }
    };
    if (epSelect.value) epSelect.onchange();
  });
};

const _save = async () => {
  if (!_editing) return;
  const payload = memberEditPayload({
    name: document.getElementById('career-rv-name')?.value,
    personality: document.getElementById('career-rv-personality')?.value,
    model: document.getElementById('career-rv-model')?.value,
    endpoint: document.getElementById('career-rv-endpoint')?.value,
  });
  try {
    await _json(`/api/career/reviewers/${_editing.id}`, { method: 'PUT', body: JSON.stringify(payload) });
    uiModule.showToast('Reviewer updated');
    _editing = null;
    await _load();
  } catch (e) {
    uiModule.showError(`Save reviewer: ${e.message}`);
  }
};

export const renderReviewersTab = () => {
  if (_reviewers === null) { _load(); return '<p class="memory-desc">Loading reviewers…</p>'; }
  if (_editing) return _form(_editing);
  if (!_reviewers.length) return '<p class="memory-desc">No reviewers yet — open the Applications tab once to seed them.</p>';
  return `
    <div class="projects-grid">${_reviewers.map(_card).join('')}</div>
    <p class="memory-desc doclib-desc">Each reviewer reads the job description, the cover letter and your CV excerpt in its own voice. Edit a persona to change what it looks for, or pin it to a specific model.</p>`;
};

// Returns true when it handled the click (career.js stops there).
export const handleReviewersClick = (ev) => {
  if (ev.target.closest('#career-rv-save')) { _save(); return true; }
  if (ev.target.closest('#career-rv-cancel')) { _editing = null; _api.render(); return true; }
  const btn = ev.target.closest('[data-action="edit-reviewer"]');
  if (!btn) return false;
  const id = btn.closest('[data-reviewer-id]')?.dataset.reviewerId;
  _editing = (_reviewers || []).find((m) => m.id === id) || null;
  _api.render();
  if (_editing) _populateEndpointModel(_editing);
  return true;
};

export const installReviewers = (api) => {
  _api = api;
  api.registerTab('reviewers', renderReviewersTab);
  api.onPanelClick(handleReviewersClick);
};
```

- [ ] **Step 5: `static/js/careerReview.js`**

```javascript
// ============================================
// Career hub — panel review section on an application's detail view.
// Run the panel, show four reviewer verdict cards + the chair's synthesis,
// poll while running. Plugs in through installReview(api) + detailSections.
// ============================================
import uiModule from './ui.js';
import { detailSections } from './careerApplications.js';
import { REVIEWER_COLORS } from './careerLogic.js';
import { panelVerdictChip, reviewerLabel, reviewScoreBarsHtml, REVIEW_DIMS } from './careerReviewLogic.js';

const esc = uiModule.esc;

let _api = null;
const _reviews = new Map();   // review id -> full review dict
const _loading = new Set();
let _pollTimer = null;
let _runBusy = false;

const _json = async (url, opts = {}) => {
  const res = await fetch(url, { headers: { 'Content-Type': 'application/json' }, ...opts });
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.detail || `${url} failed (${res.status})`);
  return data;
};

const _loadReview = async (rid) => {
  if (_loading.has(rid)) return;
  _loading.add(rid);
  try {
    _reviews.set(rid, await _json(`/api/career/reviews/${rid}`));
  } catch (e) {
    uiModule.showError(`Review: ${e.message}`);
  } finally {
    _loading.delete(rid);
  }
  _api.render();
  _schedulePoll(rid);
};

const _schedulePoll = (rid) => {
  clearTimeout(_pollTimer);
  const r = _reviews.get(rid);
  if (!r || r.status !== 'running' || !_api.isOpen()) return;
  _pollTimer = setTimeout(async () => {
    _reviews.delete(rid);
    await _loadReview(rid);
    const again = _reviews.get(rid);
    if (again && again.status !== 'running') await _api.refresh();   // picks up latest_review_id + toast state
  }, 5000);
};

const _run = async (appId) => {
  if (_runBusy) return;
  _runBusy = true;
  _api.render();
  try {
    const data = await _json(`/api/career/applications/${appId}/reviews`, { method: 'POST' });
    uiModule.showToast('Panel convened — reviewers are reading your letter');
    _reviews.set(data.review_id, { id: data.review_id, status: 'running', scores: {}, verdicts: {} });
    await _api.refresh();
    _schedulePoll(data.review_id);
  } catch (e) {
    uiModule.showError(`Run panel: ${e.message}`);
  } finally {
    _runBusy = false;
    _api.render();
  }
};

const _reviewerCard = (dept, review) => {
  const v = (review.verdicts || {})[dept];
  const running = review.status === 'running';
  const chip = running ? { label: 'Busy', cls: 'council-chip-busy report-chip-running' } : panelVerdictChip(v?.verdict);
  return `
    <div class="proj-card admin-card council-member" style="--dept-color:${REVIEWER_COLORS[dept] || 'var(--fg)'}">
      <div class="proj-card-head">
        <span class="council-avatar">${esc(reviewerLabel(dept)[0])}</span>
        <span class="proj-name">${esc(v?.name || reviewerLabel(dept))}</span>
        <span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>
      </div>
      <div class="council-dept">${esc(reviewerLabel(dept))}</div>
      ${v ? reviewScoreBarsHtml(dept, v, esc) : ''}
      ${v ? `<details class="proj-summary"><summary>Review</summary><div class="proj-summary-body">${esc(v.body || '')}</div></details>` : ''}
    </div>`;
};

const sectionHtml = (app) => {
  const rid = app.latest_review_id;
  const review = rid ? _reviews.get(rid) : null;
  if (rid && !review) _loadReview(rid);
  const running = review?.status === 'running';
  const chip = panelVerdictChip(review?.scores?.panel_verdict);
  const head = `
    <div class="council-run-form">
      <button id="career-review-run" class="memory-toolbar-btn" ${running || _runBusy || !app.cover_letter_doc_id ? 'disabled' : ''}
        title="${app.cover_letter_doc_id ? 'Four reviewers read the letter, then the chair synthesises' : 'Draft a cover letter first'}">
        ${running ? 'Panel in session…' : 'Run panel review'}
      </button>
      ${app.cover_letter_doc_id ? `<a class="memory-toolbar-btn" href="#document-${esc(app.cover_letter_doc_id)}">Open letter in editor</a>` : ''}
      ${review ? `<span class="proj-badge ${chip.cls}">${esc(chip.label)}</span>` : ''}
      ${Number.isFinite(review?.scores?.overall) ? `<span class="proj-score">${esc(String(review.scores.overall))}<span class="proj-score-denom">/100</span></span>` : ''}
    </div>`;
  if (!review) return `<h3 class="cc-section">Reviewer panel</h3>${head}<p class="memory-desc">No panel review yet.</p>`;
  if (review.status === 'error') return `<h3 class="cc-section">Reviewer panel</h3>${head}<p class="memory-desc cc-warn">Last run failed: ${esc(review.error || 'unknown error')}</p>`;
  return `
    <h3 class="cc-section">Reviewer panel</h3>${head}
    <div class="projects-grid">${Object.keys(REVIEW_DIMS).map((d) => _reviewerCard(d, review)).join('')}</div>
    ${review.panel_summary ? `<h3 class="cc-section">Chair's synthesis</h3><div class="report-body career-panel-summary">${esc(review.panel_summary)}</div>` : ''}`;
};

export const handleReviewClick = (ev, state) => {
  if (!ev.target.closest('#career-review-run')) return false;
  if (state.openId) _run(state.openId);
  return true;
};

export const installReview = (api) => {
  _api = api;
  detailSections.push(sectionHtml);
  api.onPanelClick(handleReviewClick);
};
```

- [ ] **Step 6: Wire into `static/js/career.js`**

Add after the existing imports:

```javascript
import { installReviewers } from './careerReviewers.js';
import { installReview } from './careerReview.js';
```

Plan 00 already defines `const careerApi = { ... }` followed by a `// ── plugins ──` block and `export default careerApi;`. Add these two lines inside that plugin block (after any install lines earlier plans added, before `export default careerApi;`):

```javascript
installReviewers(careerApi);
installReview(careerApi);
```

- [ ] **Step 7: `static/style.css`**

Append to the `/* ── Career hub ── */` section:

```css
.career-panel-summary { max-height: 480px; }
.career-detail .projects-grid { padding-top: 4px; }
.career-detail .proj-summary-body { max-height: 260px; }
```

- [ ] **Step 8: Run tests**

Run: `python -m pytest tests/test_career_review_js.py tests/test_career_js.py -v`
Expected: all passed

- [ ] **Step 9: Commit**

```bash
git add static/js/careerReviewLogic.js static/js/careerReviewers.js static/js/careerReview.js static/js/career.js static/style.css tests/test_career_review_js.py
git commit -m "feat(career): Reviewers tab and panel review section"
```

---

### Task 6: Full verification and smoke test

- [ ] **Step 1: Syntax and suites**

```bash
python -m py_compile routes/career_studio_routes.py services/career/*.py
node --check static/js/careerReviewLogic.js && node --check static/js/careerReviewers.js && node --check static/js/careerReview.js && node --check static/js/career.js
python -m pytest tests/test_career_*.py tests/test_council_*.py -v
python -m pytest
```
Expected: every suite green; no new failures in the full run.

- [ ] **Step 2: Manual smoke (server running, a CV uploaded, an application with a drafted cover letter)**

1. Open `/career` → Reviewers tab: four cards (Recruiter screener, Hiring manager, Senior engineer, HR / People partner), all Ready.
2. Applications → open the application → "Run panel review". The button reads "Panel in session…", the four cards show Busy and pulse; within one or two polls they flip to verdict chips with hatched score bars; the chair's synthesis appears with Verdict / Consolidated edits / Send-ready checklist; a toast "Panel verdict for …" arrives within 30 s.
3. Expand a reviewer's "Review" — line edits quote phrases from your letter.
4. Reviewers tab → Edit the Senior engineer → change the personality to demand numbers → Save → re-run the panel → the engineer's review reflects the change.
5. Reviewers tab → pin a model on one reviewer → re-run → the server log shows that reviewer's call hitting the pinned model.
6. Open an application with no cover letter → "Run panel review" is disabled; POSTing directly returns 409.
7. `/council` → Members still shows five council members only.

- [ ] **Step 3: Final commit (if anything was touched during smoke)**

```bash
git add -A
git commit -m "chore(career): reviewer panel smoke fixes"
```

---

## Done when

- [ ] `tests/test_career_review_*.py`, `tests/test_career_templates.py` and the council suites pass; full `python -m pytest` has no new failures.
- [ ] Running the panel on an application produces four persisted reviewer verdicts with scores, a chair's synthesis, an aggregate `overall` and `panel_verdict`, and updates `latest_review_id`.
- [ ] Reviews serialise on the career queue; a failing LLM call lands as `status="error"` and leaves the queue empty.
- [ ] Reviewer personas are editable (name, personality, model, endpoint) from the Reviewers tab and never appear in the council office.
- [ ] The application detail view shows Busy → verdict cards with hatched AI score bars, the synthesis, and an "Open letter in editor" link.
