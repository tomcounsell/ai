"""Tests for the steering queue module.

Tests use Redis db=1 via the autouse redis_test_db fixture in conftest.py.
"""

import ast
import json
import pathlib
import time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import AsyncMock, patch

import pytest

from agent.steering import (
    ABORT_KEYWORDS,
    _get_redis,
    _queue_key,
    _room_queue_key,
    clear_steering_queue,
    has_steering_messages,
    peek_steering_messages,
    pop_all_steering_messages,
    pop_steering_message,
    push_steering_message,
)

# Per-process unique suffix (#2707): steering keys are freeform Redis lists, so two
# pytest processes that ever share a db must never compute byte-identical key names
# from these tests. _uid() is deterministic within a process, unique across them.
_UNIQ = uuid.uuid4().hex[:8]


def _uid(purpose: str) -> str:
    """Session/room id unique per test run, still greppable by purpose."""
    return f"{purpose}_{_UNIQ}"


_BRIDGE_SRC = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"


def _bridge_ast() -> ast.Module:
    """Parse bridge/telegram_bridge.py into an AST.

    The reply-chain pre-hydration contracts below are asserted structurally
    (AST shape) rather than by grepping source literals. Literal greps have
    broken three times in this file when a refactor hoisted a value into a
    constant or renamed a local without changing behavior (#2623). The AST
    form pins the same contracts against the actual call sites, so a pure
    rename passes and a real semantic change fails.
    """
    return ast.parse(_BRIDGE_SRC.read_text())


def _reply_chain_fetch_timeouts() -> list[float]:
    """Resolved `timeout=` value of every `asyncio.wait_for(fetch_reply_chain(...))`.

    Names are resolved against the live bridge module, so hoisting the value
    into a module constant keeps the assertion green while a changed value
    fails it.
    """
    import bridge.telegram_bridge as tb

    timeouts: list[float] = []
    for node in ast.walk(_bridge_ast()):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "wait_for"):
            continue
        if not node.args:
            continue
        inner = node.args[0]
        if not (
            isinstance(inner, ast.Call)
            and isinstance(inner.func, ast.Name)
            and inner.func.id == "fetch_reply_chain"
        ):
            continue
        expr = next((kw.value for kw in node.keywords if kw.arg == "timeout"), None)
        assert expr is not None, "fetch_reply_chain guard is missing its timeout= kwarg"
        if isinstance(expr, ast.Constant):
            timeouts.append(float(expr.value))
        elif isinstance(expr, ast.Name):
            resolved = getattr(tb, expr.id, None)
            assert resolved is not None, (
                f"fetch_reply_chain timeout references unknown name {expr.id!r}"
            )
            timeouts.append(float(resolved))
        else:
            raise AssertionError(
                f"fetch_reply_chain timeout is not a literal or module constant: "
                f"{ast.unparse(expr)}"
            )
    return timeouts


def _hydration_flag_stamps() -> tuple[int, int]:
    """(total stamps of reply_chain_hydrated, stamps guarded by `if reply_chain_context:`).

    A "stamp" is any assignment whose value mentions the ``reply_chain_hydrated``
    key. The failure contract (Implementation Note C2) is that every stamp lives
    inside an ``if reply_chain_context:`` body, so a timed-out or raised fetch
    leaves the flag unset and the worker's deferred enrichment stays free to retry.
    """
    tree = _bridge_ast()

    def stamps_in(nodes) -> list[ast.AST]:
        found = []
        for node in nodes:
            for sub in ast.walk(node):
                if isinstance(sub, ast.Assign | ast.AnnAssign) and sub.value is not None:
                    if any(
                        isinstance(k, ast.Constant) and k.value == "reply_chain_hydrated"
                        for d in ast.walk(sub.value)
                        if isinstance(d, ast.Dict)
                        for k in d.keys
                        if k is not None
                    ):
                        found.append(sub)
        return found

    total = stamps_in([tree])
    guarded: list[ast.AST] = []
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.If)
            and isinstance(node.test, ast.Name)
            and node.test.id == "reply_chain_context"
        ):
            guarded.extend(stamps_in(node.body))
    return len(total), len(guarded)


class TestSteeringQueue:
    """Tests for push/pop/clear operations on the steering Redis queue."""

    def test_push_and_pop_single_message(self):
        session_id = _uid("test_session_001")
        push_steering_message(session_id, "focus on OAuth", "Tom")

        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "focus on OAuth"
        assert msg["sender"] == "Tom"
        assert msg["is_abort"] is False
        assert "timestamp" in msg

        # Queue should be empty now
        assert pop_steering_message(session_id) is None

    def test_push_and_pop_fifo_order(self):
        session_id = _uid("test_session_fifo")
        push_steering_message(session_id, "first", "Tom")
        push_steering_message(session_id, "second", "Tom")
        push_steering_message(session_id, "third", "Tom")

        msg1 = pop_steering_message(session_id)
        msg2 = pop_steering_message(session_id)
        msg3 = pop_steering_message(session_id)

        assert msg1["text"] == "first"
        assert msg2["text"] == "second"
        assert msg3["text"] == "third"
        assert pop_steering_message(session_id) is None

    def test_pop_all_drains_queue(self):
        session_id = _uid("test_session_popall")
        push_steering_message(session_id, "msg1", "Tom")
        push_steering_message(session_id, "msg2", "Tom")
        push_steering_message(session_id, "msg3", "Tom")

        messages = pop_all_steering_messages(session_id)
        assert len(messages) == 3
        assert messages[0]["text"] == "msg1"
        assert messages[1]["text"] == "msg2"
        assert messages[2]["text"] == "msg3"

        # Queue should be empty
        assert pop_all_steering_messages(session_id) == []

    def test_pop_all_empty_queue(self):
        assert pop_all_steering_messages(_uid("nonexistent_session")) == []

    def test_concurrent_drainers_split_disjointly(self):
        """Two concurrent drainers of one session_id partition the queue with no loss/dup.

        A1 acceptance criterion: the turn-boundary drain is sequential-LPOP (not a
        single atomic multi-pop), which is safe only because each LPOP is atomic. Two
        drainers racing on the same session_id must each pop every message at most once
        and together pop every message exactly once — no message lost, none duplicated.
        This locks the single-consumer safety model against a future refactor that might
        silently invalidate it (e.g. an LRANGE-then-trim drain that could double-count).
        """
        import threading

        session_id = _uid("test_concurrent_drainers")
        n = 200
        for i in range(n):
            push_steering_message(session_id, f"msg-{i}", "Tom")

        collected: list[dict] = []
        lock = threading.Lock()

        def drain():
            got = pop_all_steering_messages(session_id)
            with lock:
                collected.extend(got)

        threads = [threading.Thread(target=drain) for _ in range(2)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        texts = [m["text"] for m in collected]
        # No loss, no duplication: the union is exactly the pushed set.
        assert len(texts) == n, f"expected {n} messages across both drainers, got {len(texts)}"
        assert sorted(texts) == sorted(f"msg-{i}" for i in range(n))
        # Queue fully drained.
        assert pop_all_steering_messages(session_id) == []

    def test_clear_steering_queue(self):
        session_id = _uid("test_session_clear")
        push_steering_message(session_id, "msg1", "Tom")
        push_steering_message(session_id, "msg2", "Tom")

        count = clear_steering_queue(session_id)
        assert count == 2
        assert pop_steering_message(session_id) is None

    def test_clear_empty_queue(self):
        count = clear_steering_queue(_uid("nonexistent_session"))
        assert count == 0

    def test_has_steering_messages(self):
        session_id = _uid("test_session_has")
        assert has_steering_messages(session_id) is False

        push_steering_message(session_id, "hello", "Tom")
        assert has_steering_messages(session_id) is True

        pop_steering_message(session_id)
        assert has_steering_messages(session_id) is False

    def test_explicit_abort_flag(self):
        session_id = _uid("test_session_abort_explicit")
        push_steering_message(session_id, "stop everything", "Tom", is_abort=True)

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is True
        assert msg["text"] == "stop everything"

    @pytest.mark.parametrize("keyword", sorted(ABORT_KEYWORDS))
    def test_auto_detect_abort_keywords(self, keyword):
        session_id = _uid(f"test_session_abort_{keyword}")
        push_steering_message(session_id, keyword, "Tom")

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is True

    def test_non_abort_message(self):
        session_id = _uid("test_session_noabort")
        push_steering_message(session_id, "focus on the login page", "Tom")

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is False

    def test_abort_keyword_case_insensitive(self):
        session_id = _uid("test_session_abort_case")
        push_steering_message(session_id, "STOP", "Tom")

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is True

    def test_abort_keyword_with_whitespace(self):
        session_id = _uid("test_session_abort_ws")
        push_steering_message(session_id, "  cancel  ", "Tom")

        msg = pop_steering_message(session_id)
        assert msg["is_abort"] is True

    def test_isolation_between_sessions(self):
        push_steering_message(_uid("session_a"), "msg for a", "Tom")
        push_steering_message(_uid("session_b"), "msg for b", "Tom")

        msg_a = pop_steering_message(_uid("session_a"))
        msg_b = pop_steering_message(_uid("session_b"))

        assert msg_a["text"] == "msg for a"
        assert msg_b["text"] == "msg for b"

    def test_timestamp_is_recent(self):
        session_id = _uid("test_session_ts")
        before = time.time()
        push_steering_message(session_id, "test", "Tom")
        after = time.time()

        msg = pop_steering_message(session_id)
        assert before <= msg["timestamp"] <= after


class TestBridgeSteeringCheck:
    """Tests for the bridge steering check status matching logic.

    These tests verify that the steering check in telegram_bridge.py
    correctly matches sessions in 'running' and 'active' statuses,
    and falls through gracefully when no matching session exists.
    """

    def _create_session(self, session_id, status):
        """Create an AgentSession with the given status."""
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=session_id,
            project_key="test",
            status=status,
            message_text="test message",
            created_at=datetime.now(tz=UTC),
        )
        session.save()
        return session

    def test_steering_matches_running_status(self):
        """Steering check should find sessions in 'running' status."""
        from models.agent_session import AgentSession

        session_id = _uid("test_bridge_running")
        self._create_session(session_id, "running")

        # Replicate the bridge steering check logic
        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is not None
        assert matching_session.status == "running"

    def test_steering_matches_active_status(self):
        """Steering check should find sessions in 'active' status."""
        from models.agent_session import AgentSession

        session_id = _uid("test_bridge_active")
        self._create_session(session_id, "active")

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is not None
        assert matching_session.status == "active"

    def test_steering_prefers_running_over_active(self):
        """When both running and active sessions exist, running wins."""
        from models.agent_session import AgentSession

        session_id = _uid("test_bridge_prefer_running")
        # Create both -- running should be found first
        self._create_session(session_id, "running")
        self._create_session(session_id, "active")

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is not None
        assert matching_session.status == "running"

    def test_steering_no_match_for_pending(self):
        """Steering check should NOT match sessions in 'pending' status."""
        from models.agent_session import AgentSession

        session_id = _uid("test_bridge_pending")
        self._create_session(session_id, "pending")

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is None

    def test_steering_no_match_for_completed(self):
        """Running/active check skips completed sessions.

        Completed sessions are handled by the dedicated re-enqueue branch.
        """
        from models.agent_session import AgentSession

        session_id = _uid("test_bridge_completed")
        self._create_session(session_id, "completed")

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is None

    def test_steering_no_match_for_nonexistent_session(self):
        """Steering check should return None for nonexistent sessions."""
        from models.agent_session import AgentSession

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(
                session_id="nonexistent_session_xyz", status=check_status
            )
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is None

    def test_steering_pending_detection_for_race_window(self):
        """Steering check should detect pending sessions for logging."""
        from models.agent_session import AgentSession

        session_id = _uid("test_bridge_race_window")
        self._create_session(session_id, "pending")

        # First, the main check should find nothing
        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is None

        # Then the pending check should find it
        pending_sessions = AgentSession.query.filter(session_id=session_id, status="pending")
        assert len(pending_sessions) > 0
        assert pending_sessions[0].status == "pending"

    def test_steering_push_only_after_session_match(self):
        """push_steering_message should only be called after session match."""
        session_id = _uid("test_bridge_push_guard")
        self._create_session(session_id, "running")

        from models.agent_session import AgentSession

        matching_session = None
        for check_status in ("running", "active"):
            sessions = AgentSession.query.filter(session_id=session_id, status=check_status)
            if sessions:
                matching_session = sessions[0]
                break

        assert matching_session is not None
        # Only push if we matched
        push_steering_message(session_id, "test steering", "Tom")
        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "test steering"

    def test_steering_error_handling_connection_error(self):
        """ConnectionError should be caught separately from generic errors."""
        from models.agent_session import AgentSession

        # Verify that ConnectionError is a subclass check target
        # (the bridge catches ConnectionError and OSError separately)
        with patch.object(
            AgentSession.query,
            "filter",
            side_effect=ConnectionError("Redis unavailable"),
        ):
            caught_connection = False
            try:
                AgentSession.query.filter(session_id="test", status="running")
            except (ConnectionError, OSError):
                caught_connection = True
            except Exception:
                caught_connection = False

            assert caught_connection is True

    def test_steering_error_handling_generic_error(self):
        """Generic exceptions should be caught by the fallback handler."""
        from models.agent_session import AgentSession

        with patch.object(
            AgentSession.query,
            "filter",
            side_effect=ValueError("unexpected"),
        ):
            caught_generic = False
            try:
                AgentSession.query.filter(session_id="test", status="running")
            except (ConnectionError, OSError):
                caught_generic = False
            except Exception:
                caught_generic = True

            assert caught_generic is True


