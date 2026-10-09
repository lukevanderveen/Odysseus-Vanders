# Career Hub 01 — Cover Letter Writer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Prerequisites:** [career-00-hub-foundation.md](career-00-hub-foundation.md) must be fully landed (models, settings, personal files, `create_library_document`, the Career hub modal with `registerTab` / `onPanelClick` / `detailSections`). Design: [career-hub-design.md](career-hub-design.md).

**Goal:** From an application's job description, draft a cover letter grounded in the user's CV chunks and their closest prior example letters, open it as a library document in the editor, and show a claims check plus rubric warnings; expose the same flow to the agent through a seeded `cover-letter-writer` skill.

**Architecture:** Two server-side LLM calls (requirements extraction, then the letter) in `services/career/cover_letter.py`, fed by owner-scoped RAG retrieval in `services/career/evidence.py` and the Task-endpoint chain in `services/career/llm.py`. One route in a new `routes/career_studio_routes.py` creates or versions the document via `create_library_document` and stores the claims JSON on disk. The Applications detail view gains a cover-letter section through the `detailSections` registry; the agent path is a SKILL.md calling the same route via `app_api`.

**Tech Stack:** Python 3.11, FastAPI, SQLAlchemy (SQLite), pytest (`asyncio_mode=auto`), vanilla ES modules, Node for JS logic tests.

## Global Constraints

- TDD: write the failing test, run it, watch it fail for the right reason, then implement. Commit after each task.
- Owner scoping on every route: use `routes.career_helpers.get_owned_application(db, aid, user)`; foreign rows are 404.
- Graceful degradation: no RAG → 503; no CV or empty JD → 409 with a sentence the UI can show verbatim. Never raise out of a service function on model output quirks — parse defensively and fall back to empty structures.
- Every generation call goes through `services.career.llm.complete(messages, owner)` so Cookbook / served-model settings apply unchanged. Tests inject a fake `complete`.
- No inline event handlers in HTML (CSP). Plugins hook the Career panel through `onPanelClick` only.
- Keep each new Python module under ~200 lines.
- SKILL.md frontmatter: any value containing `:` must be double-quoted (`services/memory/skill_format.py:_emit_scalar`).
- Interfaces consumed from plan 00 (exact names): `core.database.JobApplication` (`id, owner, company, role, url, jd_text, status, notes, cover_letter_doc_id, latest_review_id`), `core.database.Document` / `DocumentVersion`; `services.career.settings.load_career_settings(owner)` (keys `cv_filename`, `voice_rules`, `target_roles`); `services.career.personal_files.list_personal_files(owner, prefix)`; `routes.career_helpers.get_owned_application(db, aid, user)`, `application_to_dict(app, settings)`; `routes.document_helpers.create_library_document(db, *, owner, title, content, language=None, session_id=None, source="user")`; `services.career.seeds.seed_skills` (copies every folder under `services/career/skills/`); `static/js/career.js` default export with `registerTab`, `onPanelClick(handler)` where `handler(ev, state)` returns `true` when it handled the click, `refresh`, `getState`; `static/js/careerApplications.js` export `detailSections` (array of `(app, state) => html`).

---

### Task 1: Evidence retrieval over the owner's personal files

**Files:**
- Create: `services/career/evidence.py`
- Test: `tests/test_career_evidence.py`

