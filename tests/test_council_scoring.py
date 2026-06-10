"""Grounded vs judged scoring (Agent Council Phase 2).

Grounded scores are pure Python over real repo signals on tmp fixture dirs —
no LLM, no subprocess. Judged scores come back from the report model as JSON
and the parser must survive prose, fences, and garbage (the companion
JSON-resilience precedent).
"""

import time

import pytest

from services.council.scoring import (
    compute_signals,
    grounded_scores,
    parse_judged_scores,
    recent_activity_score,
    technical_readiness_score,
)


def _git_log(tmp_path, age_days: float):
    """Fabricate .git/logs/HEAD whose last entry is age_days old."""
    git = tmp_path / ".git" / "logs"
    git.mkdir(parents=True)
    ts = int(time.time() - age_days * 86400)
    line = f"0000 1111 Luke <l@x> {ts} +0000\tcommit: msg\n"
    (git.parent / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (git / "HEAD").write_text(line, encoding="utf-8")


# ── signals ──────────────────────────────────────────────────────────────────

def test_signals_count_test_files_and_skip_vendored_dirs(tmp_path):
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_a.py").write_text("def test_a(): pass")
    (tmp_path / "tests" / "test_b.py").write_text("def test_b(): pass")
    (tmp_path / "node_modules").mkdir()
    (tmp_path / "node_modules" / "test_vendored.py").write_text("x")
    s = compute_signals(tmp_path)
    assert s["test_files"] == 2


def test_signals_detect_ci_config(tmp_path):
    wf = tmp_path / ".github" / "workflows"
    wf.mkdir(parents=True)
    (wf / "ci.yml").write_text("on: push")
    assert compute_signals(tmp_path)["has_ci"] is True
    empty = tmp_path / "empty"
    empty.mkdir()
    assert compute_signals(empty)["has_ci"] is False


def test_signals_readme_quality(tmp_path):
    (tmp_path / "README.md").write_text(
        "# Title\n\nIntro text.\n\n## Install\nsteps\n\n## Usage\nmore\n" * 3
    )
    s = compute_signals(tmp_path)
    assert s["readme_chars"] > 100
    assert s["readme_sections"] >= 3


def test_signals_pinned_deps_ratio(tmp_path):
    (tmp_path / "requirements.txt").write_text("fastapi==0.110.0\nuvicorn\n")
    s = compute_signals(tmp_path)
    assert s["pinned_ratio"] == pytest.approx(0.5)


def test_signals_pinned_ratio_none_without_deps(tmp_path):
    assert compute_signals(tmp_path)["pinned_ratio"] is None


def test_signals_commit_recency_from_git_log(tmp_path):
    _git_log(tmp_path, age_days=3)
    s = compute_signals(tmp_path)
    assert 2.5 < s["days_since_commit"] < 3.5


def test_signals_commit_recency_none_for_non_git(tmp_path):
    assert compute_signals(tmp_path)["days_since_commit"] is None


def test_signals_todo_density(tmp_path):
    (tmp_path / "a.py").write_text("# TODO: one\nx = 1\n# FIXME: two\n")
    s = compute_signals(tmp_path)
    assert s["todo_count"] == 2
    assert s["source_files"] == 1


# ── grounded scores ──────────────────────────────────────────────────────────

def test_technical_readiness_zero_for_bare_project():
    score = technical_readiness_score({
        "test_files": 0, "has_ci": False, "readme_chars": 0,
        "readme_sections": 0, "pinned_ratio": None, "todo_count": 0,
        "source_files": 0, "days_since_commit": None,
    })
    assert score == 0


def test_technical_readiness_rises_with_signals():
    low = technical_readiness_score({
        "test_files": 0, "has_ci": False, "readme_chars": 0,
        "readme_sections": 0, "pinned_ratio": None, "todo_count": 0,
        "source_files": 10, "days_since_commit": None,
    })
    high = technical_readiness_score({
        "test_files": 25, "has_ci": True, "readme_chars": 2000,
        "readme_sections": 5, "pinned_ratio": 1.0, "todo_count": 0,
        "source_files": 10, "days_since_commit": None,
    })
    assert high > low
    assert 0 <= high <= 100


def test_recent_activity_banding():
    fresh = recent_activity_score({"days_since_commit": 0.5})
    month = recent_activity_score({"days_since_commit": 25})
    ancient = recent_activity_score({"days_since_commit": 700})
    unknown = recent_activity_score({"days_since_commit": None})
    assert fresh > month > ancient
    assert unknown == 0


def test_grounded_scores_include_signals_for_explainability(tmp_path):
    (tmp_path / "README.md").write_text("# X\n## A\n## B\ncontent here")
    out = grounded_scores(tmp_path)
    assert set(out) == {"technical_readiness", "recent_activity", "_signals"}
    assert isinstance(out["technical_readiness"], int)
    assert 0 <= out["technical_readiness"] <= 100
    assert out["_signals"]["readme_sections"] >= 2


# ── judged scores parsing ────────────────────────────────────────────────────

def test_parse_judged_scores_clean_json():
    out = parse_judged_scores('{"monetisation_clarity": 55, "marketability": 60}')
    assert out == {"monetisation_clarity": 55, "marketability": 60}


def test_parse_judged_scores_fenced_with_prose():
    text = "Here are my scores:\n```json\n{\"monetisation_clarity\": 40, \"marketability\": 72}\n```\nThanks!"
    assert parse_judged_scores(text) == {"monetisation_clarity": 40, "marketability": 72}


def test_parse_judged_scores_garbage_returns_empty():
    assert parse_judged_scores("{not-json") == {}
    assert parse_judged_scores("") == {}
    assert parse_judged_scores(None) == {}


def test_parse_judged_scores_clamps_and_drops_unknown():
    out = parse_judged_scores(
        '{"monetisation_clarity": 150, "marketability": -5, "vibes": 99, "extra": "hi"}'
    )
    assert out == {"monetisation_clarity": 100, "marketability": 0}


def test_parse_judged_scores_drops_non_numeric():
    assert parse_judged_scores('{"monetisation_clarity": "high"}') == {}
