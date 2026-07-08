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


def test_radar_chart_svg_renders_polygon_and_axis_labels(node_available):
    script = textwrap.dedent("""
        const { radarChartSvg } = await import('./static/js/councilLogic.js');
        const esc = (s) => (s || '').replace(/[&<>"']/g, '');
        const entries = [
          { label: 'Technical readiness', value: 72, judged: false },
          { label: 'Recent activity', value: 40, judged: false },
          { label: 'Launch readiness', value: 55, judged: true },
          { label: 'Marketability', value: 60, judged: true },
        ];
        console.log(JSON.stringify({
          html: radarChartSvg(entries, esc),
          tooFew: radarChartSvg(entries.slice(0, 2), esc),
          empty: radarChartSvg([], esc),
          nullish: radarChartSvg(null, esc),
        }));
    """)
    out = _run_node(script)
    assert out["tooFew"] == ""     # a radar needs at least 3 axes
    assert out["empty"] == ""
    assert out["nullish"] == ""
    html = out["html"]
    assert "<svg" in html and "viewBox" in html
    assert "<polygon" in html                       # the data shape
    assert html.count("radar-axis-label") == 4      # one label per dimension
    assert "Technical readiness" in html


def test_member_edit_payload_normalises_blank_fields(node_available):
    script = textwrap.dedent("""
        const { memberEditPayload } = await import('./static/js/councilLogic.js');
        console.log(JSON.stringify({
          full: memberEditPayload({ name: '  Ada ', personality: 'Sharp.', model: 'qwen', endpoint: 'http://x' }),
          blankName: memberEditPayload({ name: '   ', personality: '', model: '', endpoint: '' }),
        }));
    """)
    out = _run_node(script)
    assert out["full"] == {
        "name": "Ada", "personality": "Sharp.", "model": "qwen", "endpoint_url": "http://x",
    }
    # a blank name is dropped (never blanks the member); empty selects → null
    assert out["blankName"] == {
        "name": None, "personality": "", "model": None, "endpoint_url": None,
    }


def test_dept_colors_include_council_meta_department(node_available):
    script = textwrap.dedent("""
        const { DEPT_COLORS } = await import('./static/js/councilLogic.js');
        console.log(JSON.stringify({ council: DEPT_COLORS.council || null }));
    """)
    out = _run_node(script)
    assert out["council"]          # the synthesis department has its own colour


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


def test_project_health_summarises_card_facts(node_available):
    script = textwrap.dedent("""
        const { projectHealth } = await import('./static/js/councilLogic.js');
        const reports = [
          { id: 'syn', status: 'draft', created_at: '2026-06-06',
            scores: { grounded: { technical_readiness: 80, recent_activity: 40, _signals: { days_since_commit: 3 } },
                      judged: { marketability: 60 } } },
          { id: 'd1', status: 'approved', created_at: '2026-06-05',
            scores: { grounded: { technical_readiness: 50 }, judged: {} } },
          { id: 'r1', status: 'running', created_at: '2026-06-06', scores: {} },
        ];
        console.log(JSON.stringify({
          full: projectHealth(reports),
          empty: projectHealth([]),
          nullish: projectHealth(null),
        }));
    """)
    out = _run_node(script)
    assert out["full"] == {
        "score": 60,            # newest scored report (syn): (80+40+60)/3
        "reviewCount": 3,
        "draftCount": 1,        # one report awaiting approval
        "lastCommitDays": 3,
    }
    assert out["empty"] == {"score": None, "reviewCount": 0, "draftCount": 0, "lastCommitDays": None}
    assert out["nullish"] == {"score": None, "reviewCount": 0, "draftCount": 0, "lastCommitDays": None}


def test_next_shader_mode_toggles_subtle_and_bold(node_available):
    script = textwrap.dedent("""
        const { nextShaderMode } = await import('./static/js/councilLogic.js');
        console.log(JSON.stringify({
          fromSubtle: nextShaderMode('subtle'),
          fromBold: nextShaderMode('bold'),
          fromUnknown: nextShaderMode('???'),
        }));
    """)
    out = _run_node(script)
    assert out["fromSubtle"] == "bold"
    assert out["fromBold"] == "subtle"
    assert out["fromUnknown"] == "subtle"   # anything not bold settles to subtle


def test_commit_age_label_reads_naturally(node_available):
    script = textwrap.dedent("""
        const { commitAgeLabel } = await import('./static/js/councilLogic.js');
        console.log(JSON.stringify({
          today: commitAgeLabel(0),
          yesterday: commitAgeLabel(1),
          days: commitAgeLabel(3.4),
          missing: commitAgeLabel(null),
          undef: commitAgeLabel(undefined),
        }));
    """)
    out = _run_node(script)
    assert out["today"] == "today"
    assert out["yesterday"] == "yesterday"
    assert out["days"] == "3 days ago"
    assert out["missing"] is None
    assert out["undef"] is None


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


