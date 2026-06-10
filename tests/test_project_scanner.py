"""Project scanner — deterministic discovery, stack heuristics, staleness.

Drives services/projects/scanner.py (Agent Council Phase 1, see
docs/plans/agent-council-projects-tab.md). All pure filesystem logic:
no DB, no LLM, no subprocess (git state is read from .git files directly).
"""

import time

import pytest

from services.projects.scanner import (
    current_index_ref,
    detect_stack,
    discover_projects,
    git_head_ref,
    is_stale,
)


# ── helpers ──────────────────────────────────────────────────────────────────

def _mk_git(repo, sha="a" * 40, ref="refs/heads/main", packed=False):
    git = repo / ".git"
    git.mkdir(parents=True)
    if ref is None:
        (git / "HEAD").write_text(sha + "\n", encoding="utf-8")
        return
    (git / "HEAD").write_text(f"ref: {ref}\n", encoding="utf-8")
    if packed:
        (git / "packed-refs").write_text(
            f"# pack-refs with: peeled fully-peeled sorted\n{sha} {ref}\n",
            encoding="utf-8",
        )
    else:
        ref_file = git / ref
        ref_file.parent.mkdir(parents=True, exist_ok=True)
        ref_file.write_text(sha + "\n", encoding="utf-8")


# ── discovery ────────────────────────────────────────────────────────────────

def test_discovers_projects_by_marker_files(tmp_path):
    _mk_git(tmp_path / "alpha")
    (tmp_path / "beta").mkdir()
    (tmp_path / "beta" / "package.json").write_text("{}", encoding="utf-8")
    (tmp_path / "notes").mkdir()
    (tmp_path / "notes" / "readme.txt").write_text("x", encoding="utf-8")

    found = discover_projects([str(tmp_path)])
    names = sorted(p["name"] for p in found)
    assert names == ["alpha", "beta"]


def test_discovery_skips_vendor_dirs(tmp_path):
    vendored = tmp_path / "app" / "node_modules" / "leftpad"
    vendored.mkdir(parents=True)
    (vendored / "package.json").write_text("{}", encoding="utf-8")
    venv_proj = tmp_path / "venv" / "lib"
    venv_proj.mkdir(parents=True)
    (venv_proj / "pyproject.toml").write_text("", encoding="utf-8")

    assert discover_projects([str(tmp_path)]) == []


def test_discovery_does_not_descend_into_a_found_project(tmp_path):
    _mk_git(tmp_path / "mono")
    sub = tmp_path / "mono" / "packages" / "web"
    sub.mkdir(parents=True)
    (sub / "package.json").write_text("{}", encoding="utf-8")

    found = discover_projects([str(tmp_path)])
    assert [p["name"] for p in found] == ["mono"]


def test_root_itself_with_markers_is_a_project(tmp_path):
    (tmp_path / "pyproject.toml").write_text("", encoding="utf-8")
    found = discover_projects([str(tmp_path)])
    assert len(found) == 1
    assert found[0]["path"] == str(tmp_path)


def test_discovery_respects_max_depth(tmp_path):
    deep = tmp_path / "a" / "b" / "c" / "d"
    deep.mkdir(parents=True)
    (deep / "go.mod").write_text("module x\n", encoding="utf-8")

    assert discover_projects([str(tmp_path)], max_depth=3) == []
    assert [p["name"] for p in discover_projects([str(tmp_path)], max_depth=4)] == ["d"]


def test_discovery_ignores_missing_root(tmp_path):
    assert discover_projects([str(tmp_path / "nope")]) == []


# ── stack heuristics ─────────────────────────────────────────────────────────

def test_stack_python_with_fastapi_dependency(tmp_path):
    (tmp_path / "pyproject.toml").write_text(
        '[project]\ndependencies = ["fastapi", "uvicorn"]\n', encoding="utf-8"
    )
    assert detect_stack(tmp_path) == ["python", "fastapi"]


def test_stack_node_with_react_dependency(tmp_path):
    (tmp_path / "package.json").write_text(
        '{"dependencies": {"react": "^18.0.0"}}', encoding="utf-8"
    )
    assert detect_stack(tmp_path) == ["node", "react"]


def test_stack_rust_go_dotnet(tmp_path):
    rust = tmp_path / "r"
    rust.mkdir()
    (rust / "Cargo.toml").write_text("", encoding="utf-8")
    go = tmp_path / "g"
    go.mkdir()
    (go / "go.mod").write_text("module g\n", encoding="utf-8")
    dn = tmp_path / "d"
    dn.mkdir()
    (dn / "App.sln").write_text("", encoding="utf-8")

    assert detect_stack(rust) == ["rust"]
    assert detect_stack(go) == ["go"]
    assert detect_stack(dn) == ["dotnet"]


def test_stack_empty_for_bare_git_repo(tmp_path):
    _mk_git(tmp_path)
    assert detect_stack(tmp_path) == []


# ── git HEAD / staleness refs ────────────────────────────────────────────────

def test_git_head_ref_detached(tmp_path):
    _mk_git(tmp_path, sha="b" * 40, ref=None)
    assert git_head_ref(tmp_path) == "b" * 40


def test_git_head_ref_via_ref_file(tmp_path):
    _mk_git(tmp_path, sha="c" * 40)
    assert git_head_ref(tmp_path) == "c" * 40


def test_git_head_ref_via_packed_refs(tmp_path):
    _mk_git(tmp_path, sha="d" * 40, packed=True)
    assert git_head_ref(tmp_path) == "d" * 40


def test_git_head_ref_none_for_non_git(tmp_path):
    assert git_head_ref(tmp_path) is None


def test_current_index_ref_prefers_git_head(tmp_path):
    _mk_git(tmp_path, sha="e" * 40)
    assert current_index_ref(tmp_path) == "e" * 40


def test_fallback_ref_changes_when_files_change(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("one", encoding="utf-8")
    before = current_index_ref(tmp_path)
    (tmp_path / "more.txt").write_text("two", encoding="utf-8")
    after = current_index_ref(tmp_path)
    assert before != after


def test_fallback_ref_stable_without_changes(tmp_path):
    (tmp_path / "data.txt").write_text("one", encoding="utf-8")
    assert current_index_ref(tmp_path) == current_index_ref(tmp_path)


def test_fallback_ref_changes_on_mtime_bump(tmp_path):
    f = tmp_path / "data.txt"
    f.write_text("one", encoding="utf-8")
    before = current_index_ref(tmp_path)
    future = time.time() + 100
    import os
    os.utime(f, (future, future))
    assert current_index_ref(tmp_path) != before


# ── is_stale ─────────────────────────────────────────────────────────────────

def test_is_stale_false_when_ref_matches(tmp_path):
    _mk_git(tmp_path, sha="f" * 40)
    assert is_stale(tmp_path, "f" * 40) is False


def test_is_stale_true_when_ref_differs(tmp_path):
    _mk_git(tmp_path, sha="f" * 40)
    assert is_stale(tmp_path, "0" * 40) is True


def test_is_stale_true_when_never_indexed(tmp_path):
    _mk_git(tmp_path)
    assert is_stale(tmp_path, None) is True
    assert is_stale(tmp_path, "") is True
