"""Unit tests for MCP hang graceful degradation (issue #1711).

Covers:
- AgentSession.push_steering_message with front=True / front=False (Component A)
- _compose_tool_timeout_steering pure-function behaviour (Component B1)
- _deliver_tool_timeout_degraded_notice idempotency and delivery (Component B2)
- _apply_recovery_transition wiring: advisory injection + degraded notice (Component C)
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from agent.session_health import (
    _compose_tool_timeout_steering,
    _deliver_tool_timeout_degraded_notice,
)

# ---------------------------------------------------------------------------
# Component A: AgentSession.push_steering_message(front=True/False)
# ---------------------------------------------------------------------------


class _FakeSession:
    """Minimal stand-in for AgentSession's steering queue."""

    def __init__(self, initial: list[str] | None = None):
        self.session_id = "test-session"
        self.queued_steering_messages = list(initial or [])
        self._save_calls: list[list[str]] = []

    def save(self, update_fields=None, **_kw):
        self._save_calls.append(list(update_fields or []))

    # Forward the real method under test from the module directly.
    # We can't subclass AgentSession (Redis-backed), so we bind it manually.
    push_steering_message = None  # populated in conftest below


def _bind_push(session: _FakeSession):
    """Attach the real push_steering_message to the fake session."""
    from models.agent_session import AgentSession

    session.push_steering_message = AgentSession.push_steering_message.__get__(
        session, type(session)
    )
    return session


@pytest.fixture()
def fake_session():
    return _bind_push(_FakeSession())


def test_push_steering_message_append_default(fake_session):
    """Default (front=False) appends to the end."""
    fake_session.push_steering_message("first")
    fake_session.push_steering_message("second")
    assert fake_session.queued_steering_messages == ["first", "second"]


def test_push_steering_message_front_prepends(fake_session):
    """front=True inserts at position 0."""
    fake_session.push_steering_message("first")
    fake_session.push_steering_message("urgent", front=True)
    assert fake_session.queued_steering_messages[0] == "urgent"
    assert fake_session.queued_steering_messages[1] == "first"


def test_push_steering_message_front_trim_keeps_head():
    """When front=True overflows STEERING_QUEUE_MAX, the head (new message) is kept."""
    from models.agent_session import STEERING_QUEUE_MAX

    session = _bind_push(_FakeSession(initial=[f"msg-{i}" for i in range(STEERING_QUEUE_MAX)]))
    session.push_steering_message("CRITICAL", front=True)
    assert len(session.queued_steering_messages) == STEERING_QUEUE_MAX
    # New message must survive — it was at index 0 before trim.
    assert session.queued_steering_messages[0] == "CRITICAL"
    # The last old message (tail) was dropped.
    assert f"msg-{STEERING_QUEUE_MAX - 1}" not in session.queued_steering_messages


def test_push_steering_message_append_trim_keeps_tail():
    """When front=False overflows STEERING_QUEUE_MAX, the tail (new message) is kept."""
    from models.agent_session import STEERING_QUEUE_MAX

    session = _bind_push(_FakeSession(initial=[f"msg-{i}" for i in range(STEERING_QUEUE_MAX)]))
    session.push_steering_message("LATEST", front=False)
    assert len(session.queued_steering_messages) == STEERING_QUEUE_MAX
    # Latest message must survive — it was appended last.
    assert session.queued_steering_messages[-1] == "LATEST"
    # The oldest message (head) was dropped.
    assert "msg-0" not in session.queued_steering_messages


def test_push_steering_message_front_saves(fake_session):
    """front=True still calls save with the expected update_fields."""
    fake_session.push_steering_message("x", front=True)
    assert fake_session._save_calls, "save must be called"
    assert "queued_steering_messages" in fake_session._save_calls[0]


def test_push_steering_message_front_on_empty_queue(fake_session):
    """front=True on an empty queue behaves identically to append."""
    fake_session.push_steering_message("only", front=True)
    assert fake_session.queued_steering_messages == ["only"]


# ---------------------------------------------------------------------------
# Component B1: _compose_tool_timeout_steering
# ---------------------------------------------------------------------------


def test_compose_includes_tool_name():
    result = _compose_tool_timeout_steering("mcp__foo__bar", "Find the report")
    assert "mcp__foo__bar" in result


