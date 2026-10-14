"""Sentence tagging (C company / B bridge / S self) and the word balance (plan 05a)."""
import json
import sys

# file-less stubs so services.career.llm (imported via cover_letter) loads.
for _name in ("src.endpoint_resolver", "src.llm_core"):
    _mod = sys.modules.get(_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(_name, None)
        sys.modules.pop("services.career.llm", None)

from services.career import letter_tagger as lt  # noqa: E402


def _complete_returning(payload):
    calls = []

    async def complete(messages, owner=None):
        calls.append(messages)
        return payload if isinstance(payload, str) else "```json\n" + json.dumps(payload) + "\n```"
    complete.calls = calls
    return complete


async def test_tag_letter_sends_letter_and_needs_and_parses_sentences():
    complete = _complete_returning({"sentences": [
        {"paragraph": 1, "text": "PointWire audits code for clients.", "tag": "C", "need": ""},
        {"paragraph": 2, "text": "I found an auth bypass in my own code.", "tag": "b", "need": "question the code"},
    ]})
    out = await lt.tag_letter("Dear PointWire,\n\nletter", "PointWire", "Junior Developer",
                              ["question the code"], complete, "vanders")
    prompt = complete.calls[0][-1]["content"]
    assert "Dear PointWire" in prompt and "question the code" in prompt and "Junior Developer" in prompt
    assert [s["tag"] for s in out] == ["C", "B"]
    assert out[1] == {"paragraph": 2, "text": "I found an auth bypass in my own code.", "tag": "B",
                      "need": "question the code"}


async def test_bridge_without_a_named_need_counts_as_self():
    complete = _complete_returning({"sentences": [{"paragraph": 1, "text": "I like Rust.", "tag": "B", "need": " "}]})
    out = await lt.tag_letter("x", "A", "B", [], complete)
    assert out[0]["tag"] == "S"


async def test_unknown_tags_count_as_self_and_junk_rows_are_dropped():
    complete = _complete_returning({"sentences": [{"paragraph": 1, "text": "Hmm.", "tag": "Q"}, "junk", {"tag": "C"}]})
    out = await lt.tag_letter("x", "A", "B", [], complete)
    assert out == [{"paragraph": 1, "text": "Hmm.", "tag": "S", "need": ""}]


async def test_unparseable_output_gives_no_tags():
    assert await lt.tag_letter("x", "A", "B", [], _complete_returning("sorry, no")) == []


def _s(p, tag, words):
    return {"paragraph": p, "text": " ".join(["w"] * words), "tag": tag, "need": "n" if tag == "B" else ""}


def test_balance_is_weighted_by_words_not_sentences():
    tags = [_s(1, "C", 5), _s(1, "C", 5), _s(1, "C", 5), _s(2, "S", 85)]
    b = lt.balance(tags)
    assert b == {"words": 100, "company_bridge_pct": 15, "self_pct": 85, "worst_paragraph": 2}


def test_balance_counts_bridge_with_company():
    b = lt.balance([_s(1, "C", 40), _s(2, "B", 40), _s(3, "S", 20)])
    assert b["company_bridge_pct"] == 80 and b["self_pct"] == 20 and b["worst_paragraph"] == 3


def test_balance_of_nothing_is_empty():
    assert lt.balance([]) == {"words": 0, "company_bridge_pct": 0, "self_pct": 0, "worst_paragraph": None}


def test_balance_check_passes_at_target():
    assert lt.balance_check({"words": 100, "company_bridge_pct": 75, "self_pct": 25, "worst_paragraph": 3}).ok


def test_balance_check_names_the_self_heavy_paragraph():
    r = lt.balance_check({"words": 100, "company_bridge_pct": 50, "self_pct": 50, "worst_paragraph": 3})
    assert not r.ok and r.name == "balance" and "paragraph 3" in r.detail and "50%" in r.detail
