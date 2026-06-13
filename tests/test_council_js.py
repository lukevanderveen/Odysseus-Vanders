"""Front-end council dashboard logic — guarded from Python via Node.

Tests static/js/councilLogic.js, the DOM-free module council.js renders from:
member status chips, approvals/history split, history filtering. Pattern
follows tests/test_projects_js.py.
"""

import json
import shutil
import subprocess
import textwrap
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_HAS_NODE = shutil.which("node") is not None


@pytest.fixture(scope="module")
def node_available():
    if not _HAS_NODE:
        pytest.skip("node not on PATH")


def _run_node(script: str) -> dict:
    res = subprocess.run(
        ["node", "--input-type=module", "-e", script],
        cwd=_REPO,
        capture_output=True,
        timeout=15,
        encoding="utf-8",
    )
    assert res.returncode == 0, res.stderr
    out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    return json.loads(out_lines[-1])


def test_member_status_chip_covers_all_queue_states(node_available):
    script = textwrap.dedent("""
        const { memberStatusChip } = await import('./static/js/councilLogic.js');
        const chip = (status) => memberStatusChip(status);
        console.log(JSON.stringify({
          ready: chip('Ready'),
          waiting: chip('Waiting'),
          busy: chip('Busy'),
          unknown: chip('???'),
        }));
    """)
    out = _run_node(script)
    assert out["ready"] == {"label": "Ready", "cls": "council-chip-ready"}
    assert out["waiting"] == {"label": "Waiting", "cls": "council-chip-waiting"}
    assert out["busy"] == {"label": "Busy", "cls": "council-chip-busy"}
    assert out["unknown"] == {"label": "Ready", "cls": "council-chip-ready"}


def test_split_reports_separates_inbox_from_history(node_available):
    script = textwrap.dedent("""
        const { splitReports } = await import('./static/js/councilLogic.js');
        const out = splitReports([
          { id: 'a', status: 'running' },
          { id: 'b', status: 'draft' },
          { id: 'c', status: 'approved' },
          { id: 'd', status: 'dismissed' },
          { id: 'e', status: 'error' },
        ]);
        console.log(JSON.stringify({
          inbox: out.inbox.map(r => r.id),
          history: out.history.map(r => r.id),
        }));
    """)
    out = _run_node(script)
    assert out["inbox"] == ["a", "b"]          # running + awaiting approval
    assert out["history"] == ["c", "d", "e"]   # settled outcomes


def test_score_entries_order_grounded_first_and_flag_judged(node_available):
    script = textwrap.dedent("""
        const { scoreEntries } = await import('./static/js/councilLogic.js');
        console.log(JSON.stringify({
          full: scoreEntries({
            grounded: { technical_readiness: 72, recent_activity: 40, _signals: {} },
            judged: { launch_readiness: 30, monetisation_clarity: 55, marketability: 60, ai_automation_potential: 80 },
          }),
          empty: scoreEntries({}),
          nullish: scoreEntries(null),
        }));
    """)
    out = _run_node(script)
    assert out["empty"] == []
    assert out["nullish"] == []
    entries = out["full"]
    # grounded dimensions lead, judged follow, _signals never leaks
    assert [e["key"] for e in entries] == [
        "technical_readiness", "recent_activity",
        "launch_readiness", "monetisation_clarity", "marketability", "ai_automation_potential",
    ]
    assert all(not e["judged"] for e in entries[:2])
    assert all(e["judged"] for e in entries[2:])
    assert entries[0] == {
        "key": "technical_readiness", "label": "Technical readiness",
        "value": 72, "judged": False,
    }


