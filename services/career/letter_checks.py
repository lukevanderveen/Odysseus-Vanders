"""Deterministic cover-letter checks (plan 05a).

Cheap, model-free checks run on every draft and on the baseline, so the
reviewer panel can spend its attention on judgement. Each check returns a
CheckResult; ``warnings`` turns the failures into the strings the UI shows.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Callable, Dict, List, Optional, Sequence

LETTER_WORDS = (250, 400)
# Display casing; matched case-insensitively so warnings read naturally.
BANNED_PHRASES = ("passionate", "thrilled", "I am writing to apply", "great fit", "fast-paced", "synergy", "leverage")
_US_SPELLING_RE = re.compile(
    r"\b(?:(?:optimiz|organiz|analyz|realiz|recogniz|prioritiz|utiliz|customiz|minimiz|maximiz)\w*"
    r"|colors?|behaviors?|favorites?|centers?)\b", re.I)
_GREETING_RE = re.compile(r"^(?:dear|hi|hello)\b", re.I)
_JD_LOGISTICS_RE = re.compile(r"hybrid|on-?site|in the office|travel|relocat|location|based in|commut", re.I)
_LETTER_LOGISTICS_RE = re.compile(r"based|located|location|relocat|travel|commut|right to work|on-?site|hybrid", re.I)


_WORD_RE = re.compile(r"[a-z0-9+#]+")
_STOPWORDS = frozenset("and or the for with one more similar any some other experience ability knowledge "
                       "using use good strong of in to a an".split())


def _key_words(text: str) -> List[str]:
    return [w for w in _WORD_RE.findall((text or "").lower()) if len(w) > 2 and w not in _STOPWORDS]


def mentions(letter: str, phrase: str) -> bool:
    """True when at least half of the phrase's key words appear in the letter.

    Extracted requirements and job titles are long ("Degree or similar in
    Software Development", "Junior/Mid Software Engineer"); no letter repeats
    them verbatim, so an exact substring match always fails."""
    keys = _key_words(phrase)
    if not keys:
        return (phrase or "").strip().lower() in (letter or "").lower()
    present = set(_WORD_RE.findall((letter or "").lower()))
    return sum(k in present for k in keys) * 2 >= len(keys)


@dataclass
class CheckContext:
    company: str
    role: str
    jd_text: str = ""
    hard_requirements: Sequence[str] = ()
    other_companies: Sequence[str] = ()
    banned_phrases: Sequence[str] = field(default_factory=lambda: list(BANNED_PHRASES))


@dataclass
class CheckResult:
    name: str
    ok: bool
    detail: str = ""


def paragraphs(letter: str) -> List[str]:
    return [p.strip() for p in re.split(r"\n\s*\n", letter or "") if p.strip()]


def opening_paragraph(letter: str) -> str:
    """First paragraph that isn't a short greeting line."""
    for p in paragraphs(letter):
        if _GREETING_RE.match(p) and len(p.split()) <= 12:
            continue
        return p
    return ""


def _length(letter: str, ctx: CheckContext) -> CheckResult:
    words = len((letter or "").split())
    lo, hi = LETTER_WORDS
    if words < lo:
        return CheckResult("length", False, f"Letter is {words} words; aim for {lo} to {hi}.")
    if words > hi:
        return CheckResult("length", False, f"Letter is {words} words, {words - hi} over the {hi}-word limit.")
    return CheckResult("length", True)


def banned_phrases(settings: Optional[Dict[str, Any]]) -> List[str]:
    """Built-in phrases plus the owner's own, de-duplicated case-insensitively."""
    out, seen = [], set()
    for p in list(BANNED_PHRASES) + list((settings or {}).get("banned_phrases") or []):
        p = str(p).strip()
        if p and p.lower() not in seen:
            seen.add(p.lower())
            out.append(p)
    return out


def voice_problems(text: str, banned: Sequence[str]) -> List[str]:
    low = (text or "").lower()
    problems = [f'remove "{p}"' for p in banned if p.strip() and p.lower() in low]
    if "—" in (text or ""):
        problems.append("replace each em dash with a comma or full stop")
    if "!" in (text or ""):
        problems.append("remove exclamation marks")
    us = sorted({m.group(0) for m in _US_SPELLING_RE.finditer(text or "")})
    if us:
        problems.append(f"use UK spelling for: {', '.join(us)}")
    return problems


def _voice(letter: str, ctx: CheckContext) -> CheckResult:
    problems = voice_problems(letter, ctx.banned_phrases)
    return CheckResult("voice", not problems, "Voice: " + "; ".join(problems) + "." if problems else "")


def _other_names(ctx: CheckContext) -> List[str]:
    own = (ctx.company or "").lower()
    return [n for n in ctx.other_companies
            if n.strip() and own not in n.lower() and n.lower() not in own]


def _names(letter: str, ctx: CheckContext) -> CheckResult:
    opening = opening_paragraph(letter)
    problems = []
    if ctx.company and ctx.company.lower() not in opening.lower():
        problems.append(f"name {ctx.company} in the opening paragraph")
    if ctx.role and not mentions(opening, ctx.role):
        problems.append(f"name the {ctx.role} role in the opening paragraph")
    leaked = [n for n in _other_names(ctx) if re.search(rf"(?<!\w){re.escape(n)}(?!\w)", letter or "")]
    if leaked:
        problems.append(f"remove other company names: {', '.join(leaked)}")
    return CheckResult("names", not problems, "Names: " + "; ".join(problems) + "." if problems else "")


def _logistics(letter: str, ctx: CheckContext) -> CheckResult:
    if not _JD_LOGISTICS_RE.search(ctx.jd_text or "") or _LETTER_LOGISTICS_RE.search(letter or ""):
        return CheckResult("logistics", True)
    return CheckResult("logistics", False,
                       "The advert mentions location or travel; state where you're based and that you can travel.")


def _specifics(letter: str, ctx: CheckContext) -> CheckResult:
    reqs = [r for r in ctx.hard_requirements if r.strip()]
    hit = [r for r in reqs if mentions(letter, r)]
    if len(reqs) < 2 or len(hit) >= 2:
        return CheckResult("specifics", True)
    missing = ", ".join(r for r in reqs if r not in hit)
    return CheckResult("specifics", False, f"Name at least two posting specifics; not mentioned: {missing}.")


CHECKS: List[Callable[[str, CheckContext], CheckResult]] = [_length, _voice, _names, _logistics, _specifics]


def run_checks(letter: str, ctx: CheckContext) -> List[CheckResult]:
    return [check(letter or "", ctx) for check in CHECKS]


def warnings(results: List[CheckResult]) -> List[str]:
    return [r.detail for r in results if not r.ok]
