"""GitHub and Trello activity through the integrations registry, plus the
gather step that merges every selected source for an owner.

``execute_api_call`` returns ``{"output": "HTTP <status>\\n<body>"}`` on
success; ``_parse`` strips that first line and decodes the JSON body.
"""
from __future__ import annotations

import asyncio
import json
import logging
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Iterable, List, Optional, Set, Tuple

from core.database import Project, SessionLocal
from services.career.activity_sources import ActivityItem, fetch_local_git
from src.integrations import execute_api_call, load_integrations

logger = logging.getLogger(__name__)

SOURCES = ("git", "github", "trello")
TRELLO_FILTER = "createCard,updateCard:idList,commentCard"


def resolve_integration_id(preset: str) -> Optional[str]:
    """Prefer the integration whose ``preset`` is ``preset``; fall back to a
    case-insensitive display-name match so a renamed integration still works."""
    items = load_integrations()
    for item in items:
        if (item.get("preset") or "").lower() == preset:
            return item.get("id")
    for item in items:
        if (item.get("name") or "").lower() == preset:
            return item.get("id")
    return None


def _parse(result: dict) -> Tuple[Any, Optional[str]]:
    if not result or result.get("exit_code", 0) != 0 or "error" in result:
        return None, str((result or {}).get("error") or "integration call failed")[:200]
    text = str(result.get("output", ""))
    body = text.split("\n", 1)[1] if "\n" in text else text
    try:
        return json.loads(body), None
    except (json.JSONDecodeError, ValueError):
        return None, "integration returned non-JSON or truncated output"


def _parse_dt(value: str) -> Optional[datetime]:
    try:
        return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None


def _is_since(value: str, since: datetime) -> bool:
    dt = _parse_dt(value)
    return bool(dt) and dt >= since


async def fetch_github(since: datetime, execute: Callable = execute_api_call) -> Tuple[List[ActivityItem], Optional[str]]:
    iid = resolve_integration_id("github")
    if not iid:
        return [], "GitHub integration not registered (Settings → Integrations → preset GitHub)"
    repos, err = _parse(await execute(iid, "GET", "/user/repos", params={"sort": "pushed", "per_page": 50}))
    if err:
        return [], f"GitHub: {err}"
    items: List[ActivityItem] = []
    for repo in repos or []:
        if not _is_since(repo.get("pushed_at", ""), since):
            continue
        full = repo.get("full_name") or ""
        name = repo.get("name") or full
        commits, err = _parse(await execute(iid, "GET", f"/repos/{full}/commits",
                                            params={"since": since.isoformat(), "per_page": 50}))
        if err:
            return items, f"GitHub {full}: {err}"
        for c in commits or []:
            message = (c.get("commit") or {}).get("message") or ""
            subject, _, body = message.partition("\n")
            items.append(ActivityItem("github", None, name, subject.strip(), body.strip(),
                                      ((c.get("commit") or {}).get("author") or {}).get("date") or "",
                                      c.get("html_url"), "commit"))
        pulls, err = _parse(await execute(iid, "GET", f"/repos/{full}/pulls",
                                          params={"state": "all", "sort": "updated", "direction": "desc", "per_page": 20}))
        if err:
            return items, f"GitHub {full}: {err}"
        for p in pulls or []:
            if not _is_since(p.get("updated_at", ""), since):
                continue
            items.append(ActivityItem("github", None, name, p.get("title") or "", p.get("body") or "",
                                      p.get("updated_at") or "", p.get("html_url"), "pull_request"))
    return items, None


def _trello_item(action: dict, board_name: str) -> Optional[ActivityItem]:
    data = action.get("data") or {}
    card = data.get("card") or {}
    title = card.get("name") or ""
    url = f"https://trello.com/c/{card['shortLink']}" if card.get("shortLink") else None
    kind = action.get("type")
    if kind == "createCard":
        return ActivityItem("trello", None, board_name, title, f"Created in {(data.get('list') or {}).get('name', '')}".strip(),
                            action.get("date") or "", url, "card_created")
    if kind == "updateCard" and data.get("listAfter"):
        return ActivityItem("trello", None, board_name, title,
                            f"Moved from {(data.get('listBefore') or {}).get('name', '')} to {data['listAfter'].get('name', '')}",
                            action.get("date") or "", url, "card_moved")
    if kind == "commentCard":
        return ActivityItem("trello", None, board_name, title, data.get("text") or "", action.get("date") or "", url, "card_comment")
    return None


async def fetch_trello(since: datetime, execute: Callable = execute_api_call) -> Tuple[List[ActivityItem], Optional[str]]:
    iid = resolve_integration_id("trello")
    if not iid:
        return [], "Trello integration not registered (Settings → Integrations → preset Trello)"
    boards, err = _parse(await execute(iid, "GET", "/1/members/me/boards", params={"fields": "name,url"}))
    if err:
        return [], f"Trello: {err}"
    items: List[ActivityItem] = []
    for board in boards or []:
        actions, err = _parse(await execute(iid, "GET", f"/1/boards/{board.get('id')}/actions",
                                            params={"since": since.isoformat(), "filter": TRELLO_FILTER, "limit": 50}))
        if err:
            return items, f"Trello {board.get('name')}: {err}"
        for action in actions or []:   # order is irrelevant: gather_activity sorts by date
            item = _trello_item(action, board.get("name") or "")
            if item:
                items.append(item)
    return items, None


def _owner_projects(owner: Optional[str], project_ids: Optional[Iterable[str]], session_factory) -> List[Any]:
    db = (session_factory or SessionLocal)()
    try:
        q = db.query(Project).filter(Project.archived == False)  # noqa: E712
        if owner is not None:
            q = q.filter(Project.owner == owner)
        if project_ids:
            q = q.filter(Project.id.in_(list(project_ids)))
        return [type("P", (), {"id": p.id, "name": p.name, "path": p.path})() for p in q.all()]
    finally:
        db.close()


async def gather_activity(owner: Optional[str], days: int, sources: Set[str], project_ids: Optional[Iterable[str]],
                          session_factory=None, execute: Callable = execute_api_call
                          ) -> Tuple[List[ActivityItem], List[str]]:
    since = datetime.now(timezone.utc) - timedelta(days=max(1, int(days)))
    items: List[ActivityItem] = []
    warnings: List[str] = []
    if "git" in sources:
        for project in _owner_projects(owner, project_ids, session_factory):
            got, warn = await asyncio.to_thread(fetch_local_git, project, since)
            items.extend(got)
            if warn:
                warnings.append(warn)
    if "github" in sources:
        got, warn = await fetch_github(since, execute=execute)
        items.extend(got)
        if warn:
            warnings.append(warn)
    if "trello" in sources:
        got, warn = await fetch_trello(since, execute=execute)
        items.extend(got)
        if warn:
            warnings.append(warn)
    items.sort(key=lambda i: i.date, reverse=True)
    return items, warnings
