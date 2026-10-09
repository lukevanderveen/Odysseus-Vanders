"""Deterministic scrub + per-project disclosure levels. The model never sees raw text."""
import pytest

from services.career import disclosure as d
from services.career.activity_sources import ActivityItem


@pytest.mark.parametrize("raw,expected_absent", [
    ("edited D:\\Code\\Odysseus\\src\\agent_loop.py today", "D:\\Code"),
    ("see /home/luke/app/routes/x.py", "/home/luke"),
    ("deployed to https://api.internal.example.com/v1", "internal.example.com"),
    ("bind 127.0.0.1:7000 and host 10.0.0.5", "7000"),
    ("set CHROMADB_HOST=chroma and $SEARXNG_URL", "CHROMADB_HOST"),
    ("token ghp_abcdefghijklmnopqrstuvwxyz0123456789", "ghp_"),
    ("key sk-abcdef1234567890abcdef", "sk-"),
    ("aws AKIAIOSFODNN7EXAMPLE", "AKIA"),
    ("hash 0123456789abcdef0123456789abcdef", "0123456789abcdef"),
    ("bumped fastapi to v0.115.2", "0.115.2"),
    ("mail luke@example.com", "luke@example.com"),
])
def test_redact_text_removes_identifying_fragments(raw, expected_absent):
    out = d.redact_text(raw)
    assert expected_absent not in out
    assert "[redacted]" in out


def test_blocklist_terms_are_case_insensitive():
    assert "Odysseus" not in d.redact_text("Odysseus now streams", ["odysseus"])


def test_plain_prose_is_untouched():
    text = "Rewrote the retry loop so timeouts back off exponentially."
    assert d.redact_text(text) == text


def test_level_for_uses_project_override_then_default():
    settings = {"disclosure_default": "name_only", "project_disclosure": {"p1": "public", "bad": "nonsense"}}
    assert d.level_for("p1", settings) == "public"
    assert d.level_for("p2", settings) == "name_only"
    assert d.level_for("bad", settings) == "name_only"
    assert d.level_for(None, {}) == "anonymous"


def _item(pid, name, title, body):
    return ActivityItem("git", pid, name, title, body, "2026-10-01T00:00:00+00:00", None, "commit")


def test_apply_disclosure_three_levels():
    settings = {"disclosure_default": "anonymous", "project_disclosure": {"n": "name_only", "pub": "public"},
                "blocklist_terms": ["vanders"]}
    items = d.apply_disclosure([
        _item("a", "Odysseus", "feat: tool_index.py warmup", "warm vanders cache in app.py"),
        _item("n", "Odysseus", "feat: tool_index.py warmup", "warm cache"),
        _item("pub", "Odysseus", "feat: tool_index.py warmup", "warm cache ghp_abcdefghijklmnopqrstuvwxyz0123456789"),
    ], settings)
    anon, named, public = items
    assert anon.project_name == d.ANONYMOUS_NAME
    assert "tool_index.py" not in anon.title and "app.py" not in anon.body and "vanders" not in anon.body
    assert named.project_name == "Odysseus" and "tool_index.py" not in named.title
    assert public.project_name == "Odysseus" and "tool_index.py" in public.title and "ghp_" not in public.body
