"""Personal uploads must extract text from office formats, not decode the bytes."""
from src import personal_docs


def test_docx_goes_through_markitdown(monkeypatch, tmp_path):
    f = tmp_path / "cv-luke.docx"
    f.write_bytes(b"PK\x03\x04 not really a docx")
    monkeypatch.setattr(personal_docs, "extract_office_text", lambda p: "Luke — Software Engineer")
    assert personal_docs.extract_text_for_upload(str(f), f.read_bytes()) == "Luke — Software Engineer"


def test_pdf_goes_through_pypdf(monkeypatch, tmp_path):
    f = tmp_path / "cv.pdf"
    f.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(personal_docs, "extract_pdf_text", lambda p: "pdf text")
    assert personal_docs.extract_text_for_upload(str(f), f.read_bytes()) == "pdf text"


def test_plain_text_is_decoded(tmp_path):
    f = tmp_path / "notes.md"
    f.write_bytes("# Hello\n".encode("utf-8"))
    assert personal_docs.extract_text_for_upload(str(f), f.read_bytes()) == "# Hello\n"


def test_upload_route_uses_shared_extractor():
    src = open("routes/personal_routes.py", encoding="utf-8").read()
    assert "extract_text_for_upload(" in src
    assert 'if ext == ".pdf":' not in src