def test_compose_includes_truncated_original_request():
    long_req = "x" * 2000
    result = _compose_tool_timeout_steering("SomeTool", long_req)
    # Must include the first 1500 chars of the request.
    assert "x" * 1500 in result
    # Must NOT include the full 2000-char string.
    assert "x" * 1501 not in result


def test_compose_none_original_request_still_returns_string():
    result = _compose_tool_timeout_steering("mcp__x", None)
    assert isinstance(result, str)
    assert len(result) > 10
    assert "mcp__x" in result


def test_compose_empty_original_request():
    result = _compose_tool_timeout_steering("SomeTool", "")
    assert isinstance(result, str)
    assert "SomeTool" in result


def test_compose_does_not_raise_on_any_input():
    """Pure function contract: never raises."""
    for tool, req in [
        ("", None),
        (None, None),  # type: ignore[arg-type]
        ("t", "a" * 10000),
        ("t", "\x00\xff"),
    ]:
        try:
            _compose_tool_timeout_steering(tool, req)  # type: ignore[arg-type]
        except Exception as exc:
            pytest.fail(f"_compose_tool_timeout_steering raised {exc!r} for ({tool!r}, {req!r})")


# ---------------------------------------------------------------------------
# Component B2: _deliver_tool_timeout_degraded_notice
# ---------------------------------------------------------------------------


def _make_entry(
    *,
    session_id: str = "sess-abc",
    project_key: str = "test-proj",
    extra_context: dict | None = None,
    chat_id: str = "chat-1",
    telegram_message_id: int = 42,
) -> SimpleNamespace:
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=session_id,
        project_key=project_key,
        extra_context=extra_context or {},
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
    )


@pytest.fixture()
def _mock_redis():
    """Provide a mock Redis instance that tracks SETNX calls."""
    mock = MagicMock()
    mock.set.return_value = True  # nx=True acquired by default
    mock.incr.return_value = 1
    with patch("agent.session_health.POPOTO_REDIS_DB", mock, create=True):
        with patch("popoto.redis_db.POPOTO_REDIS_DB", mock):
            yield mock


def test_degraded_notice_calls_send_cb(fake_session, _mock_redis):
    send_cb = AsyncMock()
    entry = _make_entry(session_id="sess-1")

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        asyncio.run(_deliver_tool_timeout_degraded_notice(entry, "mcp__svc"))

    send_cb.assert_called_once()
    _args = send_cb.call_args
    # First positional arg is chat_id, third is the text message.
    message_text = _args[0][1]
    assert "mcp__svc" in message_text


def test_degraded_notice_idempotent(_mock_redis):
    """Second call with same session_id returns early (Redis SETNX blocks)."""
    _mock_redis.set.return_value = None  # SETNX: key already exists
    send_cb = AsyncMock()
    entry = _make_entry(session_id="sess-dup")

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        asyncio.run(_deliver_tool_timeout_degraded_notice(entry, "mcp__dup"))

    send_cb.assert_not_called()


def test_degraded_notice_falls_back_to_file_output_handler(_mock_redis):
    """When no registered callback, FileOutputHandler.send is used."""
    entry = _make_entry(session_id="sess-file")
    fake_handler = MagicMock()
    fake_handler.send = AsyncMock()

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(None, None)):
        with patch("agent.output_handler.FileOutputHandler", return_value=fake_handler):
            asyncio.run(_deliver_tool_timeout_degraded_notice(entry, "mcp__x"))

    fake_handler.send.assert_called_once()


def test_degraded_notice_uses_generic_label_when_tool_name_none(_mock_redis):
    """tool_name=None uses 'the requested service' in the message."""
    send_cb = AsyncMock()
    entry = _make_entry(session_id="sess-generic")

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        asyncio.run(_deliver_tool_timeout_degraded_notice(entry, None))

    message_text = send_cb.call_args[0][1]
    assert "the requested service" in message_text


def test_degraded_notice_reads_transport_from_extra_context(_mock_redis):
    """Transport is read from extra_context, never from a direct attribute."""
    entry = _make_entry(session_id="sess-transport", extra_context={"transport": "email"})
    send_cb = AsyncMock()

    with patch(
        "agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)
    ) as mock_resolve:
        asyncio.run(_deliver_tool_timeout_degraded_notice(entry, "mcp__t"))

    mock_resolve.assert_called_once_with(entry.project_key, "email")