class TestPendingSessionSteering:
    """Tests for steering into pending sessions within the merge window (#619)."""

    def _create_session(self, session_id, status, chat_id="test_chat", created_at=None):
        """Create an AgentSession with the given status."""
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=session_id,
            project_key="test",
            status=status,
            chat_id=chat_id,
            message_text="test message",
            created_at=created_at or time.time(),
        )
        session.save()
        return session

    def test_pending_session_within_window_receives_steering(self):
        """A pending session within 7s should accept steering messages."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS

        session_id = _uid("test_pending_steer_recent")
        self._create_session(session_id, "pending", created_at=datetime.now(tz=UTC))

        # Simulate the bridge logic: check age and push steering
        from models.agent_session import AgentSession

        pending_sessions = AgentSession.query.filter(session_id=session_id, status="pending")
        assert len(pending_sessions) > 0
        pending_session = pending_sessions[0]
        _created = pending_session.created_at
        if isinstance(_created, datetime):
            _created = (
                _created.timestamp()
                if _created.tzinfo
                else _created.replace(tzinfo=UTC).timestamp()
            )
        age = time.time() - (_created or 0)
        assert age <= PENDING_MERGE_WINDOW_SECONDS

        push_steering_message(session_id, "follow-up context", "Tom")
        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "follow-up context"

    def test_pending_session_outside_window_not_steered(self):
        """A pending session older than 7s should NOT be steered into."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS

        session_id = _uid("test_pending_steer_old")
        # Create with timestamp 10s in the past
        self._create_session(
            session_id,
            "pending",
            created_at=datetime.now(tz=UTC) - timedelta(seconds=10),
        )

        from models.agent_session import AgentSession

        pending_sessions = AgentSession.query.filter(session_id=session_id, status="pending")
        assert len(pending_sessions) > 0
        pending_session = pending_sessions[0]
        _created = pending_session.created_at
        if isinstance(_created, datetime):
            _created = (
                _created.timestamp()
                if _created.tzinfo
                else _created.replace(tzinfo=UTC).timestamp()
            )
        age = time.time() - (_created or 0)
        assert age > PENDING_MERGE_WINDOW_SECONDS

    def test_pending_merge_window_constant_is_8(self):
        """The merge window constant should be 8 seconds."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS

        assert PENDING_MERGE_WINDOW_SECONDS == 8

    def test_multiple_steering_messages_into_pending(self):
        """Multiple follow-up messages should all queue into a pending session."""
        session_id = _uid("test_pending_multi_steer")
        self._create_session(session_id, "pending", created_at=datetime.now(tz=UTC))

        push_steering_message(session_id, "first follow-up", "Tom")
        push_steering_message(session_id, "second follow-up", "Tom")
        push_steering_message(session_id, "third follow-up", "Tom")

        messages = pop_all_steering_messages(session_id)
        assert len(messages) == 3
        assert messages[0]["text"] == "first follow-up"
        assert messages[1]["text"] == "second follow-up"
        assert messages[2]["text"] == "third follow-up"

    def test_intake_classifier_includes_recent_pending(self):
        """The intake classifier status loop should include recent pending sessions."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS
        from models.agent_session import AgentSession

        chat_id = _uid("test_intake_pending_chat")
        session_id = _uid("test_intake_pending_session")
        self._create_session(
            session_id, "pending", chat_id=chat_id, created_at=datetime.now(tz=UTC)
        )

        # Replicate the intake classifier logic from the bridge
        active_sessions = []
        for check_status in ("running", "active", "dormant"):
            sessions = AgentSession.query.filter(chat_id=chat_id, status=check_status)
            if sessions:
                active_sessions.extend(sessions)

        # Also include recent pending sessions within the merge window
        pending_sessions = AgentSession.query.filter(chat_id=chat_id, status="pending")
        if pending_sessions:
            now_ts = time.time()
            for ps in pending_sessions:
                _ct = ps.created_at
                if isinstance(_ct, datetime):
                    _ct = _ct.timestamp() if _ct.tzinfo else _ct.replace(tzinfo=UTC).timestamp()
                age = now_ts - (_ct or 0)
                if age <= PENDING_MERGE_WINDOW_SECONDS:
                    active_sessions.append(ps)

        assert len(active_sessions) == 1
        assert active_sessions[0].session_id == session_id
        assert active_sessions[0].status == "pending"

    def test_intake_classifier_excludes_old_pending(self):
        """The intake classifier should NOT include pending sessions older than 7s."""
        from bridge.telegram_bridge import PENDING_MERGE_WINDOW_SECONDS
        from models.agent_session import AgentSession

        chat_id = _uid("test_intake_old_pending_chat")
        session_id = _uid("test_intake_old_pending_session")
        self._create_session(
            session_id,
            "pending",
            chat_id=chat_id,
            created_at=datetime.now(tz=UTC) - timedelta(seconds=10),
        )

        active_sessions = []
        for check_status in ("running", "active", "dormant"):
            sessions = AgentSession.query.filter(chat_id=chat_id, status=check_status)
            if sessions:
                active_sessions.extend(sessions)

        pending_sessions = AgentSession.query.filter(chat_id=chat_id, status="pending")
        if pending_sessions:
            now_ts = time.time()
            for ps in pending_sessions:
                _ct = ps.created_at
                if isinstance(_ct, datetime):
                    _ct = _ct.timestamp() if _ct.tzinfo else _ct.replace(tzinfo=UTC).timestamp()
                age = now_ts - (_ct or 0)
                if age <= PENDING_MERGE_WINDOW_SECONDS:
                    active_sessions.append(ps)

        assert len(active_sessions) == 0


class TestWatchdogSteering:
    """Tests for steering integration in the watchdog hook.

    ``_handle_steering`` is the sole steering-injection/delivery path for
    every CLI-harness (production) session (plan #2000 Critique Results
    BLOCKER finding) -- there is no more in-process SDK-client
    interrupt()/query() arm (deleted, plan #2000 Task 2.2). Every non-abort
    message is unconditionally re-pushed to the Redis steering list for the
    worker's turn-boundary drain to pick up on the next turn.
    """

    @pytest.mark.asyncio
    async def test_watchdog_returns_continue_when_no_steering(self):
        """Watchdog should return continue when steering queue is empty."""
        from agent.health_check import _handle_steering

        result = await _handle_steering("empty_session_xyz")
        assert result is None

    @pytest.mark.asyncio
    async def test_watchdog_handles_abort(self):
        """Watchdog should inject abort directive via hookSpecificOutput.

        PostToolUse hooks can't enforce continue_: False directly, so the
        abort is injected as additionalContext with a strong stop directive.
        """
        from agent.health_check import _handle_steering

        session_id = _uid("test_watchdog_abort")
        push_steering_message(session_id, "stop", "Tom", is_abort=True)

        result = await _handle_steering(session_id)
        assert result is not None
        # Abort is delivered via hookSpecificOutput additionalContext
        hook_output = result["hookSpecificOutput"]
        assert hook_output["hookEventName"] == "PostToolUse"
        assert "ABORT from Tom" in hook_output["additionalContext"]
        assert "stop immediately" in hook_output["additionalContext"]

    @pytest.mark.asyncio
    async def test_watchdog_repushes_message_to_redis_list(self):
        """A steering message injected while a CLI-harness session runs
        lands on the Redis steering list and is drained next turn (plan
        #2000 Task 2.2 regression coverage -- the sole remaining delivery
        path now that the SDK-client injection arm is gone)."""
        from agent.health_check import _handle_steering

        session_id = _uid("test_watchdog_repush")
        push_steering_message(session_id, "focus on OAuth", "Tom")

        result = await _handle_steering(session_id)

        assert result is not None
        assert result["continue_"] is True

        msg = pop_steering_message(session_id)
        assert msg is not None, "Message should have been re-pushed to the Redis list"
        assert msg["text"] == "focus on OAuth"

    @pytest.mark.asyncio
    async def test_watchdog_repushes_multiple_messages(self):
        """Every remaining (non-abort) queued message is re-pushed together,
        regardless of whether the session record exists in the DB."""
        from agent.health_check import _handle_steering

        session_id = _uid("test_watchdog_repush_multi")
        push_steering_message(session_id, "first", "Tom")
        push_steering_message(session_id, "second", "Tom")

        result = await _handle_steering(session_id)
        assert result is not None
        assert result["continue_"] is True

        messages = pop_all_steering_messages(session_id)
        assert {m["text"] for m in messages} == {"first", "second"}

    @pytest.mark.asyncio
    async def test_watchdog_repush_retries_once_on_redis_failure(self):
        """If the primary re-push raises (e.g. a transient Redis error), the
        except arm retries the re-push once rather than losing the message
        or crashing the hook."""
        import agent.health_check as hc
        from agent.health_check import _handle_steering

        session_id = _uid("test_watchdog_repush_retry")
        push_steering_message(session_id, "update the tests", "Tom")

        real_repush = hc._repush_messages
        call_count = {"n": 0}

        def _flaky_repush(*args, **kwargs):
            call_count["n"] += 1
            if call_count["n"] == 1:
                raise RuntimeError("redis hiccup")
            return real_repush(*args, **kwargs)

        with patch("agent.health_check._repush_messages", side_effect=_flaky_repush):
            result = await _handle_steering(session_id)

        assert result is not None
        assert result["continue_"] is True
        assert call_count["n"] == 2, "the except arm must retry the re-push exactly once"

        msg = pop_steering_message(session_id)
        assert msg is not None
        assert msg["text"] == "update the tests"


