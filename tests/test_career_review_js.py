"""careerReviewLogic.js pure helpers under Node + source wiring guards (plan 02)."""
import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


@pytest.fixture(scope="module")
def node_available():
    if shutil.which("node") is None:
        pytest.skip("node not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(["node", "--input-type=module", "-e", script], cwd=_REPO,
                         capture_output=True, timeout=15, encoding="utf-8")
    assert res.returncode == 0, res.stderr
    lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    return json.loads(lines[-1])


def test_review_score_entries_follow_registry_order(node_available):
    out = _run_node(textwrap.dedent("""
        const { reviewScoreEntries, REVIEW_DIMS } = await import('./static/js/careerReviewLogic.js');
        const v = { scores: { specificity: 40, technical_credibility: 88, bogus: 1 } };
        console.log(JSON.stringify({ e: reviewScoreEntries('career_engineer', v), keys: Object.keys(REVIEW_DIMS) }));
    """))
    assert out["e"] == [
        {"key": "technical_credibility", "label": "Technical credibility", "value": 88},
        {"key": "specificity", "label": "Specificity", "value": 40},
    ]
    assert sorted(out["keys"]) == ["career_engineer", "career_hiring_manager", "career_hr", "career_recruiter"]


def test_score_bars_are_hatched_ai_bars_and_chips(node_available):
    out = _run_node(textwrap.dedent("""
        const { reviewScoreBarsHtml, panelVerdictChip, reviewerLabel } = await import('./static/js/careerReviewLogic.js');
        const esc = (s) => String(s).replace(/</g, '&lt;');
        console.log(JSON.stringify({
          html: reviewScoreBarsHtml('career_hr', { scores: { professionalism: 72 } }, esc),
          empty: reviewScoreBarsHtml('career_hr', { scores: {} }, esc),
          advance: panelVerdictChip('advance'), rewrite: panelVerdictChip('rewrite'), none: panelVerdictChip(undefined),
          label: reviewerLabel('career_hiring_manager'),
        }));
    """))
    assert 'score-bar-fill judged' in out["html"] and 'width:72%' in out["html"] and 'score-ai-tag' in out["html"]
    assert out["empty"] == ""
    assert out["advance"] == {"label": "Advance", "cls": "career-chip-positive"}
    assert out["rewrite"] == {"label": "Rewrite", "cls": "career-chip-negative"}
    assert out["none"] == {"label": "Pending", "cls": "career-chip-muted"}
    assert out["label"] == "Hiring manager"


def test_modules_parse_and_career_js_installs_them(node_available):
    for f in ("careerReviewLogic.js", "careerReviewers.js", "careerReview.js", "career.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
    src = (_REPO / "static" / "js" / "career.js").read_text(encoding="utf-8")
    assert "import { installReviewers } from './careerReviewers.js';" in src
    assert "import { installReview } from './careerReview.js';" in src
    assert "installReviewers(careerApi);" in src and "installReview(careerApi);" in src
