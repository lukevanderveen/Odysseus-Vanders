"""Grounded vs judged project scoring (Agent Council Phase 2).

Grounded scores are computed here in pure Python from real repo signals —
tests, CI, README, pinned deps, commit recency, TODO density — and stored
with their raw ``_signals`` so the UI can show *why* a number is what it is.
Judged scores (monetisation clarity, marketability) are LLM opinion produced
by the report run; this module only parses them defensively and clamps to
0–100. The split is the whole point: never present an AI guess as a measure.

No DB, no LLM, no subprocess (git state read straight from ``.git`` files,
Windows-safe like services/projects/scanner.py).
"""

from __future__ import annotations

import json
import re
import time
from pathlib import Path
from typing import Dict, Optional

from services.projects.scanner import SKIP_DIRS

# Files that mark a test, per common conventions across the detected stacks.
_TEST_FILE_RE = re.compile(r"(^test_.*\.py$|_test\.(py|go)$|\.(test|spec)\.[jt]sx?$)")

_SOURCE_SUFFIXES = {
    ".py", ".js", ".ts", ".jsx", ".tsx", ".rs", ".go", ".cs", ".java",
    ".rb", ".php", ".c", ".h", ".cpp", ".hpp",
}

_TODO_RE = re.compile(r"\b(TODO|FIXME|XXX|HACK)\b")

# The judged (LLM-opinion) dimensions any report type may return; each
# template asks for the subset that fits its department.
JUDGED_KEYS = (
    "monetisation_clarity", "marketability",
    "launch_readiness", "ai_automation_potential",
)

# The grounded (computed-from-repo) dimensions; identical across a project's
# component reports, but meaned for symmetry with the judged half.
GROUNDED_KEYS = ("technical_readiness", "recent_activity")


def _iter_files(root: Path):
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.relative_to(root).parts):
            continue
        yield p


def _has_ci(root: Path) -> bool:
    wf = root / ".github" / "workflows"
    try:
        if wf.is_dir() and any(f.suffix in (".yml", ".yaml") for f in wf.iterdir()):
            return True
    except OSError:
        pass
    return any((root / m).is_file() for m in (".gitlab-ci.yml", "azure-pipelines.yml", "Jenkinsfile"))


def _readme(root: Path) -> str:
    for name in ("README.md", "readme.md", "README.rst", "README.txt"):
        f = root / name
        if f.is_file():
            try:
                return f.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                return ""
    return ""


def _pinned_ratio(root: Path) -> Optional[float]:
    """Share of dependency declarations pinned to an exact version, across
    requirements.txt and package.json. None when the project declares none."""
    total = pinned = 0

    req = root / "requirements.txt"
    if req.is_file():
        try:
            for line in req.read_text(encoding="utf-8", errors="ignore").splitlines():
                line = line.split("#", 1)[0].strip()
                if not line or line.startswith("-"):
                    continue
                total += 1
                if "==" in line:
                    pinned += 1
        except OSError:
            pass

    pkg = root / "package.json"
    if pkg.is_file():
        try:
            data = json.loads(pkg.read_text(encoding="utf-8", errors="ignore"))
            for key in ("dependencies", "devDependencies"):
                for ver in (data.get(key) or {}).values():
                    total += 1
                    if isinstance(ver, str) and ver[:1].isdigit():
                        pinned += 1
        except (json.JSONDecodeError, OSError):
            pass

    if total == 0:
        return None
    return pinned / total


def _days_since_commit(root: Path) -> Optional[float]:
    """Age of the last commit from the reflog (``.git/logs/HEAD``) — each line
    carries a unix timestamp, so no git binary or subprocess is needed."""
    log = root / ".git" / "logs" / "HEAD"
    if not log.is_file():
        return None
    try:
        lines = log.read_text(encoding="utf-8", errors="ignore").strip().splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        # "<old> <new> Author <email> <unix_ts> <tz>\t<action>: <msg>"
        m = re.search(r"\s(\d{9,11})\s[+-]\d{4}\t", line)
        if m:
            return max(0.0, (time.time() - int(m.group(1))) / 86400)
    return None


def compute_signals(project_path) -> Dict:
    """Raw, documented repo signals — every number here is observable fact."""
    root = Path(project_path)
    test_files = 0
    source_files = 0
    todo_count = 0
    for p in _iter_files(root):
        if _TEST_FILE_RE.search(p.name):
            test_files += 1
        if p.suffix in _SOURCE_SUFFIXES:
            source_files += 1
            try:
                todo_count += len(_TODO_RE.findall(p.read_text(encoding="utf-8", errors="ignore")))
            except OSError:
                pass

    readme = _readme(root)
    return {
        "test_files": test_files,
        "source_files": source_files,
        "todo_count": todo_count,
        "has_ci": _has_ci(root),
        "readme_chars": len(readme),
        "readme_sections": len(re.findall(r"^#{1,3}\s", readme, flags=re.M)),
        "pinned_ratio": _pinned_ratio(root),
        "days_since_commit": _days_since_commit(root),
    }


