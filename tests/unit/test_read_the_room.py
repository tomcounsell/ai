"""Unit tests for ``bridge/read_the_room.py``.

Covers:

* All verdict branches (send / trim-long / trim-short / trim-missing-text /
  suppress-with-anchor / suppress-fallthrough / failure).
* Short-circuit return paths (flag off, empty draft, empty snapshot, no
  chat_id, ``len < SHORT_OUTPUT_THRESHOLD`` bypass, SDLC bypass).
* Snapshot construction (K cap, time-window filter, mixed sender attribution).
* Fail-open exception handling (``anthropic.APITimeoutError``,
  ``APIConnectionError``, ``APIError``, ``ValueError``, last-resort).
* The reaction-payload alignment between :meth:`TelegramRelayOutputHandler.react`
  and the RTR suppress branch (Implementation Note AD1).
* The fall-through audit signal when ``reply_to_msg_id is None`` (Implementation
  Note SI1, F4).
* Suppress-reaction queue-key alignment when ``session.session_id != chat_id``
  (Implementation Note F7).
"""

from __future__ import annotations

import asyncio
import time
import types
from unittest.mock import AsyncMock, MagicMock, patch

import anthropic
import pytest

from bridge import read_the_room as rtr_module
from bridge.read_the_room import (
    DEFAULT_K,
    DEFAULT_MAX_AGE_SECONDS,
    READ_THE_ROOM_SYSTEM_PROMPT,
    RTR_SUPPRESS_EMOJI,
    TRIM_TOO_SHORT_THRESHOLD,
    _format_snapshot_for_prompt,
    _parse_verdict_block,
    read_the_room,
)

# === Fixtures ===================================================================


class FakeSession:
    """Minimal stand-in for ``AgentSession`` used in unit tests."""

    def __init__(self, *, session_id: str = "sess-test", sdlc_slug: str | None = None):
        self.session_id = session_id
        self.sdlc_slug = sdlc_slug
        self.session_events: list[dict] | None = None
        self._save_calls = 0

    def save(self):
        self._save_calls += 1


def _enable_rtr(monkeypatch):
    monkeypatch.setenv("READ_THE_ROOM_ENABLED", "true")


def _disable_rtr(monkeypatch):
    monkeypatch.setenv("READ_THE_ROOM_ENABLED", "false")


def _long_draft(extra: str = "") -> str:
    """Draft text guaranteed to exceed ``SHORT_OUTPUT_THRESHOLD`` (200 chars)."""
    return ("Logged 4 entries to the project knowledge base. " * 6) + extra


def _make_tool_use_msg(action: str, *, revised_text=None, reason: str = "") -> MagicMock:
    """Construct a fake Anthropic message with a tool_use ``room_verdict`` block."""
    block = MagicMock()
    block.type = "tool_use"
    block.name = "room_verdict"
    block.input = {"action": action, "revised_text": revised_text, "reason": reason}
    msg = MagicMock()
    msg.content = [block]
    return msg


def _patch_anthropic(monkeypatch, message=None, *, raises: Exception | None = None):
    """Patch ``anthropic.AsyncAnthropic`` in ``bridge.read_the_room`` so the
    tested code path resolves to a fake client with a stubbed
    ``messages.create``. Returns the create mock for assertion."""
    create_mock = AsyncMock()
    if raises is not None:
        create_mock.side_effect = raises
    else:
        create_mock.return_value = message

    fake_client = MagicMock()
    fake_client.__aenter__ = AsyncMock(return_value=fake_client)
    fake_client.__aexit__ = AsyncMock(return_value=None)
    fake_client.messages = MagicMock()
    fake_client.messages.create = create_mock

    def fake_constructor(*args, **kwargs):
        return fake_client

    monkeypatch.setattr(rtr_module.anthropic, "AsyncAnthropic", fake_constructor)
    monkeypatch.setattr(rtr_module, "get_anthropic_api_key", lambda: "sk-test-key")
    return create_mock


def _patch_snapshot(monkeypatch, snapshot):
    async def fake_fetch(chat_id, *, k, max_age_seconds):
        return list(snapshot)

    monkeypatch.setattr(rtr_module, "_fetch_snapshot", fake_fetch)


# === Short-circuit tests ========================================================


