"""careerLogic.js pure helpers, run under Node (pattern: tests/test_council_js.py)."""
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


def test_status_chip_covers_every_status(node_available):
    out = _run_node(textwrap.dedent("""
        const { statusChip, STATUS_LABELS } = await import('./static/js/careerLogic.js');
        console.log(JSON.stringify({
          keys: Object.keys(STATUS_LABELS),
          offer: statusChip('offer'), rejected: statusChip('rejected'),
          ghosted: statusChip('ghosted'), unknown: statusChip('???'),
        }));
    """))
    assert sorted(out["keys"]) == sorted(["drafting", "applied", "acknowledged", "screening", "interview",
                                          "offer", "rejected", "withdrawn", "ghosted"])
    assert out["offer"]["cls"] == "career-chip-positive"
    assert out["rejected"]["cls"] == "career-chip-negative"
    assert out["ghosted"]["cls"] == "career-chip-muted"
    assert out["unknown"]["label"] == "Drafting"


def test_application_buckets(node_available):
    out = _run_node(textwrap.dedent("""
        const { applicationBuckets } = await import('./static/js/careerLogic.js');
        const apps = [
          { id: 'd', computed_status: 'drafting' },
          { id: 'w', computed_status: 'applied' },
          { id: 'i', computed_status: 'interview' },
          { id: 'g', computed_status: 'ghosted' },
          { id: 'r', computed_status: 'rejected' },
          { id: 'o', computed_status: 'offer' },
        ];
        const b = applicationBuckets(apps);
        console.log(JSON.stringify({ drafting: b.drafting.map(a => a.id), active: b.active.map(a => a.id),
          closed: b.closed.map(a => a.id) }));
    """))
    assert out == {"drafting": ["d"], "active": ["w", "i", "o"], "closed": ["g", "r"]}


def test_days_label_and_placeholder(node_available):
    out = _run_node(textwrap.dedent("""
        const { daysLabel, placeholderHtml } = await import('./static/js/careerLogic.js');
        console.log(JSON.stringify({ zero: daysLabel(0), one: daysLabel(1), many: daysLabel(12),
          none: daysLabel(null), ph: placeholderHtml('tracker') }));
    """))
    assert out["zero"] == "today" and out["one"] == "1 day ago" and out["many"] == "12 days ago"
    assert out["none"] == ""
    assert "career-placeholder" in out["ph"] and "tracker" in out["ph"].lower()


def test_career_module_sources_parse(node_available):
    for f in ("career.js", "careerLogic.js", "careerApplications.js", "careerSetup.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
