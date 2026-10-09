"""Reviewer persona seeding (plan 00). Reviewers are CrewMember rows with a career_* department."""
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


_sa, _sa_orm, _sa_pool, _coredb, seeds, personas = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "services.career.seeds", "services.council.personas",
    force=("core.database", "services.career.seeds", "services.council.personas"),
)
CrewMember = _coredb.CrewMember


@pytest.fixture()
def db_factory():
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    return _sa_orm.sessionmaker(bind=engine)


def test_seed_creates_four_reviewers_with_personas(db_factory):
    assert seeds.seed_reviewers("vanders", session_factory=db_factory) == 4
    db = db_factory()
    rows = db.query(CrewMember).filter(CrewMember.owner == "vanders").all()
    assert sorted(r.department for r in rows) == sorted(seeds.REVIEWER_DEPARTMENTS)
    assert all(len(r.personality or "") > 80 for r in rows)
    db.close()


def test_seed_is_idempotent_and_keeps_edits(db_factory):
    seeds.seed_reviewers("vanders", session_factory=db_factory)
    db = db_factory()
    hr = db.query(CrewMember).filter(CrewMember.department == "career_hr").first()
    hr.personality = "Edited."
    db.commit()
    db.close()
    assert seeds.seed_reviewers("vanders", session_factory=db_factory) == 0
    db = db_factory()
    assert db.query(CrewMember).filter(CrewMember.department == "career_hr").first().personality == "Edited."
    db.close()


def test_council_seed_ignores_career_reviewers(db_factory):
    seeds.seed_reviewers("vanders", session_factory=db_factory)
    assert personas.seed_council_members("vanders", session_factory=db_factory) == 5
    db = db_factory()
    assert db.query(CrewMember).count() == 9
    db.close()


def test_persona_files_exist_for_every_department():
    for dept in seeds.REVIEWER_DEPARTMENTS:
        assert "reviewer" in seeds.load_persona(dept).lower() or "you are" in seeds.load_persona(dept).lower()
