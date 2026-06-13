"""Council personas — CrewMember.department column, migration, and seeding
(Agent Council Phase 3).

A council member is just a CrewMember with ``department`` set; the five
defaults are seeded idempotently per owner on first council use. Uses the
unstub window from tests/test_project_model.py.
"""

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


_sa, _sa_orm, _sa_pool, _coredb, _personas = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool",
    "core.database", "services.council.personas",
    force=("core.database", "services.council.personas"),
)
create_engine = _sa.create_engine
text = _sa.text
sessionmaker = _sa_orm.sessionmaker
StaticPool = _sa_pool.StaticPool
Base = _coredb.Base
CrewMember = _coredb.CrewMember
personas = _personas


@pytest.fixture()
def db_factory():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(bind=engine)


# ── ORM column ───────────────────────────────────────────────────────────────

def test_crew_member_department_roundtrip(db_factory):
    db = db_factory()
    db.add(CrewMember(id="c1", owner="vanders", name="Biz", department="business"))
    db.add(CrewMember(id="c2", owner="vanders", name="Plain chat persona"))
    db.commit()
    assert db.get(CrewMember, "c1").department == "business"
    assert db.get(CrewMember, "c2").department is None  # non-council crew untouched
    db.close()


# ── migration ────────────────────────────────────────────────────────────────

def test_migration_adds_department_to_legacy_table():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    with engine.connect() as conn:
        conn.execute(text("CREATE TABLE crew_members (id TEXT PRIMARY KEY, name TEXT)"))
        conn.commit()

    _coredb._migrate_add_crew_department(engine_=engine)
    _coredb._migrate_add_crew_department(engine_=engine)  # idempotent

    with engine.connect() as conn:
        cols = [r[1] for r in conn.execute(text("PRAGMA table_info(crew_members)"))]
    assert "department" in cols


# ── seeding ──────────────────────────────────────────────────────────────────

def test_seed_creates_five_departments_for_owner(db_factory):
    created = personas.seed_council_members("vanders", session_factory=db_factory)
    assert created == 5
    db = db_factory()
    rows = db.query(CrewMember).filter(CrewMember.owner == "vanders").all()
    assert sorted(r.department for r in rows) == [
        "architect", "business", "developer", "marketing", "research",
    ]
    assert all(r.personality for r in rows)  # personas carry a system prompt
    db.close()


def test_seed_is_idempotent(db_factory):
    personas.seed_council_members("vanders", session_factory=db_factory)
    again = personas.seed_council_members("vanders", session_factory=db_factory)
    assert again == 0
    db = db_factory()
    assert db.query(CrewMember).count() == 5
    db.close()


def test_seed_fills_only_missing_departments(db_factory):
    db = db_factory()
    db.add(CrewMember(id="mine", owner="vanders", name="My Dev", department="developer"))
    db.commit()
    db.close()

    created = personas.seed_council_members("vanders", session_factory=db_factory)
    assert created == 4  # developer already covered by the user's own member

    db = db_factory()
    devs = (db.query(CrewMember)
            .filter(CrewMember.owner == "vanders", CrewMember.department == "developer").all())
    assert [d.id for d in devs] == ["mine"]  # user's member not duplicated or touched
    db.close()


def test_seed_is_owner_scoped(db_factory):
    personas.seed_council_members("vanders", session_factory=db_factory)
    personas.seed_council_members("other", session_factory=db_factory)
    db = db_factory()
    assert db.query(CrewMember).filter(CrewMember.owner == "vanders").count() == 5
    assert db.query(CrewMember).filter(CrewMember.owner == "other").count() == 5
    db.close()