**Interfaces:**
- Consumes: `src.rag_singleton.get_rag_manager()` → `VectorRAG.search(query, k, owner=)` (`src/rag_vector.py:173`), results are dicts `{"document", "metadata": {"filename", ...}, "similarity", ...}`.
- Produces: `cv_chunks(owner, query, k=8, rag=None) -> list[str]`, `example_pairs(owner, jd_text, k=2, rag=None) -> list[str]`, `CV_PREFIX = "cv-"`, `EXAMPLE_PREFIX = "cover-letter-example-"`, `SIMILARITY_THRESHOLD = 0.35`, `FETCH_K = 40`, and `class Evidence` with the same two methods bound to an owner (what the cover-letter service and plan 02 take as `evidence`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_evidence.py
"""Owner-scoped evidence retrieval for cover letters (plan 01)."""
import pytest

from services.career import evidence as ev


class FakeRag:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def search(self, query, k=5, owner=None):
        self.calls.append({"query": query, "k": k, "owner": owner})
        return self.rows


def _row(filename, text, sim):
    return {"document": text, "metadata": {"filename": filename, "owner": "vanders"}, "similarity": sim}


def test_cv_chunks_filters_by_prefix_and_threshold():
    rag = FakeRag([
        _row("cv-luke.pdf", "Built a FastAPI service", 0.9),
        _row("notes.md", "unrelated", 0.95),
        _row("cv-luke.pdf", "weak match", 0.2),
        _row("cover-letter-example-acme.md", "Dear Acme", 0.8),
    ])
    out = ev.cv_chunks("vanders", "fastapi experience", rag=rag)
    assert out == ["Built a FastAPI service"]
    assert rag.calls[0] == {"query": "fastapi experience", "k": ev.FETCH_K, "owner": "vanders"}


def test_cv_chunks_caps_at_k_in_similarity_order():
    rows = [_row("cv-luke.pdf", f"chunk {i}", 0.5 + i / 100) for i in range(12)]
    out = ev.cv_chunks("vanders", "q", k=3, rag=rows and FakeRag(list(reversed(rows))))
    assert out == ["chunk 11", "chunk 10", "chunk 9"]


def test_example_pairs_uses_example_prefix_and_k2():
    rag = FakeRag([
        _row("cover-letter-example-acme.md", "# Acme\n\n## Job description\n\nAPIs\n\n## Cover letter\n\nDear Acme", 0.7),
        _row("cover-letter-example-beta.md", "# Beta ...", 0.6),
        _row("cover-letter-example-gamma.md", "# Gamma ...", 0.5),
        _row("cv-luke.pdf", "cv", 0.99),
    ])
    out = ev.example_pairs("vanders", "Build APIs", rag=rag)
    assert out == ["# Acme\n\n## Job description\n\nAPIs\n\n## Cover letter\n\nDear Acme", "# Beta ..."]


def test_no_rag_returns_empty_lists(monkeypatch):
    monkeypatch.setattr(ev, "get_rag_manager", lambda: None)
    assert ev.cv_chunks("vanders", "q") == []
    assert ev.example_pairs("vanders", "jd") == []


def test_evidence_object_binds_owner():
    rag = FakeRag([_row("cv-luke.pdf", "chunk", 0.8)])
    e = ev.Evidence("vanders", rag=rag)
    assert e.cv_chunks("q") == ["chunk"]
    assert rag.calls[-1]["owner"] == "vanders"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_evidence.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.evidence'`

- [ ] **Step 3: Implement**

```python
# services/career/evidence.py
"""Owner-scoped retrieval of the user's CV and example cover letters.

Both live in the personal-docs RAG index with filename prefixes written by
plan 00 (``cv-*``, ``cover-letter-example-*``). VectorRAG applies no
similarity threshold and ranks across every personal file, so we fetch a
wide window, apply our own threshold, then filter on the prefix.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from src.rag_singleton import get_rag_manager

logger = logging.getLogger(__name__)

CV_PREFIX = "cv-"
EXAMPLE_PREFIX = "cover-letter-example-"
SIMILARITY_THRESHOLD = 0.35
FETCH_K = 40


def _search(owner: Optional[str], query: str, prefix: str, k: int, rag) -> List[str]:
    rag = rag if rag is not None else get_rag_manager()
    if rag is None:
        logger.warning("DEGRADED: RAG unavailable; no %s evidence for %s", prefix, owner)
        return []
    try:
        rows = rag.search(query, k=FETCH_K, owner=owner)
    except Exception:
        logger.warning("DEGRADED: evidence search failed", exc_info=True)
        return []
    keep = []
    for r in rows or []:
        name = str((r.get("metadata") or {}).get("filename") or "")
        if not name.startswith(prefix):
            continue
        if float(r.get("similarity") or 0.0) < SIMILARITY_THRESHOLD:
            continue
        keep.append((float(r.get("similarity") or 0.0), r.get("document") or ""))
    keep.sort(key=lambda t: t[0], reverse=True)
    return [doc for _, doc in keep[:k] if doc]


def cv_chunks(owner: Optional[str], query: str, k: int = 8, rag=None) -> List[str]:
    return _search(owner, query, CV_PREFIX, k, rag)


def example_pairs(owner: Optional[str], jd_text: str, k: int = 2, rag=None) -> List[str]:
    return _search(owner, jd_text, EXAMPLE_PREFIX, k, rag)


class Evidence:
    """Owner-bound facade injected into cover_letter / review services."""

    def __init__(self, owner: Optional[str], rag=None):
        self.owner = owner
        self._rag = rag

    def cv_chunks(self, query: str, k: int = 8) -> List[str]:
        return cv_chunks(self.owner, query, k=k, rag=self._rag)

    def example_pairs(self, jd_text: str, k: int = 2) -> List[str]:
        return example_pairs(self.owner, jd_text, k=k, rag=self._rag)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_evidence.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add services/career/evidence.py tests/test_career_evidence.py
git commit -m "feat(career): owner-scoped CV and example-letter evidence retrieval"
```

---

### Task 2: Shared LLM completion helper — provided by plan 00

No work in this plan. `services/career/llm.py` (`complete`, `complete_utility`, `render_template`) and `tests/test_career_llm.py` ship in `career-00-hub-foundation.md` Task 2b. Later tasks here import `complete` and `render_template` from `services.career.llm`.

---

### Task 3: Prompt templates and the cover-letter service

**Files:**
- Create: `services/career/prompts/requirements.md`
- Create: `services/career/prompts/cover_letter.md`
- Create: `services/career/cover_letter.py`
- Test: `tests/test_career_cover_letter.py`

**Interfaces:**
- Consumes: Task 1 `Evidence`, Task 2 `complete` + `render_template`, `services.career.settings.load_career_settings(owner)`.
- Produces: `load_prompt(name) -> str` (user override at `data/career/prompts/<name>.md`, else packaged); `async extract_requirements(jd_text, complete, owner) -> {"hard": list[str], "nice": list[str]}`; `async draft_cover_letter(app, owner, complete, evidence, requirements=None) -> tuple[str, list[dict]]` returning `(markdown, claims)` where each claim is `{"claim": str, "evidence": str}`; `rubric_check(markdown, hard_requirements) -> list[str]`; `split_claims(text) -> (body, claims)`; `BANNED_PHRASES`, `MAX_WORDS = 300`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_cover_letter.py
"""Cover-letter service: requirement extraction, drafting, claims split, rubric (plan 01)."""
import json
from types import SimpleNamespace

import pytest

from services.career import cover_letter as cl


def _app(**over):
    base = dict(id="a1", owner="vanders", company="Acme", role="Backend Engineer",
                jd_text="We need Python, FastAPI, PostgreSQL. Nice: Kubernetes.")
    base.update(over)
    return SimpleNamespace(**base)


class FakeEvidence:
    def __init__(self, cv=None, examples=None):
        self._cv = cv if cv is not None else ["Built FastAPI services at Beta Ltd (2023-2025)."]
        self._ex = examples if examples is not None else ["# Beta\n\n## Cover letter\n\nDear Beta, short and plain."]
        self.queries = []

    def cv_chunks(self, query, k=8):
        self.queries.append(query)
        return self._cv

    def example_pairs(self, jd_text, k=2):
        return self._ex


def _complete_returning(*outputs):
    calls = []

    async def complete(messages, owner=None):
        calls.append(messages)
        return outputs[len(calls) - 1] if len(calls) <= len(outputs) else outputs[-1]

    complete.calls = calls
    return complete


# ── requirements ─────────────────────────────────────────────────────────────

async def test_extract_requirements_parses_clean_json():
    complete = _complete_returning('{"hard": ["Python", "FastAPI", "PostgreSQL"], "nice": ["Kubernetes"]}')
    out = await cl.extract_requirements("jd", complete, "vanders")
    assert out == {"hard": ["Python", "FastAPI", "PostgreSQL"], "nice": ["Kubernetes"]}
    assert "jd" in complete.calls[0][-1]["content"]


async def test_extract_requirements_survives_fenced_and_chatty_output():
    complete = _complete_returning('Sure!\n```json\n{"hard": ["Python"], "nice": []}\n```\nHope that helps.')
    assert await cl.extract_requirements("jd", complete, None) == {"hard": ["Python"], "nice": []}


async def test_extract_requirements_bad_output_gives_empty_lists():
    complete = _complete_returning("I cannot do that")
    assert await cl.extract_requirements("jd", complete, None) == {"hard": [], "nice": []}


async def test_extract_requirements_caps_counts_and_coerces_strings():
    complete = _complete_returning(json.dumps({"hard": [1, 2, 3, 4, 5, 6, 7], "nice": ["a", "b", "c", "d"]}))
    out = await cl.extract_requirements("jd", complete, None)
    assert out["hard"] == ["1", "2", "3", "4", "5"] and out["nice"] == ["a", "b", "c"]


# ── claims split ─────────────────────────────────────────────────────────────

def test_split_claims_strips_trailing_fence():
    text = 'Dear Acme,\n\nI built FastAPI services.\n\n```json\n{"claims": [{"claim": "built FastAPI services", "evidence": "Beta Ltd 2023-2025"}]}\n```\n'
    body, claims = cl.split_claims(text)
    assert body == "Dear Acme,\n\nI built FastAPI services."
    assert claims == [{"claim": "built FastAPI services", "evidence": "Beta Ltd 2023-2025"}]


def test_split_claims_without_fence_returns_whole_body():
    body, claims = cl.split_claims("Dear Acme,\n\nplain letter\n")
    assert body == "Dear Acme,\n\nplain letter" and claims == []


# ── draft ────────────────────────────────────────────────────────────────────

async def test_draft_uses_cv_examples_voice_and_requirements():
    complete = _complete_returning(
        'Dear Acme,\n\nI built FastAPI services for Beta Ltd.\n\n```json\n{"claims": [{"claim": "built FastAPI services", "evidence": "Beta Ltd"}]}\n```')
    evidence = FakeEvidence()
    body, claims = await cl.draft_cover_letter(
        _app(), "vanders", complete, evidence,
        requirements={"hard": ["Python", "FastAPI"], "nice": ["Kubernetes"]},
        settings={"voice_rules": "plain, first person", "target_roles": ["backend"]},
    )
    prompt = complete.calls[0][-1]["content"]
    assert "Built FastAPI services at Beta Ltd" in prompt      # CV evidence
    assert "Dear Beta, short and plain." in prompt             # example letter
    assert "plain, first person" in prompt                     # voice rules
    assert "- Python" in prompt and "- FastAPI" in prompt      # requirements
    assert "Acme" in prompt and "Backend Engineer" in prompt
    assert body.startswith("Dear Acme,")
    assert claims[0]["evidence"] == "Beta Ltd"
    assert "Python FastAPI" in evidence.queries[0]              # CV queried by requirements


async def test_draft_without_requirements_extracts_them_first():
    complete = _complete_returning('{"hard": ["Go"], "nice": []}', "Dear Acme,\n\nletter")
    body, claims = await cl.draft_cover_letter(_app(), "vanders", complete, FakeEvidence(), settings={})
    assert len(complete.calls) == 2 and body == "Dear Acme,\n\nletter" and claims == []


async def test_draft_raises_without_cv_evidence():
    with pytest.raises(cl.NoEvidenceError):
        await cl.draft_cover_letter(_app(), "vanders", _complete_returning("x"), FakeEvidence(cv=[]),
                                    requirements={"hard": [], "nice": []}, settings={})


# ── rubric ───────────────────────────────────────────────────────────────────

def test_rubric_flags_length_banned_phrases_and_missing_specifics():
    long = "word " * 301
    warnings = cl.rubric_check(long + " I am passionate about this. I am writing to apply.", ["FastAPI", "PostgreSQL"])
    assert any("300 words" in w for w in warnings)
    assert any("passionate" in w for w in warnings)
    assert any("I am writing to apply" in w for w in warnings)
    assert any("FastAPI" in w and "PostgreSQL" in w for w in warnings)


def test_rubric_passes_a_clean_letter():
    letter = "Dear Acme,\n\nI have shipped FastAPI services backed by PostgreSQL for three years.\n\nLuke"
    assert cl.rubric_check(letter, ["FastAPI", "PostgreSQL", "Python"]) == []
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_cover_letter.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'services.career.cover_letter'`

- [ ] **Step 3: Write the prompt templates**

`services/career/prompts/requirements.md`:

```markdown
You extract hiring requirements from a job description for a software engineering role.

Return ONLY a JSON object, no prose, in exactly this shape:
{"hard": ["<up to 5 must-have requirements, each a short phrase naming a skill, technology, experience level or domain>"],
 "nice": ["<up to 3 nice-to-have requirements>"]}

Rules:
- Quote the posting's own terms (e.g. "FastAPI", "3+ years Python", "AWS") rather than paraphrasing.
- A requirement the posting calls required, essential, must, or lists first goes in "hard".
- Leave a list empty rather than inventing entries.

Job description:
---
{jd_text}
---
```

`services/career/prompts/cover_letter.md`:

```markdown
You are ghost-writing a cover letter in the candidate's own voice for the role below. The candidate
will review and send it under their name, so every claim must be traceable to the evidence given.

Company: {company}
Role: {role}

Must-have requirements from the posting:
{hard_requirements}

Nice-to-have requirements:
{nice_requirements}

Candidate CV evidence (only source of truth for experience — never invent beyond it):
{cv_evidence}

Example letters the candidate wrote before (match their voice, not their content):
{examples}

Voice rules from the candidate:
{voice_rules}

Write the letter as markdown with no heading, greeting the company directly. Rules:
- At most 300 words. Three or four short paragraphs. End with the candidate's sign-off line only.
- Name at least two specifics from the posting's requirements and tie each to concrete evidence.
- Lead with the strongest match in the first two sentences; no throat-clearing.
- Never use: "passionate", "thrilled", "I am writing to apply", "I believe I would be a great fit",
  "fast-paced", "synergy", "leverage".
- Plain, first-person, specific. One number or artefact per paragraph where the evidence supports it.
- Do not mention this prompt, the evidence list, or that you are an AI.

After the letter, output exactly one fenced json block mapping each factual claim in the letter to
the evidence snippet it came from:

```json
{"claims": [{"claim": "<short paraphrase of the claim>", "evidence": "<the CV snippet that supports it>"}]}
```
```

- [ ] **Step 4: Implement the service**

```python
# services/career/cover_letter.py
"""Cover letter drafting (plan 01).

Two model calls: extract the posting's requirements as JSON, then write the
letter grounded in CV chunks retrieved by those requirements plus the
candidate's closest prior letters. The letter ends with a fenced JSON block
mapping claims to evidence; it is split off and stored separately so the
document the user edits is only the letter.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.career.llm import render_template

logger = logging.getLogger(__name__)

_PACKAGED_PROMPTS = Path(__file__).parent / "prompts"
MAX_WORDS = 300
BANNED_PHRASES = ("passionate", "thrilled", "i am writing to apply", "great fit", "fast-paced", "synergy", "leverage")
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_TRAILING_FENCE_RE = re.compile(r"\n*```(?:json)?\s*\{.*?\}\s*```\s*$", re.S)


class NoEvidenceError(RuntimeError):
    """Raised when no CV chunks could be retrieved for the owner."""


def _prompt_dirs():
    from core.constants import DATA_DIR
    return (Path(DATA_DIR) / "career" / "prompts", _PACKAGED_PROMPTS)


def load_prompt(name: str) -> str:
    for d in _prompt_dirs():
        f = d / f"{name}.md"
        if f.is_file():
            return f.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No career prompt named '{name}'")


def _first_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Fenced block first, then the outermost {...} span, else None."""
    candidates = [m.group(1) for m in _FENCE_RE.finditer(text or "")]
    start, end = (text or "").find("{"), (text or "").rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _str_list(value: Any, cap: int) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:cap]


