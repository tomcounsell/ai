"""`core/mail.py`: the reply-all a turn may ask for, and the message an
`email.send` payload becomes, measured as it is sent."""

import os

import pytest

from core import bridge, broker, mail

pytestmark = pytest.mark.spend(usd=0)

VALOR = "valor@test.local"


def row(**over):
    base = {
        "message_id": "<tom-2@yuda.me>",
        "sender_id": "tom@yuda.me",
        "thread": [{"id": "<root@valor.test>"}, {"id": "<tom-1@yuda.me>"}],
        "headers": {
            "subject": "Plans",
            "to": ["Valor@Test.Local", "ann@example.com"],
            "cc": ["bob@example.com", "ANN@example.com", "tom@yuda.me"],
        },
    }
    base.update(over)
    return base


def test_reply_all_answers_the_sender_and_copies_everyone_else_once():
    r = mail.reply_all(row(), VALOR)
    assert r["to"] == ["tom@yuda.me"]
    assert r["cc"] == ["ann@example.com", "bob@example.com"]
    assert r["subject"] == "Re: Plans"
    assert r["in_reply_to"] == "<tom-2@yuda.me>"
    assert r["references"] == ["<root@valor.test>", "<tom-1@yuda.me>", "<tom-2@yuda.me>"]


@pytest.mark.parametrize(
    ("subject", "expected"),
    [("RE: Plans", "RE: Plans"), ("re:x", "re:x"), ("", "Re: (no subject)"), ("Regarding", "Re: Regarding")],
)
def test_reply_subject(subject, expected):
    assert mail.reply_all(row(headers={"subject": subject}), VALOR)["subject"] == expected


def test_a_first_message_has_no_thread():
    r = mail.reply_all(row(thread=[], headers={"subject": "Hi"}), VALOR)
    assert r["references"] == ["<tom-2@yuda.me>"] and r["cc"] == []


def test_message_ids_are_per_key():
    assert mail.message_id("k1", VALOR) == mail.message_id("k1", VALOR)
    assert mail.message_id("k1", VALOR) != mail.message_id("k2", VALOR)
    assert mail.message_id("k1", VALOR).endswith("@test.local>")


def _payload(tmp_path, size):
    path = tmp_path / "file.bin"
    path.write_bytes(os.urandom(size))
    return {"to": ["tom@yuda.me"], "cc": [], "subject": "Re: x", "body": "see attached",
            "in_reply_to": None, "references": [], "files": [{"path": str(path), "sha256": ""}]}  # fmt: skip


def test_the_email_limit_is_on_the_whole_encoded_message(tmp_path):
    limits = bridge.LIMITS["email"]
    assert limits.max_message_bytes == 25_000_000 and limits.message_bytes is not None
    small = broker.Action("email.send", "tom@yuda.me", _payload(tmp_path, 1000))
    measured = limits.message_bytes(small)
    assert 1000 * 4 / 3 < measured < 1000 * 4 / 3 + 2000
    # 18.9 MB of file bytes encode past 25,000,000; 18.0 MB do not.
    over = broker.Action("email.send", "tom@yuda.me", _payload(tmp_path, 18_900_000))
    assert limits.message_bytes(over) > 25_000_000
    under = broker.Action("email.send", "tom@yuda.me", _payload(tmp_path, 18_000_000))
    assert limits.message_bytes(under) <= 25_000_000