class TestResolveRootSessionId:
    """Tests for resolve_root_session_id in bridge/context.py.

    These tests verify that reply-to messages resolve to the original human
    message's session_id regardless of which message in the thread is replied to.
    All tests use mocked Telegram clients and mocked TelegramMessage cache records.
    """

    @pytest.mark.asyncio
    async def test_reply_to_valor_response_resolves_root_session(self):
        """Reply to Valor's message should resolve to the original human session_id.

        Scenario:
          msg_8111 (human) → msg_8113 (Valor response) → msg_8114 (human reply to 8113)
        Expected: resolve_root_session_id(client, chat, 8113, key) == "tg_key_chat_8111"
        """
        from bridge.context import resolve_root_session_id

        chat_id = 99001
        project_key = "testproject"

        # Simulate TelegramMessage cache:
        # msg_8113 is a Valor outbound message that has reply_to_msg_id=8111
        # msg_8111 is the original human inbound message
        def mock_filter(chat_id=None, message_id=None):
            if message_id == 8113:
                record = type(
                    "TelegramMsg",
                    (),
                    {"sender": "Valor", "reply_to_msg_id": 8111, "message_id": 8113},
                )()
                return [record]
            elif message_id == 8111:
                record = type(
                    "TelegramMsg",
                    (),
                    {
                        "sender": "Valor Engels",
                        "reply_to_msg_id": None,
                        "message_id": 8111,
                    },
                )()
                return [record]
            return []

        mock_query = type("Q", (), {"filter": staticmethod(mock_filter)})()
        mock_client = AsyncMock()

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query = mock_query
            result = await resolve_root_session_id(mock_client, chat_id, 8113, project_key)

        assert result == f"tg_{project_key}_{chat_id}_8111"
        # Telegram API should NOT have been called (cache hit path)
        mock_client.get_messages.assert_not_called()

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_fallback_on_error(self):
        """Exception during chain walk should return fallback session_id.

        If both cache walk and API walk throw, the result must be derived
        directly from reply_to_msg_id (old behavior, safe fallback).
        """
        from bridge.context import resolve_root_session_id

        chat_id = 99002
        project_key = "testproject"
        reply_to_msg_id = 5555

        mock_client = AsyncMock()
        mock_client.get_messages.side_effect = ConnectionError("Telegram unavailable")

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query.filter.side_effect = RuntimeError("Redis unavailable")
            result = await resolve_root_session_id(
                mock_client, chat_id, reply_to_msg_id, project_key
            )

        # Must fall back gracefully to reply_to_msg_id-based session_id
        assert result == f"tg_{project_key}_{chat_id}_{reply_to_msg_id}"

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_all_valor_chain(self):
        """Chain consisting entirely of Valor messages falls through to API.

        If the cache walk finds only Valor messages with no parent, the function
        should accept the last Valor message as the root rather than looping forever.
        When the API also returns only Valor messages, it should use the fallback.
        """
        from bridge.context import resolve_root_session_id

        chat_id = 99003
        project_key = "testproject"
        reply_to_msg_id = 7777

        # Cache: single Valor message with no reply_to_msg_id
        def mock_filter(chat_id=None, message_id=None):
            if message_id == 7777:
                record = type(
                    "TelegramMsg",
                    (),
                    {"sender": "Valor", "reply_to_msg_id": None, "message_id": 7777},
                )()
                return [record]
            return []

        mock_query = type("Q", (), {"filter": staticmethod(mock_filter)})()
        mock_client = AsyncMock()

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query = mock_query
            result = await resolve_root_session_id(
                mock_client, chat_id, reply_to_msg_id, project_key
            )

        # Valor-only chain with no parent: should use the Valor msg_id as root
        # (best-effort fallback within cache walk — still deterministic)
        assert result == f"tg_{project_key}_{chat_id}_7777"

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_direct_reply_to_human(self):
        """Reply directly to a human message resolves to that human message."""
        from bridge.context import resolve_root_session_id

        chat_id = 99004
        project_key = "testproject"
        reply_to_msg_id = 1234

        # Cache: msg_1234 is a human message (not Valor)
        def mock_filter(chat_id=None, message_id=None):
            if message_id == 1234:
                record = type(
                    "TelegramMsg",
                    (),
                    {"sender": "Alice", "reply_to_msg_id": None, "message_id": 1234},
                )()
                return [record]
            return []

        mock_query = type("Q", (), {"filter": staticmethod(mock_filter)})()
        mock_client = AsyncMock()

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query = mock_query
            result = await resolve_root_session_id(
                mock_client, chat_id, reply_to_msg_id, project_key
            )

        assert result == f"tg_{project_key}_{chat_id}_1234"

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_api_fallback_on_cache_miss(self):
        """Cache miss triggers API chain walk; result is persisted to Redis cache."""
        from bridge.context import _get_cached_root, resolve_root_session_id

        chat_id = 99005
        project_key = "testproject"
        reply_to_msg_id = 9999

        # Cache: no records (simulate TelegramMessage cache miss)
        def mock_filter(chat_id=None, message_id=None):
            return []

        mock_query = type("Q", (), {"filter": staticmethod(mock_filter)})()

        # API chain: returns a chain with root human message at msg_id=8800
        async def mock_fetch_reply_chain(client, cid, mid, max_depth=20):
            return [
                {"sender": "Bob", "content": "hello", "message_id": 8800, "date": None},
                {"sender": "Valor", "content": "hi", "message_id": 8801, "date": None},
                {
                    "sender": "Bob",
                    "content": "follow up",
                    "message_id": 9999,
                    "date": None,
                },
            ]

        mock_client = AsyncMock()

        with patch("models.telegram.TelegramMessage") as mock_tm:
            mock_tm.query = mock_query
            with patch("bridge.context.fetch_reply_chain", side_effect=mock_fetch_reply_chain):
                result = await resolve_root_session_id(
                    mock_client, chat_id, reply_to_msg_id, project_key
                )

        # First human message in chain is msg_id=8800
        assert result == f"tg_{project_key}_{chat_id}_8800"

        # After API fallback, the root must be persisted to the authoritative Redis cache.
        # A second call via _get_cached_root should return the same root without needing
        # the API again — proving the cache was written on the first resolution.
        cached = await _get_cached_root(chat_id, reply_to_msg_id)
        assert cached == 8800, f"Expected root 8800 to be cached after API fallback, got {cached}"

    @pytest.mark.asyncio
    async def test_resolve_root_session_id_uses_cached_root(self):
        """Pre-populated Redis cache is hit first; cache walk and API are never called."""
        from bridge.context import _set_cached_root, resolve_root_session_id

        chat_id = 99006
        project_key = "testproject"
        reply_to_msg_id = 7001
        expected_root = 7000

        # Pre-populate the authoritative Redis cache
        await _set_cached_root(chat_id, reply_to_msg_id, expected_root)

        mock_client = AsyncMock()

        with patch("bridge.context._cache_walk_root") as mock_cache_walk:
            with patch("bridge.context.fetch_reply_chain") as mock_api_walk:
                result = await resolve_root_session_id(
                    mock_client, chat_id, reply_to_msg_id, project_key
                )

        assert result == f"tg_{project_key}_{chat_id}_{expected_root}"
        # Neither the TelegramMessage cache walk nor the Telegram API should be called
        mock_cache_walk.assert_not_called()
        mock_api_walk.assert_not_called()

    @pytest.mark.asyncio
    async def test_reply_to_completed_session_reenqueues_with_context(self):
        """Reply to a completed session re-enqueues with context_summary prepended.

        Tests the actual bridge helper _build_completed_resume_text which is called
        by bridge/telegram_bridge.py's completed-session branch to augment the new
        message with prior session context before re-enqueueing.
        """
        from bridge.telegram_bridge import _build_completed_resume_text
        from models.agent_session import AgentSession

        follow_up_text = "Can you also add logging?"
        context_summary = "Implemented feature X and wrote tests."

        # Create a completed session with context_summary
        session = AgentSession(
            session_id=_uid("test_completed_resume_helper_ctx"),
            project_key="test",
            status="completed",
            message_text="original task",
            context_summary=context_summary,
            created_at=datetime.now(tz=UTC),
        )
        session.save()

        # Call the actual bridge helper — not a re-implementation
        result = _build_completed_resume_text(session, follow_up_text)

        # Verify augmented text contains the context summary preamble
        assert "[Prior session context:" in result, (
            f"Expected '[Prior session context:' prefix, got: {result!r}"
        )
        assert context_summary in result, f"Expected context summary in result, got: {result!r}"
        assert follow_up_text in result, f"Expected follow-up text in result, got: {result!r}"
        # Canonical format check
        assert result == f"[Prior session context: {context_summary}]\n\n{follow_up_text}"

        # --- Test fallback when context_summary is None ---
        session_no_summary = AgentSession(
            session_id=_uid("test_completed_resume_helper_no_ctx"),
            project_key="test",
            status="completed",
            message_text="done",
            context_summary=None,
            created_at=datetime.now(tz=UTC),
        )
        session_no_summary.save()

        fallback_result = _build_completed_resume_text(session_no_summary, follow_up_text)

        assert (
            "[Prior session context: This continues a previously completed session.]"
            in fallback_result
        ), f"Expected fallback string, got: {fallback_result!r}"
        assert follow_up_text in fallback_result

        # --- Extended by issue #949: with reply chain, both blocks appear ---
        from bridge.context import REPLY_THREAD_CONTEXT_HEADER

        chain_block = (
            f"{REPLY_THREAD_CONTEXT_HEADER} (oldest to newest):\n"
            "----------------------------------------\n"
            "Tom: any update?\nValor: fixed yesterday\n"
            "----------------------------------------"
        )
        with_chain = _build_completed_resume_text(
            session, follow_up_text, reply_chain_context=chain_block
        )
        assert context_summary in with_chain
        assert REPLY_THREAD_CONTEXT_HEADER in with_chain
        assert follow_up_text in with_chain
        # Exactly one header — Race 1 / IN-1 guard
        assert with_chain.count(REPLY_THREAD_CONTEXT_HEADER) == 1
        # Order: summary -> chain -> follow_up
        assert (
            with_chain.index("[Prior session context:")
            < with_chain.index(REPLY_THREAD_CONTEXT_HEADER)
            < with_chain.index(follow_up_text)
        )

    @pytest.mark.asyncio
    async def test_reply_to_completed_session_fallback_without_summary(self):
        """Reply to a completed session with no context_summary still carries the reply chain.

        Replays the 2026-04-14 11:54 incident (issue #949): the prior session's
        context_summary was empty, so the fallback preamble is used. With the
        new reply-chain carry, the agent still sees the thread context.
        """
        from bridge.context import REPLY_THREAD_CONTEXT_HEADER
        from bridge.telegram_bridge import _build_completed_resume_text
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=_uid("test_fallback_reply_chain_949"),
            project_key="test",
            status="completed",
            message_text="errored out",
            context_summary=None,  # the 11:54 incident had no summary
            created_at=datetime.now(tz=UTC),
        )
        session.save()

        chain_block = (
            f"{REPLY_THREAD_CONTEXT_HEADER} (oldest to newest):\n"
            "----------------------------------------\n"
            "Tom: can you check and see if we got this fixed?\n"
            "----------------------------------------"
        )
        result = _build_completed_resume_text(
            session,
            "did we get this fixed?",
            reply_chain_context=chain_block,
        )
        # Fallback sentinel still present
        assert "This continues a previously completed session." in result
        # Reply chain hydrated -- this is the new carry
        assert REPLY_THREAD_CONTEXT_HEADER in result
        assert result.count(REPLY_THREAD_CONTEXT_HEADER) == 1
        assert "did we get this fixed?" in result

    @pytest.mark.asyncio
    async def test_resume_completed_carries_reply_chain(self):
        """End-to-end: the helper produces a prompt containing the REPLY THREAD CONTEXT
        block when the handler passes a reply_chain_context.

        This is the guard against regression of the gap described in #949:
        the resume-completed branch used to omit reply-thread context.
        """
        from bridge.context import REPLY_THREAD_CONTEXT_HEADER, format_reply_chain
        from bridge.telegram_bridge import _build_completed_resume_text
        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=_uid("test_resume_carries_chain"),
            project_key="test",
            status="completed",
            message_text="",
            context_summary="prior context",
            created_at=datetime.now(tz=UTC),
        )
        session.save()

        # Build a realistic reply chain block via the real formatter
        chain = [
            {"sender": "Tom", "content": "is the bug fixed?", "message_id": 1, "date": None},
            {"sender": "Valor", "content": "yes, shipped yesterday", "message_id": 2, "date": None},
        ]
        chain_block = format_reply_chain(chain)
        assert REPLY_THREAD_CONTEXT_HEADER in chain_block

        augmented = _build_completed_resume_text(
            session,
            "can you verify it's still working?",
            reply_chain_context=chain_block,
        )
        assert "prior context" in augmented
        assert REPLY_THREAD_CONTEXT_HEADER in augmented
        assert "is the bug fixed?" in augmented
        assert "can you verify" in augmented

    @pytest.mark.parametrize(
        "hydration_site",
        ["resume_completed", "fresh_session_non_valor"],
    )
    def test_no_double_hydration_when_handler_prehydrates(self, hydration_site):
        """Race 1 / IN-1 / IN-7: belt-and-suspenders idempotency guard.

        The deferred enrichment must skip the reply-chain fetch when either:
          - Primary:   extra_context["reply_chain_hydrated"] flag is set by
                       the bridge handler at enqueue time.
          - Defensive: REPLY_THREAD_CONTEXT_HEADER substring is present in
                       message_text.

        Guards both the flag-based and header-based checks in
        agent/agent_session_queue.py against being accidentally removed or
        re-ordered. Also guards both bridge handler call sites against
        regressing on the extra_context stamp:
          - resume_completed: PR #953's resume-completed branch (reply-to-Valor).
          - fresh_session_non_valor: Issue #1064's fresh-session branch (reply
            to a non-Valor message, semantic-route miss).

        The guarantee is a SINGLE assertion contract: exactly one
        REPLY THREAD CONTEXT block per prompt regardless of which handler
        branch hydrated (plan Implementation Note C5).
        """
        import pathlib

        from bridge.context import REPLY_THREAD_CONTEXT_HEADER

        # Read the source and assert the guards are in place. This is a
        # structural test -- simulating the full worker path would pull in
        # Claude SDK / Popoto queues. The guards are a handful of lines and
        # regress only by deletion, which this test catches.
        #
        # Note: the worker-side guard lives in agent/session_executor.py
        # after the agent_session_queue.py split in commit b7e1a1db
        # (PR #1023 / #1051). Prior to that refactor it was in
        # agent/agent_session_queue.py.
        executor_src = pathlib.Path(__file__).resolve().parents[2] / "agent" / "session_executor.py"
        executor_content = executor_src.read_text()
        assert "REPLY_THREAD_CONTEXT_HEADER" in executor_content, (
            "Defensive header guard removed — reply chain may double-hydrate"
        )
        assert "reply_chain_hydrated" in executor_content, (
            "Primary flag guard (IN-1 belt-and-suspenders) removed from worker enrichment"
        )
        # Must do the check AGAINST enrich_reply_to_msg_id so the fetch is skipped
        assert "enrich_reply_to_msg_id = None" in executor_content
        assert REPLY_THREAD_CONTEXT_HEADER  # sanity check the import

        # Both bridge handler call sites must stamp the primary flag when
        # they hydrate the reply chain synchronously.
        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()
        assert '"reply_chain_hydrated": True' in bridge_content, (
            "Handler stopped stamping reply_chain_hydrated=True on extra_context — "
            "primary IN-1 guard is no longer populated"
        )
        # Exactly two call sites must exist: resume-completed (PR #953) and
        # fresh-session-non-valor (issue #1064). Any additional site should be
        # reviewed explicitly because it risks double-hydration if not gated.
        flag_stamp_count = bridge_content.count('"reply_chain_hydrated": True')
        assert flag_stamp_count >= 2, (
            f"Expected at least 2 reply_chain_hydrated stamp sites (resume-completed + "
            f"fresh-session), found {flag_stamp_count}. Did the fresh-session pre-hydration "
            f"block get removed or renamed?"
        )

        # Per-site structural guards:
        if hydration_site == "resume_completed":
            assert "RESUME_REPLY_CHAIN_FAIL" in bridge_content, (
                "Resume-completed failure-path log tag missing"
            )
        else:  # fresh_session_non_valor
            assert "FRESH_REPLY_CHAIN_FAIL" in bridge_content, (
                "Fresh-session failure-path log tag missing — reply-to non-Valor "
                "messages may silently drop thread context"
            )
            assert "fresh_reply_chain_prehydrated" in bridge_content, (
                "Fresh-session success log tag missing — observability parity broken"
            )
            assert "REPLY_CHAIN_PREHYDRATION_DISABLED" in bridge_content, (
                "Fresh-session kill-switch removed — rollback without deploy is broken"
            )

    def test_fresh_session_non_valor_reply_prehydrates_chain(self):
        """Issue #1064: the fresh-session pre-hydration block must exist and
        produce a REPLY_THREAD_CONTEXT block in enqueued_message_text with
        extra_context[reply_chain_hydrated]=True.

        Structural test — we assert the code shape rather than simulate the
        full Telegram/Telethon handler invocation, which would pull in the
        Claude SDK, Popoto queues, and a mocked client. The code shape is a
        handful of lines and regresses only by deletion or gate-condition
        drift, which this test catches.
        """
        import pathlib

        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()

        # The new block must exist with the canonical section marker.
        assert "FRESH-SESSION NON-VALOR REPLY PRE-HYDRATION" in bridge_content, (
            "Fresh-session pre-hydration block removed or section comment stripped"
        )
        # Gate condition: reply_to_msg_id truthy AND NOT is_reply_to_valor AND kill-switch off.
        # The handler topology already enforces the fresh-session placement; we only need
        # to assert the two explicit predicates plus the kill-switch.
        assert "not is_reply_to_valor" in bridge_content, (
            "Gate predicate `not is_reply_to_valor` missing — would double-hydrate "
            "when resume-completed branch already pre-hydrated"
        )
        # The prepend format must include the canonical header and the CURRENT MESSAGE marker.
        assert "CURRENT MESSAGE:" in bridge_content, (
            "CURRENT MESSAGE marker missing — agent can't distinguish thread from new text"
        )
        # Success path stamps the flag AND emits the INFO log.
        assert '"reply_chain_hydrated": True' in bridge_content
        assert "fresh_reply_chain_prehydrated" in bridge_content

    def test_fresh_session_non_valor_reply_timeout_falls_back(self):
        """Issue #1064 failure path: 3s timeout logs FRESH_REPLY_CHAIN_FAIL
        and does NOT stamp reply_chain_hydrated, so the worker's deferred
        enrichment remains free to retry.

        Implementation Note C2: three outcomes, only success-with-chain stamps.
        """
        import pathlib

        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()

        # Both failure branches must log with FRESH_REPLY_CHAIN_FAIL tag.
        # grep-style: the tag appears at least twice (timeout + exception).
        assert bridge_content.count("FRESH_REPLY_CHAIN_FAIL") >= 2, (
            "FRESH_REPLY_CHAIN_FAIL log tag must appear in both TimeoutError and "
            "generic Exception branches — at least 2 occurrences required"
        )
        assert "FRESH_REPLY_CHAIN_FAIL timeout" in bridge_content, (
            "Timeout branch log missing the 'timeout' discriminator"
        )
        assert "FRESH_REPLY_CHAIN_FAIL exception" in bridge_content, (
            "Exception branch log missing the 'exception' discriminator"
        )

        # Both fetch_reply_chain guards must use the same 3.0s budget as
        # PR #953's resume-completed site (tuning timeouts belongs in a
        # separate telemetry-driven change). Asserted against the resolved
        # value at each call site, so hoisting 3.0 into a module constant
        # passes and a changed budget fails (#2623).
        timeouts = _reply_chain_fetch_timeouts()
        assert len(timeouts) == 2, (
            f"Expected exactly 2 timeout-guarded fetch_reply_chain call sites "
            f"(resume-completed + fresh-session), found {len(timeouts)}"
        )
        assert timeouts == [3.0, 3.0], (
            f"Reply-chain pre-hydration timeout diverged from PR #953's 3.0s "
            f"(found {timeouts}) — tuning belongs in a follow-up with telemetry"
        )

        # Failure path must NOT stamp the flag: every reply_chain_hydrated
        # assignment must sit inside an `if reply_chain_context:` body, not
        # unconditionally after the try/except.
        total_stamps, guarded_stamps = _hydration_flag_stamps()
        assert total_stamps == 2, (
            f"Expected 2 reply_chain_hydrated stamps (resume-completed + "
            f"fresh-session), found {total_stamps}"
        )
        assert guarded_stamps == total_stamps, (
            f"{total_stamps - guarded_stamps} reply_chain_hydrated stamp(s) are not "
            f"gated on `if reply_chain_context:` — a failed or empty fetch would "
            f"stamp the flag and suppress the worker's deferred enrichment retry "
            f"(Implementation Note C2)"
        )

    def test_fresh_session_reply_to_valor_skips_new_block(self):
        """Issue #1064: `is_reply_to_valor=True` messages must NOT hit the
        new fresh-session block. They are handled by the resume-completed
        branch (PR #953) which returns earlier in the handler, so placement
        enforces non-double-hydration.

        Structural check: the new block must explicitly gate on
        `not is_reply_to_valor` so even if handler topology changes in a
        way that lets control flow reach here with is_reply_to_valor=True,
        the gate prevents the pre-fetch.
        """
        import pathlib

        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()

        # The new block's gate must include `not is_reply_to_valor`.
        # We look for the section comment followed by the gate clause.
        fresh_block_start = bridge_content.find("FRESH-SESSION NON-VALOR REPLY PRE-HYDRATION")
        assert fresh_block_start >= 0, "Fresh-session block section comment missing"

        # Find the gate `if` statement within the fresh-session block.
        # The gate is within ~2000 chars of the section comment.
        fresh_block_region = bridge_content[fresh_block_start : fresh_block_start + 3000]
        assert "not is_reply_to_valor" in fresh_block_region, (
            "Fresh-session gate missing `not is_reply_to_valor` predicate — "
            "would double-hydrate replies-to-Valor if resume-completed branch "
            "ever failed to short-circuit"
        )
        assert "message.reply_to_msg_id" in fresh_block_region, (
            "Fresh-session gate missing `message.reply_to_msg_id` predicate"
        )

    def test_fresh_session_prehydration_kill_switch(self):
        """Issue #1064: REPLY_CHAIN_PREHYDRATION_DISABLED kill-switch env var
        must mirror REPLY_CONTEXT_DIRECTIVE_DISABLED's parsing exactly —
        truthy set ("1", "true", "yes", "on"), .strip().lower(), default "".

        Implementation Note C3: parity prevents a subtle bug where a rollout
        uses "TRUE" to disable the directive but "true" to disable the chain.
        """
        import pathlib

        bridge_src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        bridge_content = bridge_src.read_text()

        # The kill-switch env var must be referenced.
        assert "REPLY_CHAIN_PREHYDRATION_DISABLED" in bridge_content, (
            "Kill-switch env var REPLY_CHAIN_PREHYDRATION_DISABLED missing — "
            "rollback without deploy is broken"
        )

        # Parsing must mirror the sibling REPLY_CONTEXT_DIRECTIVE_DISABLED
        # exactly — same truthy set, same normalization. Both bridge sites
        # use the multi-line tuple form, so we assert the full
        # `os.getenv(...).strip().lower() in (...)` shape via the env-var
        # name + normalization chain, then verify all four truthy values
        # appear together in the surrounding region of the new block.
        assert ".strip().lower() in (" in bridge_content, (
            "Kill-switch normalization must use `.strip().lower() in (...)` chain "
            "matching REPLY_CONTEXT_DIRECTIVE_DISABLED sibling pattern"
        )

        # Locate the fresh-session block and assert its truthy set matches
        # the sibling — all four truthy values present in a narrow region
        # following the env-var name.
        disabled_marker = "REPLY_CHAIN_PREHYDRATION_DISABLED"
        marker_pos = bridge_content.find(disabled_marker)
        assert marker_pos >= 0, "Kill-switch env var name not found"
        # Region from the env-var name to ~500 chars later covers the
        # os.getenv(...).strip().lower() in (...) block.
        region = bridge_content[marker_pos : marker_pos + 500]
        for truthy_value in ('"1"', '"true"', '"yes"', '"on"'):
            assert truthy_value in region, (
                f"Kill-switch truthy set missing {truthy_value} — must mirror "
                f"REPLY_CONTEXT_DIRECTIVE_DISABLED's set exactly for parity"
            )

        # The normalization chain `.strip().lower() in (` must appear twice
        # (once for each env var) so the two sites stay in lock-step.
        assert bridge_content.count(".strip().lower() in (") >= 2, (
            "Kill-switch normalization chain must appear at both sites "
            "(REPLY_CONTEXT_DIRECTIVE_DISABLED + REPLY_CHAIN_PREHYDRATION_DISABLED)"
        )

    def test_implicit_context_directive_injected(self):
        """Plan Change C: messages that reference prior context without reply-to
        get a [CONTEXT DIRECTIVE] prepended before enqueue.

        Tests the predicate and directive contents that the handler uses.
        """
        from bridge.context import matched_context_patterns, references_prior_context

        # Positive case -- message references prior context
        assert references_prior_context("did we get this fixed?") is True
        assert len(matched_context_patterns("did we get this fixed?")) >= 1

        # Negative case -- fresh request, no directive injection
        assert references_prior_context("please create a new issue") is False
        assert matched_context_patterns("please create a new issue") == []

        # The directive string itself is embedded in telegram_bridge.py.
        # Assert its canonical prefix ships so the agent sees a recognizable marker.
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        content = src.read_text()
        assert "[CONTEXT DIRECTIVE]" in content, (
            "Implicit-context directive removed from bridge handler"
        )
        assert "REPLY_CONTEXT_DIRECTIVE_DISABLED" in content, (
            "Env kill-switch REPLY_CONTEXT_DIRECTIVE_DISABLED removed"
        )

    @pytest.mark.parametrize(
        "hydration_site,expected_log_tag",
        [
            ("resume_completed", "RESUME_REPLY_CHAIN_FAIL"),
            ("fresh_session_non_valor", "FRESH_REPLY_CHAIN_FAIL"),
        ],
    )
    def test_reply_chain_fetch_failure_falls_back(self, hydration_site, expected_log_tag):
        """Plan failure-path: a fetch_reply_chain exception must not prevent
        the handler from enqueueing the session.

        Parametrized across both handler call sites per Implementation Note C5:
          - resume_completed: PR #953's branch uses summary-only fallback via
            _build_completed_resume_text(reply_chain_context=None).
          - fresh_session_non_valor: issue #1064's branch leaves the enqueued
            message_text untouched and does NOT stamp reply_chain_hydrated,
            so worker-side deferred enrichment is free to retry.

        Both branches share the same failure contract: the session enqueues,
        the warning log fires with a distinguishable tag, and the flag is
        only stamped on success-with-non-empty-chain.
        """
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[2] / "bridge" / "telegram_bridge.py"
        content = src.read_text()

        # Both call sites must emit their distinguishable warning tags.
        assert expected_log_tag in content, (
            f"{expected_log_tag} log tag missing — failure path invisible in logs"
        )

        if hydration_site == "resume_completed":
            # Summary-only fallback via the existing helper — verify the
            # contract end-to-end against _build_completed_resume_text.
            from bridge.telegram_bridge import _build_completed_resume_text
            from models.agent_session import AgentSession

            session = AgentSession(
                session_id=_uid("test_fetch_fail_fallback_resume"),
                project_key="test",
                status="completed",
                message_text="prior",
                context_summary="did work",
                created_at=datetime.now(tz=UTC),
            )
            session.save()

            # Simulate the handler's catch branch: reply_chain_context is None
            result = _build_completed_resume_text(session, "follow up", reply_chain_context=None)

            # Summary-only format; the agent still gets SOMETHING
            assert result == "[Prior session context: did work]\n\nfollow up"
        else:
            # Fresh-session fallback is structural: on failure the handler
            # leaves enqueued_message_text unchanged and does NOT stamp the
            # flag. We assert the code-shape contract: the flag stamp is
            # gated on `if reply_chain_context:` so the failure branch
            # (exception caught, reply_chain_context remains None) falls
            # through without modification.
            total_stamps, guarded_stamps = _hydration_flag_stamps()
            assert total_stamps > 0, "reply_chain_hydrated stamp disappeared entirely"
            assert guarded_stamps == total_stamps, (
                "Fresh-session flag stamp must be gated on `if reply_chain_context:` "
                "so failed fetches do NOT stamp reply_chain_hydrated (Impl Note C2)"
            )
            # Belt-and-suspenders: the extra_overrides seed declared before
            # the try/except must be Optional and must not itself carry the
            # flag, so the failure branch passes None (or the injection
            # banner alone) through to dispatch. Asserted on the AST so a
            # reseed like `dict(_injection_ctx) or None` still counts (#2623).
            seeds = [
                node
                for node in ast.walk(_bridge_ast())
                if isinstance(node, ast.AnnAssign)
                and isinstance(node.target, ast.Name)
                and node.target.id.endswith("extra_overrides")
            ]
            assert seeds, "extra_overrides seed declaration disappeared"
            for seed in seeds:
                assert ast.unparse(seed.annotation) == "dict | None", (
                    f"extra_overrides seed must stay Optional so the failure branch "
                    f"can pass None through; got {ast.unparse(seed.annotation)}"
                )
                assert seed.value is not None and "reply_chain_hydrated" not in ast.unparse(
                    seed.value
                ), (
                    "extra_overrides seed must not carry reply_chain_hydrated — "
                    "the failure branch would stamp the flag unconditionally"
                )


