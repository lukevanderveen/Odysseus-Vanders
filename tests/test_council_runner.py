"""Report runner — queue, persistence, notification (Agent Council Phase 2).

Drives services/council/runner.py against an in-memory SQLite DB with an
injected fake LLM. Uses the unstub window from tests/test_project_model.py so
the runner binds the real ORM classes even in a full-suite run.
"""

import asyncio
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


_sa, _sa_orm, _coredb, _runner = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "core.database", "services.council.runner",
    force=("core.database", "services.council.runner"),
)
create_engine = _sa.create_engine
sessionmaker = _sa_orm.sessionmaker
Base = _coredb.Base
Project = _coredb.Project
Report = _coredb.Report
runner = _runner


@pytest.fixture()
def db_factory(tmp_path):
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=_sa.pool.StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    factory = sessionmaker(bind=engine)

    db = factory()
    db.add(Project(id="p1", owner="vanders", name="odysseus",
                   path=str(tmp_path), deep_summary="## Purpose\nWorkspace app."))
    db.add(Report(id="r1", owner="vanders", project_id="p1",
                  title="Developer Review: odysseus"))
    db.commit()
    db.close()
    return factory


GOOD_OUTPUT = (
    "## Findings\nSolid codebase.\n\n## Risks\nFew tests.\n\n"
    "```json\n{\"monetisation_clarity\": 40, \"marketability\": 65}\n```"
)


def fake_retrieve(project_id, query, n=6):
    return "(snippets)"


async def test_run_report_happy_path_lands_in_draft(db_factory):
    notes = []

    async def fake_complete(messages):
        return GOOD_OUTPUT

    result = await runner.run_report(
        "r1", complete=fake_complete, session_factory=db_factory, retrieve=fake_retrieve,
        notify=lambda **kw: notes.append(kw),
    )
    assert result["status"] == "draft"

    db = db_factory()
    row = db.get(Report, "r1")
    assert row.status == "draft"
    assert "Solid codebase" in row.body
    import json
    scores = json.loads(row.scores)
    assert scores["judged"] == {"monetisation_clarity": 40, "marketability": 65}
    assert "technical_readiness" in scores["grounded"]
    assert "_signals" in scores["grounded"]
    assert row.started_at is not None and row.finished_at is not None
    db.close()
    assert len(notes) == 1
    assert notes[0]["status"] == "completed"
    assert notes[0]["owner"] == "vanders"


async def test_run_report_prompt_grounds_in_facts_and_repo_structure(db_factory):
    seen = {}

    async def fake_complete(messages):
        seen["user"] = messages[-1]["content"]
        return GOOD_OUTPUT

    await runner.run_report(
        "r1", complete=fake_complete, session_factory=db_factory, retrieve=fake_retrieve,
        context_builder=lambda path: "REPO_TREE_MARKER", notify=lambda **kw: None)
    p = seen["user"]
    assert "odysseus" in p
    assert "Workspace app." in p                  # deep_summary fed in
    assert "Verified repository facts" in p        # authoritative facts block present
    assert "CI/CD configured" in p                 # a concrete signal, as prose not JSON
    assert "REPO_TREE_MARKER" in p                 # real repo structure fed in, not just a digest


async def test_run_report_retrieves_evidence_per_concern(db_factory):
    queries = []

    def cap_retrieve(project_id, query, n=6):
        queries.append(query)
        return f"chunk for {query}"

    async def fake_complete(messages):
        return GOOD_OUTPUT

    await runner.run_report(
        "r1", complete=fake_complete, session_factory=db_factory, retrieve=cap_retrieve,
        context_builder=lambda path: "", notify=lambda **kw: None)
    assert len(queries) >= 3            # several targeted queries, not one generic sweep
    joined = " ".join(queries).lower()
    assert "test" in joined and ("ci" in joined or "build" in joined)


async def test_run_report_error_path_persists_error(db_factory):
    notes = []

    async def boom(messages):
        raise RuntimeError("endpoint down")

    result = await runner.run_report("r1", complete=boom, session_factory=db_factory, retrieve=fake_retrieve,
                                     notify=lambda **kw: notes.append(kw))
    assert result["status"] == "error"
    db = db_factory()
    row = db.get(Report, "r1")
    assert row.status == "error"
    assert "endpoint down" in row.error
    db.close()
    assert notes[0]["status"] == "failed"


async def test_run_report_empty_output_is_error(db_factory):
    async def empty(messages):
        return "   "

    result = await runner.run_report("r1", complete=empty, session_factory=db_factory, retrieve=fake_retrieve,
                                     notify=lambda **kw: None)
    assert result["status"] == "error"


async def test_run_report_unparseable_judged_scores_still_drafts(db_factory):
    async def no_json(messages):
        return "## Findings\nGood stuff, no scores though."

    result = await runner.run_report("r1", complete=no_json, session_factory=db_factory, retrieve=fake_retrieve,
                                     notify=lambda **kw: None)
    assert result["status"] == "draft"
    db = db_factory()
    import json
    scores = json.loads(db.get(Report, "r1").scores)
    assert scores["judged"] == {}
    db.close()


