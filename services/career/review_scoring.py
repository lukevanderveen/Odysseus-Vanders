"""Reviewer-panel scoring: a fixed per-reviewer dimension registry, parsing of
the JSON fence each reviewer ends with, and the panel aggregate.

Mirrors services/council/scoring.py in spirit (clamp 0-100, drop unknown keys,
never raise) but every reviewer owns its own two dimensions. The JS mirror is
static/js/careerReviewLogic.js — keep REVIEW_DIMS and DIM_LABELS in sync.
"""
from __future__ import annotations

import json
import re
from typing import Dict, Tuple

REVIEW_DIMS: Dict[str, Tuple[str, str]] = {
    "career_recruiter": ("ats_match", "clarity"),
    "career_hiring_manager": ("role_fit", "impact_evidence"),
    "career_engineer": ("technical_credibility", "specificity"),
    "career_hr": ("consistency_with_cv", "professionalism"),
}

DIM_LABELS = {
    "ats_match": "ATS keyword match",
    "clarity": "Clarity",
    "role_fit": "Role fit",
    "impact_evidence": "Impact evidence",
    "technical_credibility": "Technical credibility",
    "specificity": "Specificity",
    "consistency_with_cv": "Consistency with CV",
    "professionalism": "Professionalism",
}

VERDICTS = ("advance", "maybe", "reject")
PANEL_VERDICTS = ("advance", "revise", "rewrite")

_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_TRAILING_FENCE_RE = re.compile(r"```(?:json)?\s*\{[^`]*\}\s*```\s*$", re.S)


def dims_json_example(department: str) -> str:
    body = {"verdict": "maybe"}
    body.update({d: 70 for d in REVIEW_DIMS.get(department, ())})
    return json.dumps(body)


def _last_fence(text: str) -> dict:
    for raw in reversed(_FENCE_RE.findall(text or "")):
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if isinstance(data, dict):
            return data
    return {}


def _clamp(v) -> int:
    return int(round(max(0, min(100, float(v)))))


def parse_review_block(text: str, department: str) -> dict:
    """``{"verdict": advance|maybe|reject, "scores": {dim: 0-100}}`` from the
    last JSON fence; unknown dims dropped, non-numbers dropped, bools excluded."""
    data = _last_fence(text)
    verdict = str(data.get("verdict", "")).strip().lower()
    scores = {}
    for dim in REVIEW_DIMS.get(department, ()):
        v = data.get(dim)
        if isinstance(v, (int, float)) and not isinstance(v, bool):
            scores[dim] = _clamp(v)
    return {"verdict": verdict if verdict in VERDICTS else "maybe", "scores": scores}


def parse_panel_verdict(text: str) -> str:
    v = str(_last_fence(text).get("panel_verdict", "")).strip().lower()
    return v if v in PANEL_VERDICTS else "revise"


def strip_json_fence(text: str) -> str:
    return _TRAILING_FENCE_RE.sub("", text or "").strip()


def aggregate_panel(verdicts: dict) -> dict:
    """Per-reviewer mean of its scores, overall mean of those, verdict tally."""
    by_reviewer: Dict[str, int] = {}
    counts: Dict[str, int] = {}
    for dept, v in (verdicts or {}).items():
        scores = [s for s in (v.get("scores") or {}).values() if isinstance(s, (int, float))]
        if scores:
            by_reviewer[dept] = int(round(sum(scores) / len(scores)))
        verdict = v.get("verdict")
        if verdict:
            counts[verdict] = counts.get(verdict, 0) + 1
    overall = int(round(sum(by_reviewer.values()) / len(by_reviewer))) if by_reviewer else None
    return {"overall": overall, "by_reviewer": by_reviewer, "verdict_counts": counts}
