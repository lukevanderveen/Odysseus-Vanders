"""Post drafting: prompt assembly, JSON parsing with fallback, N variants."""
import sys

import pytest

# Other test modules stub src.endpoint_resolver at collection time; evict
# file-less stubs so services.career.llm (imported by posts) loads.
for _name in ("src.endpoint_resolver", "src.llm_core"):
    _mod = sys.modules.get(_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(_name, None)
        sys.modules.pop("services.career.llm", None)
        sys.modules.pop("services.career.posts", None)

from services.career import posts  # noqa: E402
from services.career.activity_sources import ActivityItem  # noqa: E402


def _items():
    return [ActivityItem("git", "p1", "a project I'm building", "feat: incremental tokenizer",
                         "stream tokens as they arrive", "2026-10-03T00:00:00+00:00", None, "commit")]


def test_messages_include_rules_type_template_voice_and_items():
    msgs = posts.build_post_messages(_items(), "technique", {"voice_rules": "no exclamation marks", "target_roles": []})
    assert msgs[0]["role"] == "system" and "140" in msgs[0]["content"] and "never name internal modules" in msgs[0]["content"].lower()
    user = msgs[1]["content"]
    assert "incremental tokenizer" in user and "no exclamation marks" in user
    assert "technique" in user.lower()


def test_narrative_adds_target_roles_and_optional_stats():
    msgs = posts.build_post_messages(_items(), "narrative", {"target_roles": ["backend engineer"]},
                                     tracker_stats={"interviews": 2})
    assert "backend engineer" in msgs[1]["content"] and "2 interviews" in msgs[1]["content"]


def test_unknown_type_raises():
    with pytest.raises(ValueError):
        posts.build_post_messages(_items(), "rant", {})


def test_parse_fenced_json_and_fallback():
    out = posts.parse_post_output('Here you go:\n```json\n{"post": "Body text", "hooks": ["a", "b", "c", "d"]}\n```')
    assert out == {"body": "Body text", "hooks": ["a", "b", "c"]}
    raw = posts.parse_post_output("Just prose, no json.")
    assert raw == {"body": "Just prose, no json.", "hooks": []}


async def test_draft_post_calls_complete_per_variant():
    seen = []

    async def fake_complete(messages, owner=None):
        seen.append((messages[1]["content"], owner))
        return '{"post": "variant %d", "hooks": ["h1","h2","h3"]}' % len(seen)

    out = await posts.draft_post(_items(), "progress", {}, "vanders", complete=fake_complete, n_variants=3)
    assert [v["body"] for v in out] == ["variant 1", "variant 2", "variant 3"]
    assert all(o == "vanders" for _, o in seen)
    assert "Variant 2 of 3" in seen[1][0]


async def test_draft_post_requires_items():
    with pytest.raises(ValueError):
        await posts.draft_post([], "progress", {}, "vanders", complete=None)
