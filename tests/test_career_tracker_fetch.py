"""fetch_full parses one RFC822 message from the owner-scoped IMAP helper."""
import email.message
from contextlib import contextmanager

import pytest

pytest.importorskip("fastapi")

import routes.email_helpers as eh
import routes.email_routes as er
from services.career import tracker_fetch as tf


def _raw():
    m = email.message.EmailMessage()
    m["Subject"] = "Interview invitation"
    m["From"] = "Sarah <sarah@acme.com>"
    m["Reply-To"] = "recruiting@acme.com"
    m["Message-ID"] = "<i1@acme>"
    m["In-Reply-To"] = "<conf@acme>"
    m["List-Unsubscribe"] = "<https://x>"
    m.set_content("Are you free Thursday?")
    return m.as_bytes()


async def test_fetch_full_returns_headers_and_body(monkeypatch):
    seen = {}

    class Conn:
        def select(self, folder, readonly=False):
            seen["folder"] = (folder, readonly)

    @contextmanager
    def fake_imap(account_id, owner=""):
        seen["scope"] = (account_id, owner)
        yield Conn()

    monkeypatch.setattr(eh, "_imap", fake_imap)
    monkeypatch.setattr(er, "_imap_uid_fetch", lambda conn, uid, q: ("OK", [(b"5 (RFC822 {1}", _raw())]))
    out = await tf.fetch_full("vanders", "acc1", "5")
    assert seen["scope"] == ("acc1", "vanders") and seen["folder"][1] is True
    h = out["headers"]
    assert h["subject"] == "Interview invitation" and h["from_address"] == "sarah@acme.com"
    assert h["reply_to"] == "recruiting@acme.com" and h["message_id"] == "<i1@acme>"
    assert h["in_reply_to"] == "<conf@acme>" and h["list_unsubscribe"] == "<https://x>"
    assert "Thursday" in out["body"]


async def test_fetch_full_raises_when_uid_missing(monkeypatch):
    @contextmanager
    def fake_imap(account_id, owner=""):
        class Conn:
            def select(self, folder, readonly=False):
                pass
        yield Conn()

    monkeypatch.setattr(eh, "_imap", fake_imap)
    monkeypatch.setattr(er, "_imap_uid_fetch", lambda conn, uid, q: ("OK", [None]))
    with pytest.raises(RuntimeError):
        await tf.fetch_full("vanders", "acc1", "99")
