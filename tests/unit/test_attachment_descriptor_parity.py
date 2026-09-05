"""Medium parity for attachments (#3136).

An agent's interaction with a bridge is identical for every messaging
medium. For a file, that means one descriptor shape (`bridge.context.
media_descriptor`), one classifier (`describe_local_media`), one renderer
(`format_media_descriptor` / `format_attachments`), and one delivery seam
(`agent.session_executor.prepend_trigger_attachments`), with the Telegram
intake and the email intake as two producers feeding it.

These tests put the same bytes at the same path and assert the descriptor
each producer builds is equal, structurally and rendered, across the
resolved and every degraded state the two producers share.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent.session_executor import prepend_trigger_attachments
from bridge.context import (
    describe_local_media,
    format_attachments,
    format_media_descriptor,
    media_descriptor,
    telegram_media_descriptor,
)
from bridge.email_bridge import _attachment_descriptors

pytestmark = pytest.mark.unit


def _document_media(filename: str):
    """A genuine MessageMediaDocument that get_media_type classifies from its filename."""
    from telethon.tl.types import DocumentAttributeFilename, MessageMediaDocument

    media = MessageMediaDocument.__new__(MessageMediaDocument)
    media.document = SimpleNamespace(attributes=[DocumentAttributeFilename(file_name=filename)])
    return media


def _telegram_msg(filename: str, msg_id: int = 10):
    return SimpleNamespace(
        id=msg_id,
        media=_document_media(filename),
        file=SimpleNamespace(name=filename),
    )


def _email_att(path, content_type: str) -> dict:
    return {
        "filename": path.name,
        "content_type": content_type,
        "size": path.stat().st_size if path.exists() else 0,
        "path": str(path),
    }


@pytest.fixture
def shared_root(tmp_path, monkeypatch):
    """One media root for both producers, so local_path is byte-identical."""
    root = tmp_path / "media"
    root.mkdir()
    monkeypatch.setattr("bridge.context.MEDIA_DIR", root)
    monkeypatch.setattr("bridge.email_bridge.EMAIL_ATTACHMENT_DIR", root)
    return root


def _both(shared_root, filename: str, content_type: str, *, write: bool = True):
    path = shared_root / filename
    if write:
        path.write_bytes(b"identical bytes on both mediums")
    telegram = telegram_media_descriptor(
        _telegram_msg(filename), local_path=str(path), download_error=None
    )
    email = _attachment_descriptors(
        {
            "attachments": [_email_att(path, content_type)],
            "attachments_truncated": False,
            "body": "see attached",
        }
    )
    return telegram, email, path


@pytest.mark.parametrize(
    "filename,content_type,expected_type",
    [
        ("report.pdf", "application/pdf", "document"),
        ("chart.png", "image/png", "image"),
        ("memo.m4a", "audio/mp4", "audio"),
        ("data.csv", "text/csv", "document"),
    ],
)
def test_same_file_yields_identical_descriptor_on_both_mediums(
    shared_root, filename, content_type, expected_type
):
    telegram, email, path = _both(shared_root, filename, content_type)
    assert email == [telegram]
    assert telegram == media_descriptor("resolved", filename, expected_type, str(path), None)
    assert format_attachments(email) == format_media_descriptor(telegram)


def test_missing_file_degrades_identically_on_both_mediums(shared_root):
    telegram, email, _ = _both(shared_root, "gone.pdf", "application/pdf", write=False)
    assert email == [telegram]
    assert telegram["kind"] == "unreadable" and telegram["reason"] == "file_missing"


def test_path_outside_root_degrades_identically_on_both_mediums(shared_root, tmp_path):
    outside = tmp_path / "elsewhere" / "leak.pdf"
    outside.parent.mkdir()
    outside.write_bytes(b"x")
    telegram = telegram_media_descriptor(
        _telegram_msg("leak.pdf"), local_path=str(outside), download_error=None
    )
    email = _attachment_descriptors(
        {
            "attachments": [_email_att(outside, "application/pdf")],
            "attachments_truncated": False,
            "body": "",
        }
    )
    assert email == [telegram]
    assert telegram["reason"] == "invalid_path" and telegram["local_path"] is None


def test_describe_local_media_is_the_single_classifier(shared_root):
    """Both producers are thin wrappers over one classification."""
    path = shared_root / "report.pdf"
    path.write_bytes(b"x")
    direct = describe_local_media("report.pdf", "document", str(path), media_root=shared_root)
    telegram, email, _ = _both(shared_root, "report.pdf", "application/pdf")
    assert direct == telegram == email[0]


def test_turn_input_is_identical_for_the_same_file_on_both_mediums(shared_root):
    telegram, email, path = _both(shared_root, "report.pdf", "application/pdf")
    body = "can you summarize this?"
    via_telegram = prepend_trigger_attachments(body, {"attachments": [telegram]}, "p")
    via_email = prepend_trigger_attachments(body, {"attachments": email}, "p")
    assert via_telegram == via_email
    assert via_telegram == (f"[attachment: report.pdf (document) at machine path {path}]\n\n{body}")


def test_delivery_seam_is_fail_quiet_and_transparent_without_attachments():
    assert prepend_trigger_attachments("hi", None, "p") == "hi"
    assert prepend_trigger_attachments("hi", {}, "p") == "hi"
    assert prepend_trigger_attachments("hi", {"attachments": []}, "p") == "hi"
    assert prepend_trigger_attachments("hi", {"attachments": "garbage"}, "p") == "hi"


def test_non_file_telegram_media_yields_no_descriptor():
    """Link previews, polls, geo pins: get_media_type declines, so no marker is ever built."""
    from telethon.tl.types import MessageMediaPoll

    poll = SimpleNamespace(id=3, media=MessageMediaPoll.__new__(MessageMediaPoll), file=None)
    assert telegram_media_descriptor(poll, local_path=None, download_error=None) is None


def test_telegram_download_error_wins_over_path(shared_root):
    path = shared_root / "report.pdf"
    path.write_bytes(b"x")
    descriptor = telegram_media_descriptor(
        _telegram_msg("report.pdf"), local_path=str(path), download_error="timeout after 30s"
    )
    assert descriptor["kind"] == "unreadable"
    assert descriptor["reason"] == "download_error: timeout after 30s"
