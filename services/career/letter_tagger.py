"""Company / bridge / self sentence tagging and the word balance (plan 05a).

Tagging is a separate call from drafting and never sees the balance target,
so the drafter can't label its own sentences to hit it. A "bridge" must name
the need it answers, otherwise it counts as self. Balance is measured in
words so one long self paragraph can't hide behind short company lines.
"""
from __future__ import annotations

from typing import Any, Dict, List, Optional

from services.career.cover_letter import _bullets, _first_json_object, load_prompt
from services.career.letter_checks import CheckResult
from services.career.llm import render_template

TAGS = ("C", "B", "S")
BALANCE_TARGET = 75   # company + bridge, % of words


def _clean(row: Any) -> Optional[Dict[str, Any]]:
    if not isinstance(row, dict) or not str(row.get("text") or "").strip():
        return None
    tag = str(row.get("tag") or "").strip().upper()
    need = str(row.get("need") or "").strip()
    if tag not in TAGS or (tag == "B" and not need):
        tag = "S"
    try:
        paragraph = int(row.get("paragraph") or 0)
    except (TypeError, ValueError):
        paragraph = 0
    return {"paragraph": paragraph, "text": str(row["text"]).strip(), "tag": tag,
            "need": need if tag == "B" else ""}


async def tag_letter(letter: str, company: str, role: str, needs: List[str], complete,
                     owner: Optional[str] = None) -> List[Dict[str, Any]]:
    prompt = render_template(load_prompt("letter_tagger"), {
        "company": company or "", "role": role or "", "needs": _bullets(needs), "letter": letter or "",
    })
    raw = await complete([{"role": "user", "content": prompt}], owner)
    rows = (_first_json_object(raw) or {}).get("sentences") or []
    return [c for c in (_clean(r) for r in rows if isinstance(rows, list)) if c]


def balance(tagged: List[Dict[str, Any]]) -> Dict[str, Any]:
    words = {t: 0 for t in TAGS}
    self_by_paragraph: Dict[int, int] = {}
    for s in tagged:
        n = len(s["text"].split())
        words[s["tag"]] += n
        if s["tag"] == "S":
            self_by_paragraph[s["paragraph"]] = self_by_paragraph.get(s["paragraph"], 0) + n
    total = sum(words.values())
    if not total:
        return {"words": 0, "company_bridge_pct": 0, "self_pct": 0, "worst_paragraph": None}
    cb = round(100 * (words["C"] + words["B"]) / total)
    worst = max(self_by_paragraph, key=self_by_paragraph.get) if self_by_paragraph else None
    return {"words": total, "company_bridge_pct": cb, "self_pct": 100 - cb, "worst_paragraph": worst}


def balance_check(b: Dict[str, Any]) -> CheckResult:
    if b["company_bridge_pct"] >= BALANCE_TARGET:
        return CheckResult("balance", True)
    return CheckResult("balance", False,
                       f"Only {b['company_bridge_pct']}% of words point at the company (target {BALANCE_TARGET}%); "
                       f"paragraph {b['worst_paragraph']} is mostly about you.")
