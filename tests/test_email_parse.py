"""Raw mail to an inbound record's fields, and attachments to files."""

import hashlib
import os
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import pytest

from bridges.email import parse

pytestmark = pytest.mark.spend(usd=0)

FIXTURES = Path(__file__).parent / "fixtures" / "mail"


def message(subject="Hello", body="", files=(), **headers) -> bytes:
    msg = EmailMessage()
    msg["From"] = headers.pop("sender", "Tom <tom@yuda.me>")
    msg["To"] = "valor@test.local"
    if subject is not None:
        msg["Subject"] = subject
    msg["Message-ID"] = headers.pop("message_id", "<m1@yuda.me>")
    msg["Date"] = headers.pop("date", "Sat, 03 Oct 2026 09:00:00 +0000")
    for name, value in headers.items():
        msg[name.replace("_", "-")] = value
    if body is not None:
        msg.set_content(body)
    for name, data in files:
        msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=name)
    return msg.as_bytes()


def test_empty_body_no_attachments_is_a_record_of_the_subject_and_a_blank_line():
    f = parse.parse_email_message(message(body="")).fields
    assert f["text"] == "Hello\n\n"
    assert f["attachments"] == []
    assert f["sender_id"] == "tom@yuda.me" and f["sender_name"] == "Tom"
    assert f["chat_kind"] == "email" and f["kind"] == "message"


def test_empty_body_with_an_attachment_attachment_only_and_no_subject(tmp_path):
    f = parse.parse_email_message(message(body="", files=[("a.txt", b"one")])).fields
    assert f["text"] == "Hello\n\n" and [a["name"] for a in f["attachments"]] == ["a.txt"]

    only = EmailMessage()
    only["From"], only["Message-ID"] = "tom@yuda.me", "<only@yuda.me>"
    only.add_attachment(b"%PDF", maintype="application", subtype="pdf", filename="doc.pdf")
    p = parse.parse_email_message(only.as_bytes())
    assert p.fields["text"] == "\n\n" and p.fields["attachments"][0]["bytes"] == 4

    assert parse.parse_email_message(message(subject=None, body="hi")).fields["text"] == "\n\nhi"


def test_large_attachments_and_many_parts_are_all_saved(tmp_path):
    nine = os.urandom(9 * 1024 * 1024)
    thirty = os.urandom(30 * 1024 * 1024)
    p = parse.parse_email_message(message(body="big", files=[("nine.bin", nine), ("thirty.bin", thirty)]))
    parse.persist(p, tmp_path)
    for att, data in zip(p.fields["attachments"], (nine, thirty), strict=True):
        saved = Path(att["path"]).read_bytes()
        assert att["bytes"] == len(data) and hashlib.sha256(saved).digest() == hashlib.sha256(data).digest()

    parts = [(f"part{i}.bin", os.urandom(64)) for i in range(60)]
    p = parse.parse_email_message(message(body="many", files=parts))
    parse.persist(p, tmp_path / "many")
    assert len(p.fields["attachments"]) == 60
    assert all(Path(a["path"]).exists() for a in p.fields["attachments"])


def test_an_extension_is_kept_whole_unless_the_filesystem_cannot_name_the_file(tmp_path):
    long = "a" * 40
    p = parse.parse_email_message(
        message(body="x", files=[(f"f.{long}", b"one"), ("g." + "b" * 400, b"two")])
    )
    parse.persist(p, tmp_path)
    one, two = (Path(a["path"]) for a in p.fields["attachments"])
    assert one.name.endswith(f".{long}") and one.read_bytes() == b"one"
    assert "." not in two.name and two.read_bytes() == b"two"


def test_filenames_are_sanitized_and_files_named_by_their_bytes(tmp_path):
    files = [("../../etc/passwd", b"one"), (".", b"two"), ("", b"three"), ("report.pdf", b"four"),
             ("report.pdf", b"five")]  # fmt: skip
    p = parse.parse_email_message(message(body="x", files=files))
    parse.persist(p, tmp_path)
    atts = p.fields["attachments"]
    assert [a["name"] for a in atts][:1] == ["passwd"]
    assert all("/" not in a["name"] and a["name"] not in ("", ".", "..") for a in atts)
    assert atts[3]["name"] == atts[4]["name"] == "report.pdf"
    paths = [Path(a["path"]) for a in atts]
    assert len(set(paths)) == 5 and all(path.parent == tmp_path for path in paths)
    for path, (_, data) in zip(paths, files, strict=True):
        assert path.name.split(".")[0] == hashlib.sha256(data).hexdigest()
    assert (tmp_path.parent / "etc").exists() is False


