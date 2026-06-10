"""Project table — schema + owner scoping (Agent Council Phase 1).

Exercises the real ORM model against a throwaway in-memory SQLite engine,
so it pins the schema without touching data/app.db.

Earlier-collected test modules (test_agent_loop.py, test_companion_pairing.py)
stub sqlalchemy/core.database in sys.modules at import time, which would hand
this module MagicMock classes in a full-suite run. We import the real modules
inside an unstub window and put the stubs back so sibling modules collected
after us see the exact environment they see today.
"""

import importlib
import sys
import types

import pytest


def _import_real(*names, force=()):
    """Import real modules even when a sibling test stubbed their dep tree;
    restores the popped entries afterwards (zero blast radius for later tests).

    ``force`` modules are popped even when cached as real file-backed modules —
    a project module imported earlier UNDER stubs is real but holds mock class
    bindings. Never force third-party packages (re-importing sqlalchemy beside
    its cached submodules produces a second, inconsistent package instance).
    """
    def _is_stub(m):
        # A real file-backed module has a str __file__. Sibling stubs are
        # MagicMocks (not ModuleType) or ModuleType subclasses whose
        # __getattr__ fabricates attributes (returns a MagicMock, not a str).
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


_sa, _sa_exc, _sa_orm, _coredb = _import_real(
    "sqlalchemy", "sqlalchemy.exc", "sqlalchemy.orm", "core.database",
    force=("core.database",),
)
create_engine = _sa.create_engine
IntegrityError = _sa_exc.IntegrityError
sessionmaker = _sa_orm.sessionmaker
Base = _coredb.Base
Project = _coredb.Project


@pytest.fixture()
def db():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    session = TestSession()
    yield session
    session.close()


def _mk(db, **overrides):
    fields = dict(
        id="p1",
        owner="vanders",
        name="odysseus",
        path=r"D:\odysseus",
        stack='["python", "fastapi"]',
    )
    fields.update(overrides)
    proj = Project(**fields)
    db.add(proj)
    db.commit()
    return proj


def test_project_roundtrip_with_defaults(db):
    _mk(db)
    got = db.query(Project).one()
    assert got.name == "odysseus"
    assert got.owner == "vanders"
    assert got.stack == '["python", "fastapi"]'
    assert got.archived is False
    assert got.index_ref is None
    assert got.deep_summary is None
    assert got.created_at is not None  # TimestampMixin


def test_project_path_unique_per_owner(db):
    _mk(db)
    with pytest.raises(IntegrityError):
        _mk(db, id="p2")  # same owner + path


def test_same_path_allowed_for_different_owner(db):
    _mk(db)
    _mk(db, id="p2", owner="other")
    assert db.query(Project).count() == 2


def test_owner_scoped_query_excludes_other_users(db):
    _mk(db)
    _mk(db, id="p2", owner="other", path=r"D:\other")
    mine = db.query(Project).filter(Project.owner == "vanders").all()
    assert [p.id for p in mine] == ["p1"]
