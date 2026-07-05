"""Unit tests for MCP hang graceful degradation (issue #1711).

Covers:
- _compose_tool_timeout_steering pure-function behaviour (Component B1)
- _deliver_tool_timeout_degraded_notice idempotency and delivery (Component B2)
- _apply_recovery_transition wiring: advisory injection + degraded notice (Component C)

The urgent advisory push (front=True) now goes through the Redis-list
primitive in agent.steering.push_steering_message rather than the removed
AgentSession.push_steering_message ListField method (issue #1817 A1). See
tests/integration/test_steering.py for direct coverage of
push_steering_message(front=True) LPUSH ordering.
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


def test_degraded_notice_calls_send_cb(_mock_redis):
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
    """Minimal fake AgentSession for recovery transition tests.

    The urgent advisory push now goes through agent.steering.push_steering_message
    (module-level, Redis-backed) rather than an instance method — tests patch
    that function directly instead of inspecting an attribute on this fake.
    """
    saves: list = []

    def _save(update_fields=None, **_kw):
        saves.append(update_fields)

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
        _saves=saves,
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
    with patch("agent.steering.push_steering_message") as mock_push:
        _run_recovery(entry, reason_kind="tool_timeout")

    mock_push.assert_called_once()
    args, kwargs = mock_push.call_args
    assert args[0] == entry.session_id
    assert "mcp__svc" in args[1]
    assert kwargs.get("front") is True, "must be prepended (front=True)"


def test_requeue_no_steering_without_tool_name():
    """else-branch: tool_timeout but no tool_name -> no steering injection."""
    entry = _recovery_entry(current_tool_name=None, recovery_attempts=0)
    with patch("agent.steering.push_steering_message") as mock_push:
        _run_recovery(entry, reason_kind="tool_timeout")
    mock_push.assert_not_called()


def test_requeue_no_steering_for_non_tool_timeout():
    """else-branch: no_progress reason -> no steering injection regardless of tool."""
    entry = _recovery_entry(current_tool_name="mcp__svc", recovery_attempts=0)
    with patch("agent.steering.push_steering_message") as mock_push:
        _run_recovery(entry, reason_kind="no_progress")
    mock_push.assert_not_called()


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
    with patch("agent.steering.push_steering_message") as mock_push:
        _run_recovery(entry, reason_kind="tool_timeout")

    mock_push.assert_called_once()
    text = mock_push.call_args[0][1]
    assert "please look up the latest metrics" in text


def test_steering_push_failure_does_not_block_requeue():
    """push_steering_message raising must not prevent transition_status(pending)."""
    from unittest.mock import patch as _patch

    from agent.session_health import _apply_recovery_transition

    entry = _recovery_entry(current_tool_name="mcp__svc", recovery_attempts=0)

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
        _patch(
            "agent.steering.push_steering_message",
            side_effect=RuntimeError("push exploded"),
        ),
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
    with patch("agent.steering.push_steering_message") as mock_push:
        _run_recovery(entry, reason_kind="tool_timeout")
    # On the failed branch steering must NOT be injected — session won't be requeued.
    mock_push.assert_not_called()


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
    with patch("agent.steering.push_steering_message") as mock_push:
        _run_recovery_not_confirmed_dead(entry, reason_kind="tool_timeout")
    mock_push.assert_not_called()


# ---------------------------------------------------------------------------
# Component D: _deliver_deferred_self_draft_fallback (issue #1730)
# ---------------------------------------------------------------------------


from agent.session_health import _deliver_deferred_self_draft_fallback  # noqa: E402


def _make_deferred_entry(
    *,
    session_id: str = "sess-deferred",
    project_key: str = "test-proj",
    deferred_pending: bool = True,
    deferred_text: str = "Here is the answer you were looking for.",
    transport: str = "email",
    chat_id: str = "chat-1",
    telegram_message_id: int = 42,
) -> SimpleNamespace:
    # Default transport is "email": since 7fb7e609 (#1794) the async helper is
    # EMAIL-only — it early-returns for telegram (and None), which is delivered
    # by the synchronous flush_deferred_self_draft_sync chokepoint in
    # finalize_session instead (covered by
    # tests/unit/test_deferred_self_draft_completed.py).
    extra = {"transport": transport}
    if deferred_pending:
        extra["deferred_self_draft_pending"] = True
        extra["deferred_self_draft_text"] = deferred_text
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=session_id,
        project_key=project_key,
        extra_context=extra,
        chat_id=chat_id,
        telegram_message_id=telegram_message_id,
    )


def test_deferred_fallback_delivers_when_flag_set(_mock_redis):
    """When deferred_self_draft_pending=True, send_cb is called with the deferred text."""
    send_cb = AsyncMock()
    entry = _make_deferred_entry(deferred_text="Here is the answer.")

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        asyncio.run(_deliver_deferred_self_draft_fallback(entry))

    send_cb.assert_called_once()
    delivered_text = send_cb.call_args[0][1]
    assert "Here is the answer" in delivered_text


def test_deferred_fallback_no_op_when_flag_absent(_mock_redis):
    """When deferred_self_draft_pending is not set, helper is a no-op."""
    send_cb = AsyncMock()
    entry = _make_deferred_entry(deferred_pending=False)

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        asyncio.run(_deliver_deferred_self_draft_fallback(entry))

    send_cb.assert_not_called()


def test_deferred_fallback_no_op_when_extra_context_none(_mock_redis):
    """extra_context=None is handled defensively — no crash, no delivery."""
    send_cb = AsyncMock()
    entry = SimpleNamespace(
        session_id="sess-none-ctx",
        agent_session_id="sess-none-ctx",
        project_key="test-proj",
        extra_context=None,
        chat_id="c",
        telegram_message_id=0,
    )

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        asyncio.run(_deliver_deferred_self_draft_fallback(entry))

    send_cb.assert_not_called()


def test_deferred_fallback_canned_notice_when_text_missing(_mock_redis):
    """deferred_self_draft_pending=True but text absent → delivers explicit notice."""
    send_cb = AsyncMock()
    entry = SimpleNamespace(
        session_id="sess-no-text",
        agent_session_id="sess-no-text",
        project_key="test-proj",
        # No deferred_self_draft_text; transport must be email — the async
        # helper is email-only since 7fb7e609 (#1794).
        extra_context={"deferred_self_draft_pending": True, "transport": "email"},
        chat_id="c",
        telegram_message_id=0,
    )

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        asyncio.run(_deliver_deferred_self_draft_fallback(entry))

    send_cb.assert_called_once()
    delivered_text = send_cb.call_args[0][1]
    assert delivered_text.strip(), "delivered text must not be empty"


def test_deferred_fallback_idempotent(_mock_redis):
    """Second call with same session_id is blocked by Redis SETNX lock."""
    _mock_redis.set.return_value = None  # SETNX: key already exists
    send_cb = AsyncMock()
    entry = _make_deferred_entry(session_id="sess-idemp")

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        asyncio.run(_deliver_deferred_self_draft_fallback(entry))

    send_cb.assert_not_called()


def test_deferred_fallback_never_raises_on_send_failure(_mock_redis):
    """Exception in send_cb is swallowed — helper must not propagate."""
    send_cb = AsyncMock(side_effect=RuntimeError("send exploded"))
    entry = _make_deferred_entry(session_id="sess-err")

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        # Must complete without raising.
        asyncio.run(_deliver_deferred_self_draft_fallback(entry))


def test_deferred_fallback_falls_back_to_file_output_handler(_mock_redis):
    """When no registered callback, FileOutputHandler.send is used."""
    entry = _make_deferred_entry(session_id="sess-file-fb")
    fake_handler = MagicMock()
    fake_handler.send = AsyncMock()

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(None, None)):
        with patch("agent.output_handler.FileOutputHandler", return_value=fake_handler):
            asyncio.run(_deliver_deferred_self_draft_fallback(entry))

    fake_handler.send.assert_called_once()


def test_deferred_fallback_setnx_key_distinct_from_degraded_notice(_mock_redis):
    """The SETNX keys for the two helpers are distinct — neither blocks the other."""
    setnx_keys: list[str] = []

    def _track_set(key, val, *, nx, ex):
        setnx_keys.append(key)
        return True  # always acquired

    _mock_redis.set.side_effect = _track_set
    send_cb = AsyncMock()
    entry = _make_deferred_entry(session_id="sess-keys")

    with patch("agent.agent_session_queue._resolve_callbacks", return_value=(send_cb, None)):
        asyncio.run(_deliver_deferred_self_draft_fallback(entry))
        asyncio.run(_deliver_tool_timeout_degraded_notice(entry, "mcp__svc"))

    # Both helpers must use distinct Redis keys.
    assert len(setnx_keys) >= 2
    assert len(set(setnx_keys)) == len(setnx_keys), (
        f"SETNX keys must be distinct — got {setnx_keys}"
    )


def _run_recovery_with_deferred(entry, *, reason_kind="tool_timeout", worker_key="wk-1"):
    """Like _run_recovery but patches _deliver_deferred_self_draft_fallback instead."""
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
        patch(
            "agent.session_health._deliver_deferred_self_draft_fallback",
            new_callable=AsyncMock,
        ) as mock_fallback,
        patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
        patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
        patch("models.session_lifecycle.StatusConflictError", Exception),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("agent.session_health._active_events", {}),
        patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
    ):
        from agent.session_health import SubprocessKillResult

        mock_kill.return_value = SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        mock_loop.return_value.run_in_executor = AsyncMock(
            return_value=SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        )
        if worker_key == "local-test" or worker_key.startswith("local"):
            pass  # is_local will be True

        async def _run():
            return await _apply_recovery_transition(
                entry,
                reason="test-reason",
                reason_kind=reason_kind,
                handle=None,
                worker_key=worker_key,
            )

        result = asyncio.run(_run())
    return result, mock_degraded, mock_fallback


def test_deferred_fallback_wired_into_failed_branch():
    """failed branch (MAX_RECOVERY_ATTEMPTS): _deliver_deferred_self_draft_fallback called."""
    from agent.session_health import MAX_RECOVERY_ATTEMPTS

    entry = _recovery_entry(
        current_tool_name="mcp__svc",
        recovery_attempts=MAX_RECOVERY_ATTEMPTS - 1,
        extra_context={"deferred_self_draft_pending": True},
    )
    _result, _mock_degraded, mock_fallback = _run_recovery_with_deferred(
        entry, reason_kind="tool_timeout"
    )
    mock_fallback.assert_called_once()


def test_deferred_fallback_suppresses_degraded_notice_when_flag_set():
    """When deferred_self_draft_pending=True, the generic degraded notice is suppressed."""
    from agent.session_health import MAX_RECOVERY_ATTEMPTS

    entry = _recovery_entry(
        current_tool_name="mcp__svc",
        recovery_attempts=MAX_RECOVERY_ATTEMPTS - 1,
        extra_context={"deferred_self_draft_pending": True},
    )
    _result, mock_degraded, _mock_fallback = _run_recovery_with_deferred(
        entry, reason_kind="tool_timeout"
    )
    mock_degraded.assert_not_called()


def test_degraded_notice_fires_when_no_deferred_flag():
    """When deferred_self_draft_pending is absent, the generic degraded notice still fires."""
    from agent.session_health import MAX_RECOVERY_ATTEMPTS

    entry = _recovery_entry(
        current_tool_name="mcp__svc",
        recovery_attempts=MAX_RECOVERY_ATTEMPTS - 1,
        extra_context={},  # no deferred flag
    )
    _result, mock_degraded, _mock_fallback = _run_recovery_with_deferred(
        entry, reason_kind="tool_timeout"
    )
    mock_degraded.assert_called_once()


def test_deferred_fallback_wired_into_abandoned_branch():
    """abandoned (is_local) branch: _deliver_deferred_self_draft_fallback called."""
    entry = _recovery_entry(
        current_tool_name="mcp__svc",
        recovery_attempts=0,
        extra_context={"deferred_self_draft_pending": True},
    )
    _result, _mock_degraded, mock_fallback = _run_recovery_with_deferred(
        entry, reason_kind="no_progress", worker_key="local-test"
    )
    mock_fallback.assert_called_once()
    assert entry.status == "abandoned"
