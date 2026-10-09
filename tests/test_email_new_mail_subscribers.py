"""_record_email_received_events hands NEW list dicts to registered subscribers
(after the first baseline call) without blocking the list handler."""
import asyncio

import pytest

fastapi = pytest.importorskip("fastapi")

import routes.email_routes as er


@pytest.fixture()
def env(monkeypatch, tmp_path):
    monkeypatch.setattr(er, "SCHEDULED_DB", str(tmp_path / "sched.db"))
    monkeypatch.setattr(er, "_NEW_MAIL_SUBSCRIBERS", [])
    calls = []

    async def sub(owner, account_id, fresh):
        calls.append((owner, account_id, [e["message_id"] for e in fresh]))

    er.register_new_mail_subscriber(sub)
    return calls


def _run(fn):
    async def main():
        fn()
        await asyncio.sleep(0)   # let create_task'd subscribers run
        await asyncio.sleep(0)
    asyncio.run(main())


def test_first_call_baselines_without_dispatch(env):
    calls = env
    first = [{"uid": "1", "message_id": "<a@x>", "subject": "hi"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", first))
    assert calls == []


def test_second_call_dispatches_only_new_messages(env):
    calls = env
    first = [{"uid": "1", "message_id": "<a@x>", "subject": "hi"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", first))
    second = first + [{"uid": "2", "message_id": "<b@x>", "subject": "Thanks for applying"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", second))
    assert calls == [("vanders", "acc1", ["<b@x>"])]


def test_non_inbox_and_anonymous_are_ignored(env):
    calls = env
    msgs = [{"uid": "1", "message_id": "<a@x>"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "Sent", msgs))
    _run(lambda: er._record_email_received_events("", "acc1", "INBOX", msgs))
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", msgs))
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", msgs + [{"uid": "9", "message_id": "<z@x>"}]))
    assert calls == [("vanders", "acc1", ["<z@x>"])]


def test_subscriber_exception_does_not_propagate(env):
    calls = env

    async def bad(owner, account_id, fresh):
        raise RuntimeError("boom")

    er.register_new_mail_subscriber(bad)
    msgs = [{"uid": "1", "message_id": "<a@x>"}]
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", msgs))
    _run(lambda: er._record_email_received_events("vanders", "acc1", "INBOX", msgs + [{"uid": "2", "message_id": "<b@x>"}]))
    assert calls == [("vanders", "acc1", ["<b@x>"])]
