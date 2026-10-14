# Career 05 — Company-first cover letters

Implementation plan for `Cover Letter Generator Company-First Spec.md` (Oct 14, 2026). That spec
says *what* to build; this file says *where it lands in Odysseus*, what already exists, and where
the spec is adjusted. Follow TDD per phase: pure functions first, LLM calls behind injected
`complete` callables as in `cover_letter.py` and `review_runner.py`.

## Already done (2026-10-13/14)

- Panel sees the whole CV (`evidence.full_cv_text` / `cv_evidence`), not similarity chunks; a
  missing CV is flagged to reviewers as a tool gap.
- Placeholder and invented-figure edits are banned in `reviewers/prompts/reviewer.md` and caught
  deterministically by `services/career/review_guards.py` ("Tool checks" section on each review).
- Chair drops flagged edits and lists "Missing inputs" separately (`reviewers/prompts/panel.md`).
- Single-page company scan: `company_url` / `company_context` on `JobApplication`,
  `POST /applications/{aid}/scan-company` (uncommitted at time of writing).

## Adjustments to the spec

1. **Don't trust the drafter's own C/B/S tags.** A model asked to hit 75% company will label
   sentences to hit it. Tag with a *separate* call (`llm.complete_utility`, temperature 0) that
   never sees the target, and make **B** require a named need: the sentence must reference a
   requirement or dossier id, or it counts as S.
2. **Grounding is a substring check first, a model second.** A dossier fact is kept only if its
   `quote` appears verbatim (whitespace-normalised) in the fetched page text. This is
   deterministic and catches most hallucinated facts before any entailment call.
3. **The CV is an exclusion list *and* a truth source.** It still goes to the panel and to the
   guards (degree, dates, figures). Allow one short credentials clause (degree, university, year)
   in paragraph 1; the overlap check exempts it.
4. **0.85 cosine is model-specific.** Local embeddings are fastembed (`src/embeddings.py`);
   small BGE-style models score paraphrases lower and unrelated text higher than OpenAI models.
   Calibrate on your five past letters vs your CV before enforcing (open question 2 → yes).
5. **Word limits move to one place.** `cover_letter.MAX_WORDS = 300` and the prompt's "at most
   300 words" become `LETTER_WORDS = (250, 400)` used by the prompt, checks and UI.
6. **Export check is a separate bug.** The dark-box PDF is an export/rendering defect, not a
   letter-quality check. Track it on its own with a reproducing test.

## Phases

Each phase is shippable and testable on its own. Order differs slightly from the spec: cheap
panel/profile fixes come early because they improve every letter immediately.

### 05a — Letter checks + baseline  (spec stage 5, step 1) — DONE 2026-10-14
Built: `letter_checks.py` (replaces `rubric_check`; requirement and role matching uses key words,
since extracted phrases are never repeated verbatim), `letter_tagger.py` + `prompts/letter_tagger.md`,
`letter_baseline.py`, `routes/career_letter_routes.py` (`POST/GET /api/career/letters/baseline`).
First real baseline (2 tool-drafted letters): avg 148 words, company+bridge 40%, pass rates
length 0 / voice 50 / names 0 / logistics 0 / specifics 100 / balance 0.
Follow-ups: score the hand-written examples in Personal Docs too (they aren't `JobApplication`
documents); no UI for the baseline yet; tagger runs at the model's default temperature.

Original plan:
- New `services/career/letter_checks.py` (pure, no I/O). Replace `rubric_check` with
  `run_checks(letter, ctx) -> List[CheckResult(name, ok, detail, paragraph)]`:
  length 250–400; voice regex (banned phrases from settings, em dash, `!`, a small US→UK
  spelling list); names (company and role in paragraph 1; no other company name — the "other
  names" list is the owner's other `JobApplication.company` values plus example-letter companies);
  logistics present when the JD mentions location/hybrid/travel/relocation.
- `services/career/letter_tagger.py`: separate tagging call → `[{sentence, tag, words}]`, and a
  pure `balance(tags) -> {"company_bridge_pct", "self_pct", "worst_paragraph"}`.
- Baseline: a small route or script that runs both over the owner's last N cover-letter
  `Document`s and stores the numbers. This is the score to tune against.
- Tests: one per check, a balance test with a long S paragraph hiding behind short C lines.

### 05b — Panel and voice  (spec stage 5, panel changes) — DONE 2026-10-14
Built: `banned_phrases` setting (Setup tab field, merged with built-ins by
`letter_checks.banned_phrases`); voice rules + banned phrases in `reviewer.md` and `panel.md`;
`review_guards` flags rewrites that break them (shared `letter_checks.voice_problems`);
`swap_test` dim on the hiring manager with scoring notes (`review_scoring.dim_notes`), JS mirror
guarded by `test_js_dims_and_labels_mirror_python`; chair splits edits into "Apply now" /
"Needs your input".

Original plan:
- Pass `voice_rules` + banned phrases into `reviewer.md` and `panel.md` (`build_context` gains a
  field). Run `letter_checks` voice regex over line-edit rewrites in `review_guards`.
- Add a `swap_test` dimension: add it to `REVIEW_DIMS` (likely the hiring manager) **and** the JS
  mirror `static/js/careerReviewLogic.js` (`test_career_review_js.py` guards the sync).
- Chair splits "Consolidated edits" into "Apply now" and "Needs your input".

### 05c — Candidate profile / logistics
- Add `profile` to `services/career/settings.py` DEFAULTS: `home_location`, `right_to_work`,
  `travel`, `notice_period`, `availability`, `links`. Coerce as a dict of strings.
- Setup UI fields in `static/js/careerSetup.js`; feed into the draft prompt for paragraph 5.

### 05d — CV exclusion
- `services/career/cv_overlap.py`: split `full_cv_text` into bullets/sentences, embed once per CV
  (cache keyed on CV filename + mtime), `overlaps(sentence) -> (max_sim, bullet, shared_6gram)`.
- Pure 6-gram check is testable without embeddings; inject the embedder for the cosine part.
- Calibrate the threshold on past letters (adjustment 4) before it blocks anything.

### 05e — Evidence bank  (spec stage 3)
- Reuse plan 03's sources: `activity_sources.py` (git log), `activity_remote.py` (GitHub,
  Trello cards incl. Done/Defects) and `disclosure.py` redaction. Add README/ADR file reads for
  configured project paths.
