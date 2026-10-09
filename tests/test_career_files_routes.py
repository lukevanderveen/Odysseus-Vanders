"""CV upload and example cover-letter pairs through /api/career."""
import importlib
import sys
import types

import pytest

fastapi = pytest.importorskip("fastapi")
from fastapi import FastAPI
from fastapi.testclient import TestClient


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


_atomic = sys.modules.get("core.atomic_io")
if _atomic is not None and not getattr(_atomic, "__file__", None):
    # A collection-time stub (writes "{}") would make settings saves vanish.
    sys.modules.pop("core.atomic_io", None)
    sys.modules.pop("services.career.settings", None)

_sa, _sa_orm, _sa_pool, _coredb, crr, cs, pr = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "routes.career_routes", "services.career.settings", "routes.personal_routes",
    force=("core.database", "routes.career_routes"),
)


class FakeRag:
    def __init__(self):
        self.docs = []

    def _split_into_chunks(self, text, chunk_size=500):
        return [text]

    def add_document(self, text, metadata):
        self.docs.append(metadata)
        return True

    def delete_by_source(self, source):
        return 1


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    monkeypatch.setattr(crr, "SessionLocal", _sa_orm.sessionmaker(bind=engine))
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setattr(pr, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(crr, "get_current_user", lambda request: "vanders")
    rag = FakeRag()
    app = FastAPI()
    app.include_router(crr.setup_career_routes(None, rag))
    return TestClient(app), rag


def test_cv_upload_sets_setting_and_prefixes_filename(env):
    client, rag = env
    res = client.post("/api/career/cv", files={"file": ("Luke CV.md", b"# Luke\nEngineer", "text/markdown")})
    assert res.status_code == 200
    assert res.json()["filename"].startswith("cv-")
    assert cs.load_career_settings("vanders")["cv_filename"] == res.json()["filename"]
    assert rag.docs[0]["owner"] == "vanders"
    assert client.get("/api/career/bootstrap").json()["has_cv"] is True


def test_cv_upload_without_rag_is_503(monkeypatch, tmp_path):
    monkeypatch.setattr(crr, "get_current_user", lambda request: "vanders")
    app = FastAPI()
    app.include_router(crr.setup_career_routes(None, None))
    res = TestClient(app).post("/api/career/cv", files={"file": ("cv.md", b"x", "text/markdown")})
    assert res.status_code == 503


def test_example_pair_lifecycle(env):
    client, _ = env
    res = client.post("/api/career/examples",
                      json={"title": "Acme backend", "jd_text": "Build APIs", "letter_text": "Dear Acme"})
    assert res.status_code == 201
    fname = res.json()["filename"]
    assert fname == "cover-letter-example-acme-backend.md"
    listed = client.get("/api/career/examples").json()["examples"]
    assert [e["filename"] for e in listed] == [fname]
    assert client.delete(f"/api/career/examples/{fname}").json() == {"deleted": True}
    assert client.get("/api/career/examples").json()["examples"] == []
    assert client.delete(f"/api/career/examples/{fname}").status_code == 404


def test_example_pair_requires_both_texts(env):
    client, _ = env
    assert client.post("/api/career/examples", json={"title": "x", "jd_text": "", "letter_text": "y"}).status_code == 422