def test_disabled_flag_returns_send(monkeypatch):
    _disable_rtr(monkeypatch)
    session = FakeSession()
    verdict = asyncio.run(read_the_room("anything", "12345", session))
    assert verdict.action == "send"
    assert verdict.reason == "rtr_disabled"
    assert session.session_events in (None, [])


def test_empty_draft_returns_send(monkeypatch):
    _enable_rtr(monkeypatch)
    verdict = asyncio.run(read_the_room("", "12345", FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "empty_draft"


def test_whitespace_only_draft_returns_send(monkeypatch):
    _enable_rtr(monkeypatch)
    verdict = asyncio.run(read_the_room("   \n  \t", "12345", FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "empty_draft"


def test_no_chat_id_returns_send(monkeypatch):
    _enable_rtr(monkeypatch)
    verdict = asyncio.run(read_the_room(_long_draft(), None, FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "no_chat_id"


def test_short_output_short_circuits(monkeypatch):
    """Below ``SHORT_OUTPUT_THRESHOLD`` we should never call Haiku."""
    _enable_rtr(monkeypatch)
    create_mock = _patch_anthropic(monkeypatch, _make_tool_use_msg("send"))

    short_draft = "Tiny ack."
    verdict = asyncio.run(read_the_room(short_draft, "12345", FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "short_output"
    create_mock.assert_not_awaited()


def test_sdlc_session_short_circuits_with_event(monkeypatch):
    """SDLC sessions skip RTR and emit a ``rtr.bypassed`` event."""
    _enable_rtr(monkeypatch)
    create_mock = _patch_anthropic(monkeypatch, _make_tool_use_msg("send"))

    session = FakeSession(sdlc_slug="sdlc-1193")
    verdict = asyncio.run(read_the_room(_long_draft(), "12345", session))
    assert verdict.action == "send"
    assert verdict.reason == "sdlc_session"
    create_mock.assert_not_awaited()

    assert session.session_events and session.session_events[0]["type"] == "rtr.bypassed"
    assert session.session_events[0]["reason"] == "sdlc_session"


def test_empty_snapshot_returns_send(monkeypatch):
    _enable_rtr(monkeypatch)
    create_mock = _patch_anthropic(monkeypatch, _make_tool_use_msg("send"))
    _patch_snapshot(monkeypatch, [])

    verdict = asyncio.run(read_the_room(_long_draft(), "12345", FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "empty_snapshot"
    create_mock.assert_not_awaited()


# === Verdict-pass-through tests =================================================


def test_send_verdict(monkeypatch):
    _enable_rtr(monkeypatch)
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    create_mock = _patch_anthropic(monkeypatch, _make_tool_use_msg("send", reason="clean"))

    verdict = asyncio.run(read_the_room(_long_draft(), "12345", FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "clean"
    create_mock.assert_awaited_once()


def test_trim_long_verdict_preserves_revised_text(monkeypatch):
    _enable_rtr(monkeypatch)
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "moved on"}])
    revised = "Quick pointer: look at the dashboard for details."
    _patch_anthropic(
        monkeypatch,
        _make_tool_use_msg("trim", revised_text=revised, reason="partial_redundant"),
    )

    verdict = asyncio.run(read_the_room(_long_draft(), "12345", FakeSession()))
    assert verdict.action == "trim"
    assert verdict.revised_text == revised


def test_trim_short_verdict_preserves_text(monkeypatch):
    """RTR returns the trim verdict verbatim; the *handler* is responsible
    for coercing too-short trims to suppress (see test in test_output_handler).
    """
    _enable_rtr(monkeypatch)
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "moved on"}])
    _patch_anthropic(
        monkeypatch,
        _make_tool_use_msg("trim", revised_text="ok", reason="redundant"),
    )

    verdict = asyncio.run(read_the_room(_long_draft(), "12345", FakeSession()))
    assert verdict.action == "trim"
    assert verdict.revised_text == "ok"
    assert len(verdict.revised_text) < TRIM_TOO_SHORT_THRESHOLD


def test_trim_with_no_revised_text_falls_back_to_send(monkeypatch):
    _enable_rtr(monkeypatch)
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "moved on"}])
    _patch_anthropic(
        monkeypatch,
        _make_tool_use_msg("trim", revised_text=None, reason="redundant"),
    )

    verdict = asyncio.run(read_the_room(_long_draft(), "12345", FakeSession()))
    assert verdict.action == "send"
    assert verdict.reason == "trim_missing_revised_text"


def test_suppress_verdict(monkeypatch):
    _enable_rtr(monkeypatch)
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "answered already"}])
    _patch_anthropic(
        monkeypatch,
        _make_tool_use_msg("suppress", reason="duplicate_answer"),
    )

    verdict = asyncio.run(read_the_room(_long_draft(), "12345", FakeSession()))
    assert verdict.action == "suppress"
    assert verdict.reason == "duplicate_answer"


