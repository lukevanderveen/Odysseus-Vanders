"""Privacy scrub for project activity before it reaches a model or the UI.

Two layers: ``redact_text`` is a table of regexes that always run (paths,
hosts, ports, env vars, secrets, versions, emails, IPs, the user's blocklist),
and ``apply_disclosure`` adds the per-project level — anonymous projects lose
their name and any module-ish identifiers; name_only keeps the name; public
keeps module names but still never leaks secrets or blocklisted terms.
"""
from __future__ import annotations

import re
from dataclasses import replace
from typing import Any, Dict, Iterable, List, Optional

from services.career.activity_sources import ActivityItem

LEVELS = ("anonymous", "name_only", "public")
ANONYMOUS_NAME = "a project I'm building"
REDACTED = "[redacted]"

# Order matters: secrets before hex-ish tokens, URLs before host:port.
_RULES = [
    ("secret_prefixed", re.compile(r"\b(?:ghp|gho|ghs|github_pat|sk|xox[bap]|AKIA)[A-Za-z0-9_\-]{8,}\b")),
    ("hex_token", re.compile(r"\b[0-9a-fA-F]{32,}\b")),
    ("url", re.compile(r"\bhttps?://[^\s)\]]+")),
    ("email", re.compile(r"\b[\w.+-]+@[\w-]+\.[\w.-]+\b")),
    ("win_path", re.compile(r"\b[A-Za-z]:\\[^\s'\"`]+")),
    ("posix_path", re.compile(r"(?<![\w/])/(?:[\w.-]+/)+[\w.-]+")),
    ("ip", re.compile(r"\b\d{1,3}(?:\.\d{1,3}){3}(?::\d{2,5})?\b")),
    ("host_port", re.compile(r"\b[\w.-]+:\d{2,5}\b")),
    ("env_assign", re.compile(r"\b[A-Z][A-Z0-9_]{2,}=\S*")),
    ("env_ref", re.compile(r"\$\{?[A-Z][A-Z0-9_]{2,}\}?")),
    ("semver", re.compile(r"\bv?\d+\.\d+\.\d+(?:[-+][\w.]+)?\b")),
]
_ALWAYS = ("secret_prefixed", "hex_token", "email")   # applied even at the public level
_MODULE_RE = re.compile(r"\b[\w\-]+\.(?:py|js|ts|tsx|jsx|css|html|sql|go|rs|java|cs)\b")


def _apply_blocklist(text: str, blocklist: Iterable[str]) -> str:
    out = text
    for term in blocklist or ():
        term = (term or "").strip()
        if term:
            out = re.sub(re.escape(term), REDACTED, out, flags=re.IGNORECASE)
    return out


def redact_text(text: str, blocklist: Iterable[str] = ()) -> str:
    out = text or ""
    for _name, rx in _RULES:
        out = rx.sub(REDACTED, out)
    return _apply_blocklist(out, blocklist)


def level_for(project_id: Optional[str], settings: Dict[str, Any]) -> str:
    default = settings.get("disclosure_default") or "anonymous"
    if default not in LEVELS:
        default = "anonymous"
    level = (settings.get("project_disclosure") or {}).get(project_id or "", default)
    return level if level in LEVELS else default


def _scrub(text: str, level: str, blocklist: Iterable[str]) -> str:
    if level == "public":
        # Secrets, emails and the blocklist still go; paths and versions may stay.
        out = text or ""
        for name, rx in _RULES:
            if name in _ALWAYS:
                out = rx.sub(REDACTED, out)
        return _apply_blocklist(out, blocklist)
    return _MODULE_RE.sub(REDACTED, redact_text(text, blocklist))


def apply_disclosure(items: Iterable[ActivityItem], settings: Dict[str, Any]) -> List[ActivityItem]:
    blocklist = settings.get("blocklist_terms") or []
    out: List[ActivityItem] = []
    for item in items:
        level = level_for(item.project_id, settings)
        name = ANONYMOUS_NAME if level == "anonymous" else item.project_name
        out.append(replace(
            item, project_name=name,
            title=_scrub(item.title, level, blocklist),
            body=_scrub(item.body, level, blocklist),
            url=item.url if level == "public" else None,
        ))
    return out