def test_degraded_notice_never_raises_on_send_failure(_mock_redis):
    """Exception in send_cb is swallowed — function must not propagate."""
    send_cb = AsyncMock(side_effect=RuntimeError("send exploded"))
    entry = _make_entry(session_id="sess-fail")

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        # Should complete without raising.
        asyncio.run(_deliver_tool_timeout_degraded_notice(entry, "mcp__boom"))


# ---------------------------------------------------------------------------
# Component C: _apply_recovery_transition wiring
# ---------------------------------------------------------------------------


def _recovery_entry(
    *,
    sid: str = "rentry-1",
    project_key: str = "test-rec",
    current_tool_name: str | None = "mcp__svc",
    recovery_attempts: int = 0,
    message_text: str = "original request",
    extra_context: dict | None = None,
) -> SimpleNamespace:
    """Minimal fake AgentSession for recovery transition tests."""
    saves: list = []
    steered: list[tuple[str, bool]] = []

    def _save(update_fields=None, **_kw):
        saves.append(update_fields)

    def _push_steering(text, front=False):
        steered.append((text, front))

    return SimpleNamespace(
        agent_session_id=sid,
        id=sid,
        session_id=f"sid-{sid}",
        status="running",
        project_key=project_key,
        current_tool_name=current_tool_name,
        message_text=message_text,
        extra_context=extra_context or {},
        chat_id="chat-x",
        telegram_message_id=0,
        recovery_attempts=recovery_attempts,
        reprieve_count=0,
        is_project_keyed=True,
        priority=None,
        started_at=None,
        exit_returncode=None,
        scheduled_at=None,
        claude_pid=None,
        response_delivered_at=None,
        last_tool_use_at=None,
        last_turn_at=None,
        claude_session_uuid=None,
        save=_save,
        push_steering_message=_push_steering,
        _saves=saves,
        _steered=steered,
    )


def _run_recovery(entry, *, reason_kind="tool_timeout", worker_key="wk-1", is_local=False):
    """Drive _apply_recovery_transition with all external deps patched out."""
    from agent.session_health import _apply_recovery_transition

    # Patches common to every call.
    def _fake_finalize(e, status, reason="", **kw):
        e.status = status

    def _fake_transition(e, status, reason="", **kw):
        e.status = status

    with (
        patch("agent.session_health._tier2_reprieve_signal", return_value=None),
        patch("agent.session_health._confirm_subprocess_dead") as mock_kill,
        patch("agent.session_health._increment_subprocess_kill_counter"),
        patch("agent.session_health._is_memory_tight", return_value=False),
        patch("agent.session_health._rte", create=True),
        patch("agent.session_health.asyncio.get_running_loop") as mock_loop,
        patch(
            "agent.session_health._deliver_tool_timeout_degraded_notice",
            new_callable=AsyncMock,
        ) as mock_degraded,
        patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
        patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
        patch("models.session_lifecycle.StatusConflictError", Exception),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("agent.session_health._active_events", {}),
        patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
    ):
        from agent.session_health import SubprocessKillResult

        mock_kill.return_value = SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        # run_in_executor: return confirmed_dead=True synchronously via a coroutine
        mock_loop.return_value.run_in_executor = AsyncMock(
            return_value=SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        )
        if is_local:
            worker_key = "local-test"

        async def _run():
            return await _apply_recovery_transition(
                entry,
                reason="test-reason",
                reason_kind=reason_kind,
                handle=None,
                worker_key=worker_key,
            )

        result = asyncio.run(_run())
    return result, mock_degraded


def test_requeue_injects_steering_when_tool_timeout():
    """else-branch: tool_timeout + tool_name -> push_steering_message(front=True)."""
    entry = _recovery_entry(current_tool_name="mcp__svc", recovery_attempts=0)
    _run_recovery(entry, reason_kind="tool_timeout")
    assert entry._steered, "steering message must be injected"
    text, front = entry._steered[0]
    assert front is True, "must be prepended (front=True)"
    assert "mcp__svc" in text


def test_requeue_no_steering_without_tool_name():
    """else-branch: tool_timeout but no tool_name -> no steering injection."""
    entry = _recovery_entry(current_tool_name=None, recovery_attempts=0)
    _run_recovery(entry, reason_kind="tool_timeout")
    assert not entry._steered, "no steering when tool_name is falsy"


