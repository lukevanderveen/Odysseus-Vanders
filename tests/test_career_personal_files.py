"""Career personal files: stored under the owner's upload dir and indexed with owner metadata."""
import pytest

from services.career import personal_files as pf


class FakeRag:
    def __init__(self):
        self.docs = []
        self.deleted = []

    def _split_into_chunks(self, text, chunk_size=500):
        return [text[i:i + chunk_size] for i in range(0, len(text), chunk_size)] or []

    def add_document(self, text, metadata):
        self.docs.append((text, metadata))
        return True

    def delete_by_source(self, source):
        self.deleted.append(source)
        return 1


@pytest.fixture()
def uploads(tmp_path, monkeypatch):
    import routes.personal_routes as pr
    monkeypatch.setattr(pr, "UPLOADS_DIR", str(tmp_path))
    return tmp_path


def test_store_indexes_every_chunk_with_owner(uploads):
    rag = FakeRag()
    out = pf.store_personal_file(rag, "vanders", "cv-luke.md", b"# Luke\n" + b"x" * 900)
    assert out["filename"] == "cv-luke.md"
    assert out["chunks"] == 2
    assert (uploads / "vanders" / out["stored_filename"]).exists()
    assert all(m["owner"] == "vanders" and m["filename"] == "cv-luke.md" for _, m in rag.docs)


def test_store_rejects_empty_text(uploads):
    with pytest.raises(ValueError):
        pf.store_personal_file(FakeRag(), "vanders", "empty.md", b"   ")


def test_list_filters_by_prefix_and_owner(uploads):
    rag = FakeRag()
    pf.store_personal_file(rag, "vanders", "cover-letter-example-acme.md", b"JD...\nLetter...")
    pf.store_personal_file(rag, "vanders", "cv-luke.md", b"cv")
    pf.store_personal_file(rag, "other", "cover-letter-example-theirs.md", b"x")
    names = [f["filename"] for f in pf.list_personal_files("vanders", "cover-letter-example-")]
    assert names == ["cover-letter-example-acme.md"]


def test_delete_removes_file_and_chunks(uploads):
    rag = FakeRag()
    out = pf.store_personal_file(rag, "vanders", "cv-luke.md", b"cv text")
    assert pf.delete_personal_file(rag, "vanders", "cv-luke.md") is True
    assert not (uploads / "vanders" / out["stored_filename"]).exists()
    assert rag.deleted == [out["path"]]
    assert pf.delete_personal_file(rag, "vanders", "missing.md") is False
