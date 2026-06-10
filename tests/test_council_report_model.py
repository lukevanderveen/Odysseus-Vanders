"""Report table — schema + owner scoping (Agent Council Phase 2).

Exercises the real ORM model against a throwaway in-memory SQLite engine,
so it pins the schema without touching data/app.db.

Uses the same unstub window as tests/test_project_model.py: earlier-collected
modules stub sqlalchemy/core.database in sys.modules, so we import the real
modules and restore the stubs afterwards.
"""

import importlib
import sys
import types

import pytest


def _import_real(*names, force=()):
    """Import real modules even when a sibling test stubbed their dep tree;
    restores the popped entries afterwards (zero blast radius for later tests).
    """
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


_sa, _sa_orm, _coredb = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "core.database",
    force=("core.database",),
)
create_engine = _sa.create_engine
sessionmaker = _sa_orm.sessionmaker
Base = _coredb.Base
Project = _coredb.Project
Report = _coredb.Report


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()


def _mk_project(db, **overrides):
    fields = dict(
        id="p1",
        owner="vanders",
        name="odysseus",
        path=r"D:\odysseus",
    )
    fields.update(overrides)
    proj = Project(**fields)
    db.add(proj)
    db.commit()
    return proj


def _mk_report(db, **overrides):
    fields = dict(
        id="r1",
        owner="vanders",
        project_id="p1",
        title="Developer Review: odysseus",
    )
    fields.update(overrides)
    report = Report(**fields)
    db.add(report)
    db.commit()
    return report


def test_report_roundtrip_with_defaults(db):
    _mk_project(db)
    _mk_report(db)
    got = db.query(Report).one()
    assert got.project_id == "p1"
    assert got.department == "developer"
    assert got.report_type == "developer_review"
    assert got.status == "running"
    assert got.body is None
    assert got.scores is None
    assert got.error is None
    assert got.model is None
    assert got.started_at is None
    assert got.finished_at is None
    assert got.created_at is not None  # TimestampMixin


def test_report_stores_scores_json_and_body(db):
    _mk_project(db)
    scores = '{"grounded": {"technical_readiness": 72}, "judged": {"marketability": 60}}'
    _mk_report(db, body="## Findings\nLooks solid.", scores=scores, status="draft")
    got = db.query(Report).one()
    assert got.scores == scores
    assert got.body.startswith("## Findings")
    assert got.status == "draft"


def test_report_owner_scoped_query_excludes_other_users(db):
    _mk_project(db)
    _mk_report(db)
    _mk_report(db, id="r2", owner="other")
    mine = db.query(Report).filter(Report.owner == "vanders").all()
    assert [r.id for r in mine] == ["r1"]


def test_reports_filterable_by_project_and_status(db):
    _mk_project(db)
    _mk_report(db, id="r1", status="draft")
    _mk_report(db, id="r2", status="approved")
    _mk_report(db, id="r3", project_id="p-other", status="draft")
    drafts = (db.query(Report)
              .filter(Report.project_id == "p1", Report.status == "draft").all())
    assert [r.id for r in drafts] == ["r1"]