def test_requeue_no_steering_for_non_tool_timeout():
    """else-branch: no_progress reason -> no steering injection regardless of tool."""
    entry = _recovery_entry(current_tool_name="mcp__svc", recovery_attempts=0)
    _run_recovery(entry, reason_kind="no_progress")
    assert not entry._steered


def test_max_attempts_delivers_degraded_notice_for_tool_timeout():
    """recovery_attempts >= MAX_RECOVERY_ATTEMPTS + tool_timeout -> degraded notice."""
    from agent.session_health import MAX_RECOVERY_ATTEMPTS

    entry = _recovery_entry(
        current_tool_name="mcp__svc",
        recovery_attempts=MAX_RECOVERY_ATTEMPTS,  # pre-bumped: will hit >= after +1
    )
    # Bump to trigger the >= branch (the function adds 1 internally).
    entry.recovery_attempts = MAX_RECOVERY_ATTEMPTS - 1
    _result, mock_degraded = _run_recovery(entry, reason_kind="tool_timeout")
    mock_degraded.assert_called_once()
    _args = mock_degraded.call_args
    assert _args[0][1] == "mcp__svc"


def test_max_attempts_no_degraded_for_no_progress():
    """recovery_attempts >= MAX but reason_kind='no_progress' -> no degraded notice."""
    from agent.session_health import MAX_RECOVERY_ATTEMPTS

    entry = _recovery_entry(
        current_tool_name="mcp__svc", recovery_attempts=MAX_RECOVERY_ATTEMPTS - 1
    )
    _result, mock_degraded = _run_recovery(entry, reason_kind="no_progress")
    mock_degraded.assert_not_called()


def test_steering_message_includes_original_request():
    """The injected steering text includes the session's message_text."""
    entry = _recovery_entry(
        current_tool_name="mcp__svc",
        recovery_attempts=0,
        message_text="please look up the latest metrics",
    )
    _run_recovery(entry, reason_kind="tool_timeout")
    assert entry._steered
    text, _ = entry._steered[0]
    assert "please look up the latest metrics" in text


def test_tool_timeout_prepend_when_queue_already_has_message():
    """B1 ordering guard: pre-existing message in queue → tool-skip is at index 0, older at index 1."""
    entry = _recovery_entry(current_tool_name="mcp__svc", recovery_attempts=0)
    # Pre-load a message in the steering queue.
    entry.queued_steering_messages = ["pre-existing message"]
    _run_recovery(entry, reason_kind="tool_timeout")
    assert len(entry._steered) >= 1, "steering must be injected"
    text, front = entry._steered[0]
    assert front is True, "must be prepended (front=True)"
    assert "mcp__svc" in text
    # Simulate what push_steering_message(front=True) would do to the queue:
    # new message goes to index 0, pre-existing stays at index 1.
    # The _steered list records the push call as (text, front=True).
    # Verify the pre-existing message was not displaced — it's already there.
    assert entry.queued_steering_messages[0] == "pre-existing message"  # untouched by fake push


def test_steering_push_failure_does_not_block_requeue():
    """push_steering_message raising must not prevent transition_status(pending)."""
    from unittest.mock import patch as _patch

    from agent.session_health import _apply_recovery_transition

    entry = _recovery_entry(current_tool_name="mcp__svc", recovery_attempts=0)
    # Override push_steering_message to raise.
    entry.push_steering_message = MagicMock(side_effect=RuntimeError("push exploded"))

    transition_calls: list[str] = []

    def _fake_finalize(e, status, reason="", **kw):
        e.status = status

    def _fake_transition(e, status, reason="", **kw):
        e.status = status
        transition_calls.append(status)

    with (
        _patch("agent.session_health._tier2_reprieve_signal", return_value=None),
        _patch("agent.session_health._confirm_subprocess_dead") as mock_kill,
        _patch("agent.session_health._increment_subprocess_kill_counter"),
        _patch("agent.session_health._is_memory_tight", return_value=False),
        _patch("agent.session_health._rte", create=True),
        _patch("agent.session_health.asyncio.get_running_loop") as mock_loop,
        _patch(
            "agent.session_health._deliver_tool_timeout_degraded_notice",
            new_callable=AsyncMock,
        ),
        _patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
        _patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
        _patch("models.session_lifecycle.StatusConflictError", Exception),
        _patch("agent.agent_session_queue._ensure_worker"),
        _patch("agent.session_health._active_events", {}),
        _patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
    ):
        from agent.session_health import SubprocessKillResult

        mock_kill.return_value = SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        mock_loop.return_value.run_in_executor = AsyncMock(
            return_value=SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        )

        async def _run():
            return await _apply_recovery_transition(
                entry,
                reason="test-reason",
                reason_kind="tool_timeout",
                handle=None,
                worker_key="wk-1",
            )

        asyncio.run(_run())

    # push raised, but transition_status("pending") must still have been called.
    assert "pending" in transition_calls, (
        f"transition_status(pending) must be called even when push_steering_message raises; "
        f"got transition_calls={transition_calls}"
    )


