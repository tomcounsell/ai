"""Tests for agent/steering.py — self-draft attempt budget helpers.

Covers:
- bump_self_draft_attempts: atomic increment, TTL wiring, distinct post-increment values
- reset_self_draft_attempts: deletes the key
"""

from unittest.mock import patch


class TestSelfDraftAttempts:
    """Tests for bump_self_draft_attempts and reset_self_draft_attempts."""

    def _make_fake_redis(self):
        """Return a fake Redis that mimics INCR / EXPIRE / DELETE for the counter."""
        store = {}

        class FakeRedis:
            def incr(self, key):
                store[key] = store.get(key, 0) + 1
                return store[key]

            def expire(self, key, ttl):
                # Record that expire was called (not needed for logic, but allows assertion).
                self._expire_calls = getattr(self, "_expire_calls", [])
                self._expire_calls.append((key, ttl))

            def delete(self, key):
                store.pop(key, None)

            def get(self, key):
                v = store.get(key)
                return str(v).encode() if v is not None else None

        return FakeRedis(), store

    def test_bump_returns_post_increment_value(self):
        """First bump returns 1, second returns 2, third returns 3."""
        fake_r, _ = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import bump_self_draft_attempts

            assert bump_self_draft_attempts("sess-1") == 1
            assert bump_self_draft_attempts("sess-1") == 2
            assert bump_self_draft_attempts("sess-1") == 3

    def test_bump_ttl_set_only_on_first_bump(self):
        """TTL is set on the first bump (count==1) and not on subsequent bumps."""
        fake_r, _ = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import _SELF_DRAFT_ATTEMPTS_TTL, bump_self_draft_attempts

            bump_self_draft_attempts("sess-ttl")
            assert len(fake_r._expire_calls) == 1
            assert fake_r._expire_calls[0][1] == _SELF_DRAFT_ATTEMPTS_TTL

            bump_self_draft_attempts("sess-ttl")
            # expire should NOT have been called again
            assert len(fake_r._expire_calls) == 1

    def test_bump_distinct_values_under_sequential_calls(self):
        """Sequential bumps produce distinct, monotonically increasing values.

        Goal: self_draft_attempts_atomic_increment requirement from the plan.
        The fake-Redis test validates the functional contract; the production
        Redis INCR is genuinely atomic (documented by Redis).
        """
        fake_r, _ = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import bump_self_draft_attempts

            results = [bump_self_draft_attempts("sess-atomic") for _ in range(5)]

        assert results == [1, 2, 3, 4, 5], "Each bump must return a unique, increasing value"

    def test_reset_deletes_key(self):
        """reset_self_draft_attempts deletes the key so the next bump starts at 1."""
        fake_r, store = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import bump_self_draft_attempts, reset_self_draft_attempts

            bump_self_draft_attempts("sess-reset")
            bump_self_draft_attempts("sess-reset")
            # Key is present at 2
            key = "steering:attempts:sess-reset"
            assert store[key] == 2

            reset_self_draft_attempts("sess-reset")
            # Key is gone
            assert key not in store

            # After reset, next bump starts fresh at 1
            result = bump_self_draft_attempts("sess-reset")
            assert result == 1

    def test_counters_are_per_session_independent(self):
        """Bumps for different sessions do not interfere."""
        fake_r, _ = self._make_fake_redis()

        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import bump_self_draft_attempts

            assert bump_self_draft_attempts("sess-A") == 1
            assert bump_self_draft_attempts("sess-B") == 1
            assert bump_self_draft_attempts("sess-A") == 2
            assert bump_self_draft_attempts("sess-B") == 2

    def test_self_draft_max_attempts_constant(self):
        """SELF_DRAFT_MAX_ATTEMPTS is 2 (matches the plan spec)."""
        from agent.steering import SELF_DRAFT_MAX_ATTEMPTS

        assert SELF_DRAFT_MAX_ATTEMPTS == 2


