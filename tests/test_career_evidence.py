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