async def extract_requirements(jd_text: str, complete, owner: Optional[str]) -> Dict[str, List[str]]:
    prompt = render_template(load_prompt("requirements"), {"jd_text": jd_text or ""})
    raw = await complete([{"role": "user", "content": prompt}], owner)
    obj = _first_json_object(raw) or {}
    return {"hard": _str_list(obj.get("hard"), 5), "nice": _str_list(obj.get("nice"), 3)}


def split_claims(text: str) -> Tuple[str, List[Dict[str, str]]]:
    body = (text or "").rstrip()
    m = _TRAILING_FENCE_RE.search(body)
    claims: List[Dict[str, str]] = []
    if m:
        obj = _first_json_object(m.group(0)) or {}
        for c in obj.get("claims") or []:
            if isinstance(c, dict) and c.get("claim"):
                claims.append({"claim": str(c.get("claim")), "evidence": str(c.get("evidence") or "")})
        body = body[:m.start()].rstrip()
    return body, claims


def _bullets(items: List[str]) -> str:
    return "\n".join(f"- {i}" for i in items) or "- (none listed)"


async def draft_cover_letter(app, owner: Optional[str], complete, evidence,
                             requirements: Optional[Dict[str, List[str]]] = None,
                             settings: Optional[Dict[str, Any]] = None) -> Tuple[str, List[Dict[str, str]]]:
    if settings is None:
        from services.career.settings import load_career_settings
        settings = load_career_settings(owner)
    if requirements is None:
        requirements = await extract_requirements(app.jd_text or "", complete, owner)
    query = " ".join(requirements.get("hard", []) + requirements.get("nice", [])) or f"{app.role} experience"
    cv = evidence.cv_chunks(query)
    if not cv:
        raise NoEvidenceError("No CV text found — upload a CV in Career → Setup first.")
    examples = evidence.example_pairs(app.jd_text or "")
    prompt = render_template(load_prompt("cover_letter"), {
        "company": app.company or "",
        "role": app.role or "",
        "hard_requirements": _bullets(requirements.get("hard", [])),
        "nice_requirements": _bullets(requirements.get("nice", [])),
        "cv_evidence": "\n\n".join(f"- {c}" for c in cv),
        "examples": "\n\n---\n\n".join(examples) or "(none yet)",
        "voice_rules": (settings.get("voice_rules") or "").strip() or "(none given — plain and specific)",
    })
    raw = await complete([{"role": "user", "content": prompt}], owner)
    return split_claims(raw)


