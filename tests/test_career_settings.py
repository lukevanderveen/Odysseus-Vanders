"""Per-owner Career hub settings file (plan 00)."""
import json
import os
import sys

import pytest

# Other test modules stub core.atomic_io at collection time with a fake that
# writes "{}"; evict file-less stubs so settings binds the real atomic writer.
for _name in ("core.atomic_io", "services.career.settings"):
    _mod = sys.modules.get(_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(_name, None)
        sys.modules.pop("services.career.settings", None)

from services.career import settings as cs  # noqa: E402


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(cs, "DATA_DIR", str(tmp_path))
    return tmp_path


def test_defaults_when_no_file(data_dir):
    s = cs.load_career_settings("vanders")
    assert s["disclosure_default"] == "anonymous"
    assert s["nudge_after_days"] == 10
    assert s["ghosted_after_days"] == 21
    assert s["cv_filename"] == ""
    assert s["target_roles"] == []


def test_save_merges_known_keys_and_ignores_unknown(data_dir):
    out = cs.save_career_settings("vanders", {"voice_rules": "plain, first person", "bogus": 1})
    assert out["voice_rules"] == "plain, first person"
    assert "bogus" not in out
    on_disk = json.loads((data_dir / "career" / "vanders" / "settings.json").read_text(encoding="utf-8"))
    assert on_disk["voice_rules"] == "plain, first person"
    assert cs.load_career_settings("vanders")["nudge_after_days"] == 10  # defaults still fill gaps


def test_settings_are_per_owner_and_anonymous_owner_maps_to_local(data_dir):
    cs.save_career_settings("vanders", {"cv_filename": "cv-luke.pdf"})
    assert cs.load_career_settings("other")["cv_filename"] == ""
    assert cs.settings_path(None).endswith(os.path.join("career", "local", "settings.json"))


def test_integer_fields_are_coerced_and_bounded(data_dir):
    out = cs.save_career_settings("vanders", {"nudge_after_days": "7", "ghosted_after_days": -3})
    assert out["nudge_after_days"] == 7
    assert out["ghosted_after_days"] == 1
