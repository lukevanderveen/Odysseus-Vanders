"""Deep Index — agent structural summary of a project (Agent Council Phase 1).

Builds a deterministic context pack (file tree + README + key configs) and
asks the Utility model for a structural markdown summary, stored on
``Project.deep_summary``. The LLM call is injectable for tests; the default
resolves the Utility endpoint chain (falls back to Default Chat) so it runs
on local Ollama out of the box.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Callable, Dict, Optional

from services.projects.scanner import SKIP_DIRS, current_index_ref
from src.personal_docs import read_text_file

logger = logging.getLogger(__name__)

MAX_CONTEXT_CHARS = 20_000
MAX_TREE_ENTRIES = 400
MAX_TREE_DEPTH = 4
KEY_FILE_CHARS = 3_000

# Root files worth quoting verbatim — they carry most of a repo's identity.
KEY_FILES = (
    "README.md", "readme.md", "README.rst",
    "package.json", "pyproject.toml", "requirements.txt",
    "Cargo.toml", "go.mod", "docker-compose.yml", ".env.example",
)

SUMMARY_SYSTEM_PROMPT = (
    "You are a senior engineer writing a structural summary of a codebase for "
    "a project dashboard. From the file tree and key files provided, produce "
    "concise markdown with these sections:\n"
    "## Purpose — what the project does, one short paragraph\n"
    "## Stack — languages, frameworks, notable dependencies\n"
    "## Layout — the key directories/files and what lives where\n"
    "## Entry points — how it is started or used\n"
    "## Observations — tests/CI/docs present or missing, anything notable\n\n"
    "Only state what the provided files support; write 'unclear' rather than "
    "guessing. No preamble, start at the first heading."
)


def build_context(project_path, max_chars: int = MAX_CONTEXT_CHARS) -> str:
    """Deterministic context pack: depth-limited file tree + key file bodies."""
    root = Path(project_path)
    tree = []
    for p in sorted(root.rglob("*")):
        rel = p.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        if len(rel.parts) > MAX_TREE_DEPTH:
            continue
        tree.append(rel.as_posix() + ("/" if p.is_dir() else ""))
        if len(tree) >= MAX_TREE_ENTRIES:
            tree.append("… (tree truncated)")
            break

    parts = [f"Project: {root.name}", "", "## File tree", "\n".join(tree)]
    for fname in KEY_FILES:
        f = root / fname
        if not f.is_file():
            continue
        text = read_text_file(str(f))[:KEY_FILE_CHARS].strip()
        if text:
            parts.append(f"\n## {fname}\n{text}")
    return "\n".join(parts)[:max_chars]


async def _default_complete(messages, owner: Optional[str] = None) -> str:
    from src.endpoint_resolver import resolve_endpoint, resolve_utility_fallback_candidates
    from src.llm_core import llm_call_async_with_fallback

    url, model, headers = resolve_endpoint("utility", owner=owner)
    candidates = ([(url, model, headers)] if url and model else [])
    candidates += resolve_utility_fallback_candidates(owner)
    return await llm_call_async_with_fallback(
        candidates, messages, timeout=600, max_tokens=2000,
    )


async def run_deep_index(
    project_id: str,
    project_path: str,
    owner: Optional[str] = None,
    complete: Optional[Callable] = None,
) -> Dict:
    """Summarise the project. Returns ``{"status": "ok", "summary", "ref"}``
    or ``{"status": "error", "error"}`` — never raises into the caller."""
    root = Path(project_path)
    messages = [
        {"role": "system", "content": SUMMARY_SYSTEM_PROMPT},
        {"role": "user", "content": build_context(root)},
    ]
    try:
        if complete is None:
            summary = await _default_complete(messages, owner=owner)
        else:
            summary = await complete(messages)
    except Exception as e:
        logger.warning("Deep index for %s failed: %s", project_id, e)
        return {"status": "error", "error": str(e)}

    summary = (summary or "").strip()
    if not summary:
        return {"status": "error", "error": "model returned an empty summary"}
    return {"status": "ok", "summary": summary, "ref": current_index_ref(root)}
