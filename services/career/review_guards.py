"""Deterministic checks on reviewer line edits.

Reviewers are told not to invent experience, but personas that demand
"a number" still propose placeholders ("[degree]") or made-up figures, and
suggest phrasing the candidate has banned. Each rewrite is checked against
the CV, the phrase it replaces and the voice rules; anything
unsupported is listed under a "Tool checks" section so the candidate and the
panel chair can see which edits not to take.
"""
from __future__ import annotations

import re
from typing import List, Sequence, Set, Tuple

from services.career.letter_checks import BANNED_PHRASES, voice_problems

_EDIT_RE = re.compile(r'replace:\s*["“](.*?)["”]\s*(?:→|->)\s*with:\s*["“](.*?)["”]', re.I)
_PLACEHOLDER_RE = re.compile(r"\[[^\]\n]+\]")
_NUMBER_RE = re.compile(r"\d[\d,.]*")


def line_edits(body: str) -> List[Tuple[str, str]]:
    return [(m.group(1), m.group(2)) for m in _EDIT_RE.finditer(body or "")]


def _numbers(text: str) -> Set[str]:
    return {n.replace(",", "").rstrip(".") for n in _NUMBER_RE.findall(text or "")}


def unsupported_edit_warnings(body: str, cv_text: str,
                              banned_phrases: Sequence[str] = BANNED_PHRASES) -> List[str]:
    known = _numbers(cv_text)
    warnings: List[str] = []
    for original, rewrite in line_edits(body):
        for ph in _PLACEHOLDER_RE.findall(rewrite):
            warnings.append(f'Edit "{rewrite}" contains the placeholder {ph}; fill it from the CV or skip the edit.')
        invented = sorted(_numbers(rewrite) - _numbers(original) - known)
        if invented:
            warnings.append(f'Edit "{rewrite}" uses figures not in the CV ({", ".join(invented)}); '
                            "use a real figure or skip the edit.")
        voice = voice_problems(rewrite, banned_phrases)
        if voice:
            warnings.append(f'Edit "{rewrite}" breaks your voice rules: {"; ".join(voice)}.')
    return warnings


def annotate(body: str, cv_text: str, banned_phrases: Sequence[str] = BANNED_PHRASES) -> str:
    warnings = unsupported_edit_warnings(body, cv_text, banned_phrases)
    if not warnings:
        return body
    return body + "\n\n## Tool checks\n" + "\n".join(f"- {w}" for w in warnings)