async def test_run_report_missing_report_returns_error(db_factory):
    result = await runner.run_report("nope", session_factory=db_factory, retrieve=fake_retrieve,
                                     notify=lambda **kw: None)
    assert result["status"] == "error"


async def test_queue_state_tracks_running_and_queued(db_factory):
    db = db_factory()
    db.add(Report(id="r2", owner="vanders", project_id="p1", title="second"))
    db.commit()
    db.close()

    release = asyncio.Event()
    observed = {}

    async def gated(messages):
        observed.setdefault("mid_run", runner.queue_state())
        await release.wait()
        return GOOD_OUTPUT

    t1 = asyncio.create_task(runner.run_report(
        "r1", complete=gated, session_factory=db_factory, retrieve=fake_retrieve,
        notify=lambda **kw: None))
    t2 = asyncio.create_task(runner.run_report(
        "r2", complete=gated, session_factory=db_factory, retrieve=fake_retrieve,
        notify=lambda **kw: None))
    while not observed.get("mid_run"):
        await asyncio.sleep(0.01)
    await asyncio.sleep(0.05)  # let the second run join the queue

    state = runner.queue_state()
    assert state["running"] in ("r1", "r2")
    other = ({"r1", "r2"} - {state["running"]}).pop()
    assert state["queued"] == [other]
    release.set()
    await asyncio.gather(t1, t2)

    state = runner.queue_state()
    assert state["running"] is None
    assert state["queued"] == []


def _add_project(db_factory, tmp_path, pid="p2"):
    proj_dir = tmp_path / pid          # distinct path: projects are unique per (owner, path)
    proj_dir.mkdir()
    db = db_factory()
    db.add(Project(id=pid, owner="vanders", name="freshproj",
                   path=str(proj_dir), deep_summary="## Purpose\nThing."))
    db.commit()
    db.close()


async def test_run_full_council_drafts_synthesis_over_approved_components(db_factory, tmp_path):
    _add_project(db_factory, tmp_path, "p2")

    async def fake_complete(messages):
        return GOOD_OUTPUT

    result = await runner.run_full_council(
        "p2", "vanders", complete=fake_complete, session_factory=db_factory,
        retrieve=fake_retrieve, context_builder=lambda path: "", notify=lambda **kw: None)
    assert result["status"] == "draft"
    assert result["components"] == 5

    db = db_factory()
    try:
        components = db.query(Report).filter(
            Report.project_id == "p2",
            Report.report_type.in_(list(runner.COMPONENT_TYPES)),
        ).all()
        assert len(components) == 5
        # components are kept (approved) so they never flood the approval inbox
        assert all(c.status == "approved" for c in components)

        synth = db.query(Report).filter(
            Report.project_id == "p2", Report.report_type == "council_review").one()
        assert synth.status == "draft"            # the synthesis alone awaits approval
        assert synth.department == "council"
        import json
        scores = json.loads(synth.scores)
        # every component returned the same judged scores → aggregated to them
        assert scores["judged"] == {"monetisation_clarity": 40, "marketability": 65}
        assert "technical_readiness" in scores["grounded"]
    finally:
        db.close()


async def test_run_full_council_synthesis_prompt_includes_member_reports(db_factory, tmp_path):
    _add_project(db_factory, tmp_path, "p2")
    seen = []

    async def fake_complete(messages):
        seen.append(messages[-1]["content"])
        return GOOD_OUTPUT

    await runner.run_full_council(
        "p2", "vanders", complete=fake_complete, session_factory=db_factory,
        retrieve=fake_retrieve, context_builder=lambda path: "", notify=lambda **kw: None)
    synthesis_prompt = seen[-1]          # the synthesis is the final completion
    assert "Department reviews" in synthesis_prompt
    assert "Solid codebase" in synthesis_prompt   # a component body fed in
    assert "developer" in synthesis_prompt


async def test_run_full_council_missing_project_errors(db_factory):
    result = await runner.run_full_council(
        "nope", "vanders", complete=lambda m: None, session_factory=db_factory,
        retrieve=fake_retrieve, notify=lambda **kw: None)
    assert result["status"] == "error"


async def test_runs_serialise_on_the_queue(db_factory):
    db = db_factory()
    db.add(Report(id="r2", owner="vanders", project_id="p1", title="second"))
    db.commit()
    db.close()

    state = {"running": 0, "max_running": 0}

    async def slow(messages):
        state["running"] += 1
        state["max_running"] = max(state["max_running"], state["running"])
        await asyncio.sleep(0.05)
        state["running"] -= 1
        return GOOD_OUTPUT

    await asyncio.gather(
        runner.run_report("r1", complete=slow, session_factory=db_factory, retrieve=fake_retrieve,
                          notify=lambda **kw: None),
        runner.run_report("r2", complete=slow, session_factory=db_factory, retrieve=fake_retrieve,
                          notify=lambda **kw: None),
    )
    assert state["max_running"] == 1
