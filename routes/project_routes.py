# routes/project_routes.py
"""Projects tab API — workspace scan, per-project RAG index, staleness.

Agent Council Phase 1 (docs/plans/agent-council-projects-tab.md). Workspace
roots come exclusively from the admin setting ``projects_workspace_roots`` —
the scan endpoint accepts no caller-supplied paths, which is the security
boundary for filesystem access. Rows are owner-scoped like notes.
"""

import asyncio
import logging
import os
import uuid
import json
from datetime import datetime
from typing import Any, Dict, Optional

from fastapi import APIRouter, HTTPException, Request

from core.database import SessionLocal, Project
from src.auth_helpers import get_current_user
from src.settings import get_setting
from services.projects.deep_index import run_deep_index
from services.projects.indexer import index_project
from services.projects.scanner import discover_projects, is_stale

logger = logging.getLogger(__name__)

# Deep-index run state, in-memory (v1 — the Phase 2 council runner replaces
# this). One run at a time globally: summaries serialise on a single GPU.
_deep_runs: Dict[str, Dict] = {}
_deep_tasks: set = set()


async def _deep_index_bg(pid: str, path: str, owner: Optional[str]):
    try:
        result = await run_deep_index(pid, path, owner=owner)
    except Exception as e:  # belt-and-braces: runner already catches
        result = {"status": "error", "error": str(e)}
    if result.get("status") != "ok":
        _deep_runs[pid] = {"status": "error", "error": result.get("error", "unknown")}
        return
    db = SessionLocal()
    try:
        proj = db.query(Project).filter(Project.id == pid).first()
        if not proj:
            _deep_runs[pid] = {"status": "error", "error": "project row vanished"}
            return
        proj.deep_summary = result["summary"]
        proj.deep_indexed_at = datetime.utcnow()
        proj.deep_index_ref = result["ref"]
        db.commit()
        _deep_runs[pid] = {"status": "done", "error": None}
    except Exception as e:
        _deep_runs[pid] = {"status": "error", "error": f"db write failed: {e}"}
    finally:
        db.close()


def _project_to_dict(p: Project) -> Dict[str, Any]:
    try:
        stack = json.loads(p.stack) if p.stack else []
    except (json.JSONDecodeError, TypeError):
        stack = []
    return {
        "id": p.id,
        "owner": p.owner,
        "name": p.name,
        "path": p.path,
        "stack": stack,
        "exists": os.path.isdir(p.path),
        "stale": is_stale(p.path, p.index_ref),
        "indexed_at": p.indexed_at.isoformat() if p.indexed_at else None,
        "deep_summary": p.deep_summary,
        "deep_indexed_at": p.deep_indexed_at.isoformat() if p.deep_indexed_at else None,
        "archived": p.archived,
    }


def setup_project_routes():
    router = APIRouter(prefix="/api/projects", tags=["projects"])

    def _owner(request: Request) -> Optional[str]:
        return get_current_user(request)

    def _get_owned(db, pid: str, user: Optional[str]) -> Project:
        q = db.query(Project).filter(Project.id == pid)
        if user is not None:
            q = q.filter(Project.owner == user)
        proj = q.first()
        if not proj:
            raise HTTPException(status_code=404, detail="Project not found")
        return proj

    @router.get("")
    def list_projects(request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            q = db.query(Project).filter(Project.archived == False)  # noqa: E712
            if user is not None:
                q = q.filter(Project.owner == user)
            projects = q.order_by(Project.name).all()
            return {"projects": [_project_to_dict(p) for p in projects]}
        finally:
            db.close()

    @router.post("/scan")
    def scan_workspace(request: Request):
        user = _owner(request)
        roots = get_setting("projects_workspace_roots", []) or []
        if not roots:
            raise HTTPException(
                status_code=400,
                detail="No workspace roots configured (projects_workspace_roots)",
            )
        discovered = discover_projects([str(r) for r in roots])
        db = SessionLocal()
        try:
            for d in discovered:
                q = db.query(Project).filter(Project.path == d["path"])
                if user is not None:
                    q = q.filter(Project.owner == user)
                row = q.first()
                if row:
                    row.name = d["name"]
                    row.stack = json.dumps(d["stack"])
                    row.archived = False
                else:
                    db.add(Project(
                        id=uuid.uuid4().hex[:12],
                        owner=user,
                        name=d["name"],
                        path=d["path"],
                        stack=json.dumps(d["stack"]),
                    ))
            db.commit()
            q = db.query(Project).filter(Project.archived == False)  # noqa: E712
            if user is not None:
                q = q.filter(Project.owner == user)
            projects = q.order_by(Project.name).all()
            return {"projects": [_project_to_dict(p) for p in projects]}
        finally:
            db.close()

    @router.get("/{pid}")
    def project_detail(pid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            return _project_to_dict(_get_owned(db, pid, user))
        finally:
            db.close()

    @router.post("/{pid}/index")
    def index_one(pid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            proj = _get_owned(db, pid, user)
            if not os.path.isdir(proj.path):
                raise HTTPException(status_code=410, detail="Project path no longer exists")
            result = index_project(proj.id, proj.path, owner=user)
            if result.get("status") != "ok":
                raise HTTPException(
                    status_code=503,
                    detail=f"Index unavailable: {result.get('error', 'unknown')}",
                )
            proj.indexed_at = datetime.utcnow()
            proj.index_ref = result["ref"]
            db.commit()
            return {"status": "ok", "files": result["files"], "chunks": result["chunks"]}
        finally:
            db.close()

    @router.post("/{pid}/deep-index")
    async def deep_index_one(pid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            proj = _get_owned(db, pid, user)
            if not os.path.isdir(proj.path):
                raise HTTPException(status_code=410, detail="Project path no longer exists")
            path = proj.path
        finally:
            db.close()
        if any(r.get("status") == "running" for r in _deep_runs.values()):
            raise HTTPException(status_code=409, detail="A deep index is already running")
        _deep_runs[pid] = {"status": "running", "error": None}
        task = asyncio.create_task(_deep_index_bg(pid, path, user))
        _deep_tasks.add(task)
        task.add_done_callback(_deep_tasks.discard)
        return {"status": "running"}

    @router.get("/{pid}/deep-index/status")
    def deep_index_status(pid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            _get_owned(db, pid, user)
        finally:
            db.close()
        return _deep_runs.get(pid) or {"status": "idle", "error": None}

    @router.delete("/{pid}")
    def archive_project(pid: str, request: Request):
        user = _owner(request)
        db = SessionLocal()
        try:
            proj = _get_owned(db, pid, user)
            proj.archived = True
            db.commit()
            return {"status": "archived", "id": pid}
        finally:
            db.close()

    return router