class TestSteerChildDelivery:
    """Integration tests for steer_child.py → CLI-harness delivery path.

    These tests use real AgentSession objects (no mock of
    push_steering_message) to verify the end-to-end steering delivery path.
    """

    def _create_dev_session(self, parent_agent_id: str, session_id: str, status: str = "running"):
        """Create a child Eng AgentSession with a parent-child relationship.

        session_id: the Popoto session_id field (used by steer_session() for lookup).
        Returns the saved session — use session.agent_session_id as the ID for _steer_child().
        """
        from datetime import UTC, datetime

        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=session_id,
            project_key="test-steer-child",
            status=status,
            session_type="eng",
            parent_agent_session_id=parent_agent_id,
            message_text="eng task",
            created_at=datetime.now(tz=UTC),
        )
        session.save()
        return session

    def _create_pm_session(self, session_id: str):
        """Create a parent Eng AgentSession. Returns the saved session."""
        from datetime import UTC, datetime

        from models.agent_session import AgentSession

        session = AgentSession(
            session_id=session_id,
            project_key="test-steer-child",
            status="running",
            session_type="eng",
            message_text="eng task",
            created_at=datetime.now(tz=UTC),
        )
        session.save()
        return session

    def test_steer_child_cli_harness_delivery(self):
        """_steer_child() (non-abort) routes to the Redis steering list via steer_session().

        The Redis list is the sole steering inbox. ``steer_session()`` is a
        conversation-level originating writer, so the message lands on the
        child's **Room** key — it survives the child session and is served to
        whichever session next drains that Room. The child's own mortal key
        stays empty.
        """
        from models.room import room_id_for_session
        from scripts.steer_child import _steer_child

        parent = self._create_pm_session("tg_test_parent_p001")
        child = self._create_dev_session(parent.agent_session_id, "tg_test_child_c001")

        exit_code = _steer_child(
            session_id=child.agent_session_id,
            message="focus on error handling",
            parent_id=parent.agent_session_id,
            abort=False,
        )

        assert exit_code == 0

        assert pop_steering_message(child.session_id) is None, (
            "a conversation-level steer must not be parked on the mortal session key"
        )
        msg = pop_steering_message(child.session_id, room_id=room_id_for_session(child))
        assert msg is not None, "Non-abort steer should land on the child's Room key"
        assert msg["text"] == "focus on error handling"
        assert msg.get("is_abort") in (False, None)

    def test_steer_child_abort_uses_redis_list(self):
        """_steer_child() with abort=True pushes an is_abort message to the Redis list.

        The abort path pushes with session_id set to the id passed to _steer_child (the
        child's agent_session_id here), so the watchdog hook delivers it immediately.
        The session_id-keyed inbox that non-abort steers use stays empty.
        """
        from scripts.steer_child import _steer_child

        parent = self._create_pm_session("tg_test_parent_p002")
        child = self._create_dev_session(parent.agent_session_id, "tg_test_child_c002")

        exit_code = _steer_child(
            session_id=child.agent_session_id,
            message="stop",
            parent_id=parent.agent_session_id,
            abort=True,
        )

        assert exit_code == 0

        # Abort message is on the Redis list keyed by the id passed to _steer_child.
        msg = pop_steering_message(child.agent_session_id)
        assert msg is not None, "Abort message should be in Redis steering list"
        assert msg["is_abort"] is True
        assert "stop" in msg["text"]

        # The non-abort (session_id-keyed) inbox stays empty for an abort.
        assert pop_steering_message(child.session_id) is None

    def test_steer_child_terminal_session_exits_nonzero(self):
        """_steer_child() exits non-zero when session is in a terminal status."""
        from scripts.steer_child import _steer_child

        parent = self._create_pm_session("tg_test_parent_p003")
        child = self._create_dev_session(
            parent.agent_session_id, "tg_test_child_c003", status="completed"
        )

        exit_code = _steer_child(
            session_id=child.agent_session_id,
            message="too late",
            parent_id=parent.agent_session_id,
            abort=False,
        )

        assert exit_code == 1

    def test_steer_child_steer_session_failure_exits_nonzero(self, capsys):
        """_steer_child() exits non-zero and prints error when steer_session fails."""
        from scripts.steer_child import _steer_child

        parent = self._create_pm_session("tg_test_parent_p004")
        child = self._create_dev_session(parent.agent_session_id, "tg_test_child_c004")

        # Patch at the source module — _steer_child imports steer_session lazily
        with patch(
            "agent.session_executor.steer_session",
            return_value={
                "success": False,
                "session_id": child.session_id,
                "error": "mock failure",
            },
        ):
            exit_code = _steer_child(
                session_id=child.agent_session_id,
                message="will fail",
                parent_id=parent.agent_session_id,
                abort=False,
            )

        assert exit_code == 1
        captured = capsys.readouterr()
        assert "mock failure" in captured.err