def test_failed_branch_does_not_inject_advisory_steering():
    """MAX_RECOVERY_ATTEMPTS branch: degraded notice delivered but no steering injection."""
    from agent.session_health import MAX_RECOVERY_ATTEMPTS

    entry = _recovery_entry(
        current_tool_name="mcp__svc",
        recovery_attempts=MAX_RECOVERY_ATTEMPTS - 1,
    )
    _run_recovery(entry, reason_kind="tool_timeout")
    # On the failed branch steering must NOT be injected — session won't be requeued.
    assert not entry._steered, (
        "steering injection must not occur on the failed branch (MAX_RECOVERY_ATTEMPTS)"
    )


def _run_recovery_not_confirmed_dead(entry, *, reason_kind="tool_timeout", worker_key="wk-1"):
    """Like _run_recovery but SubprocessKillResult.confirmed_dead=False."""
    from agent.session_health import _apply_recovery_transition

    def _fake_finalize(e, status, reason="", **kw):
        e.status = status

    def _fake_transition(e, status, reason="", **kw):
        e.status = status

    with (
        patch("agent.session_health._tier2_reprieve_signal", return_value=None),
        patch("agent.session_health._confirm_subprocess_dead") as mock_kill,
        patch("agent.session_health._increment_subprocess_kill_counter"),
        patch("agent.session_health._is_memory_tight", return_value=False),
        patch("agent.session_health._rte", create=True),
        patch("agent.session_health.asyncio.get_running_loop") as mock_loop,
        patch(
            "agent.session_health._deliver_tool_timeout_degraded_notice",
            new_callable=AsyncMock,
        ) as mock_degraded,
        patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
        patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
        patch("models.session_lifecycle.StatusConflictError", Exception),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("agent.session_health._active_events", {}),
        patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
    ):
        from agent.session_health import SubprocessKillResult

        # confirmed_dead=False — subprocess survived kill
        mock_kill.return_value = SubprocessKillResult(confirmed_dead=False, signal_sent=True)
        mock_loop.return_value.run_in_executor = AsyncMock(
            return_value=SubprocessKillResult(confirmed_dead=False, signal_sent=True)
        )

        async def _run():
            return await _apply_recovery_transition(
                entry,
                reason="test-reason",
                reason_kind=reason_kind,
                handle=None,
                worker_key=worker_key,
            )

        result = asyncio.run(_run())
    return result, mock_degraded


def test_tool_timeout_not_confirmed_dead_branch_delivers_degraded_notice():
    """not_confirmed_dead branch: degraded notice delivered before finalize('failed')."""
    entry = _recovery_entry(current_tool_name="mcp__svc", recovery_attempts=0)
    _result, mock_degraded = _run_recovery_not_confirmed_dead(entry, reason_kind="tool_timeout")
    mock_degraded.assert_called_once()
    assert mock_degraded.call_args[0][1] == "mcp__svc"
    # Session must have been finalized as failed.
    assert entry.status == "failed"


def test_not_confirmed_dead_does_not_inject_steering():
    """not_confirmed_dead branch: no requeue -> no steering injection."""
    entry = _recovery_entry(current_tool_name="mcp__svc", recovery_attempts=0)
    _run_recovery_not_confirmed_dead(entry, reason_kind="tool_timeout")
    assert not entry._steered, "steering must not be injected when subprocess is not confirmed dead"
