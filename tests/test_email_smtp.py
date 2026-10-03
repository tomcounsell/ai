"""`email.send` against the local servers: the performer sends once, and
the lookup finds the send in the folder flagged `\\Sent` by its exact
Message-ID, or says it cannot tell."""

import email
import email.policy
import hashlib
import os
import smtplib
from datetime import UTC, datetime, timedelta

import pytest

from bridges.email import smtp
from bridges.email.config import SMTPTimeouts
from core import bridge, broker, mail

pytestmark = pytest.mark.spend(usd=0)

NOW = datetime.now(UTC).isoformat()


def action(tmp_path=None, files=(), **over) -> broker.Action:
    payload = {
        "to": ["tom@yuda.me"],
        "cc": ["ann@example.com"],
        "subject": "Re: Plans",
        "body": "Done.\n.a line starting with a dot\n",
        "in_reply_to": "<tom-2@yuda.me>",
        "references": ["<root@valor.test>", "<tom-2@yuda.me>"],
        "files": [],
    }
    for name, data in files:
        path = tmp_path / name
        path.write_bytes(data)
        payload["files"].append({"path": str(path), "sha256": hashlib.sha256(data).hexdigest()})
    payload.update(over)
    return broker.Action("email.send", ",".join(sorted(payload["to"])), payload)


def test_a_send_arrives_once_with_the_message_id_of_its_key_and_is_found(mailbox, tmp_path):
    cfg = mailbox.config()
    act = action(tmp_path, files=[("notes.txt", b"the notes\n")])
    key = act.key("effect-0")
    result = smtp.perform(cfg, act, key)

    mid = bridge.email_message_id(key, cfg.address)
    assert result["message_id"] == mid
    assert result["accepted"] == ["tom@yuda.me", "ann@example.com"] and result["refused"] == {}
    assert result["sent"] == [{"channel": "email", "chat_id": "<root@valor.test>", "message_id": mid}]

    [got] = mailbox.smtp.accepted
    msg = email.message_from_bytes(got.data, policy=email.policy.default)
    assert msg["Message-ID"] == mid and msg["In-Reply-To"] == "<tom-2@yuda.me>"
    assert msg["References"] == "<root@valor.test> <tom-2@yuda.me>"
    assert got.rcpts == ["tom@yuda.me", "ann@example.com"]
    body = next(p for p in msg.walk() if p.get_content_type() == "text/plain" and not p.get_filename())
    assert ".a line starting with a dot" in body.get_content()
    attached = next(p for p in msg.walk() if p.get_filename() == "notes.txt")
    assert attached.get_payload(decode=True) == b"the notes\n"

    found = smtp.lookup(cfg, act, key, NOW)
    assert found["message_id"] == mid and found["sent"] == result["sent"]
    assert smtp.lookup(cfg, act, key + "-other", NOW) is None


def test_the_measured_size_is_the_size_sent(mailbox, tmp_path):
    cfg = mailbox.config()
    act = action(tmp_path, files=[("a.bin", os.urandom(200_000)), ("b.pdf", os.urandom(3333))])
    smtp.perform(cfg, act, act.key("effect-0"))
    [got] = mailbox.smtp.accepted
    assert len(got.data) == mail.email_encoded_bytes(act.payload, cfg.address)


def test_two_identical_replies_are_two_messages(mailbox, tmp_path):
    cfg = mailbox.config()
    act = action()
    first = smtp.perform(cfg, act, act.key("effect-1"))
    second = smtp.perform(cfg, act, act.key("effect-2"))
    assert first["message_id"] != second["message_id"]
    assert len(mailbox.smtp.accepted) == 2


def test_a_refused_recipient_is_recorded_and_the_rest_receive_it(mailbox):
    mailbox.smtp.behavior.refuse = {"ann@example.com"}
    result = smtp.perform(mailbox.config(), action(), action().key("effect-0"))
    assert result["accepted"] == ["tom@yuda.me"]
    assert result["refused"]["ann@example.com"].startswith("550")
    assert len(mailbox.smtp.accepted) == 1


def test_every_recipient_refused_is_a_definite_refusal(mailbox):
    mailbox.smtp.behavior.refuse = {"tom@yuda.me", "ann@example.com"}
    with pytest.raises(smtp.SendRefused, match="every recipient refused"):
        smtp.perform(mailbox.config(), action(), action().key("effect-0"))
    assert mailbox.smtp.accepted == []


