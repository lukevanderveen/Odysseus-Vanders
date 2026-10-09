"""LLM entry points and the prompt renderer shared by every Career feature.

``complete`` mirrors services/council/runner._default_complete: the Task
model chain (cloud model when configured) falling back through the utility
chain, so Cookbook / served-model settings apply unchanged.
``complete_utility`` is for cheap classification: Utility first, then Task,
then the utility fallbacks. Services take a ``complete`` callable as a
parameter so tests inject fakes instead of patching this module.
"""
from __future__ import annotations

from typing import Dict, List, Optional

from src.endpoint_resolver import resolve_endpoint, resolve_utility_fallback_candidates
from src.llm_core import llm_call_async_with_fallback


def _candidate(prefix: str, owner: Optional[str]):
    url, model, headers = resolve_endpoint(prefix, owner=owner)
    return (url, model, headers) if url and model else None


async def complete(messages: List[Dict[str, str]], owner: Optional[str] = None) -> str:
    candidates = [c for c in (_candidate("task", owner),) if c]
    candidates += resolve_utility_fallback_candidates(owner)
    return await llm_call_async_with_fallback(candidates, messages, timeout=900, max_tokens=4000)


async def complete_utility(messages: List[Dict[str, str]], owner: Optional[str] = None,
                           max_tokens: int = 800) -> str:
    candidates = [c for c in (_candidate("utility", owner), _candidate("task", owner)) if c]
    candidates += resolve_utility_fallback_candidates(owner)
    return await llm_call_async_with_fallback(candidates, messages, timeout=120, max_tokens=max_tokens)


def render_template(template: str, mapping: Dict[str, str]) -> str:
    """Replace only known ``{key}`` tokens — prompts, JDs and letters contain
    braces, so str.format would explode."""
    out = template
    for key, val in mapping.items():
        out = out.replace("{" + key + "}", val)
    return out