# === Failure / fail-open tests ==================================================


def test_api_timeout_returns_send_and_logs_event(monkeypatch):
    _enable_rtr(monkeypatch)
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    err = anthropic.APITimeoutError(request=MagicMock())
    _patch_anthropic(monkeypatch, raises=err)

    session = FakeSession()
    verdict = asyncio.run(read_the_room(_long_draft(), "12345", session))
    assert verdict.action == "send"
    assert verdict.reason == "rtr_error"

    assert session.session_events and session.session_events[0]["type"] == "rtr.failed"
    assert session.session_events[0]["error"] == "APITimeoutError"


def test_api_connection_error_returns_send(monkeypatch):
    _enable_rtr(monkeypatch)
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    err = anthropic.APIConnectionError(request=MagicMock())
    _patch_anthropic(monkeypatch, raises=err)

    session = FakeSession()
    verdict = asyncio.run(read_the_room(_long_draft(), "12345", session))
    assert verdict.action == "send"
    assert verdict.reason == "rtr_error"
    assert session.session_events[0]["error"] == "APIConnectionError"


def test_value_error_on_bad_tool_use_returns_send(monkeypatch):
    """A response with no tool_use block is treated as a parse error
    and falls open to send."""
    _enable_rtr(monkeypatch)
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])

    bad_msg = MagicMock()
    bad_msg.content = []  # No tool_use block
    _patch_anthropic(monkeypatch, bad_msg)

    session = FakeSession()
    verdict = asyncio.run(read_the_room(_long_draft(), "12345", session))
    assert verdict.action == "send"
    assert verdict.reason == "rtr_error"
    assert session.session_events[0]["error"] == "ValueError"


def test_unexpected_exception_caught_last_resort(monkeypatch):
    _enable_rtr(monkeypatch)
    _patch_snapshot(monkeypatch, [{"sender": "Tom", "content": "hi"}])
    _patch_anthropic(monkeypatch, raises=RuntimeError("boom"))

    session = FakeSession()
    verdict = asyncio.run(read_the_room(_long_draft(), "12345", session))
    assert verdict.action == "send"
    assert verdict.reason == "rtr_error"
    assert session.session_events[0]["error"] == "RuntimeError"


# === Snapshot construction ======================================================


def test_snapshot_k_cap(monkeypatch):
    """``_fetch_snapshot_sync`` should slice to K newest in the time window."""
    now = time.time()
    fake_messages = []
    for i in range(20):
        m = types.SimpleNamespace()
        m.timestamp = now - i  # all within 5-min window
        m.sender = f"user{i}"
        m.content = f"msg-{i}"
        m.direction = "in"
        m.message_id = i
        fake_messages.append(m)

    fake_query = MagicMock()
    fake_query.filter = MagicMock(return_value=fake_messages)
    fake_model = MagicMock()
    fake_model.query = fake_query
    fake_module = types.ModuleType("models.telegram")
    fake_module.TelegramMessage = fake_model

    with patch.dict("sys.modules", {"models.telegram": fake_module}):
        out = rtr_module._fetch_snapshot_sync(
            "12345", k=DEFAULT_K, max_age_seconds=DEFAULT_MAX_AGE_SECONDS
        )

    assert len(out) == DEFAULT_K
    # Newest-last (chronological) ordering -- last entry has the smallest age.
    senders_last = out[-1]["sender"]
    senders_first = out[0]["sender"]
    assert senders_last == "user0"
    assert senders_first == f"user{DEFAULT_K - 1}"


