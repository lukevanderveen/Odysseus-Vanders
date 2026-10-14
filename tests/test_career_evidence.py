"""Owner-scoped evidence retrieval for cover letters (plan 01)."""
import pytest

from services.career import evidence as ev


class FakeRag:
    def __init__(self, rows):
        self.rows = rows
        self.calls = []

    def search(self, query, k=5, owner=None):
        self.calls.append({"query": query, "k": k, "owner": owner})
        return self.rows


def _row(filename, text, sim):
    return {"document": text, "metadata": {"filename": filename, "owner": "vanders"}, "similarity": sim}


def test_cv_chunks_filters_by_prefix_and_threshold():
    rag = FakeRag([
        _row("cv-luke.pdf", "Built a FastAPI service", 0.9),
        _row("notes.md", "unrelated", 0.95),
        _row("cv-luke.pdf", "weak match", 0.2),
        _row("cover-letter-example-acme.md", "Dear Acme", 0.8),
    ])
    out = ev.cv_chunks("vanders", "fastapi experience", rag=rag)
    assert out == ["Built a FastAPI service"]
    assert rag.calls[0] == {"query": "fastapi experience", "k": ev.FETCH_K, "owner": "vanders"}


def test_cv_chunks_caps_at_k_in_similarity_order():
    rows = [_row("cv-luke.pdf", f"chunk {i}", 0.5 + i / 100) for i in range(12)]
    out = ev.cv_chunks("vanders", "q", k=3, rag=rows and FakeRag(list(reversed(rows))))
    assert out == ["chunk 11", "chunk 10", "chunk 9"]


def test_example_pairs_uses_example_prefix_and_k2():
    rag = FakeRag([
        _row("cover-letter-example-acme.md", "# Acme\n\n## Job description\n\nAPIs\n\n## Cover letter\n\nDear Acme", 0.7),
        _row("cover-letter-example-beta.md", "# Beta ...", 0.6),
        _row("cover-letter-example-gamma.md", "# Gamma ...", 0.5),
        _row("cv-luke.pdf", "cv", 0.99),
    ])
    out = ev.example_pairs("vanders", "Build APIs", rag=rag)
    assert out == ["# Acme\n\n## Job description\n\nAPIs\n\n## Cover letter\n\nDear Acme", "# Beta ..."]


def test_no_rag_returns_empty_lists(monkeypatch):
    monkeypatch.setattr(ev, "get_rag_manager", lambda: None)
    assert ev.cv_chunks("vanders", "q") == []
    assert ev.example_pairs("vanders", "jd") == []


def test_evidence_object_binds_owner():
    rag = FakeRag([_row("cv-luke.pdf", "chunk", 0.8)])
    e = ev.Evidence("vanders", rag=rag)
    assert e.cv_chunks("q") == ["chunk"]
    assert rag.calls[-1]["owner"] == "vanders"


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    import routes.personal_routes as pr
    import services.career.settings as cs
    monkeypatch.setattr(pr, "UPLOADS_DIR", str(tmp_path / "uploads"))
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path / "data"))
    return tmp_path


def _store_cv(owner, name, text):
    from services.career import personal_files as pf
    from services.career.settings import save_career_settings

    class _Rag:
        def _split_into_chunks(self, t, chunk_size=500):
            return [t]

        def add_document(self, t, m):
            return True
    pf.store_personal_file(_Rag(), owner, name, text.encode())
    save_career_settings(owner, {"cv_filename": name})


def test_full_cv_text_returns_whole_current_cv(uploads):
    _store_cv("vanders", "cv-old.md", "Old CV")
    _store_cv("vanders", "cv-luke.md", "BSc Computer Science, Lancaster 2025, 2:1\n\nBuilt a RAG platform")
    assert ev.full_cv_text("vanders") == "BSc Computer Science, Lancaster 2025, 2:1 Built a RAG platform"


def test_full_cv_text_empty_without_cv(uploads):
    assert ev.full_cv_text("vanders") == ""


def test_full_cv_text_caps_length(uploads):
    _store_cv("vanders", "cv-long.md", "x" * (ev.FULL_CV_CHARS + 500))
    assert len(ev.full_cv_text("vanders")) == ev.FULL_CV_CHARS


def test_cv_evidence_prefers_full_cv_over_weak_search(uploads):
    _store_cv("vanders", "cv-luke.md", "BSc Computer Science, Lancaster")
    rag = FakeRag([_row("cv-luke.md", "weak", 0.1)])
    assert ev.cv_evidence("vanders", "Junior Developer at PointWire", rag=rag) == ["BSc Computer Science, Lancaster"]


def test_cv_evidence_falls_back_to_search_without_cv_file(uploads):
    rag = FakeRag([_row("cv-luke.pdf", "Built a FastAPI service", 0.9)])
    assert ev.cv_evidence("vanders", "fastapi", rag=rag) == ["Built a FastAPI service"]


def test_evidence_object_cv_evidence_binds_owner(uploads):
    _store_cv("vanders", "cv-luke.md", "Luke's CV")
    _store_cv("other", "cv-them.md", "Their CV")
    assert ev.Evidence("vanders", rag=FakeRag([])).cv_evidence("q") == ["Luke's CV"]


def test_full_cv_text_collapses_pdf_style_whitespace(uploads):
    _store_cv("vanders", "cv-luke.md", "I\n \nhave\n \nplayed   rugby\n\n\nPage 3")
    assert ev.full_cv_text("vanders") == "I have played rugby Page 3"
