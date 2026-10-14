"""Letter sentences that restate the CV (plan 05d).

The letter and CV are read together, so a sentence the CV already says is
wasted space. Two signals: a shared run of six or more words (near-copies,
flagged anywhere) and embedding similarity above a calibrated threshold
(paraphrases). A credentials sentence in the opening paragraph (degree,
university, year) is exempt from the similarity signal.

The embedder is injected (``embed(texts) -> vectors``); with none, only copied
runs are flagged, so the check degrades instead of failing.
"""
from __future__ import annotations

import hashlib
import logging
import math
import re
from typing import Callable, Dict, List, Optional, Sequence, Tuple

from services.career.evidence import full_cv_text
from services.career.letter_checks import body_paragraphs

logger = logging.getLogger(__name__)

MIN_UNIT_WORDS = 5
MIN_SHARED_RUN = 6
# all-MiniLM-L6-v2, calibrated 2026-10-14 on three real letters vs the real CV: restatements
# scored 0.83-0.87, new angles on the same projects 0.57-0.71, company sentences <= 0.52.
# Provisional; re-run the calibration as more letters accumulate.
SIMILARITY_THRESHOLD = 0.75
# A one-line credentials clause in the opening is allowed (degree, university, year).
_CREDENTIALS_RE = re.compile(r"\b(?:degree|graduat\w*|universit\w*|bsc|msc|meng|phd|2:1|first[- ]class)\b", re.I)
_SENTENCE_SPLIT_RE = re.compile(r"(?<=[.!?])\s+|\s*[•▪●◦]\s*")
_TOKEN_RE = re.compile(r"[a-z0-9']+")

Embed = Callable[[List[str]], Sequence[Sequence[float]]]


def cv_units(cv_text: str) -> List[str]:
    parts = (p.strip() for p in _SENTENCE_SPLIT_RE.split(cv_text or ""))
    return [p for p in parts if len(p.split()) >= MIN_UNIT_WORDS]


def letter_sentences(letter: str) -> List[Tuple[int, str]]:
    out = []
    for n, para in enumerate(body_paragraphs(letter), start=1):
        out += [(n, s.strip()) for s in re.split(r"(?<=[.!?])\s+", para) if s.strip()]
    return out


def _tokens(text: str) -> List[str]:
    return _TOKEN_RE.findall((text or "").lower())


def shared_run(a: str, b: str, n: int = MIN_SHARED_RUN) -> Optional[str]:
    """Longest run of words the two texts share, if at least ``n`` long."""
    ta, tb = _tokens(a), _tokens(b)
    best_len, best_end = 0, 0
    prev = [0] * (len(tb) + 1)
    for i in range(1, len(ta) + 1):
        cur = [0] * (len(tb) + 1)
        for j in range(1, len(tb) + 1):
            if ta[i - 1] == tb[j - 1]:
                cur[j] = prev[j - 1] + 1
                if cur[j] > best_len:
                    best_len, best_end = cur[j], i
        prev = cur
    return " ".join(ta[best_end - best_len:best_end]) if best_len >= n else None


def _cosine(u: Sequence[float], v: Sequence[float]) -> float:
    dot = sum(x * y for x, y in zip(u, v))
    norm = math.sqrt(sum(x * x for x in u)) * math.sqrt(sum(y * y for y in v))
    return dot / norm if norm else 0.0


class CvOverlap:
    """Bound to one CV; embeds its units once."""

    def __init__(self, cv_text: str, embed: Optional[Embed], threshold: float = SIMILARITY_THRESHOLD):
        self.units = cv_units(cv_text)
        self.embed = embed
        self.threshold = threshold
        self._unit_vecs: Optional[List[Sequence[float]]] = None

    def _vectors(self, sentences: List[str]) -> Optional[Tuple[List, List]]:
        if self.embed is None or not self.units or not sentences:
            return None
        if self._unit_vecs is None:
            self._unit_vecs = list(self.embed(self.units))
        return self._unit_vecs, list(self.embed(sentences))

    def _best(self, vec, unit_vecs) -> Tuple[float, str]:
        scored = [(_cosine(vec, uv), unit) for uv, unit in zip(unit_vecs, self.units)]
        return max(scored) if scored else (0.0, "")

    def max_similarities(self, letter: str) -> List[Tuple[int, str, float]]:
        """(paragraph, sentence, best similarity to any CV unit), for calibration."""
        sents = letter_sentences(letter)
        vecs = self._vectors([s for _, s in sents])
        if vecs is None:
            return []
        unit_vecs, sent_vecs = vecs
        return [(p, s, round(self._best(v, unit_vecs)[0], 2)) for (p, s), v in zip(sents, sent_vecs)]

    def flags(self, letter: str) -> List[dict]:
        sents = letter_sentences(letter)
        vecs = self._vectors([s for _, s in sents])
        out = []
        for i, (para, sentence) in enumerate(sents):
            copied = next(((u, r) for u in self.units if (r := shared_run(sentence, u))), None)
            if copied:
                out.append({"paragraph": para, "sentence": sentence, "bullet": copied[0],
                            "reason": "copied", "shared": copied[1], "similarity": None})
                continue
            if vecs is None or (para == 1 and _CREDENTIALS_RE.search(sentence)):
                continue
            sim, unit = self._best(vecs[1][i], vecs[0])
            if sim >= self.threshold:
                out.append({"paragraph": para, "sentence": sentence, "bullet": unit,
                            "reason": "similar", "shared": None, "similarity": round(sim, 2)})
        return out


_cache: Dict[str, CvOverlap] = {}
_CACHE_SIZE = 4


def flags_for_owner(owner: Optional[str], letter: str) -> Optional[List[dict]]:
    """Overlap flags against the owner's current CV, or None when there is no CV.

    Blocking (embeds locally): call via asyncio.to_thread from async routes.
    The CV's embeddings are reused until the CV text changes."""
    cv = full_cv_text(owner)
    if not cv:
        return None
    key = hashlib.sha256(cv.encode("utf-8")).hexdigest()
    if key not in _cache:
        if len(_cache) >= _CACHE_SIZE:
            _cache.pop(next(iter(_cache)))
        _cache[key] = CvOverlap(cv, default_embed())
    return _cache[key].flags(letter)


def default_embed() -> Optional[Embed]:
    """Local embedding client, or None (copied-run check only) when unavailable."""
    try:
        from src.embeddings import get_embedding_client
        client = get_embedding_client()
    except Exception:
        logger.warning("DEGRADED: embeddings unavailable; CV overlap checks copied runs only", exc_info=True)
        return None
    if client is None:
        return None
    return lambda texts: [list(map(float, v)) for v in client.encode(texts)]
