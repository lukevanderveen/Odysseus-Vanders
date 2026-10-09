"""careerTrackerLogic.js helpers under Node, plus wiring assertions."""
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
    return json.loads([ln for ln in res.stdout.splitlines() if ln.strip()][-1])


def test_tiles_and_chips(node_available):
    out = _run_node(textwrap.dedent("""
        const { tilesHtml, sentimentChip } = await import('./static/js/careerTrackerLogic.js');
        const esc = (s) => String(s);
        console.log(JSON.stringify({
          tiles: tilesHtml({ active: 3, awaiting: 1, interviews: 1, offers: 0, rejected: 2, response_rate: 40 }, esc),
          pos: sentimentChip('interview_invite', 'positive'), neg: sentimentChip('rejection', 'negative'),
          neu: sentimentChip('confirmation', 'neutral'), other: sentimentChip('other', 'neutral'),
        }));
    """))
    assert out["tiles"].count('class="career-tile admin-card"') == 6
    assert "40%" in out["tiles"] and "Response rate" in out["tiles"]
    assert out["pos"] == {"label": "Interview invite", "cls": "career-chip-positive"}
    assert out["neg"] == {"label": "Rejection", "cls": "career-chip-negative"}
    assert out["neu"] == {"label": "Confirmation", "cls": "career-chip-waiting"}
    assert out["other"] == {"label": "Other", "cls": "career-chip-muted"}


def test_event_row_buttons_and_nudge_due(node_available):
    out = _run_node(textwrap.dedent("""
        const { eventRowHtml, nudgeDue, summaryChanged } = await import('./static/js/careerTrackerLogic.js');
        const esc = (s) => String(s ?? '');
        const ev = { id: 'e1', kind: 'interview_invite', sentiment: 'positive', company: 'Acme', role: 'Dev',
                     subject: 'Interview', summary: 'Thu 10:00', uid: '5', folder: 'INBOX', can_reply: true,
                     suggested_reply: 'Hi Sarah' };
        const noReply = { ...ev, id: 'e2', can_reply: false, suggested_reply: null, reply_blocked_reason: 'no-reply sender' };
        console.log(JSON.stringify({
          withReply: eventRowHtml(ev, esc, { showApp: true }),
          blocked: eventRowHtml(noReply, esc, { showApp: false }),
          due: nudgeDue({ computed_status: 'applied', days_since_event: 11 }, { nudge_after_days: 10 }),
          notDue: nudgeDue({ computed_status: 'applied', days_since_event: 3 }, { nudge_after_days: 10 }),
          closed: nudgeDue({ computed_status: 'rejected', days_since_event: 40 }, { nudge_after_days: 10 }),
          changed: summaryChanged({ updated_at: 'a' }, { updated_at: 'b' }),
          same: summaryChanged({ updated_at: 'a' }, { updated_at: 'a' }),
        }));
    """))
    assert 'data-action="open-email"' in out["withReply"] and 'data-uid="5"' in out["withReply"]
    assert 'data-action="copy-reply"' in out["withReply"] and "Acme — Dev" in out["withReply"]
    assert 'data-action="copy-reply"' not in out["blocked"] and "no-reply sender" in out["blocked"]
    assert "Acme — Dev" not in out["blocked"]
    assert out["due"] is True and out["notDue"] is False and out["closed"] is False
    assert out["changed"] is True and out["same"] is False


def test_tracker_wired_into_hub():
    career = (_REPO / "static" / "js" / "career.js").read_text(encoding="utf-8")
    tracker = (_REPO / "static" / "js" / "careerTracker.js").read_text(encoding="utf-8")
    assert "import { installTracker } from './careerTracker.js';" in career
    assert "installTracker(careerApi);" in career
    assert "export const installTracker" in tracker
    assert "api.registerTab('tracker'" in tracker and "api.onPanelClick(" in tracker
    assert "from './career.js'" not in tracker
    assert "detailSections.push(" in tracker
    assert "/api/career/tracker/summary" in tracker
    assert "#email=" in tracker
    for f in ("careerTracker.js", "careerTrackerLogic.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