def test_the_same_bytes_land_in_one_file(tmp_path):
    p = parse.parse_email_message(message(body="x", files=[("a.txt", b"same"), ("b.txt", b"same")]))
    parse.persist(p, tmp_path)
    assert p.fields["attachments"][0]["path"] == p.fields["attachments"][1]["path"]
    assert len(list(tmp_path.iterdir())) == 1


def test_encoded_words_and_an_unknown_charset():
    f = parse.parse_email_message((FIXTURES / "charsets.eml").read_bytes()).fields
    assert f["sender_name"] == "Jürgen"
    assert f["text"] == "Grüße aus Berlin\n\nplain words"
    assert f["headers"]["subject"] == "Grüße aus Berlin"


def test_malformed_mime_parses_loosely_and_a_part_that_fails_to_decode_is_skipped(tmp_path):
    p = parse.parse_email_message((FIXTURES / "malformed.eml").read_bytes())
    parse.persist(p, tmp_path)
    assert p.fields["text"] == "broken\n\nthe body survives"
    [att] = p.fields["attachments"]
    assert att["name"] == "cut.pdf" and att["bytes"] == 0 and "skipped" in att and "path" not in att
    assert list(tmp_path.iterdir()) == []


def test_html_only_mail_is_reduced_to_its_text():
    f = parse.parse_email_message((FIXTURES / "html-only.eml").read_bytes()).fields
    assert f["text"] == "html only\n\nFish & chips\nsecond line"


def test_no_message_id_is_given_the_digest_of_the_bytes():
    raw = message(body="x").replace(b"Message-ID: <m1@yuda.me>\n", b"")
    first = parse.parse_email_message(raw).fields
    assert first["message_id"] == "sha256:" + hashlib.sha256(raw).hexdigest()
    assert parse.parse_email_message(raw).fields["message_id"] == first["message_id"]
    assert first["chat_id"] == first["message_id"]


def test_no_from_is_kept_with_an_empty_sender():
    raw = message(body="x").replace(b"From: Tom <tom@yuda.me>\n", b"")
    f = parse.parse_email_message(raw).fields
    assert f["sender_id"] == "" and f["headers"]["from"] == []


def test_references_give_the_thread_and_its_root():
    folded = b"References: <root@yuda.me> stray text\n <second@valor.test>\n\t<third@yuda.me>\n"
    raw = folded + message(body="x", In_Reply_To="<third@yuda.me>")
    f = parse.parse_email_message(raw).fields
    assert f["thread"] == [{"id": "<root@yuda.me>"}, {"id": "<second@valor.test>"}, {"id": "<third@yuda.me>"}]
    assert f["chat_id"] == "<root@yuda.me>" and f["reply_to"] == "<third@yuda.me>"
    alone = parse.parse_email_message(message(body="x")).fields
    assert alone["thread"] == [] and alone["chat_id"] == "<m1@yuda.me>" and alone["reply_to"] is None


def test_raw_identity_headers_are_carried_topmost_first():
    raw = b"Authentication-Results: mx.google.com; dmarc=pass header.from=yuda.me\r\n" + message(
        body="x"
    ).replace(b"\n", b"\r\n").replace(b"\r\r\n", b"\r\n")
    raw = raw.replace(b"\r\nFrom:", b"\r\nAuthentication-Results: forged; dmarc=pass\r\nFrom:", 1)
    h = parse.parse_email_message(raw).fields["headers"]
    assert h["authentication_results"][0].startswith("mx.google.com")
    assert h["authentication_results"][1].startswith("forged")
    assert h["from"] == ["Tom <tom@yuda.me>"]


def test_an_unparseable_date_takes_the_internal_date():
    internal = datetime(2026, 10, 1, 8, 30, tzinfo=UTC)
    f = parse.parse_email_message(message(body="x", date="not a date"), internal).fields
    assert f["sent_at"] == internal.isoformat()
    good = parse.parse_email_message(message(body="x"), internal).fields
    assert good["sent_at"] == "2026-10-03T09:00:00+00:00"
