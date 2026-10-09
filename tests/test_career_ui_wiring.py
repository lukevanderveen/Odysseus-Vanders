"""Career hub shell is wired like the council: deep link, modal, sidebar, route opener."""
from pathlib import Path

_REPO = Path(__file__).resolve().parents[1]
_INDEX = (_REPO / "static" / "index.html").read_text(encoding="utf-8")
_APP_JS = (_REPO / "static" / "app.js").read_text(encoding="utf-8")
_APP_PY = (_REPO / "app.py").read_text(encoding="utf-8")


def test_career_deep_link_route_exists():
    assert '@app.get("/career")' in _APP_PY


def test_modal_and_sidebar_markup():
    assert 'id="career-modal"' in _INDEX
    assert 'role="dialog" aria-label="Career"' in _INDEX
    assert 'id="close-career-modal"' in _INDEX and 'aria-label="Close career modal"' in _INDEX
    for tab in ("tracker", "applications", "reviewers", "posts", "setup"):
        assert f'data-career-tab="{tab}"' in _INDEX
    assert 'id="career-panel"' in _INDEX
    assert 'id="tool-career-btn"' in _INDEX
    assert "'/career':" in _INDEX and "'Career — Odysseus'" in _INDEX


def test_app_js_imports_and_routes_career():
    assert "import careerModule from './js/career.js';" in _APP_JS
    assert "Modals.toggle('career-modal')" in _APP_JS
    assert "'/career': () => document.getElementById('tool-career-btn')?.click()" in _APP_JS
