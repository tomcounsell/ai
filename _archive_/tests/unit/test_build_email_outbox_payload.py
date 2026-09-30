"""Unit tests for the ``build_email_outbox_payload`` pure function.

This function is extracted from ``TelegramRelayOutputHandler._send_via_email_outbox``
and is shared by the async send path and the synchronous
``flush_deferred_self_draft_sync`` chokepoint.
"""

from __future__ import annotations

import types

from agent.output_handler import build_email_outbox_payload


def _session(
    session_id: str = "test-session-1",
    *,
    email_subject: str | None = "Deployment Status",
    email_message_id: str | None = "<msg123@example.com>",
    email_to_addrs: list[str] | None = None,
    email_cc_addrs: list[str] | None = None,
) -> types.SimpleNamespace:
    """Build a minimal session stub with the right extra_context shape."""
    extra = {}
    if email_subject is not None:
        extra["email_subject"] = email_subject
    if email_message_id is not None:
        extra["email_message_id"] = email_message_id
    if email_to_addrs is not None:
        extra["email_to_addrs"] = email_to_addrs
    if email_cc_addrs is not None:
        extra["email_cc_addrs"] = email_cc_addrs
    return types.SimpleNamespace(session_id=session_id, extra_context=extra)


# ---------------------------------------------------------------------------
# Basic payload shape
# ---------------------------------------------------------------------------


def test_returns_dict_with_expected_keys():
    s = _session()
    payload = build_email_outbox_payload(s, "sender@example.com", "Hello")
    required_keys = {
        "session_id",
        "to",
        "subject",
        "body",
        "attachments",
        "in_reply_to",
        "references",
        "from_addr",
        "timestamp",
    }
    assert required_keys.issubset(payload.keys())


def test_body_is_verbatim_text():
    s = _session()
    body = "The deployment is complete and all tests pass."
    payload = build_email_outbox_payload(s, "sender@example.com", body)
    assert payload["body"] == body


# ---------------------------------------------------------------------------
# Subject prefixing
# ---------------------------------------------------------------------------


def test_plain_subject_gets_re_prefix():
    s = _session(email_subject="Deployment Status")
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["subject"] == "Re: Deployment Status"


def test_subject_already_re_prefixed_passes_through():
    s = _session(email_subject="Re: Deployment Status")
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["subject"] == "Re: Deployment Status"


def test_subject_re_case_insensitive_passthrough():
    """'RE:' or 'rE:' must not get a second prefix — the original is passed through unchanged."""
    for prefix in ("RE:", "Re:", "rE:", "re:"):
        original = f"{prefix} Ticket #42"
        s = _session(email_subject=original)
        payload = build_email_outbox_payload(s, "sender@example.com", "Done")
        assert payload["subject"] == original, (
            f"subject already starting with '{prefix}' must not be re-prefixed"
        )


def test_empty_subject_becomes_re_no_subject():
    s = _session(email_subject="")
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["subject"] == "Re: (no subject)"


def test_none_subject_becomes_re_no_subject():
    s = _session(email_subject=None)
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["subject"] == "Re: (no subject)"


# ---------------------------------------------------------------------------
# threading headers
# ---------------------------------------------------------------------------


def test_in_reply_to_from_email_message_id():
    s = _session(email_message_id="<abc123@host.example>")
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["in_reply_to"] == "<abc123@host.example>"
    assert payload["references"] == "<abc123@host.example>"


def test_missing_email_message_id_yields_none():
    s = _session(email_message_id=None)
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["in_reply_to"] is None
    assert payload["references"] is None


# ---------------------------------------------------------------------------
# from_addr
# ---------------------------------------------------------------------------


def test_from_addr_reads_smtp_user_env(monkeypatch):
    monkeypatch.setenv("SMTP_USER", "robot@example.com")
    s = _session()
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["from_addr"] == "robot@example.com"


def test_from_addr_empty_when_smtp_user_unset(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    s = _session()
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["from_addr"] == ""


# ---------------------------------------------------------------------------
# reply-all recipient construction and deduplication
# ---------------------------------------------------------------------------


def test_to_field_starts_with_chat_id():
    s = _session(email_to_addrs=[], email_cc_addrs=[])
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["to"][0] == "sender@example.com"


def test_sender_in_original_to_does_not_appear_twice():
    """The primary recipient (chat_id) in email_to_addrs must be deduplicated."""
    s = _session(
        email_to_addrs=["sender@example.com", "other@example.com"],
        email_cc_addrs=[],
    )
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    addresses = [a.lower() for a in payload["to"]]
    assert addresses.count("sender@example.com") == 1, (
        "primary recipient must appear exactly once in to"
    )


def test_smtp_user_filtered_from_to(monkeypatch):
    """The SMTP_USER (our own address) must be excluded from the to list."""
    monkeypatch.setenv("SMTP_USER", "robot@example.com")
    s = _session(
        email_to_addrs=["robot@example.com", "other@example.com"],
        email_cc_addrs=["robot@example.com"],
    )
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    lower_addrs = [a.lower() for a in payload["to"]]
    assert "robot@example.com" not in lower_addrs, "SMTP_USER (self) must not appear in the to list"


def test_cc_addresses_included_in_reply_all(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    s = _session(
        email_to_addrs=["to1@example.com"],
        email_cc_addrs=["cc1@example.com", "cc2@example.com"],
    )
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    lower_addrs = [a.lower() for a in payload["to"]]
    assert "to1@example.com" in lower_addrs
    assert "cc1@example.com" in lower_addrs
    assert "cc2@example.com" in lower_addrs


def test_duplicate_cc_address_deduplicated(monkeypatch):
    monkeypatch.delenv("SMTP_USER", raising=False)
    s = _session(
        email_to_addrs=["dup@example.com"],
        email_cc_addrs=["dup@example.com"],
    )
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    lower_addrs = [a.lower() for a in payload["to"]]
    assert lower_addrs.count("dup@example.com") == 1, (
        "duplicate address across to/cc must appear only once"
    )


# ---------------------------------------------------------------------------
# session_id fallback
# ---------------------------------------------------------------------------


def test_session_id_from_session_attribute():
    s = _session(session_id="my-session-42")
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["session_id"] == "my-session-42"


def test_session_id_falls_back_to_chat_id():
    """When session has no session_id, chat_id is the fallback."""
    s = types.SimpleNamespace(session_id=None, extra_context={})
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["session_id"] == "sender@example.com"


# ---------------------------------------------------------------------------
# file_paths / attachments
# ---------------------------------------------------------------------------


def test_empty_attachments_by_default():
    s = _session()
    payload = build_email_outbox_payload(s, "sender@example.com", "Done")
    assert payload["attachments"] == []


def test_file_paths_passed_through_as_attachments():
    s = _session()
    payload = build_email_outbox_payload(
        s, "sender@example.com", "Done", file_paths=["/tmp/a.pdf", "/tmp/b.png"]
    )
    assert payload["attachments"] == ["/tmp/a.pdf", "/tmp/b.png"]
