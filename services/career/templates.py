"""Prompt templates for Career hub features.

Lookup order: data/career/<subdir>/<name>.md (user override, gitignored) then
the packaged services/career/<subdir>/<name>.md. Same pattern as
services/council/runner.load_template.
"""
from __future__ import annotations

from pathlib import Path

from core.constants import DATA_DIR
from services.career.llm import render_template  # re-exported for callers

__all__ = ["load_template", "render_template"]

_PACKAGED_ROOT = Path(__file__).parent


def load_template(subdir: str, name: str) -> str:
    for root in (Path(DATA_DIR) / "career", _PACKAGED_ROOT):
        f = root / subdir / f"{name}.md"
        if f.is_file():
            try:
                return f.read_text(encoding="utf-8")
            except OSError:
                continue
    raise FileNotFoundError(f"No template {subdir}/{name}.md")
