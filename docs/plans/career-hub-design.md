# Career Hub — Design

Status: designed 2026-10-09. Five isolated implementation plans hang off this spec:

```
career-00-hub-foundation ──┬──> career-01-cover-letter ──> career-02-reviewer-panel
                           ├──> career-03-linkedin-posts
                           └──> career-04-job-tracker
```

`00` must land first. `01`, `03`, `04` are independent of each other and can be built in any order
(or in parallel worktrees). `02` needs `01` because it reviews the cover letter document attached to an
application. Each plan is self-contained: it names the interfaces it consumes from earlier plans so an
engineer never has to read another plan to build it.

## Why

The user is job-hunting for software engineering roles. Odysseus should carry the repetitive parts:

1. **Cover letters** tailored to a job description, grounded in the user's CV and prior letters.
2. **A reviewer panel** (Recruiter screener, Hiring manager, Blunt senior engineer, HR/People partner)
   that critiques a cover letter against the JD before it is sent.
3. **LinkedIn posts** drafted from real project activity (local git, GitHub, Trello) without leaking
   private implementation details.
4. **A job tracker** that classifies incoming email (confirmations, rejections, interview invites,
   offers), keeps a live dashboard, and drafts follow-up replies when a reply is possible.

Nothing ever auto-sends or auto-publishes. Replies and posts are drafts the user acts on.

## Decisions (confirmed with the user)

| Decision | Value |
|---|---|
| UI home | One **Career** hub modal + `/career` deep link. Tabs: Tracker, Applications, Reviewers, Posts, Setup. |
| Reviewer personas | `career_recruiter`, `career_hiring_manager`, `career_engineer`, `career_hr` |
| Post sources | Local git repos (Project rows), GitHub API (token), Trello API (key + token) |
| Post types | `progress`, `technique`, `lesson`, `narrative` |
| Default disclosure | `anonymous` for every project until raised per project |
| Tracker intake | Auto from email **and** manual add |
| Classifier | Cheap subject/sender prefilter, then the configured LLM on candidates only |
| Timing defaults | nudge after 10 days, ghosted at 21 (editable in Setup) |
| Cover letter IO | JD text or URL in; library document in the editor out |
| Old skill | `data/skills/general/linkedin-post-writer` v1.0.0 is retired by the seeder in favour of v2 |
| Agent path | Seeded skills call hub routes through `app_api` (admin-only tool; this is a single-admin install) |

## Codebase constraints the design respects

- **Council runner is repository-bound** (`services/council/runner.py` needs a `Project` row and walks
  the repo). The reviewer panel is a sibling runner under `services/career/` with its own queue. It
  reuses the council's *patterns* (template dirs with user override, `render_template`, JSON score
  fence, `_default_complete`), not its functions.
- **Every non-null `CrewMember.department` is treated as a council seat** in
  `services/council/personas.py:75`, `routes/council_routes.py:229` and `:260`. Those three queries
  switch to `department.in_(DEPARTMENTS)` so `career_*` reviewers never appear in the council office.
- **No server-side IMAP watcher.** New mail is detected only in
  `routes/email_routes.py:_record_email_received_events`, called from `GET /api/email/list`, which the
  browser polls every 60 s (`static/js/emailInbox.js`). "Live while Odysseus is open" is exactly this
  path, so the tracker subscribes to a callback registry added at the `new_keys` point. The payload is
  the list dict (`uid, message_id, subject, from_name, from_address, date, …`); Reply-To, List-*,
  In-Reply-To and the body require a full fetch via `routes/email_helpers._imap(owner=)` +
  `routes/email_routes._imap_uid_fetch`. `_read_email_sync` is a closure and not importable.
- **`email_ai_replies`** (keyed by message_id, no owner column) is what the email reader shows as
  "AI reply (cached draft ready)". The tracker writes `INSERT OR IGNORE` plain text there and keeps the
  authoritative copy on `JobApplicationEvent.suggested_reply`.
