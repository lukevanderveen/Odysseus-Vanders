"""Baseline scoring of past cover letters (plan 05a)."""
import json
import sys

for _name in ("src.endpoint_resolver", "src.llm_core"):
    _mod = sys.modules.get(_name)
    if _mod is not None and not getattr(_mod, "__file__", None):
        sys.modules.pop(_name, None)
        sys.modules.pop("services.career.llm", None)
_atomic = sys.modules.get("core.atomic_io")
if _atomic is not None and not getattr(_atomic, "__file__", None):
    sys.modules.pop("core.atomic_io", None)

from services.career import letter_baseline as lb  # noqa: E402
from services.career.letter_checks import CheckContext  # noqa: E402

LETTER = "Dear Acme,\n\nI'd like the Backend role at Acme.\n\nI built FastAPI services.\n\nKind regards,\nLuke"


def _tagger(sentences):
    async def complete(messages, owner=None):
        return json.dumps({"sentences": sentences})
    return complete


def _ctx():
    return CheckContext(company="Acme", role="Backend", hard_requirements=["FastAPI", "Postgres"])


async def test_score_letter_combines_checks_and_balance():
    complete = _tagger([{"paragraph": 1, "text": "I'd like the Backend role at Acme.", "tag": "C"},
                        {"paragraph": 2, "text": "I built FastAPI services.", "tag": "S"}])
    out = await lb.score_letter(LETTER, _ctx(), complete)
    by_name = {c["name"]: c for c in out["checks"]}
    assert set(by_name) == {"length", "voice", "names", "logistics", "specifics", "balance"}
    assert not by_name["length"]["ok"] and by_name["names"]["ok"]
    assert out["balance"]["company_bridge_pct"] == 64 and not by_name["balance"]["ok"]
    assert out["words"] == len(LETTER.split()) and len(out["tags"]) == 2


async def test_score_letter_skips_balance_when_tagging_fails():
    out = await lb.score_letter(LETTER, _ctx(), _tagger([]))
    assert "balance" not in {c["name"] for c in out["checks"]}
    assert out["balance"]["words"] == 0


def _scored(words, cb, failed):
    names = ["length", "voice", "names", "logistics", "specifics", "balance"]
    return {"words": words, "balance": {"words": words, "company_bridge_pct": cb},
            "checks": [{"name": n, "ok": n not in failed, "detail": ""} for n in names]}


def test_summarise_averages_and_pass_rates():
    s = lb.summarise([_scored(300, 40, {"balance"}), _scored(500, 60, {"length", "balance"})])
    assert s["letters"] == 2 and s["avg_words"] == 400 and s["avg_company_bridge_pct"] == 50
    assert s["pass_rate"]["length"] == 50 and s["pass_rate"]["balance"] == 0 and s["pass_rate"]["voice"] == 100


def test_summarise_ignores_untagged_letters_in_the_balance_average():
    untagged = {"words": 300, "balance": {"words": 0, "company_bridge_pct": 0},
                "checks": [{"name": "length", "ok": True, "detail": ""}]}
    s = lb.summarise([_scored(300, 80, set()), untagged])
    assert s["avg_company_bridge_pct"] == 80 and s["pass_rate"]["balance"] == 100


def test_summarise_of_nothing():
    assert lb.summarise([]) == {"letters": 0, "avg_words": 0, "avg_company_bridge_pct": None, "pass_rate": {}}


def test_baseline_round_trips_per_owner(tmp_path, monkeypatch):
    monkeypatch.setattr(lb, "DATA_DIR", str(tmp_path))
    lb.save_baseline("vanders", {"summary": {"letters": 1}})
    assert lb.load_baseline("vanders") == {"summary": {"letters": 1}}
    assert lb.load_baseline("other") is None