def test_score_bars_html_renders_labels_values_and_ai_tags(node_available):
    script = textwrap.dedent("""
        const { scoreBarsHtml } = await import('./static/js/councilLogic.js');
        // Mirrors uiModule.esc: string-only, throws on numbers — the builder
        // must never hand it a non-string.
        const esc = (s) => (s || '').replace(/[&<>"']/g, '');
        const html = scoreBarsHtml({
          grounded: { technical_readiness: 72, _signals: {} },
          judged: { marketability: 60 },
        }, esc);
        console.log(JSON.stringify({
          html,
          empty: scoreBarsHtml({}, esc),
        }));
    """)
    out = _run_node(script)
    assert out["empty"] == ""
    html = out["html"]
    assert "Technical readiness" in html
    assert "width:72%" in html
    assert "width:60%" in html
    assert html.count("score-ai-tag") == 1      # judged rows are labelled AI
    assert "judged" in html                      # judged fill styled distinctly


def test_office_layout_places_nodes_on_ellipse_within_bounds(node_available):
    script = textwrap.dedent("""
        const { officeLayout } = await import('./static/js/councilLogic.js');
        const members = ['research', 'business', 'architect', 'developer', 'marketing']
          .map((d) => ({ department: d }));
        const nodes = officeLayout(members, 800, 400);
        console.log(JSON.stringify({
          n: nodes.length,
          depts: nodes.map((n) => n.department),
          first: nodes[0],
          allInBounds: nodes.every((n) => n.x >= 0 && n.x <= 800 && n.y >= 0 && n.y <= 400),
          distinct: new Set(nodes.map((n) => `${n.x},${n.y}`)).size === 5,
          empty: officeLayout([], 800, 400),
        }));
    """)
    out = _run_node(script)
    assert out["n"] == 5
    assert out["depts"] == ["research", "business", "architect", "developer", "marketing"]
    assert out["first"]["x"] == 400  # first node sits top-centre
    assert out["allInBounds"] is True
    assert out["distinct"] is True
    assert out["empty"] == []


def test_overall_score_means_all_dimensions(node_available):
    script = textwrap.dedent("""
        const { overallScore } = await import('./static/js/councilLogic.js');
        console.log(JSON.stringify({
          mixed: overallScore({
            grounded: { technical_readiness: 80, recent_activity: 40, _signals: {} },
            judged: { marketability: 60 },
          }),
          empty: overallScore({}),
          nullish: overallScore(null),
        }));
    """)
    out = _run_node(script)
    assert out["mixed"] == 60  # (80+40+60)/3
    assert out["empty"] is None
    assert out["nullish"] is None


def test_latest_scored_report_picks_newest_with_scores(node_available):
    script = textwrap.dedent("""
        const { latestScoredReport } = await import('./static/js/councilLogic.js');
        const reports = [
          { id: 'new-unscored', created_at: '2026-06-10T12:00:00', scores: {} },
          { id: 'old-scored', created_at: '2026-06-08T12:00:00',
            scores: { grounded: { technical_readiness: 50 }, judged: {} } },
          { id: 'new-scored', created_at: '2026-06-09T12:00:00',
            scores: { grounded: { technical_readiness: 70 }, judged: {} } },
        ];
        console.log(JSON.stringify({
          picked: latestScoredReport(reports)?.id,
          none: latestScoredReport([{ id: 'x', scores: {} }]),
          empty: latestScoredReport([]),
        }));
    """)
    out = _run_node(script)
    assert out["picked"] == "new-scored"
    assert out["none"] is None
    assert out["empty"] is None


def test_filter_history_by_department_and_project(node_available):
    script = textwrap.dedent("""
        const { filterHistory } = await import('./static/js/councilLogic.js');
        const rows = [
          { id: 'a', department: 'developer', project_id: 'p1' },
          { id: 'b', department: 'business', project_id: 'p1' },
          { id: 'c', department: 'developer', project_id: 'p2' },
        ];
        console.log(JSON.stringify({
          all: filterHistory(rows, {}).map(r => r.id),
          dev: filterHistory(rows, { department: 'developer' }).map(r => r.id),
          p1dev: filterHistory(rows, { department: 'developer', projectId: 'p1' }).map(r => r.id),
        }));
    """)
    out = _run_node(script)
    assert out["all"] == ["a", "b", "c"]
    assert out["dev"] == ["a", "c"]
    assert out["p1dev"] == ["a"]
