"""Owner-scoped retrieval of the user's CV and example cover letters.

Both live in the personal-docs RAG index with filename prefixes written by
plan 00 (``cv-*``, ``cover-letter-example-*``). VectorRAG applies no
similarity threshold and ranks across every personal file, so we fetch a
wide window, apply our own threshold, then filter on the prefix.
"""
from __future__ import annotations

import logging
from typing import List, Optional

from src.rag_singleton import get_rag_manager

logger = logging.getLogger(__name__)

CV_PREFIX = "cv-"
EXAMPLE_PREFIX = "cover-letter-example-"
SIMILARITY_THRESHOLD = 0.35
FETCH_K = 40
FULL_CV_CHARS = 12000


def _search(owner: Optional[str], query: str, prefix: str, k: int, rag) -> List[str]:
    rag = rag if rag is not None else get_rag_manager()
    if rag is None:
        logger.warning("DEGRADED: RAG unavailable; no %s evidence for %s", prefix, owner)
        return []
    try:
        rows = rag.search(query, k=FETCH_K, owner=owner)
    except Exception:
        logger.warning("DEGRADED: evidence search failed", exc_info=True)
        return []
    keep = []
    for r in rows or []:
        name = str((r.get("metadata") or {}).get("filename") or "")
        if not name.startswith(prefix):
            continue
        if float(r.get("similarity") or 0.0) < SIMILARITY_THRESHOLD:
            continue
        keep.append((float(r.get("similarity") or 0.0), r.get("document") or ""))
    keep.sort(key=lambda t: t[0], reverse=True)
    return [doc for _, doc in keep[:k] if doc]


def cv_chunks(owner: Optional[str], query: str, k: int = 8, rag=None) -> List[str]:
    return _search(owner, query, CV_PREFIX, k, rag)


def full_cv_text(owner: Optional[str]) -> str:
    """The whole current CV (settings.cv_filename, else the newest cv-* file).

    A CV fits in a prompt, and similarity search against a job ad misses
    sections like education, so callers prefer this over chunks."""
    from services.career.personal_files import list_personal_files
    from services.career.settings import load_career_settings
    from src.personal_docs import extract_text_for_upload
    try:
        files = list_personal_files(owner, CV_PREFIX)
        if not files:
            return ""
        current = load_career_settings(owner).get("cv_filename")
        meta = next((f for f in files if f["filename"] == current), files[-1])
        with open(meta["path"], "rb") as f:
            raw = f.read()
        # PDF extraction often emits one word per line; collapse it to save prompt space.
        return " ".join((extract_text_for_upload(meta["path"], raw) or "").split())[:FULL_CV_CHARS]
    except Exception:
        logger.warning("DEGRADED: could not read full CV for %s", owner, exc_info=True)
        return ""


def cv_evidence(owner: Optional[str], query: str, k: int = 8, rag=None) -> List[str]:
    """Full CV when readable, otherwise the retrieved chunks."""
    full = full_cv_text(owner)
    return [full] if full else cv_chunks(owner, query, k=k, rag=rag)


def example_pairs(owner: Optional[str], jd_text: str, k: int = 2, rag=None) -> List[str]:
    return _search(owner, jd_text, EXAMPLE_PREFIX, k, rag)


class Evidence:
    """Owner-bound facade injected into cover_letter / review services."""

    def __init__(self, owner: Optional[str], rag=None):
        self.owner = owner
        self._rag = rag

    def cv_chunks(self, query: str, k: int = 8) -> List[str]:
        return cv_chunks(self.owner, query, k=k, rag=self._rag)

    def cv_evidence(self, query: str, k: int = 8) -> List[str]:
        return cv_evidence(self.owner, query, k=k, rag=self._rag)

    def example_pairs(self, jd_text: str, k: int = 2) -> List[str]:
        return example_pairs(self.owner, jd_text, k=k, rag=self._rag)