def test_stack_panel_renders_radar_actions_and_body_for_a_draft_overview(node_available):
    script = textwrap.dedent("""
        const { stackPanelHtml } = await import('./static/js/councilStack.js');
        const esc = (s) => (s || '').replace(/[&<>"']/g, '');
        const overview = { id: 'syn', report_type: 'council_review', department: 'council',
          status: 'draft', title: 'Council Review: P',
          scores: { grounded: { technical_readiness: 72, recent_activity: 40, _signals: {} },
                    judged: { launch_readiness: 55, marketability: 60 } } };
        console.log(JSON.stringify({
          actionable: stackPanelHtml(overview, esc, { showActions: true }),
          readonly: stackPanelHtml(overview, esc, { showActions: false }),
        }));
    """)
    out = _run_node(script)
    a = out["actionable"]
    assert "Council Review: P" in a
    assert "radar-chart" in a                      # council_review gets the radar
    assert 'data-action="approve"' in a            # a draft is actionable when allowed
    assert 'data-action="dismiss"' in a
    assert 'data-body-for="syn"' in a              # body loads on demand
    # the same panel, read-only, drops the approve/dismiss controls
    assert 'data-action="approve"' not in out["readonly"]


def test_stack_panel_hides_body_and_actions_while_running(node_available):
    script = textwrap.dedent("""
        const { stackPanelHtml } = await import('./static/js/councilStack.js');
        const esc = (s) => (s || '').replace(/[&<>"']/g, '');
        const running = { id: 'r1', report_type: 'marketing_audit', department: 'marketing',
          status: 'running', title: 'Marketing Audit: P', scores: {} };
        const errored = { id: 'e1', report_type: 'architecture_review', department: 'architect',
          status: 'error', title: 'Arch: P', error: 'boom', scores: {} };
        const devApproved = { id: 'd1', report_type: 'developer_review', department: 'developer',
          status: 'approved', title: 'Dev: P', scores: { grounded: { technical_readiness: 72 }, judged: {} } };
        console.log(JSON.stringify({
          running: stackPanelHtml(running, esc, { showActions: true }),
          errored: stackPanelHtml(errored, esc, { showActions: true }),
          dev: stackPanelHtml(devApproved, esc, { showActions: true }),
        }));
    """)
    out = _run_node(script)
    # a running report has no body to load and nothing to approve yet
    assert 'data-body-for' not in out["running"]
    assert 'data-action="approve"' not in out["running"]
    # an error surfaces its message, still no body
    assert "boom" in out["errored"]
    assert 'data-body-for' not in out["errored"]
    # a non-council, settled report: body yes, no radar, no approve (not a draft)
    assert 'data-body-for="d1"' in out["dev"]
    assert "radar-chart" not in out["dev"]
    assert 'data-action="approve"' not in out["dev"]


def test_stack_html_builds_tabbed_stack_overview_first(node_available):
    script = textwrap.dedent("""
        const { stackHtml } = await import('./static/js/councilStack.js');
        const esc = (s) => (s || '').replace(/[&<>"']/g, '');
        const overview = { id: 'syn', report_type: 'council_review', department: 'council',
          status: 'draft', title: 'Council Review: P', created_at: '2026-06-06',
          scores: { grounded: { technical_readiness: 72, recent_activity: 40, _signals: {} },
                    judged: { launch_readiness: 55, marketability: 60 } } };
        const dev = { id: 'd1', report_type: 'developer_review', department: 'developer',
          status: 'approved', title: 'Developer Review: P', created_at: '2026-06-05',
          scores: { grounded: { technical_readiness: 72 }, judged: {} } };
        const group = { projectId: 'p1', name: 'My Project', reports: [dev, overview] };
        console.log(JSON.stringify({
          deflt: stackHtml(group, esc, { showActions: true }),
          devTab: stackHtml(group, esc, { showActions: true, activeType: 'developer_review' }),
          empty: stackHtml({ projectId: 'p', name: 'P', reports: [] }, esc, {}),
        }));
    """)
    out = _run_node(script)
    assert out["empty"] == ""
    d = out["deflt"]
    assert 'data-project-id="p1"' in d
    assert "My Project" in d
    assert d.count("report-tab") >= 2                       # one tab per report type
    assert 'data-report-tab="council_review"' in d
    assert 'data-report-tab="developer_review"' in d
    # Overview (synthesis) is the default active panel: its radar + draft actions show
    assert "radar-chart" in d
    assert 'data-action="approve"' in d
    # switching the active tab to the developer review swaps the panel
    dev = out["devTab"]
    assert 'data-body-for="d1"' in dev
    assert "radar-chart" not in dev                         # developer review has no radar


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