- New owner-scoped table `CareerStory` (problem, decision, trade_off, outcome, learnt,
  artefact_url, skills JSON, source, `cv_overlap` float, updated_at). Add an owner-scope test in
  the style of `test_career_routes_owner_scope.py`.
- `in_cv` is *computed* by 05d, not hand-set. Rebuild on demand ("Refresh stories" button), not
  per letter.

### 05f — Company research dossier  (spec stage 1)
- Grow `scan-company` into `services/career/research.py`:
  - Fetch with `src/search/content.fetch_webpage_content` — keep it: it already blocks private
    addresses (SSRF guard), caches, and uses httpx + BeautifulSoup.
  - Add: `robots.txt` check, `sitemap.xml` discovery then home-page links, ~15 page cap,
    ~1 req/s, honest user agent.
  - Playwright is **not** installed (open question 1). Ship HTTP-only; when
    `_detect_js_frameworks` fires or text is thin, set `research_confidence: low` and show the
    paste box.
  - Recent news via `src/search/`; Companies House behind an optional API-key setting.
- LLM extracts the dossier schema; every fact passes the substring grounding check
  (adjustment 2) or is dropped. Facts get stable ids (`d1`, `d2`, …).
- New table `CompanyDossier` keyed by (owner, domain) with `fetched_at`; reuse when < 30 days old.
- LinkedIn stays a manual paste box.

### 05g — Hook options  (spec stage 2)
- `services/career/hooks.py`: propose 4–6 angles from dossier + JD + story bank, score on the
  spec's five criteria, return the top 3–4 with fact ids and the bridging story id.
- Store the choice on `JobApplication`: `hook_json` (primary, optional secondary, fact ids,
  story ids) and `hook_reason` (your free text, which always wins).
- UI cards in the application view (`careerApplications.js`); record chosen angle types per owner
  in settings for later ranking.
- Low research confidence → always ask "What drew you to this company?" (open question 3: ask
  always, but make it optional when confidence is high).

### 05h — Structured drafting  (spec stage 4)
- New prompt `cover_letter_v2.md`: inputs per the spec table, paragraph budgets, CV as
  "do not restate", output JSON `{paragraphs: [{sentences: [{text, fact_ids}]}], claims}`.
  Render to markdown in Python; keep `split_claims` compatibility for the claims panel.
- Plumb `temperature` through `services/career/llm.complete` (it currently has none; the
  underlying `src/llm_core` call accepts one). 0.5, two variants.
- Loop: draft → 05a checks + tagger + 05d overlap → redraft with failures named, max 2 retries,
  then show the best variant with remaining failures listed.
- Keep v1 behind a setting until the baseline from 05a shows v2 scoring better.

## Example letters (your work, in parallel with 05a–05d)

Examples are RAG files with the `cover-letter-example-` prefix (`evidence.example_pairs`).
To pick by role family without a schema change, name them
`cover-letter-example-<family>-<company>.md` (`security`, `ai`, `backend`, `general`) and filter
on the family in `example_pairs`. Put the C/B/S tags in the file as a trailing comment block so
they never reach the letter. Delete the old CV-heavy examples in Career → Setup, or the model
will average old and new.

## Open questions — current answers

1. Playwright: not installed → HTTP-only first (05f).
2. Threshold: calibrate on past letters before enforcing (05d).
3. Own reason: always offered, required only when research confidence is low (05g).
4. Drafting model: whatever the Task endpoint resolves to. Test JSON reliability with the 05a
   baseline run before 05h; `_first_json_object` already tolerates chatty output.
