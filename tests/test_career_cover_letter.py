"""Cover-letter service: requirement extraction, drafting, claims split, rubric (plan 01)."""
import json
from types import SimpleNamespace

import pytest

from services.career import cover_letter as cl


def _app(**over):
    base = dict(id="a1", owner="vanders", company="Acme", role="Backend Engineer",
                jd_text="We need Python, FastAPI, PostgreSQL. Nice: Kubernetes.")
    base.update(over)
    return SimpleNamespace(**base)


class FakeEvidence:
    def __init__(self, cv=None, examples=None):
        self._cv = cv if cv is not None else ["Built FastAPI services at Beta Ltd (2023-2025)."]
        self._ex = examples if examples is not None else ["# Beta\n\n## Cover letter\n\nDear Beta, short and plain."]
        self.queries = []

    def cv_chunks(self, query, k=8):
        self.queries.append(query)
        return self._cv

    def example_pairs(self, jd_text, k=2):
        return self._ex


def _complete_returning(*outputs):
    calls = []

    async def complete(messages, owner=None):
        calls.append(messages)
        return outputs[len(calls) - 1] if len(calls) <= len(outputs) else outputs[-1]

    complete.calls = calls
    return complete


# ── requirements ─────────────────────────────────────────────────────────────

async def test_extract_requirements_parses_clean_json():
    complete = _complete_returning('{"hard": ["Python", "FastAPI", "PostgreSQL"], "nice": ["Kubernetes"]}')
    out = await cl.extract_requirements("jd", complete, "vanders")
    assert out == {"hard": ["Python", "FastAPI", "PostgreSQL"], "nice": ["Kubernetes"]}
    assert "jd" in complete.calls[0][-1]["content"]


async def test_extract_requirements_survives_fenced_and_chatty_output():
    complete = _complete_returning('Sure!\n```json\n{"hard": ["Python"], "nice": []}\n```\nHope that helps.')
    assert await cl.extract_requirements("jd", complete, None) == {"hard": ["Python"], "nice": []}


async def test_extract_requirements_bad_output_gives_empty_lists():
    complete = _complete_returning("I cannot do that")
    assert await cl.extract_requirements("jd", complete, None) == {"hard": [], "nice": []}


async def test_extract_requirements_caps_counts_and_coerces_strings():
    complete = _complete_returning(json.dumps({"hard": [1, 2, 3, 4, 5, 6, 7], "nice": ["a", "b", "c", "d"]}))
    out = await cl.extract_requirements("jd", complete, None)
    assert out["hard"] == ["1", "2", "3", "4", "5"] and out["nice"] == ["a", "b", "c"]


# ── claims split ─────────────────────────────────────────────────────────────

def test_split_claims_strips_trailing_fence():
    text = 'Dear Acme,\n\nI built FastAPI services.\n\n```json\n{"claims": [{"claim": "built FastAPI services", "evidence": "Beta Ltd 2023-2025"}]}\n```\n'
    body, claims = cl.split_claims(text)
    assert body == "Dear Acme,\n\nI built FastAPI services."
    assert claims == [{"claim": "built FastAPI services", "evidence": "Beta Ltd 2023-2025"}]


def test_split_claims_without_fence_returns_whole_body():
    body, claims = cl.split_claims("Dear Acme,\n\nplain letter\n")
    assert body == "Dear Acme,\n\nplain letter" and claims == []


# ── draft ────────────────────────────────────────────────────────────────────

async def test_draft_uses_cv_examples_voice_and_requirements():
    complete = _complete_returning(
        'Dear Acme,\n\nI built FastAPI services for Beta Ltd.\n\n```json\n{"claims": [{"claim": "built FastAPI services", "evidence": "Beta Ltd"}]}\n```')
    evidence = FakeEvidence()
    body, claims = await cl.draft_cover_letter(
        _app(), "vanders", complete, evidence,
        requirements={"hard": ["Python", "FastAPI"], "nice": ["Kubernetes"]},
        settings={"voice_rules": "plain, first person", "target_roles": ["backend"]},
    )
    prompt = complete.calls[0][-1]["content"]
    assert "Built FastAPI services at Beta Ltd" in prompt      # CV evidence
    assert "Dear Beta, short and plain." in prompt             # example letter
    assert "plain, first person" in prompt                     # voice rules
    assert "- Python" in prompt and "- FastAPI" in prompt      # requirements
    assert "Acme" in prompt and "Backend Engineer" in prompt
    assert body.startswith("Dear Acme,")
    assert claims[0]["evidence"] == "Beta Ltd"
    assert "Python FastAPI" in evidence.queries[0]              # CV queried by requirements


async def test_draft_without_requirements_extracts_them_first():
    complete = _complete_returning('{"hard": ["Go"], "nice": []}', "Dear Acme,\n\nletter")
    body, claims = await cl.draft_cover_letter(_app(), "vanders", complete, FakeEvidence(), settings={})
    assert len(complete.calls) == 2 and body == "Dear Acme,\n\nletter" and claims == []


async def test_draft_raises_without_cv_evidence():
    with pytest.raises(cl.NoEvidenceError):
        await cl.draft_cover_letter(_app(), "vanders", _complete_returning("x"), FakeEvidence(cv=[]),
                                    requirements={"hard": [], "nice": []}, settings={})


# ── rubric ───────────────────────────────────────────────────────────────────

def test_rubric_flags_length_banned_phrases_and_missing_specifics():
    long = "word " * 301
    warnings = cl.rubric_check(long + " I am passionate about this. I am writing to apply.", ["FastAPI", "PostgreSQL"])
    assert any("300 words" in w for w in warnings)
    assert any("passionate" in w for w in warnings)
    assert any("I am writing to apply" in w for w in warnings)
    assert any("FastAPI" in w and "PostgreSQL" in w for w in warnings)


def test_rubric_passes_a_clean_letter():
    letter = "Dear Acme,\n\nI have shipped FastAPI services backed by PostgreSQL for three years.\n\nLuke"
    assert cl.rubric_check(letter, ["FastAPI", "PostgreSQL", "Python"]) == []