def rubric_check(markdown: str, hard_requirements: List[str]) -> List[str]:
    warnings: List[str] = []
    text = markdown or ""
    words = len(text.split())
    if words > MAX_WORDS:
        warnings.append(f"Letter is {words} words; keep it under {MAX_WORDS} words.")
    low = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase in low:
            warnings.append(f'Remove the phrase "{phrase}".')
    reqs = [r for r in hard_requirements if r.strip()]
    hit = [r for r in reqs if r.lower() in low]
    if len(reqs) >= 2 and len(hit) < 2:
        missing = ", ".join(r for r in reqs if r not in hit)
        warnings.append(f"Name at least two posting specifics; not mentioned: {missing}.")
    return warnings
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_career_cover_letter.py -v`
Expected: 11 passed

- [ ] **Step 6: Commit**

```bash
git add services/career/prompts services/career/cover_letter.py tests/test_career_cover_letter.py
git commit -m "feat(career): cover letter drafting service with claims split and rubric"
```

---

### Task 4: Cover-letter route in the studio router

**Files:**
- Create: `routes/career_studio_routes.py`
- Create: `services/career/claims_store.py`
- Modify: `app.py` (mount directly after `setup_career_routes`)
- Test: `tests/test_career_cover_letter_routes.py`

**Interfaces:**
- Consumes: `core.database.JobApplication`, `Document`, `DocumentVersion`, `SessionLocal`; `routes.career_helpers.get_owned_application`, `application_to_dict`; `routes.document_helpers.create_library_document`; `services.career.settings.load_career_settings`; Task 1 `Evidence`; Task 2 `complete`; Task 3 service.
- Produces: `setup_career_studio_routes(rag_manager) -> APIRouter` (prefix `/api/career`; plans 02 and 03 add their endpoints inside this same factory), `services.career.claims_store.save_claims(owner, application_id, payload)` / `load_claims(owner, application_id) -> dict | None`, and `upsert_cover_letter_document(db, app, owner, markdown) -> (doc_id, version)`.

Routes:

| Method | Path | Returns |
|---|---|---|
| POST | `/applications/{id}/cover-letter` | `{doc_id, version, claims, rubric_warnings, requirements}`; 409 no CV / no JD / no evidence; 503 no RAG |
| GET | `/applications/{id}/cover-letter` | same shape from disk, or `{doc_id: null, claims: [], rubric_warnings: [], requirements: {}}` |

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_cover_letter_routes.py
"""POST/GET /api/career/applications/{id}/cover-letter (plan 01)."""
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


_sa, _sa_orm, _sa_pool, _coredb, sr, cs, claims_store = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "routes.career_studio_routes", "services.career.settings", "services.career.claims_store",
    force=("core.database", "routes.career_studio_routes", "routes.document_helpers"),
)
JobApplication = _coredb.JobApplication
Document = _coredb.Document
DocumentVersion = _coredb.DocumentVersion

_LETTER = 'Dear Acme,\n\nI built FastAPI services backed by PostgreSQL.\n\n```json\n{"claims": [{"claim": "built FastAPI services", "evidence": "Beta Ltd"}]}\n```'


class FakeRag:
    def search(self, query, k=5, owner=None):
        return [{"document": "Built FastAPI services at Beta Ltd", "similarity": 0.9,
                 "metadata": {"filename": "cv-luke.pdf", "owner": owner}}]


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    TestSession = _sa_orm.sessionmaker(bind=engine)
    monkeypatch.setattr(sr, "SessionLocal", TestSession)
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(claims_store, "DATA_DIR", str(tmp_path))
    current = {"user": "vanders"}
    monkeypatch.setattr(sr, "get_current_user", lambda request: current["user"])
    outputs = ['{"hard": ["FastAPI", "PostgreSQL"], "nice": []}', _LETTER]
    calls = []

    async def fake_complete(messages, owner=None):
        calls.append(messages)
        return outputs[(len(calls) - 1) % 2]

    monkeypatch.setattr(sr, "complete", fake_complete)
    cs.save_career_settings("vanders", {"cv_filename": "cv-luke.pdf"})

    db = TestSession()
    db.add(JobApplication(id="a1", owner="vanders", company="Acme", role="Backend",
                          jd_text="Need FastAPI and PostgreSQL"))
    db.add(JobApplication(id="nojd", owner="vanders", company="Acme", role="Backend"))
    db.add(JobApplication(id="a2", owner="other", company="Theirs", role="Dev", jd_text="x"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(sr.setup_career_studio_routes(FakeRag()))
    return TestClient(app), TestSession, calls


def test_draft_creates_document_claims_and_warnings(env):
    client, TestSession, calls = env
    res = client.post("/api/career/applications/a1/cover-letter")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] == 1
    assert body["claims"] == [{"claim": "built FastAPI services", "evidence": "Beta Ltd"}]
    assert body["requirements"] == {"hard": ["FastAPI", "PostgreSQL"], "nice": []}
    assert body["rubric_warnings"] == []
    assert len(calls) == 2
    db = TestSession()
    doc = db.get(Document, body["doc_id"])
    assert doc.owner == "vanders" and doc.language == "markdown" and doc.session_id is None
    assert "```json" not in doc.current_content and doc.current_content.startswith("Dear Acme,")
    assert doc.title == "Cover letter — Acme Backend"
    assert db.get(JobApplication, "a1").cover_letter_doc_id == doc.id
    db.close()
    got = client.get("/api/career/applications/a1/cover-letter").json()
    assert got["doc_id"] == body["doc_id"] and got["claims"] == body["claims"]