class TestRoomDualRead:
    """Room-scoped steering dual-read (issue #2494).

    The drain consumer reads the LEGACY session key first, then the Room key.
    These tests exercise the read half in isolation, so they write the Room leg
    directly rather than through ``push_steering_message``; the writer's own key
    selection is covered by ``TestKeySelection`` below.
    """

    ROOM_ID = "test-proj|telegram:4242"

    def _push_room(self, room_id, text, sender="Tom", is_abort=False):
        """Write the Room leg directly, bypassing the writer's key selection."""
        import json
        import time as _time

        from agent.steering import _get_redis, _room_queue_key

        _get_redis().rpush(
            _room_queue_key(room_id),
            json.dumps(
                {"text": text, "sender": sender, "timestamp": _time.time(), "is_abort": is_abort}
            ),
        )

    def test_pop_all_drains_legacy_then_room(self):
        session_id = _uid("test_dualread_order")
        push_steering_message(session_id, "legacy-1", "Tom")
        self._push_room(self.ROOM_ID, "room-1")

        msgs = pop_all_steering_messages(session_id, room_id=self.ROOM_ID)
        assert [m["text"] for m in msgs] == ["legacy-1", "room-1"]
        assert pop_all_steering_messages(session_id, room_id=self.ROOM_ID) == []

    def test_pop_all_without_room_id_is_legacy_only(self):
        session_id = _uid("test_dualread_legacy_only")
        push_steering_message(session_id, "legacy-only", "Tom")
        self._push_room(self.ROOM_ID + "-b", "room-untouched")

        msgs = pop_all_steering_messages(session_id)
        assert [m["text"] for m in msgs] == ["legacy-only"]
        # The room key was NOT drained by a room-less pop.
        msgs = pop_all_steering_messages(session_id, room_id=self.ROOM_ID + "-b")
        assert [m["text"] for m in msgs] == ["room-untouched"]

    def test_has_and_peek_see_the_room_leg(self):
        session_id = _uid("test_dualread_haspeek")
        assert has_steering_messages(session_id, room_id=self.ROOM_ID + "-c") is False
        self._push_room(self.ROOM_ID + "-c", "room-only")
        assert has_steering_messages(session_id) is False  # legacy leg empty
        assert has_steering_messages(session_id, room_id=self.ROOM_ID + "-c") is True

        from agent.steering import peek_steering_messages

        push_steering_message(session_id, "legacy-first", "Tom")
        peeked = peek_steering_messages(session_id, room_id=self.ROOM_ID + "-c")
        assert [m["text"] for m in peeked] == ["legacy-first", "room-only"]
        # Peek is non-destructive on both legs.
        assert has_steering_messages(session_id, room_id=self.ROOM_ID + "-c") is True
        clear_steering_queue(session_id, room_id=self.ROOM_ID + "-c")

    def test_clear_clears_both_legs(self):
        session_id = _uid("test_dualread_clear")
        push_steering_message(session_id, "legacy", "Tom")
        self._push_room(self.ROOM_ID + "-d", "room")
        cleared = clear_steering_queue(session_id, room_id=self.ROOM_ID + "-d")
        assert cleared == 2
        assert pop_all_steering_messages(session_id, room_id=self.ROOM_ID + "-d") == []

    @pytest.mark.asyncio
    async def test_handle_steering_drains_room_leg(self):
        """The watchdog-hook path participates in the dual-read.

        A message on the Room key is drained and re-pushed to the leg it came
        from — the Room key, not the legacy one. A requeue is not an
        origination: promoting or demoting a message here would launder it out
        of the class it was written into.
        """
        from agent.health_check import _handle_steering

        session_id = _uid("test_dualread_hook")
        self._push_room(self.ROOM_ID + "-e", "room-steer")

        result = await _handle_steering(session_id, room_id=self.ROOM_ID + "-e")
        assert result is not None
        assert result["continue_"] is True

        assert pop_all_steering_messages(session_id) == [], "must not demote onto the mortal key"
        msgs = pop_all_steering_messages(session_id, room_id=self.ROOM_ID + "-e")
        assert [m["text"] for m in msgs] == ["room-steer"]


