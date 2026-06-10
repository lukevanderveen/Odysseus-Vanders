"""Deterministic project discovery, stack detection, and staleness checks.

Pure filesystem logic — no DB, no LLM, no subprocess. Git state is read
straight from ``.git`` files so this works on native Windows without a git
binary on PATH. See docs/plans/agent-council-projects-tab.md (Phase 1).
"""

from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
from typing import Dict, List, Optional

# Directories never descended into (vendored deps, envs, VCS internals).
SKIP_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__",
    ".tox", ".mypy_cache", ".pytest_cache", "dist", "build",
    "target", ".idea", ".vscode", "env",
}

# Marker file → base stack tag. ``.git`` marks a project but carries no tag.
_MARKER_TAGS = {
    "pyproject.toml": "python",
    "requirements.txt": "python",
    "package.json": "node",
    "Cargo.toml": "rust",
    "go.mod": "go",
}
_MARKER_SUFFIX_TAGS = {".sln": "dotnet", ".csproj": "dotnet"}

# Substring of a dependency declaration → framework tag, per base tag.
_FRAMEWORK_HINTS = {
    "python": [("fastapi", "fastapi"), ("django", "django"), ("flask", "flask")],
    "node": [("react", "react"), ("vue", "vue"), ("next", "nextjs")],
}


def _python_deps_text(project_dir: Path) -> str:
    chunks = []
    for fname in ("pyproject.toml", "requirements.txt"):
        f = project_dir / fname
        if f.is_file():
            try:
                chunks.append(f.read_text(encoding="utf-8", errors="ignore"))
            except OSError:
                pass
    return "\n".join(chunks).lower()


def _node_deps_text(project_dir: Path) -> str:
    f = project_dir / "package.json"
    if not f.is_file():
        return ""
    try:
        data = json.loads(f.read_text(encoding="utf-8", errors="ignore"))
    except (json.JSONDecodeError, OSError):
        return ""
    deps: Dict[str, str] = {}
    for key in ("dependencies", "devDependencies"):
        if isinstance(data.get(key), dict):
            deps.update(data[key])
    return " ".join(deps).lower()


def detect_stack(project_dir: Path) -> List[str]:
    """Table-driven stack tags for a project directory. Deterministic order:
    base tags in marker order, then framework tags."""
    project_dir = Path(project_dir)
    tags: List[str] = []
    for marker, tag in _MARKER_TAGS.items():
        if (project_dir / marker).is_file() and tag not in tags:
            tags.append(tag)
    if not any(t in tags for t in _MARKER_SUFFIX_TAGS.values()):
        try:
            for entry in project_dir.iterdir():
                if entry.suffix in _MARKER_SUFFIX_TAGS:
                    tags.append(_MARKER_SUFFIX_TAGS[entry.suffix])
                    break
        except OSError:
            pass

    deps_text = {
        "python": _python_deps_text,
        "node": _node_deps_text,
    }
    for base, hints in _FRAMEWORK_HINTS.items():
        if base not in tags:
            continue
        text = deps_text[base](project_dir)
        for needle, tag in hints:
            if needle in text and tag not in tags:
                tags.append(tag)
    return tags


def _is_project(d: Path) -> bool:
    if (d / ".git").is_dir():
        return True
    if any((d / marker).is_file() for marker in _MARKER_TAGS):
        return True
    try:
        return any(e.suffix in _MARKER_SUFFIX_TAGS for e in d.iterdir())
    except OSError:
        return False


def discover_projects(roots: List[str], max_depth: int = 3) -> List[Dict]:
    """Walk each workspace root (depth-limited), returning one record per
    project directory. A found project is not descended into, so nested
    sub-packages don't surface as separate projects."""
    found: List[Dict] = []
    seen: set = set()

    def visit(d: Path, depth: int):
        if d.name in SKIP_DIRS:
            return
        if _is_project(d):
            key = str(d).lower()
            if key not in seen:
                seen.add(key)
                found.append({
                    "name": d.name,
                    "path": str(d),
                    "stack": detect_stack(d),
                    "is_git": (d / ".git").is_dir(),
                })
            return
        if depth >= max_depth:
            return
        try:
            children = sorted(e for e in d.iterdir() if e.is_dir())
        except OSError:
            return
        for child in children:
            visit(child, depth + 1)

    for root in roots:
        root_path = Path(root)
        if root_path.is_dir():
            visit(root_path, 0)
    return found


# ── staleness refs ───────────────────────────────────────────────────────────

def git_head_ref(project_dir: Path) -> Optional[str]:
    """Current commit sha read from ``.git`` files (HEAD → ref file →
    packed-refs). None when not a git repo or unreadable."""
    git = Path(project_dir) / ".git"
    head = git / "HEAD"
    if not head.is_file():
        return None
    try:
        content = head.read_text(encoding="utf-8", errors="ignore").strip()
    except OSError:
        return None
    if not content.startswith("ref:"):
        return content or None
    ref = content[4:].strip()
    ref_file = git / ref
    if ref_file.is_file():
        try:
            sha = ref_file.read_text(encoding="utf-8", errors="ignore").strip()
            if sha:
                return sha
        except OSError:
            pass
    packed = git / "packed-refs"
    if packed.is_file():
        try:
            for line in packed.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.strip()
                if line.endswith(" " + ref):
                    return line.split(" ", 1)[0]
        except OSError:
            pass
    return None


def _fallback_ref(project_dir: Path) -> str:
    """Content-change fingerprint for non-git projects: hash over
    (file count, newest mtime), skipping vendored dirs."""
    count = 0
    max_mtime_ns = 0
    for dirpath, dirnames, filenames in os.walk(project_dir):
        dirnames[:] = [d for d in dirnames if d not in SKIP_DIRS]
        for fname in filenames:
            count += 1
            try:
                mtime = os.stat(os.path.join(dirpath, fname)).st_mtime_ns
            except OSError:
                continue
            if mtime > max_mtime_ns:
                max_mtime_ns = mtime
    return hashlib.sha1(f"{count}:{max_mtime_ns}".encode()).hexdigest()


def current_index_ref(project_dir: Path) -> str:
    """Ref to stamp on a fresh index: git HEAD sha when available, else the
    mtime fingerprint."""
    return git_head_ref(project_dir) or _fallback_ref(project_dir)


def is_stale(project_dir: Path, stored_ref: Optional[str]) -> bool:
    """True when the project changed since ``stored_ref`` was stamped (or was
    never indexed)."""
    if not stored_ref:
        return True
    return current_index_ref(project_dir) != stored_ref
