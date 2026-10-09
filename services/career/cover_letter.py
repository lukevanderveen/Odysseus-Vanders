"""Cover letter drafting (plan 01).

Two model calls: extract the posting's requirements as JSON, then write the
letter grounded in CV chunks retrieved by those requirements plus the
candidate's closest prior letters. The letter ends with a fenced JSON block
mapping claims to evidence; it is split off and stored separately so the
document the user edits is only the letter.
"""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from services.career.llm import render_template

logger = logging.getLogger(__name__)

_PACKAGED_PROMPTS = Path(__file__).parent / "prompts"
MAX_WORDS = 300
# Display casing; matched case-insensitively so warnings read naturally.
BANNED_PHRASES = ("passionate", "thrilled", "I am writing to apply", "great fit", "fast-paced", "synergy", "leverage")
_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.S)
_TRAILING_FENCE_RE = re.compile(r"\n*```(?:json)?\s*\{.*?\}\s*```\s*$", re.S)


class NoEvidenceError(RuntimeError):
    """Raised when no CV chunks could be retrieved for the owner."""


def _prompt_dirs():
    from core.constants import DATA_DIR
    return (Path(DATA_DIR) / "career" / "prompts", _PACKAGED_PROMPTS)


def load_prompt(name: str) -> str:
    """User override at data/career/prompts/<name>.md wins over the packaged text."""
    for d in _prompt_dirs():
        f = d / f"{name}.md"
        if f.is_file():
            return f.read_text(encoding="utf-8")
    raise FileNotFoundError(f"No career prompt named '{name}'")


def _first_json_object(text: str) -> Optional[Dict[str, Any]]:
    """Fenced block first, then the outermost {...} span, else None."""
    text = text or ""
    candidates = [m.group(1) for m in _FENCE_RE.finditer(text)]
    start, end = text.find("{"), text.rfind("}")
    if start >= 0 and end > start:
        candidates.append(text[start:end + 1])
    for cand in candidates:
        try:
            obj = json.loads(cand)
        except (json.JSONDecodeError, TypeError):
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _str_list(value: Any, cap: int) -> List[str]:
    if not isinstance(value, list):
        return []
    return [str(v).strip() for v in value if str(v).strip()][:cap]


async def extract_requirements(jd_text: str, complete, owner: Optional[str]) -> Dict[str, List[str]]:
    prompt = render_template(load_prompt("requirements"), {"jd_text": jd_text or ""})
    raw = await complete([{"role": "user", "content": prompt}], owner)
    obj = _first_json_object(raw) or {}
    return {"hard": _str_list(obj.get("hard"), 5), "nice": _str_list(obj.get("nice"), 3)}


def split_claims(text: str) -> Tuple[str, List[Dict[str, str]]]:
    body = (text or "").rstrip()
    m = _TRAILING_FENCE_RE.search(body)
    claims: List[Dict[str, str]] = []
    if m:
        obj = _first_json_object(m.group(0)) or {}
        for c in obj.get("claims") or []:
            if isinstance(c, dict) and c.get("claim"):
                claims.append({"claim": str(c.get("claim")), "evidence": str(c.get("evidence") or "")})
        body = body[:m.start()].rstrip()
    return body, claims


def _bullets(items: List[str]) -> str:
    return "\n".join(f"- {i}" for i in items) or "- (none listed)"


async def draft_cover_letter(app, owner: Optional[str], complete, evidence,
                             requirements: Optional[Dict[str, List[str]]] = None,
                             settings: Optional[Dict[str, Any]] = None) -> Tuple[str, List[Dict[str, str]]]:
    if settings is None:
        from services.career.settings import load_career_settings
        settings = load_career_settings(owner)
    if requirements is None:
        requirements = await extract_requirements(app.jd_text or "", complete, owner)
    query = " ".join(requirements.get("hard", []) + requirements.get("nice", [])) or f"{app.role} experience"
    cv = evidence.cv_chunks(query)
    if not cv:
        raise NoEvidenceError("No CV text found — upload a CV in Career → Setup first.")
    examples = evidence.example_pairs(app.jd_text or "")
    prompt = render_template(load_prompt("cover_letter"), {
        "company": app.company or "",
        "role": app.role or "",
        "hard_requirements": _bullets(requirements.get("hard", [])),
        "nice_requirements": _bullets(requirements.get("nice", [])),
        "cv_evidence": "\n\n".join(f"- {c}" for c in cv),
        "examples": "\n\n---\n\n".join(examples) or "(none yet)",
        "voice_rules": (settings.get("voice_rules") or "").strip() or "(none given — plain and specific)",
    })
    raw = await complete([{"role": "user", "content": prompt}], owner)
    return split_claims(raw)


def rubric_check(markdown: str, hard_requirements: List[str]) -> List[str]:
    warnings: List[str] = []
    text = markdown or ""
    words = len(text.split())
    if words > MAX_WORDS:
        warnings.append(f"Letter is {words} words; keep it under {MAX_WORDS} words.")
    low = text.lower()
    for phrase in BANNED_PHRASES:
        if phrase.lower() in low:
            warnings.append(f'Remove the phrase "{phrase}".')
    reqs = [r for r in hard_requirements if r.strip()]
    hit = [r for r in reqs if r.lower() in low]
    if len(reqs) >= 2 and len(hit) < 2:
        missing = ", ".join(r for r in reqs if r not in hit)
        warnings.append(f"Name at least two posting specifics; not mentioned: {missing}.")
    return warnings
