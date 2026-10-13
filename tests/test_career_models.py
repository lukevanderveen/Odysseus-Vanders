"""Career hub tables (plan 00). Uses the unstub window from tests/test_council_personas.py."""
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


def test_job_application_stores_company_site_and_context(db_factory):
    db = db_factory()
    db.add(JobApplication(id="c1", owner="vanders", company="Acme", role="Dev",
                          company_url="https://acme.example", company_context="Builds Rocket, an LLM gateway."))
    db.commit()
    row = db.get(JobApplication, "c1")
    assert row.company_url == "https://acme.example"
    assert row.company_context == "Builds Rocket, an LLM gateway."
    db.close()


def test_migrate_adds_company_columns_to_existing_table():
    engine = _sa.create_engine("sqlite://", poolclass=_sa_pool.StaticPool)
    with engine.connect() as conn:
        conn.execute(_sa.text("CREATE TABLE job_applications (id TEXT PRIMARY KEY, company TEXT, role TEXT)"))
        conn.commit()
    _coredb._migrate_add_job_application_company_cols(engine_=engine)
    _coredb._migrate_add_job_application_company_cols(engine_=engine)  # idempotent
    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(_sa.text("PRAGMA table_info(job_applications)"))]
    assert "company_url" in cols and "company_context" in cols