class TestAbortSiblingPreservation:
    """agent/health_check.py abort drain must re-push the abort's siblings
    (Task 11, issue #2494): a 'stop' message must not destroy the
    instructions queued alongside it."""

    @pytest.mark.asyncio
    async def test_abort_repushes_non_abort_siblings(self):
        from agent.health_check import _handle_steering

        session_id = _uid("test_abort_siblings")
        push_steering_message(session_id, "do X first", "Tom")
        push_steering_message(session_id, "stop", "Tom")  # auto-detected abort
        push_steering_message(session_id, "then do Y", "Tom")

        result = await _handle_steering(session_id)
        assert result is not None
        assert "ABORT" in result["hookSpecificOutput"]["additionalContext"]

        remaining = pop_all_steering_messages(session_id)
        assert {m["text"] for m in remaining} == {"do X first", "then do Y"}, (
            "abort must not destroy the instructions queued alongside it"
        )

    @pytest.mark.asyncio
    async def test_room_sourced_siblings_return_to_the_room_key(self):
        """Room-sourced siblings go back on the Room key; the abort does not.

        This is the one place abort routing and the Room leg meet: the abort is
        consumed rather than re-pushed, and it could not have been on the Room
        leg to begin with.
        """
        from agent.health_check import _handle_steering
        from agent.steering import _room_queue_key

        session_id = _uid("test_abort_siblings_room")
        room = f"test-proj-{_UNIQ}|telegram:4343"
        self_push = _get_redis().rpush
        self_push(
            _room_queue_key(room),
            json.dumps(
                {
                    "text": "keep doing X",
                    "sender": "Tom",
                    "timestamp": time.time(),
                    "is_abort": False,
                }
            ),
        )
        push_steering_message(session_id, "stop", "Tom")  # legacy leg, auto-detected abort

        result = await _handle_steering(session_id, room_id=room)
        assert "ABORT" in result["hookSpecificOutput"]["additionalContext"]

        assert pop_all_steering_messages(session_id) == [], "the abort is consumed, not re-pushed"
        survivors = pop_all_steering_messages(session_id, room_id=room)
        assert [m["text"] for m in survivors] == ["keep doing X"]

    @pytest.mark.asyncio
    async def test_abort_alone_leaves_queue_empty(self):
        from agent.health_check import _handle_steering

        session_id = _uid("test_abort_alone")
        push_steering_message(session_id, "stop", "Tom")

        result = await _handle_steering(session_id)
        assert result is not None
        assert "ABORT" in result["hookSpecificOutput"]["additionalContext"]
        assert pop_all_steering_messages(session_id) == []


# ═══════════════════════════════════════════════════════════════════════════════
# Room-key steering WRITER (issue #2642)
#
# The read half of the Room inbox shipped in #2622; everything below covers the
# write half. Its centre of gravity is the durability property itself — a steer
# written for session A is delivered to a *different* session B serving the same
# Room, after A is gone — plus the three boundaries that keep the flip correct:
# aborts never leave the legacy key, a requeue writes to the leg it read from,
# and the Room leg is age-bounded by time since origination.
# ═══════════════════════════════════════════════════════════════════════════════

TEST_PROJECT = f"test-room-durability-{_UNIQ}"
ROOM = f"test-proj-{_UNIQ}|telegram:9001"


# ── helpers ───────────────────────────────────────────────────────────────────


def _raw(key: str) -> list[dict]:
    """Every entry on ``key``, decoded, without consuming anything."""
    return [json.loads(x) for x in _get_redis().lrange(key, 0, -1)]


def _texts(key: str) -> list[str]:
    return [m.get("text") for m in _raw(key)]


def _push_raw(key: str, text: str, *, sender="Tom", is_abort=False, timestamp=None, **extra):
    """Write a payload directly, so a test can backdate the origination stamp."""
    payload = {
        "text": text,
        "sender": sender,
        "timestamp": time.time() if timestamp is None else timestamp,
        "is_abort": is_abort,
    }
    payload.update(extra)
    _get_redis().rpush(key, json.dumps(payload))


class _Row:
    """Minimal stand-in for an AgentSession, for room_id_for_session()."""

    def __init__(self, project_key=TEST_PROJECT, chat_id=None, session_id="s"):
        self.project_key = project_key
        self.chat_id = chat_id
        self.session_id = session_id


def _bare_runner(agent_session):
    """A SessionRunner bound only to ``_agent_session``.

    ``_default_steering_pop``/``_default_steering_push`` touch the session via
    ``getattr`` only, so no harness spawn is needed. Reading the durability
    property back through the runner — rather than calling
    ``pop_all_steering_messages`` with a test-computed room_id — is what makes
    the test able to catch a writer/reader derivation mismatch.
    """
    from agent.session_runner.runner import SessionRunner

    runner = object.__new__(SessionRunner)
    runner._agent_session = agent_session
    runner._pending_steers = []
    runner._push_steering = runner._default_steering_push
    return runner