def test_a_file_changed_after_approval_is_refused_before_connecting(mailbox, tmp_path):
    act = action(tmp_path, files=[("plan.txt", b"approved bytes")])
    (tmp_path / "plan.txt").write_bytes(b"swapped bytes")
    with pytest.raises(smtp.SendRefused, match="sha256"):
        smtp.perform(mailbox.config(), act, act.key("effect-0"))
    (tmp_path / "plan.txt").unlink()
    with pytest.raises(smtp.SendRefused, match="cannot be read"):
        smtp.perform(mailbox.config(), act, act.key("effect-0"))
    assert mailbox.smtp.connections == 0 and mailbox.smtp.accepted == []


def test_a_final_reply_later_than_its_timeout_is_in_doubt_and_the_lookup_finds_it(mailbox):
    cfg = mailbox.config(smtp_timeouts=SMTPTimeouts(data_end=1.0))
    mailbox.smtp.behavior.reply_delay_s = 3.0
    act = action()
    with pytest.raises(broker.Unknown, match="in doubt"):
        smtp.perform(cfg, act, act.key("effect-0"))
    assert len(mailbox.smtp.accepted) == 1
    found = smtp.lookup(cfg, act, act.key("effect-0"), NOW)
    assert found["message_id"] == bridge.email_message_id(act.key("effect-0"), cfg.address)


def test_a_body_that_stalls_before_its_end_of_data_line_is_a_definite_failure(mailbox, tmp_path):
    cfg = mailbox.config(smtp_timeouts=SMTPTimeouts(data_block=1.0))
    mailbox.smtp.behavior.read_delay_s = 30.0
    act = action(tmp_path, files=[("big.bin", os.urandom(9_000_000))])
    with pytest.raises(smtp.SendRefused, match="before the end of data line"):
        smtp.perform(cfg, act, act.key("effect-0"))
    assert mailbox.smtp.accepted == []
    assert smtp.lookup(cfg, act, act.key("effect-0"), NOW) is None


def test_a_9_mb_attachment_sends_through_a_held_read_rate(mailbox, tmp_path):
    """Each send call has its own timer (RFC 5321 4.5.3.2.5), so a body
    longer in transit than any one timer still goes."""
    cfg = mailbox.config(smtp_timeouts=SMTPTimeouts(data_block=1.0))
    mailbox.smtp.behavior.read_bytes_per_s = 4_000_000
    data = os.urandom(9_000_000)
    act = action(tmp_path, files=[("big.bin", data)])
    smtp.perform(cfg, act, act.key("effect-0"))
    [got] = mailbox.smtp.accepted
    msg = email.message_from_bytes(got.data, policy=email.policy.default)
    attached = next(p for p in msg.walk() if p.get_filename() == "big.bin")
    assert hashlib.sha256(attached.get_payload(decode=True)).hexdigest() == hashlib.sha256(data).hexdigest()


def test_a_server_whose_certificate_the_ca_did_not_sign_is_refused(mailbox):
    cfg = mailbox.config(imap_port=mailbox.rogue.port)
    with pytest.raises(broker.Unknown):
        smtp.lookup(cfg, action(), action().key("effect-0"), NOW)
    with pytest.raises(OSError):
        smtp.perform(
            mailbox.config(cafile=str(mailbox.root / "rogue.pem")), action(), action().key("effect-0")
        )
    assert mailbox.smtp.accepted == []


def test_lookup_with_the_imap_server_down_cannot_tell(mailbox):
    mailbox.imap.stop()
    with pytest.raises(broker.Unknown):
        smtp.lookup(mailbox.config(imap_timeout_s=3.0), action(), action().key("effect-0"), NOW)


def test_lookup_searches_from_the_day_before_the_intent(mailbox):
    cfg = mailbox.config()
    act = action()
    smtp.perform(cfg, act, act.key("effect-0"))
    yesterday = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    assert smtp.lookup(cfg, act, act.key("effect-0"), yesterday) is not None
    later = (datetime.now(UTC) + timedelta(days=3)).isoformat()
    assert smtp.lookup(cfg, act, act.key("effect-0"), later) is None


def test_a_wrong_password_is_refused_and_appears_in_no_error(mailbox):
    wrong = "not-the-password-" + os.urandom(8).hex()
    cfg = mailbox.config(smtp_password=wrong, imap_password=wrong)
    with pytest.raises(smtplib.SMTPAuthenticationError) as sent:
        smtp.perform(cfg, action(), action().key("effect-0"))
    with pytest.raises(broker.Unknown) as looked:
        smtp.lookup(cfg, action(), action().key("effect-0"), NOW)
    for e in (sent.value, looked.value):
        assert wrong not in str(e) and wrong not in repr(e)
    assert mailbox.smtp.accepted == []
