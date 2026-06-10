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
        text=True,
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
