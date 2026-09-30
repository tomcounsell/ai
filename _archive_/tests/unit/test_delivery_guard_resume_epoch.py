"""Tests for the delivery-guard resume epoch scoping fix.

The health-check "already delivered" guard (issue #918) finalizes a
``running`` session instead of recovering it to ``pending`` when
``response_delivered_at`` is set — this prevents duplicate Telegram
delivery. But field-presence alone is not sufficient: if a session is
resumed after a crash, ``started_at`` is re-stamped fresh on pickup while a
stale ``response_delivered_at`` from the PRIOR run may still be sitting on
the record. Naively finalizing on presence alone would wrongly finalize a
genuinely-stuck CURRENT run just because a PRIOR run delivered a response.

``_delivery_belongs_to_current_run(entry)`` fixes this by comparing
``response_delivered_at`` against the run's start anchor
(``started_at``, falling back to ``created_at``): the delivery only
"counts" for the guard if it falls at or after that anchor.

Tests:
1. Pure helper unit tests (fast, no mocking) covering all six required cases.
2. Integration tests against ``_apply_recovery_transition`` confirming:
   - a stale prior-run delivery does NOT force-finalize (falls through to
     the normal requeue path instead)
   - a genuine same-run delivery DOES force-finalize
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from agent.session_health import _delivery_belongs_to_current_run


def _dt(seconds_ago: float) -> datetime:
    return datetime.now(tz=UTC) - timedelta(seconds=seconds_ago)


# ==========================================================================
# Pure helper tests — _delivery_belongs_to_current_run
# ==========================================================================


class TestDeliveryBelongsToCurrentRun:
    """Direct tests of the epoch-comparison helper (no mocking needed)."""

    def test_resumed_session_delivery_before_started_at_returns_false(self):
        """Delivered in a prior run, then resumed (started_at re-stamped
        fresh, AFTER the stale delivery) → False. This is the core
        regression case: the guard must not fire for a stale prior-run
        delivery."""
        started_at = _dt(10)  # re-stamped fresh on pickup, 10s ago
        response_delivered_at = _dt(3600)  # delivered an hour ago, prior run

        entry = SimpleNamespace(
            response_delivered_at=response_delivered_at,
            started_at=started_at,
            created_at=None,
        )

        assert _delivery_belongs_to_current_run(entry) is False

    def test_boundary_equal_timestamps_returns_true(self):
        """response_delivered_at == started_at exactly → True (inclusive >=)."""
        now = _dt(0)

        entry = SimpleNamespace(
            response_delivered_at=now,
            started_at=now,
            created_at=None,
        )

        assert _delivery_belongs_to_current_run(entry) is True

    def test_same_run_delivery_after_started_at_returns_true(self):
        """Normal case: delivered after the run started → True."""
        started_at = _dt(60)
        response_delivered_at = _dt(10)  # delivered later than started_at

        entry = SimpleNamespace(
            response_delivered_at=response_delivered_at,
            started_at=started_at,
            created_at=None,
        )

        assert _delivery_belongs_to_current_run(entry) is True

    def test_legacy_no_anchor_at_all_returns_true(self):
        """Both started_at and created_at are None (truly legacy row) →
        True, preserving the original always-fire behavior."""
        entry = SimpleNamespace(
            response_delivered_at=_dt(5),
            started_at=None,
            created_at=None,
        )

        assert _delivery_belongs_to_current_run(entry) is True

    def test_response_delivered_at_none_returns_false(self):
        """No delivery recorded at all → False regardless of anchors."""
        entry = SimpleNamespace(
            response_delivered_at=None,
            started_at=_dt(10),
            created_at=None,
        )

        assert _delivery_belongs_to_current_run(entry) is False

    def test_garbage_response_delivered_at_returns_false(self):
        """Unparseable response_delivered_at (a plain non-timestamp string,
        which _ts() cannot coerce and returns None for) → False. The helper
        must not raise; the defensive direction on ambiguous data is to
        skip finalize."""
        entry = SimpleNamespace(
            response_delivered_at="not-a-timestamp",
            started_at=_dt(10),
            created_at=None,
        )

        # Must not raise.
        assert _delivery_belongs_to_current_run(entry) is False

    def test_created_at_fallback_used_when_started_at_none(self):
        """started_at=None but created_at set → created_at is the anchor."""
        created_at = _dt(60)
        response_delivered_at = _dt(3600)  # before created_at → stale

        entry = SimpleNamespace(
            response_delivered_at=response_delivered_at,
            started_at=None,
            created_at=created_at,
        )

        assert _delivery_belongs_to_current_run(entry) is False

    def test_created_at_fallback_delivery_after_returns_true(self):
        """started_at=None, created_at set, delivery after created_at → True."""
        created_at = _dt(60)
        response_delivered_at = _dt(10)

        entry = SimpleNamespace(
            response_delivered_at=response_delivered_at,
            started_at=None,
            created_at=created_at,
        )

        assert _delivery_belongs_to_current_run(entry) is True


# ==========================================================================
# Integration tests — _apply_recovery_transition
# ==========================================================================


def _make_entry(**overrides):
    """Minimal AgentSession-like stub for _apply_recovery_transition,
    following the convention in test_session_health_subprocess_kill.py."""
    defaults = {
        "agent_session_id": "sess-epoch-1",
        "session_id": "sid-epoch-1",
        "project_key": "test-proj-epoch",
        "chat_id": "chat-1",
        "claude_pid": 4321,
        "claude_session_uuid": None,
        "recovery_attempts": 0,
        "reprieve_count": 0,
        "priority": "normal",
        "started_at": _dt(10),
        "created_at": _dt(20),
        "response_delivered_at": None,
        "exit_returncode": 0,
        "is_project_keyed": False,
        "save": lambda **kw: None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.fixture
def recovery_patches():
    """Patch the lifecycle helpers and worker-ensure side effects, following
    the convention in test_session_health_subprocess_kill.py."""
    import agent.session_health as session_health

    with (
        patch("models.session_lifecycle.finalize_session") as mock_finalize,
        patch("models.session_lifecycle.transition_status") as mock_transition,
        patch.object(session_health, "_tier2_reprieve_signal", return_value=None),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("popoto.redis_db.POPOTO_REDIS_DB"),
        patch.object(
            session_health, "_deliver_deferred_self_draft_fallback", new_callable=AsyncMock
        ),
        patch.object(
            session_health, "_deliver_terminal_interrupt_notice", new_callable=AsyncMock
        ) as mock_terminal,
    ):
        yield {
            "finalize": mock_finalize,
            "transition": mock_transition,
            "terminal_notice": mock_terminal,
        }


def _run_recovery(entry):
    import agent.session_health as session_health

    return asyncio.run(
        session_health._apply_recovery_transition(
            entry,
            reason="no progress",
            reason_kind="no_progress",
            handle=None,
            worker_key="worker-1",
        )
    )


class TestApplyRecoveryTransitionDeliveryGuard:
    """Integration coverage: the delivery guard inside _apply_recovery_transition
    must respect epoch scoping, not bare field presence."""

    def test_resumed_prior_run_delivery_does_not_force_finalize(self, recovery_patches):
        """response_delivered_at from a PRIOR run (before the current
        started_at) must NOT force-finalize the session via the delivery
        guard. The recovery instead falls through to the normal
        no-progress/subprocess-kill path."""
        import agent.session_health as session_health

        entry = _make_entry(
            started_at=_dt(10),  # re-stamped fresh on pickup
            response_delivered_at=_dt(3600),  # stale delivery from a prior run
        )

        killed = session_health.SubprocessKillResult(confirmed_dead=True, signal_sent=True)
        with patch.object(session_health, "_confirm_subprocess_dead", return_value=killed):
            _run_recovery(entry)

        # The delivery guard must not have fired: no finalize_session call
        # with the "already delivered" reason.
        for call in recovery_patches["finalize"].call_args_list:
            reason = call.kwargs.get("reason", "")
            assert "already delivered" not in reason, (
                "Delivery guard incorrectly force-finalized a stale "
                "prior-run delivery on a resumed session"
            )
        # Normal requeue path should have run instead.
        recovery_patches["transition"].assert_called_once()
        assert recovery_patches["transition"].call_args.args[1] == "pending"

    def test_genuine_same_run_delivery_does_force_finalize(self, recovery_patches):
        """response_delivered_at set AFTER this run's started_at (a genuine
        same-run stuck-after-delivery session) DOES get finalized as
        completed by the delivery guard, without reaching the
        subprocess-kill/requeue logic at all."""
        entry = _make_entry(
            started_at=_dt(60),
            response_delivered_at=_dt(5),  # delivered after this run started
        )

        result = _run_recovery(entry)

        assert result is True
        recovery_patches["finalize"].assert_called_once()
        args, kwargs = recovery_patches["finalize"].call_args
        assert args[1] == "completed"
        assert "already delivered" in kwargs.get("reason", "")
        # Must NOT have reached the requeue path.
        recovery_patches["transition"].assert_not_called()