@pytest.fixture
def sibling_rows():
    """Two persisted sessions sharing one Room, torn down through the ORM."""
    from models.agent_session import AgentSession

    created = []

    def _make(session_id: str) -> AgentSession:
        row = AgentSession()
        row.session_id = session_id
        row.project_key = TEST_PROJECT
        row.chat_id = None  # chatless → SYSTEM_ADDRESSEE, so siblings share a Room
        row.status = "running"
        row.save()
        created.append(row)
        return row

    yield _make

    for row in created:
        try:
            row.delete()
        except Exception:  # pragma: no cover — teardown must never fail a test
            pass


# ── the durability property (the reason this release exists) ──────────────────


def test_steer_survives_target_session_and_reaches_room_sibling(sibling_rows):
    """A steer written for A is delivered to sibling B after A is gone."""
    from models.room import room_id_for_session

    session_a = sibling_rows("test_durability_a")
    session_b = sibling_rows("test_durability_b")
    room_id = room_id_for_session(session_a)
    assert room_id == room_id_for_session(session_b), "siblings must share one Room"

    push_steering_message(session_a.session_id, "do X", "Tom", room_id=room_id)
    session_a.delete()

    delivered = _bare_runner(session_b)._default_steering_pop()
    assert [m["text"] for m in delivered] == ["do X"]
    assert [m["_leg"] for m in delivered] == ["room"]


def test_steer_without_room_id_does_not_reach_room_sibling(sibling_rows):
    """Negative twin: proves the test above measures the Room leg, not an artifact."""
    session_a = sibling_rows("test_durability_neg_a")
    session_b = sibling_rows("test_durability_neg_b")

    push_steering_message(session_a.session_id, "do X", "Tom", room_id=None)
    session_a.delete()

    assert _bare_runner(session_b)._default_steering_pop() == []


def test_stale_steer_does_not_reach_room_sibling(sibling_rows):
    """Staleness twin: a Room entry past the age bound is dropped, not delivered."""
    from models.room import room_id_for_session

    session_a = sibling_rows("test_durability_stale_a")
    session_b = sibling_rows("test_durability_stale_b")
    room_id = room_id_for_session(session_a)

    push_steering_message(session_a.session_id, "fresh", "Tom", room_id=room_id)
    _push_raw(_room_queue_key(room_id), "ancient", timestamp=time.time() - 10_000_000)
    session_a.delete()

    delivered = _bare_runner(session_b)._default_steering_pop()
    assert [m["text"] for m in delivered] == ["fresh"]


def test_requeued_room_steer_still_reaches_room_sibling(sibling_rows):
    """Requeue twin: B drains, wedges without injecting, C still receives it."""
    from models.room import room_id_for_session

    session_a = sibling_rows("test_durability_rq_a")
    session_b = sibling_rows("test_durability_rq_b")
    session_c = sibling_rows("test_durability_rq_c")
    room_id = room_id_for_session(session_a)

    push_steering_message(session_a.session_id, "do X", "Tom", room_id=room_id)
    session_a.delete()

    runner_b = _bare_runner(session_b)
    runner_b._pending_steers = runner_b._default_steering_pop()
    runner_b._requeue_pending_steers()

    assert _texts(_queue_key(session_b.session_id)) == [], "must not demote to B's mortal key"
    delivered = _bare_runner(session_c)._default_steering_pop()
    assert [m["text"] for m in delivered] == ["do X"]


def test_superseded_row_derives_the_live_session_room():
    """A superseded row must not decide the Room the live session drains.

    Driven through ``steer_session``, one of the selections with no status
    filter — a ``superseded`` row cannot appear in a status-filtered result set,
    so those sites cannot exercise this trigger at all.
    """
    from agent.session_executor import steer_session
    from models.agent_session import AgentSession
    from models.room import room_id_for_session

    session_id = _uid("test_superseded_room")
    rows = []
    try:
        old = AgentSession()
        old.session_id = session_id
        old.project_key = TEST_PROJECT
        old.chat_id = "555000111"  # a DIFFERENT Room from the live row's
        old.status = "superseded"
        old.save()
        rows.append(old)

        time.sleep(0.01)  # distinct created_at

        live = AgentSession()
        live.session_id = session_id
        live.project_key = TEST_PROJECT
        live.chat_id = "555999888"
        live.status = "running"
        live.save()
        rows.append(live)

        result = steer_session(session_id, "route me to the live room")
        assert result["success"] is True, result

        live_room = room_id_for_session(live)
        stale_room = room_id_for_session(old)
        assert live_room != stale_room
        assert _texts(_room_queue_key(live_room)) == ["route me to the live room"]
        assert _texts(_room_queue_key(stale_room)) == []
    finally:
        for row in rows:
            try:
                row.delete()
            except Exception:  # pragma: no cover
                pass


def test_created_at_sort_key_is_total():
    """``(created_at is not None, created_at)`` sorts a None row without raising.

    The repo's older ``<field> or 0`` fallback idiom substitutes an int into a
    list of datetimes and raises TypeError here. A single-row list would not
    exercise the comparison at all.
    """
    from datetime import UTC, datetime

    class _S:
        def __init__(self, created_at):
            self.created_at = created_at

    populated = _S(datetime.now(UTC))
    empty = _S(None)
    for rows in ([empty, populated], [populated, empty]):
        ordered = list(rows)
        ordered.sort(key=lambda s: (s.created_at is not None, s.created_at), reverse=True)
        assert ordered[0] is populated


# ── key selection ─────────────────────────────────────────────────────────────


class TestKeySelection:
    def test_room_id_targets_the_room_key(self):
        session_id = _uid("test_keysel_room")
        push_steering_message(session_id, "hello", "Tom", room_id=ROOM)
        assert _texts(_room_queue_key(ROOM)) == ["hello"]
        assert _texts(_queue_key(session_id)) == []

    @pytest.mark.parametrize("room_id", [None, ""])
    def test_falsy_room_id_falls_back_to_legacy(self, room_id):
        session_id = f"test_keysel_falsy_{room_id!r}"
        push_steering_message(session_id, "hello", "Tom", room_id=room_id)
        assert _texts(_queue_key(session_id)) == ["hello"]

    def test_session_without_project_key_derives_no_room(self):
        """The issue's explicit criterion: no project_key → legacy key."""
        from models.room import room_id_for_session

        row = _Row(project_key=None, session_id=_uid("test_keysel_noproject"))
        assert room_id_for_session(row) is None
        push_steering_message(row.session_id, "hello", "Tom", room_id=room_id_for_session(row))
        assert _texts(_queue_key(row.session_id)) == ["hello"]

    def test_room_wins_over_an_empty_session_id(self):
        """``steering:`` is a nonsensical target; the Room key is the right one."""
        push_steering_message("", "hello", "Tom", room_id=ROOM + "-empty")
        assert _texts(_room_queue_key(ROOM + "-empty")) == ["hello"]
        assert _texts(_queue_key("")) == []

    def test_payload_shape_is_unchanged(self):
        session_id = _uid("test_keysel_payload")
        push_steering_message(session_id, "hi", "Tom", room_id=ROOM + "-shape")
        (entry,) = _raw(_room_queue_key(ROOM + "-shape"))
        assert set(entry) == {"text", "sender", "timestamp", "is_abort"}

        push_steering_message(session_id, "hi", "Tom", target_agent="dev", room_id=ROOM + "-shape2")
        (entry,) = _raw(_room_queue_key(ROOM + "-shape2"))
        assert set(entry) == {"text", "sender", "timestamp", "is_abort", "target_agent"}

    def test_originating_push_stamps_now(self):
        session_id = _uid("test_keysel_stamp")
        before = time.time()
        push_steering_message(session_id, "hi", "Tom")
        (entry,) = _raw(_queue_key(session_id))
        assert before <= entry["timestamp"] <= time.time()


# ── abort routing (D4) ────────────────────────────────────────────────────────


class TestAbortRouting:
    def test_abort_routing_explicit_flag_stays_legacy(self):
        session_id = _uid("test_abort_routing_explicit")
        push_steering_message(session_id, "wind it down", "Tom", is_abort=True, room_id=ROOM + "-x")
        assert _texts(_queue_key(session_id)) == ["wind it down"]
        assert _texts(_room_queue_key(ROOM + "-x")) == []

    @pytest.mark.parametrize("keyword", sorted(ABORT_KEYWORDS))
    def test_abort_keyword_detected_stays_legacy(self, keyword):
        """Key selection must sit BELOW the ABORT_KEYWORDS auto-detect.

        Above it, ``is_abort`` reads stale and every keyword-detected abort
        lands on the shared Room key, where it can kill a session that was
        never targeted.
        """
        session_id = f"test_abort_keyword_{keyword}"
        room = f"{ROOM}-{keyword}"
        push_steering_message(session_id, keyword, "Tom", room_id=room)
        assert _texts(_queue_key(session_id)) == [keyword]
        assert _texts(_room_queue_key(room)) == []

    def test_abort_keyword_prefix_still_targets_the_room(self):
        """Only a bare keyword is an abort — a sentence starting with one is not."""
        session_id = _uid("test_abort_keyword_prefix")
        push_steering_message(session_id, "stop the deploy", "Tom", room_id=ROOM + "-prefix")
        assert _texts(_room_queue_key(ROOM + "-prefix")) == ["stop the deploy"]
        assert _texts(_queue_key(session_id)) == []


# ── Room-leg age bound (D5) ───────────────────────────────────────────────────


class TestRoomAgeBound:
    def test_stale_room_entry_dropped_fresh_kept(self):
        session_id = _uid("test_age_drop")
        room = ROOM + "-age"
        _push_raw(_room_queue_key(room), "ancient", timestamp=time.time() - 10_000_000)
        _push_raw(_room_queue_key(room), "fresh")

        drained = pop_all_steering_messages(session_id, room_id=room)
        assert [m["text"] for m in drained] == ["fresh"]
        assert _raw(_room_queue_key(room)) == [], "the drain is destructive on both"

    def test_stale_legacy_entry_is_not_dropped(self):
        """The legacy leg is never filtered — today's behavior is preserved bit for bit."""
        session_id = _uid("test_age_legacy")
        _push_raw(_queue_key(session_id), "ancient", timestamp=time.time() - 10_000_000)
        drained = pop_all_steering_messages(session_id, room_id=ROOM + "-agelegacy")
        assert [m["text"] for m in drained] == ["ancient"]

    def test_peek_skips_stale_room_entry_without_deleting(self):
        """``valor-session status`` must not advertise what the next drain discards."""
        session_id = _uid("test_age_peek")
        room = ROOM + "-agepeek"
        _push_raw(_room_queue_key(room), "ancient", timestamp=time.time() - 10_000_000)
        _push_raw(_room_queue_key(room), "fresh")

        assert [m["text"] for m in peek_steering_messages(session_id, room_id=room)] == ["fresh"]
        # Non-destructive: the stale entry is still physically present, and a
        # second peek still finds the fresh one.
        assert _texts(_room_queue_key(room)) == ["ancient", "fresh"]
        assert [m["text"] for m in peek_steering_messages(session_id, room_id=room)] == ["fresh"]

    @pytest.mark.parametrize("stamp", [None, "not-a-number"])
    def test_undatable_room_entry_is_kept(self, stamp):
        """Fail open: dropping an entry we cannot date would delete steers silently."""
        session_id = _uid("test_age_malformed")
        room = f"{ROOM}-malformed-{stamp}"
        payload = {"text": "keep me", "sender": "Tom", "is_abort": False}
        if stamp is not None:
            payload["timestamp"] = stamp
        _get_redis().rpush(_room_queue_key(room), json.dumps(payload))

        assert [m["text"] for m in peek_steering_messages(session_id, room_id=room)] == ["keep me"]
        assert [m["text"] for m in pop_all_steering_messages(session_id, room_id=room)] == [
            "keep me"
        ]

    def test_bound_is_read_from_settings(self):
        """A named setting, not a literal — so the knob is live and testable."""
        from config.settings import settings

        assert settings.timeouts.steering_room_max_age_s > 0


