"""Prefilter table and robust verdict parsing for the job tracker."""
import pytest

from services.career import email_classifier as ec


@pytest.mark.parametrize("subject,sender,expected", [
    ("Thank you for applying to Acme", "no-reply@greenhouse.io", True),
    ("Your application to Beta Ltd", "jobs@beta.com", True),
    ("Interview availability — Backend Engineer", "sarah@gamma.io", True),
    ("Unfortunately we will not be moving forward", "talent@delta.co", True),
    ("Weekly digest", "newsletter@shop.com", False),
    ("Your Amazon order has shipped", "ship-confirm@amazon.co.uk", False),
    ("Lunch?", "friend@gmail.com", False),
])
def test_prefilter_table(subject, sender, expected):
    assert ec.prefilter({"subject": subject, "from_address": sender, "from_name": ""}) is expected


def test_prefilter_honours_extra_terms():
    e = {"subject": "Hackajob update", "from_address": "x@y.com", "from_name": ""}
    assert ec.prefilter(e) is False
    assert ec.prefilter(e, extra_terms=("hackajob",)) is True


def test_is_ats_sender():
    assert ec.is_ats_sender("no-reply@lever.co")
    assert ec.is_ats_sender("jobs-noreply@linkedin.com")
    assert not ec.is_ats_sender("sarah@gamma.io")


def test_parse_verdict_takes_last_json_object_and_clamps():
    text = ('Sure. {"draft": true}\n```json\n{"is_job_related": true, "kind": "interview_invite", '
            '"sentiment": "positive", "company": "Acme Ltd", "role": "Backend Engineer", '
            '"confidence": 1.7, "summary": "Invite to a 45 min call", "next_step": "pick a slot"}\n```')
    v = ec.parse_verdict(text)
    assert v["kind"] == "interview_invite" and v["sentiment"] == "positive"
    assert v["confidence"] == 100
    assert v["company"] == "Acme Ltd"


def test_parse_verdict_defaults_on_garbage():
    v = ec.parse_verdict("no json here")
    assert v == {"is_job_related": False, "kind": "other", "sentiment": "neutral", "company": "",
                 "role": "", "confidence": 0, "summary": "", "next_step": ""}
    v2 = ec.parse_verdict('{"is_job_related": "yes", "kind": "party", "sentiment": "meh", "confidence": "0.4"}')
    assert v2["is_job_related"] is True and v2["kind"] == "other" and v2["sentiment"] == "neutral"
    assert v2["confidence"] == 40


async def test_classify_builds_prompt_and_parses():
    seen = {}

    async def complete(messages, owner=None, **kw):
        seen["messages"] = messages
        return '{"is_job_related": true, "kind": "rejection", "sentiment": "negative", "company": "Beta", "role": "Dev", "confidence": 0.9, "summary": "No", "next_step": ""}'

    headers = {"subject": "Update on your application", "from": "talent@beta.com", "date": "Mon"}
    v = await ec.classify(headers, "Unfortunately...", complete, "vanders")
    assert v["kind"] == "rejection" and v["confidence"] == 90
    assert "Update on your application" in seen["messages"][-1]["content"]
    assert seen["messages"][0]["role"] == "system"
