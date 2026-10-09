"""Reply-address resolution, suggestion gating, and the INSERT OR IGNORE cache write."""
import sqlite3

import pytest

from services.career import tracker_replies as tr


@pytest.mark.parametrize("headers,body,addr,reason", [
    ({"from": "Sarah <sarah@gamma.io>", "reply_to": ""}, "", "sarah@gamma.io", None),
    ({"from": "no-reply@greenhouse.io", "reply_to": "recruiting@acme.com"}, "", "recruiting@acme.com", None),
    ({"from": "no-reply@greenhouse.io", "reply_to": ""}, "", None, "no-reply sender"),
    ({"from": "donotreply@acme.com", "reply_to": "noreply@acme.com"}, "", None, "no-reply sender"),
    ({"from": "talent@beta.com", "reply_to": "", "list_unsubscribe": "<https://x>"}, "", None, "bulk mail (List-Unsubscribe)"),
    ({"from": "talent@beta.com", "reply_to": ""}, "Please do not reply to this email.", None, "body says do not reply"),
    ({"from": "", "reply_to": ""}, "", None, "no sender address"),
])
def test_reply_address(headers, body, addr, reason):
    assert tr.reply_address(headers, body) == (addr, reason)


@pytest.mark.parametrize("kind,sentiment,can_reply,expected", [
    ("interview_invite", "positive", True, True),
    ("info_request", "neutral", True, True),
    ("offer", "positive", True, True),
    ("confirmation", "neutral", True, False),
    ("rejection", "negative", True, False),
    ("interview_invite", "positive", False, False),
    ("recruiter_outreach", "positive", True, True),
])
def test_should_suggest_reply(kind, sentiment, can_reply, expected):
    assert tr.should_suggest_reply(kind, sentiment, can_reply) is expected


async def test_suggest_reply_uses_voice_rules_and_strips_subject_lines():
    seen = {}

    async def complete(messages, owner=None, **kw):
        seen["messages"] = messages
        return "Subject: Re: Interview\n\nHi Sarah,\n\nThursday 10:00 works for me.\n\nThanks,\nLuke"

    ctx = {"kind": "interview_invite", "company": "Gamma", "role": "Backend", "subject": "Interview",
           "summary": "Invite", "next_step": "pick a slot", "body": "Can you do Thursday?"}
    out = await tr.suggest_reply(ctx, {"voice_rules": "short, warm, no exclamation marks"}, complete, "vanders")
    assert out.startswith("Hi Sarah")
    assert "Subject:" not in out
    assert "short, warm, no exclamation marks" in seen["messages"][0]["content"]


async def test_nudge_prompt_mentions_follow_up():
    seen = {}

    async def complete(messages, owner=None, **kw):
        seen["messages"] = messages
        return "Hi, just following up on my application."

    await tr.suggest_reply({"kind": "nudge", "company": "Acme", "role": "Dev", "subject": "", "summary": "",
                            "next_step": "", "body": ""}, {}, complete, "vanders")
    assert "follow" in seen["messages"][-1]["content"].lower()


def test_write_cached_reply_inserts_once(tmp_path, monkeypatch):
    db = tmp_path / "sched.db"
    monkeypatch.setattr(tr, "SCHEDULED_DB", str(db))
    assert tr.write_cached_reply("<m1@x>", "first draft", model="m", uid="7") is True
    assert tr.write_cached_reply("<m1@x>", "second draft", model="m", uid="7") is False
    row = sqlite3.connect(db).execute("SELECT reply, uid, folder FROM email_ai_replies WHERE message_id=?", ("<m1@x>",)).fetchone()
    assert row == ("first draft", "7", "INBOX")
