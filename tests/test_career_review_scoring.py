"""Pure parsing/aggregation for the reviewer panel (plan 02)."""
from services.career import review_scoring as rs

REVIEW = (
    "## Verdict\nmaybe — decent but generic.\n\n## Top 3 issues\n1. x\n\n"
    "```json\n{\"verdict\": \"advance\", \"ats_match\": 140, \"clarity\": 62.4, \"role_fit\": 90, \"bogus\": 1}\n```\n"
)


def test_dims_registry_covers_four_reviewers():
    assert set(rs.REVIEW_DIMS) == {"career_recruiter", "career_hiring_manager", "career_engineer", "career_hr"}
    assert rs.REVIEW_DIMS["career_engineer"] == ("technical_credibility", "specificity")
    assert all(d in rs.DIM_LABELS for dims in rs.REVIEW_DIMS.values() for d in dims)


def test_parse_review_block_clamps_filters_and_reads_verdict():
    out = rs.parse_review_block(REVIEW, "career_recruiter")
    assert out == {"verdict": "advance", "scores": {"ats_match": 100, "clarity": 62}}


def test_parse_review_block_uses_last_fence_and_defaults_verdict():
    text = "```json\n{\"verdict\": \"reject\", \"ats_match\": 1}\n```\nlater\n```json\n{\"ats_match\": 55}\n```"
    out = rs.parse_review_block(text, "career_recruiter")
    assert out == {"verdict": "maybe", "scores": {"ats_match": 55}}
    assert rs.parse_review_block("no json here", "career_hr") == {"verdict": "maybe", "scores": {}}


def test_strip_json_fence_removes_only_trailing_fence():
    assert rs.strip_json_fence(REVIEW).endswith("1. x")
    assert rs.strip_json_fence("plain") == "plain"


def test_parse_panel_verdict():
    assert rs.parse_panel_verdict("## Verdict\nrevise\n```json\n{\"panel_verdict\": \"rewrite\"}\n```") == "rewrite"
    assert rs.parse_panel_verdict("```json\n{\"panel_verdict\": \"nonsense\"}\n```") == "revise"
    assert rs.parse_panel_verdict("") == "revise"


def test_dims_json_example_lists_the_reviewers_dims():
    ex = rs.dims_json_example("career_hr")
    assert '"verdict": "maybe"' in ex and '"consistency_with_cv": 70' in ex and '"professionalism": 70' in ex


def test_aggregate_panel_averages_per_reviewer_then_overall():
    verdicts = {
        "career_recruiter": {"verdict": "advance", "scores": {"ats_match": 80, "clarity": 60}},
        "career_engineer": {"verdict": "reject", "scores": {"technical_credibility": 40}},
        "career_hr": {"verdict": "maybe", "scores": {}},
    }
    agg = rs.aggregate_panel(verdicts)
    assert agg["by_reviewer"] == {"career_recruiter": 70, "career_engineer": 40}
    assert agg["overall"] == 55
    assert agg["verdict_counts"] == {"advance": 1, "maybe": 1, "reject": 1}
    assert rs.aggregate_panel({}) == {"overall": None, "by_reviewer": {}, "verdict_counts": {}}