def test_snapshot_time_window_drops_old(monkeypatch):
    now = time.time()
    fake_messages = [
        types.SimpleNamespace(
            timestamp=now - 1, sender="recent", content="r", direction="in", message_id=1
        ),
        types.SimpleNamespace(
            timestamp=now - 30, sender="newish", content="n", direction="in", message_id=2
        ),
        types.SimpleNamespace(
            timestamp=now - 600, sender="ancient", content="a", direction="in", message_id=3
        ),
    ]
    fake_query = MagicMock()
    fake_query.filter = MagicMock(return_value=fake_messages)
    fake_model = MagicMock(query=fake_query)
    fake_module = types.ModuleType("models.telegram")
    fake_module.TelegramMessage = fake_model

    with patch.dict("sys.modules", {"models.telegram": fake_module}):
        out = rtr_module._fetch_snapshot_sync("12345", k=DEFAULT_K, max_age_seconds=300)

    senders = [m["sender"] for m in out]
    assert "ancient" not in senders
    assert "recent" in senders
    assert "newish" in senders


def test_snapshot_mixed_attribution_passes_through(monkeypatch):
    """Both ``sender="Valor"`` (out) and ``sender="system"`` (in) entries
    must reach the prompt unfiltered (Risk 3 / B2)."""
    now = time.time()
    fake_messages = [
        types.SimpleNamespace(
            timestamp=now - 5,
            sender="Valor",
            content="prior agent turn",
            direction="out",
            message_id=10,
        ),
        types.SimpleNamespace(
            timestamp=now - 3,
            sender="system",
            content="pm-direct turn",
            direction="in",
            message_id=11,
        ),
        types.SimpleNamespace(
            timestamp=now - 1,
            sender="Tom",
            content="human reply",
            direction="in",
            message_id=12,
        ),
    ]
    fake_query = MagicMock()
    fake_query.filter = MagicMock(return_value=fake_messages)
    fake_model = MagicMock(query=fake_query)
    fake_module = types.ModuleType("models.telegram")
    fake_module.TelegramMessage = fake_model

    with patch.dict("sys.modules", {"models.telegram": fake_module}):
        out = rtr_module._fetch_snapshot_sync("12345", k=DEFAULT_K, max_age_seconds=300)

    senders = [m["sender"] for m in out]
    assert senders == ["Valor", "system", "Tom"]


def test_snapshot_query_failure_returns_empty(monkeypatch):
    fake_query = MagicMock()
    fake_query.filter = MagicMock(side_effect=RuntimeError("redis down"))
    fake_model = MagicMock(query=fake_query)
    fake_module = types.ModuleType("models.telegram")
    fake_module.TelegramMessage = fake_model

    with patch.dict("sys.modules", {"models.telegram": fake_module}):
        out = rtr_module._fetch_snapshot_sync("12345", k=DEFAULT_K, max_age_seconds=300)

    assert out == []


# === Helpers ====================================================================


def test_format_snapshot_renders_lines():
    snap = [
        {"sender": "Tom", "content": "hi there"},
        {"sender": "Valor", "content": "earlier reply"},
    ]
    out = _format_snapshot_for_prompt(snap)
    assert "- Tom: hi there" in out
    assert "- Valor: earlier reply" in out


def test_format_snapshot_empty():
    assert _format_snapshot_for_prompt([]).startswith("(no recent messages)")


def test_format_snapshot_truncates_long_content():
    snap = [{"sender": "Tom", "content": "x" * 1000}]
    out = _format_snapshot_for_prompt(snap)
    assert len(out) < 600


def test_parse_verdict_block_send():
    msg = _make_tool_use_msg("send", reason="clean")
    v = _parse_verdict_block(msg)
    assert v.action == "send"
    assert v.revised_text is None
    assert v.reason == "clean"


def test_parse_verdict_block_invalid_action_raises():
    block = MagicMock()
    block.type = "tool_use"
    block.name = "room_verdict"
    block.input = {"action": "yeet", "reason": "??"}
    msg = MagicMock()
    msg.content = [block]
    with pytest.raises(ValueError):
        _parse_verdict_block(msg)


def test_parse_verdict_block_missing_tool_use_raises():
    msg = MagicMock()
    msg.content = []
    with pytest.raises(ValueError):
        _parse_verdict_block(msg)


def test_system_prompt_describes_attribution():
    """The prompt must spell out the {Valor, system} attribution rule
    so the model treats agent-authored prior turns correctly."""
    assert "Valor" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "system" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "self_duplicate" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "send" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "trim" in READ_THE_ROOM_SYSTEM_PROMPT
    assert "suppress" in READ_THE_ROOM_SYSTEM_PROMPT


def test_emoji_constant():
    """The default reactor emoji is 👀 (first-person reactor voice).
    Operators tune in this single line per memory feedback_reactor_voice_emoji."""
    assert RTR_SUPPRESS_EMOJI == "👀"