# ── leg preservation on requeue (D6) ──────────────────────────────────────────


class TestLegPreservation:
    def test_pop_all_stamps_the_source_leg(self):
        session_id = _uid("test_leg_stamp")
        room = ROOM + "-stamp"
        push_steering_message(session_id, "from-legacy", "Tom")
        _push_raw(_room_queue_key(room), "from-room")

        drained = pop_all_steering_messages(session_id, room_id=room)
        assert [(m["text"], m["_leg"]) for m in drained] == [
            ("from-legacy", "legacy"),
            ("from-room", "room"),
        ]

    def test_repush_keeps_a_legacy_sourced_message_on_legacy(self):
        """Anti-laundering: a diagnostic written to legacy never reaches the Room."""
        from agent.health_check import _repush_messages

        session_id = _uid("test_leg_repush_legacy")
        room = ROOM + "-repushlegacy"
        _repush_messages(
            session_id,
            [{"text": "watchdog says", "sender": "watchdog", "_leg": "legacy"}],
            room_id=room,
        )
        assert _texts(_queue_key(session_id)) == ["watchdog says"]
        assert _texts(_room_queue_key(room)) == []

    def test_repush_returns_a_room_sourced_message_to_the_room(self):
        from agent.health_check import _repush_messages

        session_id = _uid("test_leg_repush_room")
        room = ROOM + "-repushroom"
        _repush_messages(
            session_id,
            [{"text": "do X", "sender": "Tom", "_leg": "room"}],
            room_id=room,
        )
        assert _texts(_room_queue_key(room)) == ["do X"]
        assert _texts(_queue_key(session_id)) == []

    def test_repush_without_a_leg_defaults_to_legacy(self):
        """Absent ``_leg`` → legacy, the same fail-safe as absent ``room_id``."""
        from agent.health_check import _repush_messages

        session_id = _uid("test_leg_repush_untagged")
        room = ROOM + "-repushuntagged"
        _repush_messages(session_id, [{"text": "hand built", "sender": "Tom"}], room_id=room)
        assert _texts(_queue_key(session_id)) == ["hand built"]
        assert _texts(_room_queue_key(room)) == []

    @pytest.mark.parametrize(
        "leg,expect_room",
        [("room", True), ("legacy", False), (None, False)],
    )
    def test_runner_push_preserves_the_source_leg(self, leg, expect_room):
        row = _Row(chat_id="777000111", session_id=f"test_leg_runner_{leg}")
        from models.room import room_id_for_session

        room = room_id_for_session(row)
        msg = {"text": "carry me", "sender": "Tom"}
        if leg is not None:
            msg["_leg"] = leg

        _bare_runner(row)._default_steering_push(msg)

        if expect_room:
            assert _texts(_room_queue_key(room)) == ["carry me"]
            assert _texts(_queue_key(row.session_id)) == []
        else:
            assert _texts(_queue_key(row.session_id)) == ["carry me"]
            assert _texts(_room_queue_key(room)) == []

    def test_leg_not_persisted_to_redis(self):
        """``_leg`` is a transient reader stamp — it must never reach Redis."""
        from agent.health_check import _repush_messages

        session_id = _uid("test_leg_not_persisted")
        room = ROOM + "-notpersisted"
        _repush_messages(
            session_id,
            [{"text": "do X", "sender": "Tom", "_leg": "room", "timestamp": 1234.5}],
            room_id=room,
        )
        (entry,) = _raw(_room_queue_key(room))
        assert set(entry) == {"text", "sender", "timestamp", "is_abort"}

    def test_runner_requeue_carries_target_agent(self):
        """The runner used to strip ``target_agent`` on requeue — it must not."""
        row = _Row(chat_id="777000222", session_id=_uid("test_leg_target_agent"))
        from models.room import room_id_for_session

        room = room_id_for_session(row)
        _bare_runner(row)._default_steering_push(
            {"text": "do X", "sender": "Tom", "_leg": "room", "target_agent": "dev"}
        )
        (entry,) = _raw(_room_queue_key(room))
        assert set(entry) == {"text", "sender", "timestamp", "is_abort", "target_agent"}
        assert entry["target_agent"] == "dev"


# ── origination age survives a requeue (D5 + D6) ──────────────────────────────


class TestOriginationAge:
    def test_timestamp_preserved_by_repush(self):
        from agent.health_check import _repush_messages

        session_id = _uid("test_origination_age_repush")
        stamp = time.time() - 500
        _repush_messages(
            session_id,
            [{"text": "do X", "sender": "Tom", "timestamp": stamp}],
        )
        (entry,) = _raw(_queue_key(session_id))
        assert entry["timestamp"] == pytest.approx(stamp)

    def test_timestamp_preserved_by_runner_push(self):
        row = _Row(chat_id="777000333", session_id=_uid("test_timestamp_preserved_runner"))
        stamp = time.time() - 900
        _bare_runner(row)._default_steering_push(
            {"text": "do X", "sender": "Tom", "timestamp": stamp}
        )
        (entry,) = _raw(_queue_key(row.session_id))
        assert entry["timestamp"] == pytest.approx(stamp)

    def test_origination_age_survives_a_drain_and_requeue_cycle(self):
        """A backdated entry still expires on the drain AFTER a requeue.

        Without the timestamp forward, every requeue restarts the clock and a
        message that is repeatedly drained and re-pushed without ever being
        injected stays exactly as immortal as it was before the bound existed.
        """
        from agent.health_check import _repush_messages

        session_id = _uid("test_origination_age_cycle")
        room = ROOM + "-cycle"
        _push_raw(_room_queue_key(room), "ancient", timestamp=time.time() - 10_000_000)

        drained = pop_all_steering_messages(session_id, room_id=room)
        assert drained == [], "the first drain already drops it"

        # Same entry, re-pushed by a consumer that drained it just under the bound.
        near_bound = time.time() - 10_000_000
        _repush_messages(
            session_id,
            [{"text": "ancient", "sender": "Tom", "_leg": "room", "timestamp": near_bound}],
            room_id=room,
        )
        (entry,) = _raw(_room_queue_key(room))
        assert entry["timestamp"] == pytest.approx(near_bound), "requeue must not refresh the clock"
        assert pop_all_steering_messages(session_id, room_id=room) == []

    def test_requeue_of_an_undated_message_does_not_raise(self):
        from agent.health_check import _repush_messages

        session_id = _uid("test_origination_age_undated")
        _repush_messages(session_id, [{"text": "do X", "sender": "Tom"}])
        (entry,) = _raw(_queue_key(session_id))
        assert isinstance(entry["timestamp"], float)


# ── the PostToolUse hook's four re-push paths ─────────────────────────────────


class TestHandleSteeringRepushPaths:
    @pytest.mark.asyncio
    async def test_abort_siblings_return_to_their_source_legs(self):
        """The abort itself dies with the session; siblings go back where they were."""
        from agent.health_check import _handle_steering

        session_id = _uid("test_hook_abort_siblings")
        room = ROOM + "-hookabort"
        push_steering_message(session_id, "diagnostic", "watchdog")  # legacy-sourced
        push_steering_message(session_id, "stop", "Tom")  # auto-detected abort
        _push_raw(_room_queue_key(room), "room instruction")

        result = await _handle_steering(session_id, room_id=room)
        assert "ABORT" in result["hookSpecificOutput"]["additionalContext"]

        assert _texts(_queue_key(session_id)) == ["diagnostic"]
        assert _texts(_room_queue_key(room)) == ["room instruction"]

    @pytest.mark.asyncio
    async def test_non_abort_retry_path_still_forwards_the_room(self, monkeypatch):
        """The retries live inside ``except`` blocks — a missed keyword there is silent."""
        import agent.health_check as hc

        session_id = _uid("test_hook_retry")
        room = ROOM + "-hookretry"
        _push_raw(_room_queue_key(room), "room instruction")

        real = hc._repush_messages
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("primary re-push failed")
            return real(*args, **kwargs)

        monkeypatch.setattr(hc, "_repush_messages", flaky)
        result = await hc._handle_steering(session_id, room_id=room)
        assert result == {"continue_": True}
        assert calls["n"] == 2, "the retry inside the except block must have fired"
        assert _texts(_room_queue_key(room)) == ["room instruction"]

    @pytest.mark.asyncio
    async def test_abort_sibling_retry_path_still_forwards_the_room(self, monkeypatch):
        import agent.health_check as hc

        session_id = _uid("test_hook_abort_retry")
        room = ROOM + "-hookabortretry"
        _push_raw(_room_queue_key(room), "room instruction")
        push_steering_message(session_id, "stop", "Tom")

        real = hc._repush_messages
        calls = {"n": 0}

        def flaky(*args, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("primary re-push failed")
            return real(*args, **kwargs)

        monkeypatch.setattr(hc, "_repush_messages", flaky)
        result = await hc._handle_steering(session_id, room_id=room)
        assert "ABORT" in result["hookSpecificOutput"]["additionalContext"]
        assert calls["n"] == 2
        assert _texts(_room_queue_key(room)) == ["room instruction"]


# ── the deliberately-legacy writers (D1 / D4) ─────────────────────────────────


class TestLegacyByRuleWriters:
    def test_watchdog_loop_break_steer_stays_legacy(self):
        from monitoring.session_watchdog import _inject_watchdog_steer

        session_id = _uid("test_legacy_watchdog")
        assert _inject_watchdog_steer(session_id, "Read", "repeated tool") is True
        assert len(_raw(_queue_key(session_id))) == 1

    def test_terminal_leftover_drain_reads_legacy_only(self):
        """``session_executor``'s teardown drain must not scoop the shared Room leg.

        Its survivors are turned into a *new session*, so draining another
        Room's instruction there converts it into spawned work.
        """
        import ast
        import pathlib

        src = pathlib.Path(__file__).resolve().parents[2] / "agent" / "session_executor.py"
        tree = ast.parse(src.read_text())
        drains = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "pop_all_steering_messages"
        ]
        assert drains, "the terminal leftover drain disappeared"
        for call in drains:
            room_kw = next((kw for kw in call.keywords if kw.arg == "room_id"), None)
            assert room_kw is not None, f"line {call.lineno}: drain must name room_id"
            assert isinstance(room_kw.value, ast.Constant) and room_kw.value.value is None, (
                f"agent/session_executor.py:{call.lineno}: the terminal leftover drain "
                "must pass room_id=None — it feeds _reenqueue_leftover_steering, which "
                "spawns a continuation session."
            )
