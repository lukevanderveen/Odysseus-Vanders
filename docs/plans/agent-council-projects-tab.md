# Implementation Plan — Agent Council + Projects Tab

Trello: https://trello.com/c/UO1fm8dk (card #9, Odysseus board)
Status: planned 2026-06-09. Architecture decided on the card: **department report generators + human approval queue + project index** — no peer-to-peer agent chat.

Each phase ships standalone value and lands as its own commit series (TDD throughout: failing test first).

---

## Phase 1 — Projects tab

**Goal:** scan a workspace root, auto-discover projects, detect stack, Index (per-project Chroma collection), Deep Index (agent structural summary), staleness badge.

### 1.1 Data model (`core/database.py`)

New `Project` table (TimestampMixin, follows `Note` owner pattern):

```
id, owner (TEXT, indexed, nullable for legacy sweep)
name, path (unique per owner), stack (JSON: ["python","fastapi",...])
index_status     ("none" | "indexed" | "stale")
indexed_at, index_ref (git HEAD sha, or max-mtime hash for non-git)
deep_summary     (TEXT, output of Deep Index agent run)
deep_indexed_at, deep_index_ref
archived (bool)
```

No Alembic — add an idempotent `_migrate_*` function via `PRAGMA table_info()` and call it from `init_db()` (template: `_migrate_add_crew_member_id`, database.py ~1298).

### 1.2 Scanner (`services/projects/scanner.py`)

