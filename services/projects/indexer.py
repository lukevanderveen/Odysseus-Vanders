"""Per-project RAG indexing into an isolated Chroma collection.

Each project gets its own collection (``project_{id}``) so search never
bleeds across projects. Chunking reuses src.personal_docs.split_chunks.
Follows the project-wide graceful-degradation contract: when ChromaDB or the
embedder is unavailable this logs DEGRADED and returns a status dict — it
never raises into the route.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, List, Optional

from services.projects.scanner import SKIP_DIRS, current_index_ref
from src.personal_docs import read_text_file, split_chunks

logger = logging.getLogger(__name__)

# Source/text files worth embedding. Binaries and lockfiles are skipped.
SOURCE_EXTENSIONS = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".mjs", ".cjs",
    ".rs", ".go", ".cs", ".java", ".kt", ".rb", ".php",
    ".c", ".h", ".cpp", ".hpp",
    ".html", ".css", ".scss",
    ".md", ".rst", ".txt",
    ".json", ".yaml", ".yml", ".toml", ".ini", ".cfg",
    ".sql", ".sh", ".ps1",
}
_SKIP_FILENAMES = {"package-lock.json", "yarn.lock", "poetry.lock", "Cargo.lock"}

MAX_FILE_BYTES = 512_000
UPSERT_BATCH = 100


def collection_name(project_id: str) -> str:
    return f"project_{project_id}"


def collect_source_files(project_path, max_file_bytes: int = MAX_FILE_BYTES) -> List[Path]:
    """Indexable files under the project, skipping vendored dirs, non-source
    extensions, lockfiles, and oversized files."""
    root = Path(project_path)
    files: List[Path] = []
    for path in sorted(root.rglob("*")):
        if not path.is_file():
            continue
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        if path.suffix.lower() not in SOURCE_EXTENSIONS:
            continue
        if path.name in _SKIP_FILENAMES:
            continue
        try:
            if path.stat().st_size > max_file_bytes:
                continue
        except OSError:
            continue
        files.append(path)
    return files


def _default_collection_factory(name: str):
    from src.chroma_client import get_chroma_client
    client = get_chroma_client()
    return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})


def _default_embed(texts: List[str]) -> List[List[float]]:
    import numpy as np
    from src.embeddings import get_embedding_client
    vecs = get_embedding_client().encode(texts, normalize_embeddings=True)
    return np.array(vecs, dtype=np.float32).tolist()


def index_project(
    project_id: str,
    project_path: str,
    owner: Optional[str] = None,
    collection_factory: Callable = _default_collection_factory,
    embed: Callable = _default_embed,
) -> Dict:
    """Chunk + embed the project's source files into ``project_{id}``.

    Upserts with deterministic ids (``{relpath}::{chunk_idx}``) so re-indexing
    overwrites in place. Returns ``{"status": "ok", "files", "chunks", "ref"}``
    or ``{"status": "degraded", "error"}``.
    """
    root = Path(project_path)
    files = collect_source_files(root)

    ids: List[str] = []
    documents: List[str] = []
    metadatas: List[Dict] = []
    for path in files:
        rel = path.relative_to(root).as_posix()
        for i, chunk in enumerate(split_chunks(read_text_file(str(path)))):
            ids.append(f"{rel}::{i}")
            documents.append(chunk)
            metadatas.append({
                "project_id": project_id,
                "owner": owner or "",
                "file": rel,
                "chunk": i,
            })

    try:
        collection = collection_factory(collection_name(project_id))
        for start in range(0, len(ids), UPSERT_BATCH):
            end = start + UPSERT_BATCH
            batch_docs = documents[start:end]
            collection.upsert(
                ids=ids[start:end],
                embeddings=embed(batch_docs),
                documents=batch_docs,
                metadatas=metadatas[start:end],
            )
    except Exception as e:
        logger.warning("DEGRADED: project index for %s unavailable: %s", project_id, e)
        return {"status": "degraded", "error": str(e)}

    return {
        "status": "ok",
        "files": len(files),
        "chunks": len(ids),
        "ref": current_index_ref(root),
    }