def test_second_draft_versions_the_same_document(env):
    client, TestSession, _ = env
    first = client.post("/api/career/applications/a1/cover-letter").json()
    second = client.post("/api/career/applications/a1/cover-letter").json()
    assert second["doc_id"] == first["doc_id"] and second["version"] == 2
    db = TestSession()
    assert db.query(Document).count() == 1
    assert db.query(DocumentVersion).filter_by(document_id=first["doc_id"]).count() == 2
    assert db.get(Document, first["doc_id"]).version_count == 2
    db.close()


def test_409_without_cv_or_jd(env, monkeypatch):
    client, _, _ = env
    assert client.post("/api/career/applications/nojd/cover-letter").status_code == 409
    cs.save_career_settings("vanders", {"cv_filename": ""})
    res = client.post("/api/career/applications/a1/cover-letter")
    assert res.status_code == 409 and "Setup" in res.json()["detail"]


def test_409_when_cv_has_no_retrievable_chunks(monkeypatch, env):
    client, _, _ = env

    class EmptyRag:
        def search(self, query, k=5, owner=None):
            return []

    app = FastAPI()
    app.include_router(sr.setup_career_studio_routes(EmptyRag()))
    assert TestClient(app).post("/api/career/applications/a1/cover-letter").status_code == 409


def test_503_without_rag(env):
    app = FastAPI()
    app.include_router(sr.setup_career_studio_routes(None))
    assert TestClient(app).post("/api/career/applications/a1/cover-letter").status_code == 503


def test_foreign_application_is_404(env):
    client, _, _ = env
    assert client.post("/api/career/applications/a2/cover-letter").status_code == 404
    assert client.get("/api/career/applications/a2/cover-letter").status_code == 404


def test_get_before_any_draft_is_empty(env):
    client, _, _ = env
    assert client.get("/api/career/applications/a1/cover-letter").json() == {
        "doc_id": None, "version": 0, "claims": [], "rubric_warnings": [], "requirements": {}}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_cover_letter_routes.py -v`
Expected: FAIL with `ModuleNotFoundError: No module named 'routes.career_studio_routes'`

- [ ] **Step 3: Implement the claims store**

```python
# services/career/claims_store.py
"""Claims-check JSON per application at data/career/<owner>/claims/<application_id>.json.

Kept on disk rather than as a column so plan 01 needs no schema change; the
document the user edits holds only the letter.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from core.constants import DATA_DIR
from src.upload_handler import secure_filename


def _path(owner: Optional[str], application_id: str) -> str:
    segment = secure_filename((owner or "local").strip())[:80] or "local"
    aid = secure_filename(application_id)[:64] or "unknown"
    return os.path.join(DATA_DIR, "career", segment, "claims", f"{aid}.json")


def save_claims(owner: Optional[str], application_id: str, payload: Dict[str, Any]) -> str:
    path = _path(owner, application_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    from core.atomic_io import atomic_write_json
    atomic_write_json(path, payload, indent=2)
    return path


def load_claims(owner: Optional[str], application_id: str) -> Optional[Dict[str, Any]]:
    path = _path(owner, application_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None
```

- [ ] **Step 4: Implement the router**

```python
# routes/career_studio_routes.py
"""Career hub studio API — generation endpoints.

