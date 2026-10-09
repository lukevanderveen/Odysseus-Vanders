"""POST/GET /api/career/applications/{id}/cover-letter (plan 01)."""
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
    # A collection-time stub (writes "{}") would make settings/claims saves vanish.
    sys.modules.pop("core.atomic_io", None)
    sys.modules.pop("services.career.settings", None)
    sys.modules.pop("services.career.claims_store", None)

_sa, _sa_orm, _sa_pool, _coredb, sr, cs, claims_store = _import_real(
    "sqlalchemy", "sqlalchemy.orm", "sqlalchemy.pool", "core.database",
    "routes.career_studio_routes", "services.career.settings", "services.career.claims_store",
    force=("core.database", "routes.career_studio_routes", "routes.document_helpers"),
)
JobApplication = _coredb.JobApplication
Document = _coredb.Document
DocumentVersion = _coredb.DocumentVersion

_LETTER = 'Dear Acme,\n\nI built FastAPI services backed by PostgreSQL.\n\n```json\n{"claims": [{"claim": "built FastAPI services", "evidence": "Beta Ltd"}]}\n```'


class FakeRag:
    def search(self, query, k=5, owner=None):
        return [{"document": "Built FastAPI services at Beta Ltd", "similarity": 0.9,
                 "metadata": {"filename": "cv-luke.pdf", "owner": owner}}]


@pytest.fixture()
def env(monkeypatch, tmp_path):
    engine = _sa.create_engine("sqlite://", connect_args={"check_same_thread": False},
                               poolclass=_sa_pool.StaticPool)
    _coredb.Base.metadata.create_all(bind=engine)
    TestSession = _sa_orm.sessionmaker(bind=engine)
    monkeypatch.setattr(sr, "SessionLocal", TestSession)
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path))
    monkeypatch.setattr(claims_store, "DATA_DIR", str(tmp_path))
    current = {"user": "vanders"}
    monkeypatch.setattr(sr, "get_current_user", lambda request: current["user"])
    outputs = ['{"hard": ["FastAPI", "PostgreSQL"], "nice": []}', _LETTER]
    calls = []

    async def fake_complete(messages, owner=None):
        calls.append(messages)
        return outputs[(len(calls) - 1) % 2]

    monkeypatch.setattr(sr, "complete", fake_complete)
    cs.save_career_settings("vanders", {"cv_filename": "cv-luke.pdf"})

    db = TestSession()
    db.add(JobApplication(id="a1", owner="vanders", company="Acme", role="Backend",
                          jd_text="Need FastAPI and PostgreSQL"))
    db.add(JobApplication(id="nojd", owner="vanders", company="Acme", role="Backend"))
    db.add(JobApplication(id="a2", owner="other", company="Theirs", role="Dev", jd_text="x"))
    db.commit()
    db.close()

    app = FastAPI()
    app.include_router(sr.setup_career_studio_routes(FakeRag()))
    return TestClient(app), TestSession, calls


def test_draft_creates_document_claims_and_warnings(env):
    client, TestSession, calls = env
    res = client.post("/api/career/applications/a1/cover-letter")
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["version"] == 1
    assert body["claims"] == [{"claim": "built FastAPI services", "evidence": "Beta Ltd"}]
    assert body["requirements"] == {"hard": ["FastAPI", "PostgreSQL"], "nice": []}
    assert body["rubric_warnings"] == []
    assert len(calls) == 2
    db = TestSession()
    doc = db.get(Document, body["doc_id"])
    assert doc.owner == "vanders" and doc.language == "markdown" and doc.session_id is None
    assert "```json" not in doc.current_content and doc.current_content.startswith("Dear Acme,")
    assert doc.title == "Cover letter — Acme Backend"
    assert db.get(JobApplication, "a1").cover_letter_doc_id == doc.id
    db.close()
    got = client.get("/api/career/applications/a1/cover-letter").json()
    assert got["doc_id"] == body["doc_id"] and got["claims"] == body["claims"]


def test_second_draft_versions_the_same_document(env):
    client, TestSession, _ = env
    first = client.post("/api/career/applications/a1/cover-letter").json()
    second = client.post("/api/career/applications/a1/cover-letter").json()
    assert second["doc_id"] == first["doc_id"] and second["version"] == 2
    db = TestSession()
    assert db.query(Document).count() == 1
    assert db.query(DocumentVersion).filter_by(document_id=first["doc_id"]).count() == 2
    assert db.get(Document, first["doc_id"]).version_count == 2
    db.close()


def test_409_without_cv_or_jd(env, monkeypatch):
    client, _, _ = env
    assert client.post("/api/career/applications/nojd/cover-letter").status_code == 409
    cs.save_career_settings("vanders", {"cv_filename": ""})
    res = client.post("/api/career/applications/a1/cover-letter")
    assert res.status_code == 409 and "Setup" in res.json()["detail"]


def test_409_when_cv_has_no_retrievable_chunks(monkeypatch, env):
    client, _, _ = env

    class EmptyRag:
        def search(self, query, k=5, owner=None):
            return []

    app = FastAPI()
    app.include_router(sr.setup_career_studio_routes(EmptyRag()))
    assert TestClient(app).post("/api/career/applications/a1/cover-letter").status_code == 409


def test_503_without_rag(env):
    app = FastAPI()
    app.include_router(sr.setup_career_studio_routes(None))
    assert TestClient(app).post("/api/career/applications/a1/cover-letter").status_code == 503


def test_foreign_application_is_404(env):
    client, _, _ = env
    assert client.post("/api/career/applications/a2/cover-letter").status_code == 404
    assert client.get("/api/career/applications/a2/cover-letter").status_code == 404


def test_get_before_any_draft_is_empty(env):
    client, _, _ = env
    assert client.get("/api/career/applications/a1/cover-letter").json() == {
        "doc_id": None, "version": 0, "claims": [], "rubric_warnings": [], "requirements": {}}
