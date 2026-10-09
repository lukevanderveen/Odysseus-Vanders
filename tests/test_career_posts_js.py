"""careerPostsLogic.js helpers under Node + syntax checks + wiring."""
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


def test_cadence_label(node_available):
    out = _run_node(textwrap.dedent("""
        const { cadenceLabel } = await import('./static/js/careerPostsLogic.js');
        const now = new Date('2026-10-10T00:00:00Z');
        console.log(JSON.stringify({
          none: cadenceLabel([], now),
          nine: cadenceLabel([{ status: 'posted', posted_at: '2026-10-01T00:00:00' },
                              { status: 'draft', posted_at: null }], now),
          today: cadenceLabel([{ status: 'posted', posted_at: '2026-10-10T08:00:00' }], now),
        }));
    """))
    assert out["none"] == "Nothing posted yet"
    assert out["nine"] == "Last posted 9 days ago"
    assert out["today"] == "Last posted today"


def test_post_card_and_activity_item_html(node_available):
    out = _run_node(textwrap.dedent("""
        const { postCardHtml, activityItemHtml } = await import('./static/js/careerPostsLogic.js');
        const esc = (s) => String(s ?? '').replace(/</g, '&lt;');
        console.log(JSON.stringify({
          card: postCardHtml({ id: 'p1', post_type: 'technique', body: 'Body <b>', hooks: ['h1', 'h2'], status: 'draft' }, esc),
          posted: postCardHtml({ id: 'p2', post_type: 'progress', body: 'x', hooks: [], status: 'posted' }, esc),
          item: activityItemHtml({ source: 'git', kind: 'commit', date: '2026-10-03T00:00:00+00:00', project_name: 'a project I\\'m building', title: 'feat: x', body: '' }, esc),
        }));
    """))
    assert 'data-post-id="p1"' in out["card"] and "&lt;b>" in out["card"]
    assert 'data-action="copy-post"' in out["card"] and 'data-action="mark-posted"' in out["card"] and 'data-action="discard-post"' in out["card"]
    assert "h1" in out["card"] and "Technique" in out["card"]
    assert 'data-action="mark-posted"' not in out["posted"]
    assert "2026-10-03" in out["item"] and "feat: x" in out["item"] and "git" in out["item"]


def test_posts_module_wired_and_parses(node_available):
    career = (_REPO / "static" / "js" / "career.js").read_text(encoding="utf-8")
    assert "import { installPosts } from './careerPosts.js';" in career
    assert "installPosts(careerApi);" in career
    for f in ("careerPosts.js", "careerPostsLogic.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
    posts = (_REPO / "static" / "js" / "careerPosts.js").read_text(encoding="utf-8")
    assert "export const installPosts" in posts
    assert "api.registerTab('posts'" in posts and "api.onPanelClick(" in posts
    assert "from './career.js'" not in posts
    assert "navigator.clipboard.writeText" in posts