- **RAG entry point** is `src.rag_singleton.get_rag_manager()` (a `VectorRAG`) with
  `search(query, k, owner=)`. Results carry `metadata.filename`, `document`, `similarity`; no
  threshold is applied inside. Career services pass `k=40`, apply their own 0.35 threshold, and filter
  by filename prefix (`cv-*`, `cover-letter-example-*`).
- **Personal uploads**: `POST /api/personal/upload` only special-cases PDF (a `.docx` CV becomes
  garbage) and only stamps `metadata.owner` when a user is resolved. `00` fixes the upload text
  extraction and the career CV/example routes index files themselves with the resolved owner.
- **Library documents**: `do_create_document` requires a chat session. The session-less path is inline
  in `routes/document_routes.py:46-117`; `00` factors it into
  `routes/document_helpers.create_library_document(...)`. Front-end deep link: `#document-<id>`.
  Email deep link: `#email=INBOX:<uid>`.
- **Skills** live at `data/skills/<category>/<slug>/SKILL.md` and `data/` is gitignored, so skill
  templates ship under `services/career/skills/` and are seeded by writing `SKILL.md` directly
  (`SkillsManager.add_skill` dedups non-user sources). Only name/description/when/procedure/pitfalls
  are injected into prompts; heavy lifting lives in routes the skill tells the agent to call.
- **Integrations**: `src/integrations.py` presets support `header|bearer|query|basic|none` auth plus
  `default_params`. Trello = `auth_type="query", auth_param="token", default_params={"key": …}`;
  GitHub = `auth_type="bearer"`. No new auth type.
- **Notifications**: `src.event_bus.get_task_scheduler().add_notification(task_name, status,
  task_id=None, owner=<owner>, body=<≤500 chars>)`, drained by `GET /api/tasks/notifications` every
  30 s in `static/js/tasks.js`. `owner=` is required.
- **UI touch points** (council precedent): `app.py` deep-link route; `static/index.html` modal +
  favicon shape + title + sidebar `.list-item`; `static/app.js` import, click handler, `_routeOpen`;
  pure logic in a DOM-free `*Logic.js` tested from Node.

## Architecture

```
services/career/
  settings.py          per-owner JSON at data/career/<owner>/settings.json (atomic_io)
  seeds.py             seed_reviewers(owner) + seed_skills(owner, skills_manager); idempotent
  reviewers/           persona text per reviewer (00) + prompt templates (02)
  skills/              packaged SKILL.md templates: cover-letter-writer (01), linkedin-post-writer (03)
  personal_files.py    index_personal_text(...) — owner-stamped RAG indexing of a personal file (00)
  evidence.py          cv_chunks(owner, query), example_pairs(owner, jd_text) over VectorRAG (01)
  llm.py               complete(messages, owner) — task endpoint + utility fallbacks (01)
  cover_letter.py      extract_requirements, draft_cover_letter → (markdown, claims) (01)
  review_runner.py     run_review(review_id, …) with its own asyncio.Semaphore(1) (02)
  review_scoring.py    REVIEW_DIMS, parse_review_block, aggregate_panel (02)
  activity_sources.py  fetch_local_git / fetch_github / fetch_trello → (items, warning) (03)
  disclosure.py        redact(items, settings) + per-project disclosure level (03)
  posts.py             draft_post(items, post_type, settings, complete) (03)
  email_classifier.py  prefilter(list_dict), classify(headers, body, complete) (04)
  tracker.py           on_new_inbox_messages, fetch_full, match_or_create_application, record_event (04)
  tracker_replies.py   reply_address, suggest_reply, write to email_ai_replies (04)
  tracker_scan.py      scan_backfill(owner, days) + built-in action scan_job_emails (04)

routes/career_routes.py          /api/career: bootstrap, settings, applications, cv, examples (00)
routes/career_studio_routes.py   /api/career: cover-letter (01), reviews + reviewers (02), activity + posts (03)
routes/career_tracker_routes.py  /api/career/tracker: summary, events, scan, unsorted, link-email (04)

core/database.py  JobApplication, JobApplicationEvent, ApplicationReview, CareerPost (00)

static/js/career.js            modal, tabs, delegated events; exports open/close/isOpen (00)
static/js/careerLogic.js       pure helpers (00; extended by 02/04)
static/js/careerApplications.js applications list/detail (00), cover letter (01), review panel (02)
static/js/careerSetup.js       setup tab (00)
static/js/careerReviewers.js   reviewers tab (02)
static/js/careerPosts.js       posts tab (03)
static/js/careerTracker.js     tracker tab + 20 s conditional poll (04)
```

