"""Career LLM helper: endpoint resolution order, call kwargs, template rendering."""
import pytest

from services.career import llm as cl


@pytest.fixture()
def fake_resolver(monkeypatch):
    seen = {}

    def resolve_endpoint(prefix, owner=None, **kw):
        seen.setdefault("prefixes", []).append((prefix, owner))
        return {"task": ("http://task", "task-model", {"h": "1"}),
                "utility": ("http://util", "util-model", {})}.get(prefix, (None, None, {}))

    async def call(candidates, messages, **kw):
        seen["candidates"] = candidates
        seen["messages"] = messages
        seen["kwargs"] = kw
        return "ok"

    monkeypatch.setattr(cl, "resolve_endpoint", resolve_endpoint)
    monkeypatch.setattr(cl, "resolve_utility_fallback_candidates", lambda owner=None: [("http://fb", "fb-model", {})])
    monkeypatch.setattr(cl, "llm_call_async_with_fallback", call)
    return seen


async def test_complete_prefers_task_then_utility_fallbacks(fake_resolver):
    assert await cl.complete([{"role": "user", "content": "hi"}], owner="vanders") == "ok"
    assert [c[1] for c in fake_resolver["candidates"]] == ["task-model", "fb-model"]
    assert fake_resolver["kwargs"] == {"timeout": 900, "max_tokens": 4000}
    assert fake_resolver["prefixes"][0] == ("task", "vanders")


async def test_complete_skips_task_when_unconfigured(monkeypatch, fake_resolver):
    monkeypatch.setattr(cl, "resolve_endpoint", lambda prefix, owner=None, **kw: (None, None, None))
    await cl.complete([], owner=None)
    assert [c[1] for c in fake_resolver["candidates"]] == ["fb-model"]


async def test_complete_utility_prefers_utility_then_task(fake_resolver):
    assert await cl.complete_utility([{"role": "user", "content": "classify"}], owner="vanders") == "ok"
    assert [c[1] for c in fake_resolver["candidates"]] == ["util-model", "task-model", "fb-model"]
    assert fake_resolver["kwargs"] == {"timeout": 120, "max_tokens": 800}


def test_render_template_replaces_only_known_keys():
    out = cl.render_template("Hi {name}, {\"json\": 1} {unknown} {name}", {"name": "Luke"})
    assert out == "Hi Luke, {\"json\": 1} {unknown} Luke"
