"""Front-end Projects tab logic — guarded from Python via Node (no JS runner).

Tests static/js/projectsLogic.js, the DOM-free module that projects.js
renders from: status badge selection and project ordering. Pattern follows
tests/test_compare_js.py.
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
        encoding="utf-8",  # node emits UTF-8; default cp1252 on Windows mangles it
    )
    assert res.returncode == 0, res.stderr
    out_lines = [ln for ln in res.stdout.splitlines() if ln.strip()]
    return json.loads(out_lines[-1])


def test_status_badge_covers_all_states(node_available):
    script = textwrap.dedent("""
        const { projectStatusBadge } = await import('./static/js/projectsLogic.js');
        const badge = (p) => projectStatusBadge(p).label;
        console.log(JSON.stringify({
          missing:     badge({ exists: false, indexed_at: null, stale: true }),
          unindexed:   badge({ exists: true, indexed_at: null, stale: true }),
          stale:       badge({ exists: true, indexed_at: '2026-01-01', stale: true }),
          fresh:       badge({ exists: true, indexed_at: '2026-01-01', stale: false }),
        }));
    """)
    out = _run_node(script)
    assert out == {
        "missing": "Missing",
        "unindexed": "Not indexed",
        "stale": "Index may be stale",
        "fresh": "Indexed",
    }


def test_normalize_roots_trims_dedupes_and_strips_trailing_slashes(node_available):
    script = textwrap.dedent(r"""
        const { normalizeRoots } = await import('./static/js/projectsLogic.js');
        console.log(JSON.stringify(normalizeRoots([
          '  D:\\code\\ ',          // trims + strips trailing backslash
          'D:\\CODE',               // case-insensitive duplicate of the above
          '',                       // dropped
          '   ',                    // dropped
          'D:\\',                   // drive root keeps its backslash
          '/home/luke/projects/',   // posix trailing slash stripped
        ])));
    """)
    assert _run_node(script) == ["D:\\code", "D:\\", "/home/luke/projects"]


def test_report_chip_covers_all_statuses(node_available):
    script = textwrap.dedent("""
        const { reportChip } = await import('./static/js/projectsLogic.js');
        const label = (status) => reportChip({ status }).label;
        console.log(JSON.stringify({
          running:   label('running'),
          draft:     label('draft'),
          approved:  label('approved'),
          dismissed: label('dismissed'),
          error:     label('error'),
        }));
    """)
    out = _run_node(script)
    assert out == {
        "running": "Generating…",
        "draft": "Awaiting approval",
        "approved": "Approved",
        "dismissed": "Dismissed",
        "error": "Failed",
    }


def test_newly_drafted_detects_running_to_draft_transition(node_available):
    script = textwrap.dedent("""
        const { newlyDrafted } = await import('./static/js/projectsLogic.js');
        console.log(JSON.stringify({
          landed: newlyDrafted(['r1', 'r2'], [
            { id: 'r1', status: 'draft' },      // finished — report it
            { id: 'r2', status: 'running' },    // still going
            { id: 'r3', status: 'draft' },      // was never tracked as running
          ]),
          errored: newlyDrafted(['r1'], [{ id: 'r1', status: 'error' }]),
          empty: newlyDrafted([], [{ id: 'r1', status: 'draft' }]),
        }));
    """)
    out = _run_node(script)
    assert out["landed"] == ["r1"]
    assert out["errored"] == []
    assert out["empty"] == []


def test_reports_by_project_groups_preserving_first_seen_order(node_available):
    script = textwrap.dedent("""
        const { reportsByProject } = await import('./static/js/projectsLogic.js');
        const groups = reportsByProject([
          { id: 'a', project_id: 'p1' },
          { id: 'b', project_id: 'p2' },
          { id: 'c', project_id: 'p1' },
        ]);
        console.log(JSON.stringify(groups.map(g => ({ pid: g.projectId, ids: g.reports.map(r => r.id) }))));
    """)
    out = _run_node(script)
    assert out == [
        {"pid": "p1", "ids": ["a", "c"]},
        {"pid": "p2", "ids": ["b"]},
    ]


def test_council_tabs_overview_first_and_latest_per_type(node_available):
    script = textwrap.dedent("""
        const { councilTabs } = await import('./static/js/projectsLogic.js');
        const tabs = councilTabs([
          { id: 'd1', report_type: 'developer_review', department: 'developer', status: 'approved', created_at: '2026-06-01' },
          { id: 'd2', report_type: 'developer_review', department: 'developer', status: 'approved', created_at: '2026-06-05' },
          { id: 'm1', report_type: 'marketing_audit', department: 'marketing', status: 'approved', created_at: '2026-06-02' },
          { id: 'syn', report_type: 'council_review', department: 'council', status: 'draft', created_at: '2026-06-06' },
        ]);
        console.log(JSON.stringify({
          types: tabs.map(t => t.type),
          first: tabs[0],
          dev: tabs.find(t => t.type === 'developer_review'),
          empty: councilTabs([]),
        }));
    """)
    out = _run_node(script)
    # Overview (synthesis) leads; then the fixed department order
    assert out["types"] == ["council_review", "developer_review", "marketing_audit"]
    assert out["first"]["label"] == "Overview"
    assert out["dev"]["reportId"] == "d2"      # newest report wins its type's tab
    assert out["dev"]["label"] == "Developer"
    assert out["empty"] == []


def test_sort_projects_alphabetical_missing_last(node_available):
    script = textwrap.dedent("""
        const { sortProjects } = await import('./static/js/projectsLogic.js');
        const sorted = sortProjects([
          { name: 'zeta', exists: true },
          { name: 'gone', exists: false },
          { name: 'Alpha', exists: true },
        ]);
        console.log(JSON.stringify(sorted.map(p => p.name)));
    """)
    assert _run_node(script) == ["Alpha", "zeta", "gone"]
