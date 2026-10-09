"""create_library_document: session-less doc + first version, owner-stamped."""
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


_sa, _sa_orm, _sa_pool, _coredb, dh = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database", "routes.document_helpers",
    force=("core.database", "routes.document_helpers"),
)


@pytest.fixture()
def db():
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    session = _sa_orm.sessionmaker(bind=engine)()
    yield session
    session.close()


def test_creates_doc_and_initial_version(db):
    doc = dh.create_library_document(db, owner="vanders", title="Cover letter — Acme",
                                     content="Dear team,", language="markdown")
    assert doc.owner == "vanders"
    assert doc.session_id is None
    assert doc.language == "markdown"
    assert doc.version_count == 1
    versions = db.query(_coredb.DocumentVersion).filter_by(document_id=doc.id).all()
    assert len(versions) == 1 and versions[0].content == "Dear team," and versions[0].source == "user"


def test_language_is_sniffed_when_missing(db):
    doc = dh.create_library_document(db, owner=None, title="Note", content="# Heading\n\nprose")
    assert doc.language == "markdown"


def test_post_document_route_uses_helper():
    src = open("routes/document_routes.py", encoding="utf-8").read()
    assert "create_library_document(" in src
