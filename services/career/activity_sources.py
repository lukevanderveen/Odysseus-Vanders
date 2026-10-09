"""Normalised project activity for LinkedIn post drafting (plan 03).

Every fetcher returns ``(items, warning)`` and never raises: a missing tool,
an unregistered integration or an HTTP error becomes a warning string the UI
shows next to the preview. Remote sources live in activity_remote.py.
"""
from __future__ import annotations

import logging
import os
import subprocess
from dataclasses import asdict, dataclass
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

from core.platform_compat import which_tool

logger = logging.getLogger(__name__)

GIT_LOG_TIMEOUT = 10
_FIELD_SEP = "\x1f"
_RECORD_SEP = "\x1e"


@dataclass(frozen=True)
class ActivityItem:
    source: str            # git | github | trello
    project_id: Optional[str]
    project_name: str
    title: str
    body: str
    date: str              # ISO 8601
    url: Optional[str]
    kind: str              # commit | pull_request | card_created | card_moved | card_comment

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def item_from_dict(d: Dict[str, Any]) -> ActivityItem:
    return ActivityItem(
        source=str(d.get("source", "")), project_id=d.get("project_id"),
        project_name=str(d.get("project_name", "")), title=str(d.get("title", "")),
        body=str(d.get("body", "")), date=str(d.get("date", "")),
        url=d.get("url"), kind=str(d.get("kind", "")),
    )


def _parse_git_log(raw: str, project) -> List[ActivityItem]:
    items: List[ActivityItem] = []
    for record in raw.split(_RECORD_SEP):
        record = record.strip("\n\r ")
        if not record:
            continue
        parts = record.split(_FIELD_SEP)
        if len(parts) < 3:
            continue
        sha, date, subject = parts[0].strip(), parts[1].strip(), parts[2].strip()
        body = parts[3].strip() if len(parts) > 3 else ""
        items.append(ActivityItem(
            source="git", project_id=getattr(project, "id", None), project_name=project.name,
            title=subject, body=body, date=date, url=None, kind="commit",
        ))
    return items


def fetch_local_git(project, since: datetime) -> Tuple[List[ActivityItem], Optional[str]]:
    """Commits since ``since`` from a project folder on disk."""
    path = getattr(project, "path", "") or ""
    if not os.path.isdir(os.path.join(path, ".git")):
        return [], f"{project.name}: not a git repository"
    git = which_tool("git")
    if not git:
        return [], "git is not installed or not on PATH"
    cmd = [git, "log", f"--since={since.isoformat()}", "--no-merges",
           f"--pretty=format:%H{_FIELD_SEP}%aI{_FIELD_SEP}%s{_FIELD_SEP}%b{_RECORD_SEP}"]
    try:
        res = subprocess.run(cmd, cwd=path, timeout=GIT_LOG_TIMEOUT, capture_output=True,
                             text=True, encoding="utf-8", errors="replace")
    except subprocess.TimeoutExpired:
        return [], f"{project.name}: git log timed out"
    except OSError as e:
        return [], f"{project.name}: git failed ({e})"
    if res.returncode != 0:
        return [], f"{project.name}: git log failed ({(res.stderr or '').strip()[:120]})"
    return _parse_git_log(res.stdout, project), None