class TestAC4CounterCleanupDualSeat:
    """Tests for AC4: steering:attempts counter is reset on terminal transitions.

    The dual-seat design ensures cleanup regardless of the emit_telemetry flag:
    - Seat A: inside models/session_lifecycle.py::finalize_session, OUTSIDE the
      emit_telemetry guard (covers completed + any future callers).
    - Seat B: inside agent/session_health.py next to the finalize_telemetry
      dual-seat at ~line 1607-1610 (covers failed/abandoned health-checker paths
      that pass emit_telemetry=False).

    AC4 regression: a reset placed only inside the emit_telemetry block would be
    SKIPPED on every health-checker terminal finalize (all pass emit_telemetry=False),
    leaving the counter on its 1-hour TTL on exactly the failure paths this fix targets.
    """

    def _make_fake_redis(self):
        """Return a fake Redis that tracks INCR / EXPIRE / DELETE calls."""
        store = {}

        class FakeRedis:
            def incr(self, key):
                store[key] = store.get(key, 0) + 1
                return store[key]

            def expire(self, key, ttl):
                pass

            def delete(self, key):
                store.pop(key, None)

            def get(self, key):
                v = store.get(key)
                return str(v).encode() if v is not None else None

        return FakeRedis(), store

    def test_seat_a_resets_counter_on_finalize_session(self):
        """finalize_session() calls reset_self_draft_attempts unconditionally
        (Seat A — outside the emit_telemetry guard), covering the happy-path
        `completed` finalize and any telemetry-on caller.

        We verify the contract end-to-end: bump a counter, then call
        finalize_session (with all side effects patched out) and assert the
        counter was deleted by the Seat A code path.
        """
        from unittest.mock import MagicMock, patch

        from models.session_lifecycle import finalize_session

        fake_r, store = self._make_fake_redis()

        # Seed the counter for our test session.
        with patch("agent.steering._get_redis", return_value=fake_r):
            from agent.steering import bump_self_draft_attempts

            bump_self_draft_attempts("ac4-seat-a")
            assert store.get("steering:attempts:ac4-seat-a") == 1

        session = MagicMock()
        session.session_id = "ac4-seat-a"
        session.status = "running"
        session._saved_field_values = {"status": "running"}
        session.completed_at = None
        session.claude_pid = None

        with (
            # Patch away the Popoto / Redis / telemetry side effects we don't want to exercise.
            patch("models.session_lifecycle.get_authoritative_session", return_value=None),
            patch("models.session_lifecycle.record_telemetry_event", MagicMock(), create=True),
            patch("agent.session_telemetry.record_telemetry_event", MagicMock(), create=True),
            patch("agent.session_telemetry.finalize_session", MagicMock(), create=True),
            patch("models.session_lifecycle.auto_tag_session", MagicMock(), create=True),
            patch("models.session_lifecycle.checkpoint_branch_state", MagicMock(), create=True),
            patch("agent.steering._get_redis", return_value=fake_r),
            patch.object(session, "log_lifecycle_transition", MagicMock()),
            patch.object(session, "save", MagicMock()),
        ):
            try:
                finalize_session(session, "completed", reason="test", emit_telemetry=False)
            except Exception:
                pass  # CAS / import errors are acceptable; we only care about the reset call

        # The counter must have been deleted by Seat A regardless of emit_telemetry.
        assert "steering:attempts:ac4-seat-a" not in store, (
            "Seat A must reset the self-draft counter unconditionally on terminal finalize"
        )

    def test_seat_b_reset_called_on_health_checker_terminal_finalize(self):
        """reset_self_draft_attempts is called in session_health.py next to the
        finalize_telemetry dual-seat (Seat B), covering emit_telemetry=False paths."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.session_health import MAX_RECOVERY_ATTEMPTS, _apply_recovery_transition

        # Build a minimal entry for the failed branch.
        saves: list = []
        entry = SimpleNamespace(
            agent_session_id="ac4-seat-b",
            session_id="sid-ac4-seat-b",
            status="running",
            project_key="test-proj",
            current_tool_name="mcp__svc",
            message_text="request",
            extra_context={},
            chat_id="c",
            telegram_message_id=0,
            recovery_attempts=MAX_RECOVERY_ATTEMPTS - 1,
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
            save=lambda *a, **kw: saves.append(kw),
            push_steering_message=lambda *a, **kw: None,
        )

        reset_calls: list[str] = []

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
            ),
            patch(
                "agent.session_health._deliver_deferred_self_draft_fallback",
                new_callable=AsyncMock,
            ),
            patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
            patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
            patch("models.session_lifecycle.StatusConflictError", Exception),
            patch("agent.agent_session_queue._ensure_worker"),
            patch("agent.session_health._active_events", {}),
            patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
            patch(
                "agent.steering.reset_self_draft_attempts",
                side_effect=lambda sid: reset_calls.append(sid),
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
                    reason="test",
                    reason_kind="tool_timeout",
                    handle=None,
                    worker_key="wk-1",
                )

            asyncio.run(_run())

        assert reset_calls, (
            "reset_self_draft_attempts must be called on the health-checker terminal path "
            "(Seat B AC4 regression: a single emit_telemetry-gated seat misses this path)"
        )
        assert "sid-ac4-seat-b" in reset_calls

    def test_counter_reset_failure_does_not_block_terminal_transition(self):
        """A Redis failure during reset_self_draft_attempts must not block finalization."""
        import asyncio
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch

        from agent.session_health import MAX_RECOVERY_ATTEMPTS, _apply_recovery_transition

        saves: list = []
        entry = SimpleNamespace(
            agent_session_id="ac4-redis-fail",
            session_id="sid-ac4-redis-fail",
            status="running",
            project_key="test-proj",
            current_tool_name="mcp__svc",
            message_text="request",
            extra_context={},
            chat_id="c",
            telegram_message_id=0,
            recovery_attempts=MAX_RECOVERY_ATTEMPTS - 1,
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
            save=lambda *a, **kw: saves.append(kw),
            push_steering_message=lambda *a, **kw: None,
        )

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
            ),
            patch(
                "agent.session_health._deliver_deferred_self_draft_fallback",
                new_callable=AsyncMock,
            ),
            patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
            patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
            patch("models.session_lifecycle.StatusConflictError", Exception),
            patch("agent.agent_session_queue._ensure_worker"),
            patch("agent.session_health._active_events", {}),
            patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
            patch(
                "agent.steering.reset_self_draft_attempts",
                side_effect=RuntimeError("Redis down"),
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
                    reason="test",
                    reason_kind="tool_timeout",
                    handle=None,
                    worker_key="wk-1",
                )

            # Must complete without raising even when reset_self_draft_attempts raises.
            asyncio.run(_run())

        # Terminal transition must still have occurred.
        assert entry.status == "failed", (
            f"Terminal transition must land even when counter reset fails; got {entry.status}"
        )