Plan 01: cover letters. Plans 02 (reviews/reviewers) and 03 (activity/posts)
add their endpoints inside setup_career_studio_routes so app.py mounts one
router. Owner-scoped via routes.career_helpers.get_owned_application.
"""
from __future__ import annotations

import logging
import uuid
from typing import Any, Dict, Optional, Tuple

from fastapi import APIRouter, HTTPException, Request

from core.database import Document, DocumentVersion, JobApplication, SessionLocal
from routes.career_helpers import get_owned_application
from routes.document_helpers import create_library_document
from services.career.claims_store import load_claims, save_claims
from services.career.cover_letter import NoEvidenceError, draft_cover_letter, extract_requirements, rubric_check
from services.career.evidence import Evidence
from services.career.llm import complete
from services.career.settings import load_career_settings
from src.auth_helpers import get_current_user

logger = logging.getLogger(__name__)


def upsert_cover_letter_document(db, app: JobApplication, owner: Optional[str], markdown: str) -> Tuple[str, int]:
    """Create the library document on first draft; add a version on later drafts."""
    title = f"Cover letter — {app.company} {app.role}"
    doc = db.get(Document, app.cover_letter_doc_id) if app.cover_letter_doc_id else None
    if doc is None:
        doc = create_library_document(db, owner=owner, title=title, content=markdown, language="markdown")
        app.cover_letter_doc_id = doc.id
        db.commit()
        return doc.id, 1
    new_ver = (doc.version_count or 1) + 1
    doc.current_content = markdown
    doc.version_count = new_ver
    db.add(DocumentVersion(id=str(uuid.uuid4()), document_id=doc.id, version_number=new_ver,
                           content=markdown, summary="Regenerated cover letter", source="ai"))
    db.commit()
    return doc.id, new_ver


def _empty_payload() -> Dict[str, Any]:
    return {"doc_id": None, "version": 0, "claims": [], "rubric_warnings": [], "requirements": {}}


def setup_career_studio_routes(rag_manager) -> APIRouter:
    router = APIRouter(prefix="/api/career", tags=["career"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    @router.post("/applications/{aid}/cover-letter")
    async def draft_letter(aid: str, request: Request):
        user = _owner(request)
        if rag_manager is None:
            raise HTTPException(503, "RAG is not available — the embedding service is required to read your CV")
        settings = load_career_settings(user)
        if not settings.get("cv_filename"):
            raise HTTPException(409, "No CV on file — upload one in Career → Setup first.")
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            if not (app.jd_text or "").strip():
                raise HTTPException(409, "This application has no job description — paste one or give a posting URL.")
            requirements = await extract_requirements(app.jd_text, complete, user)
            try:
                markdown, claims = await draft_cover_letter(
                    app, user, complete, Evidence(user, rag=rag_manager),
                    requirements=requirements, settings=settings)
            except NoEvidenceError as e:
                raise HTTPException(409, str(e))
            warnings = rubric_check(markdown, requirements.get("hard", []))
            doc_id, version = upsert_cover_letter_document(db, app, user, markdown)
            payload = {"doc_id": doc_id, "version": version, "claims": claims,
                       "rubric_warnings": warnings, "requirements": requirements}
            save_claims(user, aid, payload)
            return payload
        finally:
            db.close()

    @router.get("/applications/{aid}/cover-letter")
    def get_letter(aid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            app = get_owned_application(db, aid, user)
            stored = load_claims(user, aid) or _empty_payload()
            stored["doc_id"] = app.cover_letter_doc_id
            return stored
        finally:
            db.close()

    return router
```

Mount in `app.py` directly after the `setup_career_routes` line added by plan 00:

```python
from routes.career_studio_routes import setup_career_studio_routes
app.include_router(setup_career_studio_routes(rag_manager))
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_career_cover_letter_routes.py tests/test_document_library_create.py -v`
Expected: all passed

- [ ] **Step 6: Commit**

```bash
git add routes/career_studio_routes.py services/career/claims_store.py app.py tests/test_career_cover_letter_routes.py
git commit -m "feat(career): cover-letter route creating/versioning a library document"
```

---

### Task 5: Seeded `cover-letter-writer` skill

**Files:**
- Create: `services/career/skills/cover-letter-writer/SKILL.md`
- Test: `tests/test_career_cover_letter_skill.py`

**Interfaces:**
- Consumes: `services.career.seeds.seed_skills` (plan 00) copies this folder to `data/skills/career/cover-letter-writer/` on bootstrap; `services.memory.skill_format.Skill.from_markdown`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_cover_letter_skill.py
"""Packaged cover-letter-writer skill parses and points the agent at the hub routes."""
from pathlib import Path

from services.memory.skill_format import Skill

_SKILL = Path("services/career/skills/cover-letter-writer/SKILL.md")


def test_skill_parses_with_expected_frontmatter():
    sk = Skill.from_markdown(_SKILL.read_text(encoding="utf-8"), path=str(_SKILL))
    assert sk.name == "cover-letter-writer"
    assert sk.category == "career"
    assert sk.status == "published"
    assert sk.source == "user"
    assert sk.when_to_use
    assert len(sk.procedure) >= 5
    assert sk.pitfalls and sk.verification


def test_skill_names_the_routes_and_constraints():
    text = _SKILL.read_text(encoding="utf-8")
    assert "POST /api/career/applications" in text
    assert "/cover-letter" in text
    assert "app_api" in text
    assert "#document-" in text
    assert "admin" in text.lower()
    assert "Setup" in text
    assert "never invent" in text.lower()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_cover_letter_skill.py -v`
Expected: FAIL with `FileNotFoundError`

- [ ] **Step 3: Write the skill**

`services/career/skills/cover-letter-writer/SKILL.md`:

```markdown
---
name: cover-letter-writer
description: "Draft a cover letter for a job description, grounded in my CV, via the Career hub; shows a claims check."
version: 1.0.0
category: career
tags: [jobs, cover-letter, career, writing]
status: published
confidence: 0.9
source: user
---

## When to Use

When I ask for a cover letter, to tailor an application to a job description, or paste a job posting and ask what to send. Also triggered by the /cover-letter-writer slash command.

## Procedure

1. Find or create the application. Call `app_api` with POST /api/career/applications and body {"company": "...", "role": "...", "jd_text": "<pasted description>", "jd_url": "<posting URL if given>"}. If I named an existing application, GET /api/career/applications and pick the matching id instead.
2. Draft the letter: `app_api` POST /api/career/applications/{id}/cover-letter with no body. It returns doc_id, version, claims, rubric_warnings and requirements.
3. If the call returns 409 "No CV on file", stop and tell me to upload my CV in Career → Setup; do not write a letter from memory.
4. If it returns 409 about a missing job description, ask me to paste the posting text or give the URL, then retry from step 1.
5. Fetch the letter text with GET /api/document/{doc_id} and show it to me in full, followed by the claims check as a table (claim → evidence) and any rubric_warnings as a short list.
6. Link the editable document as (#document-{doc_id}) so I can open it in the editor.
7. If I ask for changes, explain them in one line and call POST /api/career/applications/{id}/cover-letter again (it creates a new version of the same document), or edit the document directly with update_document when the change is small.

## Pitfalls

- Never invent experience, employers, dates or technologies. Every claim must appear in the claims check with CV evidence; if a claim has none, remove the sentence.
- The app_api tool is admin-only. If it is unavailable, tell me to run the draft from the Career hub's Applications tab instead of writing the letter yourself.
- A job description is required: paste it or give a URL. Do not guess requirements from a job title.
- Do not pad: the letter is at most 300 words and must not contain "passionate", "thrilled" or "I am writing to apply".

## Verification

- Letter is under 300 words and names at least two specifics from the posting.
- Every factual claim maps to a CV evidence snippet in the claims check.
- rubric_warnings is empty, or each warning was addressed in a regenerated version.
- The response links #document-{doc_id} so the letter opens in the editor.
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_career_cover_letter_skill.py tests/test_career_skill_seeding.py -v`
Expected: all passed

- [ ] **Step 5: Commit**

```bash
git add services/career/skills/cover-letter-writer/SKILL.md tests/test_career_cover_letter_skill.py
git commit -m "feat(career): seeded cover-letter-writer skill"
```

---

### Task 6: Front-end — cover letter section in the application detail view

**Files:**
- Create: `static/js/careerCoverLetterLogic.js`
- Create: `static/js/careerCoverLetter.js`
- Modify: `static/js/career.js` (one import line + one install line)
- Modify: `static/style.css` (append to the Career hub section)
- Test: `tests/test_career_cover_letter_js.py`

**Interfaces:**
- Consumes: `careerApplications.js` export `detailSections` (push `(app, state) => html`); `career.js` default export `onPanelClick(handler)`, `refresh()`, `getState()`; `uiModule.esc`, `showToast`, `showError`.
- Produces: `careerCoverLetterLogic.js` exports `claimsHtml(claims, warnings, esc)` and `letterSectionHtml(app, letter, busy, esc)`; `careerCoverLetter.js` exports `installCoverLetter(api)` (plan 00 plugin contract: registers the detail section and click handler; never imports `career.js`) and `loadLetter(appId)` for plan 02 to reuse.

State kept inside `careerCoverLetter.js`: `_letters: Map<appId, payload>` (fetched lazily per opened application) and `_busy: Set<appId>`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_career_cover_letter_js.py
"""Cover-letter UI: pure helpers under Node + source wiring assertions."""
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


_ESC = "const esc = (s) => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');"


def test_claims_html_lists_claims_and_warnings(node_available):
    out = _run_node(textwrap.dedent(f"""
        const {{ claimsHtml }} = await import('./static/js/careerCoverLetterLogic.js');
        {_ESC}
        const html = claimsHtml([{{ claim: 'built <APIs>', evidence: 'Beta Ltd' }}], ['Too long'], esc);
        const empty = claimsHtml([], [], esc);
        console.log(JSON.stringify({{ html, empty }}));
    """))
    assert "built &lt;APIs&gt;" in out["html"] and "Beta Ltd" in out["html"]
    assert 'class="career-claim"' in out["html"]
    assert "career-rubric-warning" in out["html"] and "Too long" in out["html"]
    assert "No claims check yet" in out["empty"]


def test_letter_section_states(node_available):
    out = _run_node(textwrap.dedent(f"""
        const {{ letterSectionHtml }} = await import('./static/js/careerCoverLetterLogic.js');
        {_ESC}
        const app = {{ id: 'a1', cover_letter_doc_id: null }};
        const none = letterSectionHtml(app, null, false, esc);
        const busy = letterSectionHtml(app, null, true, esc);
        const done = letterSectionHtml({{ id: 'a1', cover_letter_doc_id: 'd1' }},
          {{ doc_id: 'd1', version: 2, claims: [], rubric_warnings: [] }}, false, esc);
        console.log(JSON.stringify({{ none, busy, done }}));
    """))
    assert 'data-action="draft-cover-letter"' in out["none"] and "Draft cover letter" in out["none"]
    assert "disabled" in out["busy"] and "Drafting" in out["busy"]
    assert 'href="#document-d1"' in out["done"] and "Regenerate" in out["done"] and "v2" in out["done"]


def test_wiring_and_syntax(node_available):
    career = (_REPO / "static" / "js" / "career.js").read_text(encoding="utf-8")
    assert "import { installCoverLetter } from './careerCoverLetter.js';" in career
    assert "installCoverLetter(careerApi);" in career
    plugin = (_REPO / "static" / "js" / "careerCoverLetter.js").read_text(encoding="utf-8")
    assert "export const installCoverLetter" in plugin
    assert "detailSections.push(" in plugin
    assert "api.onPanelClick(" in plugin
    assert "from './career.js'" not in plugin
    assert "/cover-letter" in plugin
    for f in ("careerCoverLetter.js", "careerCoverLetterLogic.js", "career.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_career_cover_letter_js.py -v`
Expected: FAIL with `Cannot find module` for `careerCoverLetterLogic.js`

- [ ] **Step 3: `static/js/careerCoverLetterLogic.js`**

```javascript
// ============================================
// Career hub — cover letter section, pure html builders (Node-testable)
// ============================================

export const claimsHtml = (claims, warnings, esc) => {
  const rows = (claims || []).map((c) => `
    <li class="career-claim"><strong>${esc(c.claim)}</strong>
      <span class="memory-desc">${c.evidence ? esc(c.evidence) : 'no evidence given'}</span></li>`).join('');
  const warns = (warnings || []).map((w) => `<li class="career-rubric-warning">${esc(w)}</li>`).join('');
  return `
    <div class="career-claims">
      <h4 class="cc-section">Claims check</h4>
      ${rows ? `<ul class="career-claim-list">${rows}</ul>` : '<p class="memory-desc">No claims check yet.</p>'}
      ${warns ? `<ul class="career-rubric">${warns}</ul>` : ''}
    </div>`;
};

// letter: payload from GET/POST /api/career/applications/{id}/cover-letter, or null.
export const letterSectionHtml = (app, letter, busy, esc) => {
  const docId = (letter && letter.doc_id) || app.cover_letter_doc_id;
  const version = letter && letter.version ? ` <span class="proj-badge">v${esc(String(letter.version))}</span>` : '';
  const label = busy ? 'Drafting…' : docId ? 'Regenerate' : 'Draft cover letter';
  return `
    <div class="career-letter" data-application-id="${esc(app.id)}">
      <h3 class="cc-section">Cover letter${version}</h3>
      <div class="council-run-form">
        <button class="memory-toolbar-btn" data-action="draft-cover-letter" ${busy ? 'disabled' : ''}>${label}</button>
        ${docId ? `<a class="memory-toolbar-btn" href="#document-${esc(docId)}">Open in editor</a>` : ''}
      </div>
      ${docId ? claimsHtml(letter ? letter.claims : [], letter ? letter.rubric_warnings : [], esc)
        : '<p class="memory-desc">Drafts from your CV and the job description, then opens in the editor.</p>'}
    </div>`;
};
```

- [ ] **Step 4: `static/js/careerCoverLetter.js`**

```javascript
// ============================================
// Career hub — cover letter plugin for the Applications detail view (plan 01)
// Plugs into career.js through installCoverLetter(api); never imports career.js.
// ============================================
import uiModule from './ui.js';
import { detailSections } from './careerApplications.js';
import { letterSectionHtml } from './careerCoverLetterLogic.js';

const esc = uiModule.esc;
let _api = null;
const _letters = new Map();   // application id -> last payload
const _busy = new Set();

export const loadLetter = async (appId) => {
  const res = await fetch(`/api/career/applications/${appId}/cover-letter`);
  const data = await res.json().catch(() => null);
  if (res.ok && data) _letters.set(appId, data);
  return _letters.get(appId) || null;
};

const _draft = async (appId) => {
  if (_busy.has(appId)) return;
  _busy.add(appId);
  _api.render();
  try {
    const res = await fetch(`/api/career/applications/${appId}/cover-letter`, { method: 'POST' });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) throw new Error(data.detail || `draft failed (${res.status})`);
    _letters.set(appId, data);
    uiModule.showToast(data.version > 1 ? `Cover letter regenerated (v${data.version})` : 'Cover letter drafted');
    await _api.refresh();
  } catch (e) {
    uiModule.showError(`Cover letter: ${e.message}`);
  } finally {
    _busy.delete(appId);
    _api.render();
  }
};

const sectionHtml = (app) => {
  if (!_letters.has(app.id) && app.cover_letter_doc_id) {
    loadLetter(app.id).then((p) => { if (p) _api.render(); });
  }
  return letterSectionHtml(app, _letters.get(app.id) || null, _busy.has(app.id), esc);
};

const handleClick = (ev) => {
  const btn = ev.target.closest('[data-action="draft-cover-letter"]');
  if (!btn) return false;
  const id = btn.closest('[data-application-id]')?.dataset.applicationId;
  if (id) _draft(id);
  return true;
};

export const installCoverLetter = (api) => {
  _api = api;
  detailSections.push(sectionHtml);
  api.onPanelClick(handleClick);
};
```

- [ ] **Step 5: Wire the import and styles**

In `static/js/career.js`, add one import line after the existing `import { readSettingsForm, setupHtml } from './careerSetup.js';` line, and one install line inside the `// ── plugins ──` block (after `const careerApi = ...`, before `export default careerApi;`):

```javascript
import { installCoverLetter } from './careerCoverLetter.js';
```

```javascript
installCoverLetter(careerApi);
```

`careerCoverLetter.js` never imports `career.js`, so there is no module cycle; `install` runs after every `const` in `career.js` is initialised.

Append to the `/* ── Career hub ── */` section of `static/style.css`:

```css
.career-letter { display: flex; flex-direction: column; gap: 6px; }
.career-claim-list { list-style: none; margin: 0; padding: 0; display: flex; flex-direction: column; gap: 4px; }
.career-claim { display: flex; flex-direction: column; font-size: 12px; padding: 6px 8px; border-left: 3px solid #5dbf6e; }
.career-rubric { margin: 6px 0 0; padding-left: 18px; font-size: 12px; }
.career-rubric-warning { color: #d9a13b; }
```

- [ ] **Step 6: Run the tests**

Run: `python -m pytest tests/test_career_cover_letter_js.py tests/test_career_js.py -v`
Expected: all passed

- [ ] **Step 7: Commit**

```bash
git add static/js/careerCoverLetter.js static/js/careerCoverLetterLogic.js static/js/career.js static/style.css tests/test_career_cover_letter_js.py
git commit -m "feat(career): cover letter section in the application detail view"
```

---

### Task 7: Full verification and smoke test

- [ ] **Step 1: Syntax and suites**

```bash
python -m py_compile app.py routes/career_studio_routes.py services/career/evidence.py services/career/llm.py services/career/cover_letter.py services/career/claims_store.py
node --check static/js/career.js && node --check static/js/careerCoverLetter.js && node --check static/js/careerCoverLetterLogic.js
python -m pytest tests/test_career_*.py tests/test_document_*.py -v
python -m pytest
```
Expected: every suite green; the full run shows no new failures compared with the tree before this plan.

- [ ] **Step 2: Manual smoke (server running, a Task or Utility model configured)**

1. Open `/career` → Setup → upload your CV (PDF or DOCX) → toast with chunk count.
2. Applications → Add application: company, role, paste a real job description → Save.
3. In the detail view click **Draft cover letter** → button shows "Drafting…", then a toast; the section shows a `v1` badge, the claims check table and any rubric warnings.
4. Click **Open in editor** → the editor opens `#document-<id>` with the letter and no JSON fence.
5. Click **Regenerate** → badge becomes `v2`; in the editor, version history shows two versions of one document.
6. Remove the CV setting (Setup → upload nothing; or `PUT /api/career/settings {"cv_filename": ""}`) and draft again → error toast says to upload a CV in Setup.
7. In chat (agent mode, admin): "/cover-letter-writer <paste a JD>" → the agent creates the application through `app_api`, drafts, and replies with the letter, the claims table and a `#document-<id>` link.

- [ ] **Step 3: Final commit (if smoke required fixes)**

```bash
git add -A
git commit -m "chore(career): cover letter smoke fixes"
```

---

## Done when

- [ ] `tests/test_career_evidence.py`, `test_career_llm.py`, `test_career_cover_letter.py`, `test_career_cover_letter_routes.py`, `test_career_cover_letter_skill.py`, `test_career_cover_letter_js.py` pass; full `python -m pytest` has no new failures.
- [ ] `POST /api/career/applications/{id}/cover-letter` creates one library document per application and versions it on regenerate; the stored document contains no JSON fence.
- [ ] 409 responses cover: no CV configured, no CV chunks retrievable, empty job description; 503 when RAG is down.
- [ ] The Applications detail view shows Draft/Regenerate, "Open in editor", the claims check and rubric warnings.
- [ ] `data/skills/career/cover-letter-writer/SKILL.md` exists after a hub bootstrap and the `/cover-letter-writer` slash command drives the same route through `app_api`.