Pure, deterministic, no LLM:
- Workspace roots from settings key `projects_workspace_roots` (list; set via Settings UI later, `data/settings.json` for now via `src/settings.py` accessors).
- Walk depth ≤ 3, skip `node_modules`, `venv`, `.git` internals, `__pycache__`. A directory is a project if it contains any marker: `.git/`, `package.json`, `pyproject.toml`, `requirements.txt`, `Cargo.toml`, `*.sln`/`*.csproj`, `go.mod`.
- Stack heuristics: marker file → stack tags (e.g. `pyproject.toml`+`fastapi` in deps → `python, fastapi`). Table-driven dict so it's testable and extendable.
- Use `core/platform_compat.py` for any path/OS differences (Windows-first — the user runs native Windows, projects on `D:\`).

Staleness: read `.git/HEAD` (+ resolved ref file) directly — no subprocess — and compare to `index_ref`; non-git fallback = hash of (file count, max mtime). `index_status` computed at list time, not stored stale.

### 1.3 Indexing (`services/projects/indexer.py`)

- **Index** = per-project Chroma collection `project_{id}` via `get_chroma_client().get_or_create_collection(...)` (pattern: `src/rag_vector.py`). Chunk source/text files (reuse/extract the chunker used by personal docs ~512 tokens; extract to a shared module rather than duplicating). Skip binaries, respect `.gitignore` top-level dirs. Batch adds of 100 (existing `add_documents_batch` pattern).
- **Graceful degradation:** ChromaDB down → log `DEGRADED`, mark index unavailable, UI shows keyword-only badge. Never crash (project convention).
- **Deep Index** = one agent run (local qwen by default — cheap, summarisation-shaped work) over the project tree producing a structural markdown summary → `Project.deep_summary`. Runs through the Phase 2 run queue once it exists; in Phase 1 it can run inline via `stream_agent_loop` with a spinner.

### 1.4 Routes (`routes/project_routes.py`)

`setup_project_routes()` factory, wired in `app.py` like the other ~40 routers. Owner-scoped inline (`get_current_user(request)`; filter `Project.owner == user`). Filesystem scanning is privileged — gate scan/index endpoints the same way shell/file tools are gated.

```
GET    /api/projects                 list + computed staleness
POST   /api/projects/scan            rescan workspace roots, upsert
POST   /api/projects/{id}/index
POST   /api/projects/{id}/deep-index
GET    /api/projects/{id}            detail (summary, stack, status)
DELETE /api/projects/{id}            archive
```

### 1.5 Front-end (`static/js/projects.js`)

Follow `memory.js` (tabbed modal) pattern:
- Modal HTML in `index.html` (`#projects-modal`), tool button `#tool-projects-btn` in the Tools section, click handler + module import in `app.js`.
- Deep link: `@app.get("/projects")` → `serve_index` in `app.py`; add `'/projects'` to `_routeOpen` in `app.js` + SHAPES/title entries in the `index.html` inline script.
- Card grid: name, stack chips, staleness badge ("Index may be stale — run Deep Index"), buttons Index / Deep Index / Developer Review (Phase 2 wires the last one). `spinnerModule` for in-flight, `uiModule.showToast` for results.
- CSP: no inline handlers; `addEventListener` only.

### 1.6 Tests (write first)

- `tests/test_project_scanner.py` — marker detection, stack heuristics, depth/skip rules, staleness (git HEAD change, mtime fallback) against tmp dirs.
- `tests/test_project_routes_owner_scope.py` — template: `test_skill_delete_route_owner.py` / `test_null_owner_gates.py`.
- `tests/test_projects_js.py` — Node-runs pure logic (e.g. staleness badge selection), pattern: `test_compare_js.py`.

---

## Phase 2 — One report type end-to-end (Developer Review)

**Goal:** reports table + one agent run producing a Developer Review that lands in an approval queue, with grounded vs judged scores.

### 2.1 Data model

`Report` table:

```
id, owner (indexed), project_id (FK), department ("developer"), report_type ("developer_review")
title, body (markdown)
scores (JSON: {"grounded": {"technical_readiness": 72, "recent_activity": 40, "_signals": {...}},
               "judged":   {"monetisation_clarity": 55, "marketability": 60}})
status ("running" | "draft" | "approved" | "dismissed"), error (TEXT)
model, started_at, finished_at
```

`status="draft"` = awaiting approval (the Atlas "reports generate only unless approved" insight).

### 2.2 Grounded vs judged scoring (`services/council/scoring.py`)

- **Grounded** (pure Python, no LLM): tests present + count, CI config present, README quality (length/sections), pinned deps, commit recency (from `.git` log), TODO/FIXME density. Each signal documented; score = weighted rubric. Store raw signals alongside the number (`_signals`) so the UI can show *why*.
- **Judged** (LLM opinion): monetisation clarity, marketability — produced by the report agent, schema-validated, always labelled "AI judgement" in the UI. The Atlas "20/100, Stage: unknown" failure mode is the thing this split avoids.

### 2.3 Report runner (`services/council/runner.py`)

- Programmatic agent run via `stream_agent_loop(...)` (src/agent_loop.py) — consume the generator server-side, no chat session needed; persist steps like `TaskRun.steps` does.
- **Sequential queue:** single `asyncio.Semaphore(1)` for local endpoints (single-GPU Ollama — same approach as `TaskScheduler._run_semaphore`). Per-endpoint: cloud endpoints (glm-4.6) may run parallel (cap 2–3). Council member statuses later derive from this queue: queued = Waiting, running = Busy, free = Ready.
- Prompt = report template (markdown with placeholders) from `data/council/report_types/developer_review.md` + project context (deep_summary, RAG snippets from `project_{id}` collection, grounded signals). Model default: cloud glm-4.6 for report runs, local qwen for summaries (per existing Ollama strategy).
- On completion: `status="draft"`, push via `task_scheduler.add_notification(...)` → existing browser-toast polling picks it up. On error: `status="error"` with detail, never silent.

### 2.4 Routes (`routes/council_routes.py`)

```
POST /api/council/reports                     {project_id, report_type} → queue run
GET  /api/council/reports?status=draft        approval inbox (owner-scoped)
GET  /api/council/reports/{id}                full body + scores
POST /api/council/reports/{id}/approve
POST /api/council/reports/{id}/dismiss
```

Approve/dismiss just flips status (Phase 3 may attach post-approval actions). Wire the Projects card "Developer Review" button to POST + toast.

### 2.5 Tests

- Scoring: grounded signals on fixture dirs; judged-score JSON resilience (template: companion model JSON-resilience tests).
- Runner: queue serialises two local runs; error path persists `status="error"`.
- Routes: owner scope + status transitions (draft→approved, draft→dismissed, no approve on dismissed).

---

## Phase 3 — Council dashboard + remaining personas

**Goal:** the five departments, run-queue statuses, approval inbox UI, report history.

### 3.1 Personas — reuse `CrewMember`

`CrewMember` already has owner, name, avatar, personality (system prompt), model, endpoint_url, enabled_tools. Add nullable `department` column (migration) — a council member is a CrewMember with `department` set. Seed five defaults on first council use: Research / Business / Architect / Developer / Marketing, each pointing at a report-type template in `data/council/report_types/` (market_opportunity, launch_strategy, architecture_review, developer_review, marketing_audit).

Templates are skill-shaped markdown (frontmatter + procedure) so they're editable without code changes.

### 3.2 Dashboard (`static/js/council.js`, `/council` deep link)

Tabs (memory.js pattern):
- **Members** — five cards with status chip (Ready / Waiting / Busy) derived from `GET /api/council/status` (queue introspection).
- **Approvals** — draft reports: title, project, department, score summary; Approve / Dismiss buttons; bulk select (memory.js bulk-action pattern).
- **History** — approved/dismissed, filter by project/department.

Poll while open (cookbookRunning.js monitor pattern); `add_notification` covers the closed-modal case.

### 3.3 Run-queue API

`GET /api/council/status` → per-member `{department, status, current_report_id, queue_position}` from the runner's queue state. Sequential locally, parallel allowed on cloud — already built in 2.3, this just exposes it.

---

## Phase 4 — Office node-graph view + Project Command Centre (pure presentation)

No new backend. SVG/canvas spatial view in `council.js` (or `councilOffice.js` if >200 lines): agent nodes around a central Council button, status-coloured. Project Command Centre popup per project: score bars (grounded solid, judged hatched/labelled), recent git activity, latest reports. Build last; everything above is useful without it.

---

## Decisions & risks

- **Reuse CrewMember vs new table** — reuse + `department` column. Revisit if council members need report-type-specific config beyond one template ref.
- **Runner vs TaskScheduler** — dedicated runner with its own semaphore, *not* ScheduledTask rows: reports are on-demand, project-bound, and need their own lifecycle (draft/approve). Notifications still go through `task_scheduler.add_notification` so the existing toast pipeline works.
- **Filesystem scanning is privileged** — scan/index endpoints gated like shell tools; workspace roots are an explicit allowlist setting, never user-supplied paths per request.
- **Windows-first** — scanner and git-HEAD reading must work on NT (no symlink assumptions, use `platform_compat`); user's projects live on `D:\`.
- **Chroma optional** — every index path needs the keyword/DEGRADED fallback or the feature breaks the project's graceful-degradation contract.
- **First real use** — launch-strategy / market-opportunity reports for the Procedure Review Tool, so Phase 3's Business persona template deserves early attention after Phase 2 proves the loop.
