"""The watch's IMAP steps against Dovecot: the owned-sender search, the
header fetch, the body fetch that leaves mail unseen, and `\\Seen`."""

import imaplib
import threading
import time
from datetime import UTC, date, datetime, timedelta

import pytest

from bridges.email import imap
from tests.test_email_parse import message

pytestmark = pytest.mark.spend(usd=0)


def test_the_sender_query_is_an_or_tree_of_clean_from_terms():
    assert imap.sender_query(["tom@yuda.me"]) == 'FROM "tom@yuda.me"'
    assert imap.sender_query(["a@x", 'b"@y) OR ALL', "c@z"]) == 'OR FROM "a@x" OR FROM "b@yORALL" FROM "c@z"'
    with pytest.raises(ValueError):
        imap.sender_query([])


def test_search_finds_unseen_mail_from_the_senders_since_the_date(mailbox):
    d = mailbox.dovecot
    d.deliver(message(body="1", message_id="<one@yuda.me>"))
    d.deliver(message(body="2", message_id="<two@yuda.me>", sender="xtom@yuda.me"))
    d.deliver(message(body="3", message_id="<three@yuda.me>", sender="ann@example.com"))
    d.deliver(message(body="4", message_id="<four@yuda.me>"), when=time.time() - 40 * 86400)
    d.append("INBOX", message(body="5", message_id="<five@yuda.me>"), seen=True)
    conn = imap.connect(mailbox.config())
    try:
        imap.select_inbox(conn)
        since = datetime.now(UTC).date() - timedelta(days=10)
        uids = imap.search(conn, since, ["tom@yuda.me"])
        heads = imap.headers(conn, uids)
        # FROM is a substring search: xtom@ matches here, and the watch's
        # `owns` check is what leaves it out.
        assert sorted(h["message_id"] for h in heads.values()) == ["<one@yuda.me>", "<two@yuda.me>"]
        assert {h["sender"] for h in heads.values()} == {"tom@yuda.me", "xtom@yuda.me"}
    finally:
        imap.logout(conn)


def test_fetch_leaves_mail_unseen_until_marked(mailbox):
    raw = message(body="hello", message_id="<m@yuda.me>", References="<root@valor.test>")
    mailbox.dovecot.deliver(raw)
    conn = imap.connect(mailbox.config())
    try:
        first = imap.select_inbox(conn)
        assert first.isdigit()
        [uid] = imap.search(conn, date(2026, 1, 1), ["tom@yuda.me"])
        assert imap.headers(conn, [uid])[uid]["chat_id"] == "<root@valor.test>"
        body, internal = imap.fetch(conn, uid)
        assert body.replace(b"\r\n", b"\n") == raw.replace(b"\r\n", b"\n") and internal is not None
        assert imap.search(conn, date(2026, 1, 1), ["tom@yuda.me"]) == [uid]
        imap.mark_seen(conn, uid)
        assert imap.search(conn, date(2026, 1, 1), ["tom@yuda.me"]) == []
    finally:
        imap.logout(conn)
    [(_, flags)] = mailbox.dovecot.messages("INBOX")
    assert "\\Seen" in flags


def test_uidvalidity_is_read_and_follows_the_mailbox(mailbox):
    conn = imap.connect(mailbox.config())
    try:
        before = imap.select_inbox(conn)
    finally:
        imap.logout(conn)
    mailbox.dovecot.set_uidvalidity(int(before) + 7)
    conn = imap.connect(mailbox.config())
    try:
        assert imap.select_inbox(conn) == str(int(before) + 7)
    finally:
        imap.logout(conn)


def test_idle_returns_when_mail_arrives(mailbox):
    conn = imap.connect(mailbox.config())
    try:
        imap.select_inbox(conn)
        raw = message(body="new", message_id="<new@yuda.me>")
        threading.Thread(target=lambda: mailbox.imap.idling.wait(30) and mailbox.dovecot.deliver(raw)).start()
        start = time.monotonic()
        assert imap.idle(conn) is True
        assert time.monotonic() - start < imap.IDLE_REISSUE_S
        # IDLE has ended (DONE sent), so the connection takes commands.
        assert imap.search(conn, date(2026, 1, 1), ["tom@yuda.me"])
    finally:
        imap.logout(conn)


def test_dropping_the_connection_ends_an_idle_blocked_in_another_thread(mailbox):
    conn = imap.connect(mailbox.config())
    imap.select_inbox(conn)
    ended = []

    def idling():
        try:
            imap.idle(conn)
        except (imaplib.IMAP4.error, OSError, ValueError) as e:
            ended.append(e)
        else:
            ended.append(None)

    t = threading.Thread(target=idling)
    t.start()
    assert mailbox.imap.idling.wait(30)
    imap.drop(conn)
    t.join(10)
    assert not t.is_alive() and ended


def test_mail_announced_during_another_command_is_found_before_idling(mailbox, monkeypatch):
    """Dovecot reports new mail inside the reply to the command that runs
    when it lands, which IDLE would never see; idle returns at once so the
    watch searches again, and mail is not left waiting for the re-issue."""
    monkeypatch.setattr(imap, "IDLE_REISSUE_S", 3)
    conn = imap.connect(mailbox.config())
    try:
        imap.select_inbox(conn)
        assert imap.search(conn, date(2026, 1, 1), ["tom@yuda.me"]) == []
        mailbox.dovecot.deliver(message(body="during", message_id="<during@yuda.me>"))
        conn.noop()  # the server announces the new message in this reply
        start = time.monotonic()
        assert imap.idle(conn) is True
        assert time.monotonic() - start < 2
        assert not mailbox.imap.idling.is_set()
        assert imap.search(conn, date(2026, 1, 1), ["tom@yuda.me"])
        # Seen once, it is not announced again: the next idle waits.
        assert imap.idle(conn) is False
    finally:
        imap.logout(conn)
