"""Project indexer — per-project Chroma collection + graceful degradation.

Drives services/projects/indexer.py (Agent Council Phase 1). The collection
factory and embedder are injected so no ChromaDB server is required; the
degraded path is the project-wide contract (never crash when Chroma is down).
"""

import pytest

from services.projects.indexer import (
    collect_source_files,
    collection_name,
    index_project,
)


class FakeCollection:
    def __init__(self):
        self.upserts = []

    def upsert(self, ids, embeddings, documents, metadatas):
        self.upserts.append(
            {"ids": ids, "embeddings": embeddings,
             "documents": documents, "metadatas": metadatas}
        )


def _fake_embed(texts):
    return [[0.0, 1.0, 0.0] for _ in texts]


@pytest.fixture()
def project(tmp_path):
    (tmp_path / "app.py").write_text("print('hi')\n" * 20, encoding="utf-8")
    (tmp_path / "README.md").write_text("# readme\n", encoding="utf-8")
    sub = tmp_path / "src"
    sub.mkdir()
    (sub / "core.py").write_text("x = 1\n", encoding="utf-8")
    return tmp_path


def test_collection_name_is_per_project():
    assert collection_name("abc123") == "project_abc123"


def test_collect_skips_vendor_binary_and_oversized(project):
    vend = project / "node_modules" / "pkg"
    vend.mkdir(parents=True)
    (vend / "index.js").write_text("x", encoding="utf-8")
    (project / "logo.png").write_bytes(b"\x89PNG\x00\x00")
    (project / "huge.py").write_text("a" * 1000, encoding="utf-8")

    files = collect_source_files(project, max_file_bytes=500)
    rels = sorted(f.relative_to(project).as_posix() for f in files)
    assert rels == ["README.md", "app.py", "src/core.py"]


def test_index_project_upserts_chunks_with_metadata(project):
    fake = FakeCollection()
    result = index_project(
        "p1", str(project), owner="vanders",
        collection_factory=lambda name: fake, embed=_fake_embed,
    )

    assert result["status"] == "ok"
    assert result["files"] == 3
    assert result["chunks"] >= 3
    assert result["ref"]  # staleness ref stamped

    all_ids = [i for u in fake.upserts for i in u["ids"]]
    assert len(all_ids) == len(set(all_ids)) == result["chunks"]
    metas = [m for u in fake.upserts for m in u["metadatas"]]
    assert all(m["owner"] == "vanders" and m["project_id"] == "p1" for m in metas)
    assert any(m["file"] == "app.py" for m in metas)


def test_index_project_batches_upserts(tmp_path):
    # ~120 chunks (chunk size 1000, overlap 200 → stride 800)
    (tmp_path / "big.md").write_text("z" * (800 * 120), encoding="utf-8")
    fake = FakeCollection()
    result = index_project(
        "p2", str(tmp_path), owner=None,
        collection_factory=lambda name: fake, embed=_fake_embed,
    )
    assert result["status"] == "ok"
    assert len(fake.upserts) > 1
    assert all(len(u["ids"]) <= 100 for u in fake.upserts)


def test_index_project_degraded_when_chroma_unreachable(project):
    def broken_factory(name):
        raise ConnectionError("chroma down")

    result = index_project(
        "p3", str(project), owner="vanders",
        collection_factory=broken_factory, embed=_fake_embed,
    )
    assert result["status"] == "degraded"
    assert "chroma down" in result["error"]


def test_index_project_degraded_when_embedding_fails(project):
    def broken_embed(texts):
        raise RuntimeError("onnx kaput")

    result = index_project(
        "p4", str(project), owner="vanders",
        collection_factory=lambda name: FakeCollection(), embed=broken_embed,
    )
    assert result["status"] == "degraded"
    assert "onnx kaput" in result["error"]
