"""Career-owned personal files (CV, example cover letters).

Files live in the same per-owner upload directory the Personal Docs upload
route uses, and every chunk is stamped with ``owner`` so owner-scoped RAG
search can see them (the generic upload route only stamps owner when the
request resolved a user). The display ``filename`` carries a prefix
convention that plan 01 filters on: ``cv-*`` and ``cover-letter-example-*``.

A sidecar ``<stored_filename>.meta.json`` records the display name so listing
does not depend on RAG being up.
"""
from __future__ import annotations

import json
import logging
import os
from typing import Any, Dict, List, Optional

from src.personal_docs import extract_text_for_upload

logger = logging.getLogger(__name__)

CHUNK_SIZE = 500


def _upload_dir(owner: Optional[str]) -> str:
    from routes.personal_routes import _personal_upload_dir_for_owner
    return _personal_upload_dir_for_owner(owner)


def _meta_path(file_path: str) -> str:
    return file_path + ".meta.json"


def store_personal_file(rag, owner: Optional[str], original_name: str, raw: bytes) -> Dict[str, Any]:
    """Write the file, extract text, index every chunk with owner metadata."""
    from routes.personal_routes import _unique_personal_upload_path
    upload_dir = _upload_dir(owner)
    file_path, stored_name, safe_name = _unique_personal_upload_path(upload_dir, original_name)
    with open(file_path, "wb") as f:
        f.write(raw)
    text = extract_text_for_upload(file_path, raw)
    if not text or not text.strip():
        os.remove(file_path)
        raise ValueError(f"No text could be extracted from {safe_name}")

    chunks = rag._split_into_chunks(text, chunk_size=CHUNK_SIZE) if rag else []
    indexed = 0
    for i, chunk in enumerate(chunks):
        metadata = {
            "source": file_path,
            "filename": safe_name,
            "stored_filename": stored_name,
            "directory": upload_dir,
            "type": os.path.splitext(safe_name)[1].lower(),
            "chunk_id": i,
            "owner": owner or "",
        }
        if rag.add_document(chunk, metadata):
            indexed += 1
    with open(_meta_path(file_path), "w", encoding="utf-8") as f:
        json.dump({"filename": safe_name, "stored_filename": stored_name, "chunks": indexed}, f)
    return {"filename": safe_name, "stored_filename": stored_name, "path": file_path, "chunks": indexed}


def list_personal_files(owner: Optional[str], prefix: str = "") -> List[Dict[str, Any]]:
    upload_dir = _upload_dir(owner)
    out = []
    for name in sorted(os.listdir(upload_dir)):
        if not name.endswith(".meta.json"):
            continue
        try:
            with open(os.path.join(upload_dir, name), encoding="utf-8") as f:
                meta = json.load(f)
        except Exception:
            continue
        if meta.get("filename", "").startswith(prefix):
            meta["path"] = os.path.join(upload_dir, meta["stored_filename"])
            out.append(meta)
    return out


def delete_personal_file(rag, owner: Optional[str], filename: str) -> bool:
    for meta in list_personal_files(owner):
        if meta["filename"] != filename:
            continue
        path = meta["path"]
        if rag and hasattr(rag, "delete_by_source"):
            try:
                rag.delete_by_source(path)
            except Exception:
                logger.warning("DEGRADED: could not remove RAG chunks for %s", path, exc_info=True)
        for p in (path, _meta_path(path)):
            if os.path.exists(p):
                os.remove(p)
        return True
    return False