def technical_readiness_score(signals: Dict) -> int:
    """Weighted rubric over the grounded signals, 0–100.

    tests ≤40 (saturates at 10 test files) · CI 15 · README ≤25
    (substance 10 + structure 15) · pinned deps ≤20 · TODO debt −≤10.
    """
    score = 0.0
    score += min(signals.get("test_files") or 0, 10) / 10 * 40
    if signals.get("has_ci"):
        score += 15
    if (signals.get("readme_chars") or 0) >= 300:
        score += 10
    if (signals.get("readme_sections") or 0) >= 3:
        score += 15
    pinned = signals.get("pinned_ratio")
    if pinned is not None:
        score += pinned * 20
    score -= min(signals.get("todo_count") or 0, 20) / 20 * 10
    return int(round(max(0.0, min(100.0, score))))


def recent_activity_score(signals: Dict) -> int:
    """Commit-recency band, 0–100. Unknown (non-git) scores 0 — absence of
    evidence is shown as absence, never invented."""
    days = signals.get("days_since_commit")
    if days is None:
        return 0
    for limit, score in ((1, 100), (7, 85), (30, 60), (90, 35), (365, 15)):
        if days <= limit:
            return score
    return 5


def grounded_scores(project_path) -> Dict:
    signals = compute_signals(project_path)
    return {
        "technical_readiness": technical_readiness_score(signals),
        "recent_activity": recent_activity_score(signals),
        "_signals": signals,
    }


def _mean_per_key(halves, keys) -> Dict[str, int]:
    """Mean each dimension over only the components that scored it — an absent
    key never dilutes the average — rounding and clamping to 0–100."""
    out: Dict[str, int] = {}
    for key in keys:
        vals = [h[key] for h in halves
                if isinstance(h.get(key), (int, float)) and not isinstance(h.get(key), bool)]
        if vals:
            out[key] = int(round(max(0, min(100, sum(vals) / len(vals)))))
    return out


def format_signals(signals) -> str:
    """Render the raw repo signals as authoritative plain-language facts for the
    report prompt — ground truth the model must not contradict. Every line is an
    observed fact (no judgement), so the model can't claim "many TODOs" when the
    count is 4 or invent missing CI. Tolerates a missing/empty signals dict."""
    s = signals or {}
    pinned = s.get("pinned_ratio")
    pinned_str = "none declared" if pinned is None else f"{round(pinned * 100)}% of dependencies pinned"
    days = s.get("days_since_commit")
    if days is None:
        commit_str = "unknown (not a git repo)"
    else:
        d = round(days)
        commit_str = "today" if d == 0 else "1 day ago" if d == 1 else f"{d} days ago"
    return "\n".join([
        f"- CI/CD configured: {'Yes' if s.get('has_ci') else 'No'}",
        f"- Test files: {s.get('test_files', 0)}",
        f"- Source files: {s.get('source_files', 0)}",
        f"- TODO/FIXME/HACK markers: {s.get('todo_count', 0)}",
        f"- README: {s.get('readme_chars', 0)} chars, {s.get('readme_sections', 0)} sections",
        f"- Dependencies: {pinned_str}",
        f"- Last commit: {commit_str}",
    ])


def aggregate_scores(component_scores) -> Dict[str, Dict]:
    """Combine the per-department reports of one project into a single scorecard
    for the council synthesis: mean each grounded and judged dimension across
    the components that carry it. ``_signals`` (for the activity line) is carried
    from the first grounded half that has it. Empty in → empty halves out."""
    grounded_halves = [(s or {}).get("grounded") or {} for s in (component_scores or [])]
    judged_halves = [(s or {}).get("judged") or {} for s in (component_scores or [])]
    grounded = _mean_per_key(grounded_halves, GROUNDED_KEYS)
    signals = next((g["_signals"] for g in grounded_halves if g.get("_signals")), None)
    if signals is not None:
        grounded["_signals"] = signals
    return {"grounded": grounded, "judged": _mean_per_key(judged_halves, JUDGED_KEYS)}


def parse_judged_scores(text) -> Dict[str, int]:
    """Pull the judged-score JSON out of model output, tolerating prose and
    code fences. Unknown keys and non-numeric values are dropped; numbers are
    clamped to 0–100. Garbage in → ``{}`` out, never an exception."""
    if not text or not isinstance(text, str):
        return {}
    candidates = re.findall(r"\{[^{}]*\}", text)
    for candidate in candidates:
        try:
            data = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if not isinstance(data, dict):
            continue
        out = {}
        for key in JUDGED_KEYS:
            val = data.get(key)
            if isinstance(val, bool) or not isinstance(val, (int, float)):
                continue
            out[key] = int(round(max(0, min(100, val))))
        if out:
            return out
    return {}
