"""Baseline scores for past cover letters (plan 05a).

Runs the deterministic checks plus sentence tagging over letters already
written, so later changes (examples, prompts, research) have a number to beat.
Stored as JSON at data/career/<owner>/baseline.json.
"""
from __future__ import annotations

import json
import os
from dataclasses import asdict
from typing import Any, Dict, List, Optional

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR
from services.career.letter_checks import CheckContext, run_checks
from services.career.letter_tagger import balance, balance_check, tag_letter
from src.upload_handler import secure_filename


async def score_letter(letter: str, ctx: CheckContext, complete, owner: Optional[str] = None) -> Dict[str, Any]:
    checks = run_checks(letter, ctx)
    tags = await tag_letter(letter, ctx.company, ctx.role, list(ctx.hard_requirements), complete, owner)
    b = balance(tags)
    if tags:
        checks.append(balance_check(b))
    return {"words": len((letter or "").split()), "checks": [asdict(c) for c in checks],
            "balance": b, "tags": tags}


def summarise(scored: List[Dict[str, Any]]) -> Dict[str, Any]:
    if not scored:
        return {"letters": 0, "avg_words": 0, "avg_company_bridge_pct": None, "pass_rate": {}}
    tagged = [s["balance"]["company_bridge_pct"] for s in scored if s["balance"]["words"]]
    runs: Dict[str, List[bool]] = {}
    for s in scored:
        for c in s["checks"]:
            runs.setdefault(c["name"], []).append(bool(c["ok"]))
    return {
        "letters": len(scored),
        "avg_words": round(sum(s["words"] for s in scored) / len(scored)),
        "avg_company_bridge_pct": round(sum(tagged) / len(tagged)) if tagged else None,
        "pass_rate": {name: round(100 * sum(oks) / len(oks)) for name, oks in runs.items()},
    }


def _path(owner: Optional[str]) -> str:
    segment = secure_filename((owner or "local").strip())[:80] or "local"
    return os.path.join(DATA_DIR, "career", segment, "baseline.json")


def save_baseline(owner: Optional[str], result: Dict[str, Any]) -> None:
    path = _path(owner)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, result, indent=2)


def load_baseline(owner: Optional[str]) -> Optional[Dict[str, Any]]:
    path = _path(owner)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None
