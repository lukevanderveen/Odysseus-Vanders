"""Packaged prompt templates with a data/career/ override layer (plan 02)."""
import sys

import pytest

# Other test modules stub src.endpoint_resolver at collection time; evict
# file-less stubs so services.career.llm (imported by templates) loads.
for _name in ("src.endpoint_resolver", "src.llm_core"):
    _mod = sys.modules.get(_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(_name, None)
        sys.modules.pop("services.career.llm", None)
        sys.modules.pop("services.career.templates", None)

from services.career import templates as t  # noqa: E402


def test_packaged_reviewer_and_panel_templates_have_placeholders():
    r = t.load_template("reviewers/prompts", "reviewer")
    for key in ("{reviewer_name}", "{company}", "{role}", "{jd}", "{cover_letter}", "{cv_excerpt}", "{dims_json_example}"):
        assert key in r
    p = t.load_template("reviewers/prompts", "panel")
    for key in ("{company}", "{role}", "{reviews_markdown}"):
        assert key in p
    assert "panel_verdict" in p


def test_user_override_wins(tmp_path, monkeypatch):
    monkeypatch.setattr(t, "DATA_DIR", str(tmp_path))
    d = tmp_path / "career" / "reviewers" / "prompts"
    d.mkdir(parents=True)
    (d / "panel.md").write_text("custom {company}", encoding="utf-8")
    assert t.load_template("reviewers/prompts", "panel") == "custom {company}"


def test_missing_template_raises():
    with pytest.raises(FileNotFoundError):
        t.load_template("reviewers/prompts", "nope")


def test_render_template_replaces_only_known_keys():
    out = t.render_template("A {company} {\"json\": 1} {unknown}", {"company": "Acme"})
    assert out == "A Acme {\"json\": 1} {unknown}"
