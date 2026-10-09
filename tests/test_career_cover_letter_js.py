"""Cover-letter UI: pure helpers under Node + source wiring assertions."""
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


_ESC = "const esc = (s) => String(s ?? '').replace(/&/g,'&amp;').replace(/</g,'&lt;').replace(/>/g,'&gt;').replace(/\"/g,'&quot;');"


def test_claims_html_lists_claims_and_warnings(node_available):
    out = _run_node(textwrap.dedent(f"""
        const {{ claimsHtml }} = await import('./static/js/careerCoverLetterLogic.js');
        {_ESC}
        const html = claimsHtml([{{ claim: 'built <APIs>', evidence: 'Beta Ltd' }}], ['Too long'], esc);
        const empty = claimsHtml([], [], esc);
        console.log(JSON.stringify({{ html, empty }}));
    """))
    assert "built &lt;APIs&gt;" in out["html"] and "Beta Ltd" in out["html"]
    assert 'class="career-claim"' in out["html"]
    assert "career-rubric-warning" in out["html"] and "Too long" in out["html"]
    assert "No claims check yet" in out["empty"]


def test_letter_section_states(node_available):
    out = _run_node(textwrap.dedent(f"""
        const {{ letterSectionHtml }} = await import('./static/js/careerCoverLetterLogic.js');
        {_ESC}
        const app = {{ id: 'a1', cover_letter_doc_id: null }};
        const none = letterSectionHtml(app, null, false, esc);
        const busy = letterSectionHtml(app, null, true, esc);
        const done = letterSectionHtml({{ id: 'a1', cover_letter_doc_id: 'd1' }},
          {{ doc_id: 'd1', version: 2, claims: [], rubric_warnings: [] }}, false, esc);
        console.log(JSON.stringify({{ none, busy, done }}));
    """))
    assert 'data-action="draft-cover-letter"' in out["none"] and "Draft cover letter" in out["none"]
    assert "disabled" in out["busy"] and "Drafting" in out["busy"]
    assert 'href="#document-d1"' in out["done"] and "Regenerate" in out["done"] and "v2" in out["done"]


def test_wiring_and_syntax(node_available):
    career = (_REPO / "static" / "js" / "career.js").read_text(encoding="utf-8")
    assert "import { installCoverLetter } from './careerCoverLetter.js';" in career
    assert "installCoverLetter(careerApi);" in career
    plugin = (_REPO / "static" / "js" / "careerCoverLetter.js").read_text(encoding="utf-8")
    assert "export const installCoverLetter" in plugin
    assert "detailSections.push(" in plugin
    assert "api.onPanelClick(" in plugin
    assert "from './career.js'" not in plugin
    assert "/cover-letter" in plugin
    for f in ("careerCoverLetter.js", "careerCoverLetterLogic.js", "career.js"):
        res = subprocess.run(["node", "--check", f"static/js/{f}"], cwd=_REPO, capture_output=True, encoding="utf-8")
        assert res.returncode == 0, res.stderr
