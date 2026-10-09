"""Claims-check JSON per application at data/career/<owner>/claims/<application_id>.json.

Kept on disk rather than as a column so plan 01 needs no schema change; the
document the user edits holds only the letter.
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, Optional

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR
from src.upload_handler import secure_filename


def _path(owner: Optional[str], application_id: str) -> str:
    segment = secure_filename((owner or "local").strip())[:80] or "local"
    aid = secure_filename(application_id)[:64] or "unknown"
    return os.path.join(DATA_DIR, "career", segment, "claims", f"{aid}.json")


def save_claims(owner: Optional[str], application_id: str, payload: Dict[str, Any]) -> str:
    path = _path(owner, application_id)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, payload, indent=2)
    return path


def load_claims(owner: Optional[str], application_id: str) -> Optional[Dict[str, Any]]:
    path = _path(owner, application_id)
    if not os.path.exists(path):
        return None
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else None
    except Exception:
        return None