## Data model (`core/database.py`, all `TimestampMixin + Base`, owner nullable + indexed like `Note`)

```
job_applications (JobApplication)
  id, owner, company*, role*, source="manual" (manual|email|linkedin|board), url, jd_text,
  applied_at, status="drafting" (drafting|applied|acknowledged|screening|interview|offer|rejected|
  withdrawn|ghosted), last_event_at, notes, cover_letter_doc_id → documents.id (SET NULL),
  latest_review_id, archived=False

job_application_events (JobApplicationEvent)
  id, owner, application_id → job_applications.id (CASCADE, nullable: unsorted events have none),
  kind="other" (confirmation|rejection|interview_invite|offer|info_request|recruiter_outreach|other),
  sentiment="neutral" (positive|negative|neutral), message_id (indexed), uid, folder, account_id,
  from_address, reply_to, subject, received_at, summary, can_reply=False, reply_blocked_reason,
  suggested_reply, confidence=0 (integer percent)

application_reviews (ApplicationReview)
  id, owner, application_id → job_applications.id (CASCADE), status="running" (running|done|error),
  verdicts (JSON text: {dept: {verdict, top_issues[], line_edits[], scores{}}}), panel_summary,
  scores (JSON text aggregate), error, started_at, finished_at

career_posts (CareerPost)
  id, owner, post_type, body, hooks (JSON text list), sources (JSON text list), window_days=14,
  status="draft" (draft|posted|discarded), posted_at
```

Status transitions driven by event kind: confirmation→acknowledged, interview_invite→interview,
offer→offer, rejection→rejected. Manual override always wins. `ghosted` is computed at read time
(no event for `ghosted_after_days`) unless the user set it explicitly.

## Settings (`data/career/<owner>/settings.json`)

```
cv_filename: ""             voice_rules: ""            target_roles: []        locations: []
disclosure_default: "anonymous"   project_disclosure: {}   blocklist_terms: []
scan_account_ids: []        prefilter_terms: []        nudge_after_days: 10    ghosted_after_days: 21
narrative_uses_tracker_stats: false
```

Unknown keys are ignored on save. Missing keys fall back to defaults on load.

## Cross-feature behaviour

- One application record is the spine. Its detail view shows the cover letter (01), the latest panel
  review (02) and its email events (04).
- Posts may use tracker stats for `narrative` posts only when `narrative_uses_tracker_stats` is true.
- All generation resolves the Task endpoint with utility fallbacks (council precedent), so Cookbook and
  served-model settings apply unchanged.
- Graceful degradation: no CV → cover letter route returns 409 with a setup hint; ChromaDB down → RAG
  keyword fallback already exists; no email account → tracker tab shows a setup hint; a failing
  git/GitHub/Trello source returns an empty list plus a warning string.
- Owner scoping on every route. Server-side email access goes through
  `routes/email_helpers._get_email_config / _imap(owner=)`, never the unscoped MCP email server.

## Global verification bar (every plan)

- `python -m pytest tests/test_career_*.py` green, plus the full `python -m pytest` unchanged.
- `python -m py_compile app.py routes/*.py src/*.py services/career/*.py`
- `node --check static/js/career*.js`
- Manual smoke listed at the end of each plan.
