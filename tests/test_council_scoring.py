"""Grounded vs judged scoring (Agent Council Phase 2).

Grounded scores are pure Python over real repo signals on tmp fixture dirs —
no LLM, no subprocess. Judged scores come back from the report model as JSON
and the parser must survive prose, fences, and garbage (the companion
JSON-resilience precedent).
"""

import time

import pytest

from services.council.scoring import (
    aggregate_scores,
    compute_signals,
    format_signals,
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


# ── verified-facts formatting (anti-hallucination grounding) ────────────────

def test_format_signals_renders_authoritative_facts():
    facts = format_signals({
        "has_ci": False, "test_files": 12, "source_files": 340,
        "todo_count": 4, "readme_chars": 2300, "readme_sections": 5,
        "pinned_ratio": 0.5, "days_since_commit": 2,
    })
    assert "CI/CD configured: No" in facts
    assert "Test files: 12" in facts
    assert "TODO/FIXME/HACK markers: 4" in facts   # the exact count, never "many"
    assert "50%" in facts
    assert "2 days ago" in facts


def test_format_signals_marks_unknown_git_and_no_deps():
    facts = format_signals({
        "has_ci": True, "test_files": 0, "source_files": 0, "todo_count": 0,
        "readme_chars": 0, "readme_sections": 0,
        "pinned_ratio": None, "days_since_commit": None,
    })
    assert "CI/CD configured: Yes" in facts
    assert "not a git repo" in facts        # absence shown as absence, not invented
    assert "none declared" in facts


def test_format_signals_tolerates_empty_signals():
    # never raises on a missing/empty signals dict
    assert isinstance(format_signals({}), str)
    assert isinstance(format_signals(None), str)


# ── aggregate scores (council synthesis) ─────────────────────────────────────

def test_aggregate_scores_means_each_dimension_across_components():
    out = aggregate_scores([
        {"grounded": {"technical_readiness": 80, "recent_activity": 60},
         "judged": {"marketability": 50}},
        {"grounded": {"technical_readiness": 80, "recent_activity": 60},
         "judged": {"marketability": 70}},
    ])
    assert out["grounded"]["technical_readiness"] == 80
    assert out["grounded"]["recent_activity"] == 60
    assert out["judged"]["marketability"] == 60  # (50+70)/2


def test_aggregate_scores_averages_only_components_that_scored_a_key():
    out = aggregate_scores([
        {"grounded": {}, "judged": {"launch_readiness": 80}},
        {"grounded": {}, "judged": {"marketability": 40}},
    ])
    # each key seen once → its own value, never diluted by absent components
    assert out["judged"]["launch_readiness"] == 80
    assert out["judged"]["marketability"] == 40


def test_aggregate_scores_rounds_and_clamps_to_int():
    out = aggregate_scores([
        {"grounded": {"technical_readiness": 33}, "judged": {"marketability": 55}},
        {"grounded": {"technical_readiness": 34}, "judged": {"marketability": 56}},
    ])
    assert out["grounded"]["technical_readiness"] == 34  # round((33+34)/2) = 34
    assert isinstance(out["judged"]["marketability"], int)


def test_aggregate_scores_preserves_signals_for_explainability():
    out = aggregate_scores([
        {"grounded": {"technical_readiness": 70, "_signals": {"days_since_commit": 3}},
         "judged": {}},
        {"grounded": {"technical_readiness": 70, "_signals": {"days_since_commit": 9}},
         "judged": {}},
    ])
    assert out["grounded"]["_signals"]["days_since_commit"] == 3  # from first


def test_aggregate_scores_empty_returns_empty_halves():
    assert aggregate_scores([]) == {"grounded": {}, "judged": {}}
    assert aggregate_scores([{"grounded": {}, "judged": {}}]) == {
        "grounded": {}, "judged": {},
    }


def test_parse_judged_scores_accepts_all_council_dimensions():
    out = parse_judged_scores(
        '{"monetisation_clarity": 50, "marketability": 60, '
        '"launch_readiness": 30, "ai_automation_potential": 80}'
    )
    assert out == {
        "monetisation_clarity": 50,
        "marketability": 60,
        "launch_readiness": 30,
        "ai_automation_potential": 80,
    }
