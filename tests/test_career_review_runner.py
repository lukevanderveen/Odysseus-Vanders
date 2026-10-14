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


for _name in ("src.endpoint_resolver", "src.llm_core"):
    # Collection-time stubs lack resolve_utility_fallback_candidates; evict them
    # so services.career.llm (imported via templates) binds the real resolver.
    _mod = sys.modules.get(_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(_name, None)
        sys.modules.pop("services.career.llm", None)
        sys.modules.pop("services.career.templates", None)

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


def test_default_evidence_uses_full_cv(monkeypatch):
    import services.career.evidence as ev
    seen = []
    monkeypatch.setattr(ev, "cv_evidence", lambda owner, query, k=8: seen.append((owner, k)) or ["whole CV"])
    assert runner._default_evidence("vanders", "Dev at Acme", 8) == ["whole CV"]
    assert seen == [("vanders", 8)]


def test_build_context_keeps_a_full_length_cv():
    cv = "x" * 11000
    ctx = prompts.build_context("vanders", "Acme", "Dev", "jd", "letter", [cv], [])
    assert ctx.cv_excerpt == cv


def test_build_context_flags_a_missing_cv_as_a_tool_gap():
    ctx = prompts.build_context("vanders", "Acme", "Dev", "jd", "letter", [], [])
    assert "NO CV PROVIDED" in ctx.cv_excerpt
    assert "not the candidate's" in ctx.cv_excerpt


async def test_reviewer_edits_with_placeholders_are_flagged_in_the_stored_body(db_factory):
    async def complete(messages):
        if messages[0]["role"] == "system":
            dept = next(d for d, name in seeds.REVIEWER_NAMES.items() if name in messages[-1]["content"])
            return ('## Line edits\nreplace: "I built" → with: "I hold a [degree] and built"\n\n'
                    + _review_output(dept))
        return PANEL_OUTPUT
    await runner.run_review("rv1", complete=complete, session_factory=db_factory,
                            evidence=fake_evidence, notify=lambda **kw: None)
    db = db_factory()
    body = json.loads(db.get(ApplicationReview, "rv1").verdicts)["career_hr"]["body"]
    db.close()
    assert "## Tool checks" in body and "[degree]" in body


def _member(dept="career_hiring_manager"):
    return {"id": "m", "department": dept, "name": seeds.REVIEWER_NAMES[dept], "personality": "",
            "model": "", "endpoint_url": ""}


def test_reviewer_prompt_carries_dim_notes_voice_rules_and_banned_phrases():
    ctx = prompts.build_context("vanders", "Acme", "Dev", "jd", "letter", ["cv"], [],
                                voice_rules="Plain British English.", banned_phrases=["passionate", "dive deep"])
    user = prompts.build_reviewer_messages(_member(), ctx)[-1]["content"]
    assert "competitor" in user and "Plain British English." in user and "dive deep" in user


def test_panel_prompt_carries_voice_rules_and_banned_phrases():
    ctx = prompts.build_context("vanders", "Acme", "Dev", "jd", "letter", ["cv"], [],
                                voice_rules="Plain British English.", banned_phrases=["dive deep"])
    user = prompts.build_panel_messages(ctx, {})[-1]["content"]
    assert "Plain British English." in user and "dive deep" in user


def test_context_without_voice_rules_says_so():
    ctx = prompts.build_context("vanders", "Acme", "Dev", "jd", "letter", ["cv"], [])
    assert ctx.voice_rules == "(none given)" and "passionate" in ctx.banned_phrases


async def test_run_review_passes_the_owners_voice_settings(db_factory, monkeypatch, tmp_path):
    import services.career.settings as cs
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path))
    cs.save_career_settings("vanders", {"voice_rules": "No buzzwords.", "banned_phrases": ["dive deep"]})
    log = []
    await runner.run_review("rv1", complete=_fake_complete(log), session_factory=db_factory,
                            evidence=fake_evidence, notify=lambda **kw: None)
    assert all("No buzzwords." in m[-1]["content"] and "dive deep" in m[-1]["content"] for m in log)
