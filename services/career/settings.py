"""Per-owner Career hub settings.

Stored as JSON at data/career/<owner>/settings.json (atomic writes). Unknown
keys are ignored on save; missing keys fall back to DEFAULTS on load.
"""
from __future__ import annotations

import copy
import json
import logging
import os
from typing import Any, Dict, Optional

from core.atomic_io import atomic_write_json
from core.constants import DATA_DIR
from src.upload_handler import secure_filename

logger = logging.getLogger(__name__)

DEFAULTS: Dict[str, Any] = {
    "cv_filename": "",
    "voice_rules": "",
    "target_roles": [],
    "locations": [],
    "disclosure_default": "anonymous",      # anonymous | name_only | public
    "project_disclosure": {},               # project_id -> level
    "blocklist_terms": [],
    "scan_account_ids": [],
    "prefilter_terms": [],
    "nudge_after_days": 10,
    "ghosted_after_days": 21,
    "narrative_uses_tracker_stats": False,
}

_INT_FIELDS = ("nudge_after_days", "ghosted_after_days")
_LIST_FIELDS = ("target_roles", "locations", "blocklist_terms", "scan_account_ids", "prefilter_terms")


def settings_path(owner: Optional[str]) -> str:
    segment = secure_filename((owner or "local").strip())[:80] or "local"
    return os.path.join(DATA_DIR, "career", segment, "settings.json")


def load_career_settings(owner: Optional[str]) -> Dict[str, Any]:
    out = copy.deepcopy(DEFAULTS)
    path = settings_path(owner)
    if not os.path.exists(path):
        return out
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, dict):
            out.update({k: v for k, v in data.items() if k in DEFAULTS})
    except Exception:
        logger.warning("career settings unreadable at %s; using defaults", path, exc_info=True)
    return out


def _coerce(key: str, value: Any) -> Any:
    if key in _INT_FIELDS:
        try:
            return max(1, int(value))
        except (TypeError, ValueError):
            return DEFAULTS[key]
    if key in _LIST_FIELDS:
        return [str(x) for x in value] if isinstance(value, list) else DEFAULTS[key]
    if key == "project_disclosure":
        return {str(k): str(v) for k, v in value.items()} if isinstance(value, dict) else {}
    if key == "narrative_uses_tracker_stats":
        return bool(value)
    return value


def save_career_settings(owner: Optional[str], updates: Dict[str, Any]) -> Dict[str, Any]:
    current = load_career_settings(owner)
    for key, value in (updates or {}).items():
        if key in DEFAULTS:
            current[key] = _coerce(key, value)
    path = settings_path(owner)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    atomic_write_json(path, current, indent=2)
    return current
