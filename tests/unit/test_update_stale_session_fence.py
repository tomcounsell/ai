"""The fence gate in ``/update``'s stale-session cleanup (#2518, D5).

``scripts/update/run.py::_cleanup_stale_sessions`` finalizes ``running`` rows to
``killed`` during ``/update`` Step 5.5. Before this change it decided on
``updated_at`` recency alone while stamping the reason
``"stale cleanup (no live process)"`` — a claim nothing in the function had
checked. The fence now answers that question directly.

Two properties are pinned here, and they are different in kind:

1. **The fence only ever ADDS protection.** It is checked ahead of recency, so a
   fence-live session is skipped at any age; but a fence-verified-DEAD session
   still falls through to the recency and age gates rather than being killed on
   the fence's say-so. That asymmetry is deliberate — the fence can rescue a
   session the recency window would have finalized, and can never finalize one
   the recency window would have spared.

2. **The reason string stops lying.** Only the fence path can assert that no
   live process remains. The recency path says so explicitly.

The return arity changed from ``(killed, skipped_live)`` to
``(killed, skipped_recent, skipped_fence_live)`` so an operator rolling the
fleet sees the fence gate acting instead of inferring it from a total. The
caller's unpack is asserted too — a two-value unpack against a three-tuple
raises at runtime, and ``/update`` catches that exception and logs a bare
``WARN: Session cleanup failed``, so the regression would be silent in practice.

The legacy-row path (no ``pid_create_time`` recorded) is covered in
``tests/unit/test_stale_cleanup.py``; this file adds the fenced cases.
"""

from __future__ import annotations

import inspect
import time
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

#: ``create_time`` recorded on a fenced test row. ``_run_cleanup`` patches
#: ``proc_create_time`` to report a chosen live value against it.
_RECORDED_CT = 1_700_000_000.5

_FENCE_REASON = "stale cleanup (fence verified: recorded process not live)"
_RECENCY_REASON = "stale cleanup (stale heartbeat, liveness unverified)"

#: 30 minutes. A session at 10x this is far outside any recency protection, so
#: a skip at that age can only have come from the fence.
_WAY_PAST_THE_RECENCY_WINDOW = 30 * 60 * 10


def _session(
    *,
    pid=4242,
    create_time=_RECORDED_CT,
    age_seconds=7200,
    updated_at_seconds_ago=_WAY_PAST_THE_RECENCY_WINDOW,
    chat_id="chat-fence",
    session_id="sess-fence",
):
    """A ``running`` row with a fenced execution record.

    ``create_time=None`` produces a legacy row: the fence cannot decide, so the
    function falls back to recency exactly as it did pre-#2518.
    """
    created = datetime.fromtimestamp(time.time() - age_seconds, tz=UTC)
    updated = (
        datetime.fromtimestamp(time.time() - updated_at_seconds_ago, tz=UTC)
        if updated_at_seconds_ago is not None
        else None
    )
    return SimpleNamespace(
        session_id=session_id,
        agent_session_id=f"agent-{session_id}",
        chat_id=chat_id,
        status="running",
        created_at=created,
        updated_at=updated,
        live_fence=({"pid": pid, "create_time": create_time} if pid is not None else None),
        delete=MagicMock(),
        save=MagicMock(),
        log_lifecycle_transition=MagicMock(),
    )


def _run_cleanup(sessions, *, live_ct, age_minutes=120):
    """Run the cleanup once with the real fence and a chosen live ``create_time``.

    ``agent.pid_fence.proc_create_time`` is the seam: the fabricated pids have
    no real process behind them, so without it every fence read is "dead" and
    the fence-live branch is unreachable.

    Returns ``(killed, skipped_recent, skipped_fence_live, finalize_mock)``.
    """
    from scripts.update.run import _cleanup_stale_sessions

    def mock_filter(status=None):
        return sessions if status == "running" else []

    with (
        patch("models.agent_session.AgentSession") as mock_as_class,
        patch("models.session_lifecycle.finalize_session") as mock_finalize,
        patch("agent.pid_fence.proc_create_time", return_value=live_ct),
    ):
        mock_as_class.query.filter.side_effect = mock_filter

        import agent.agent_session_queue as queue_module

        original = getattr(queue_module, "_active_workers", {})
        try:
            queue_module._active_workers = {}
            killed, skipped_recent, skipped_fence_live = _cleanup_stale_sessions(
                Path("/tmp"), age_minutes=age_minutes
            )
        finally:
            queue_module._active_workers = original

    return killed, skipped_recent, skipped_fence_live, mock_finalize


class TestFenceLiveSessionIsProtected:
    def test_fence_live_session_is_skipped_at_ten_times_the_age_window(self):
        """The rescue case: recency would have finalized this row; the fence saves it.

        ``updated_at`` is 5 hours old against a 30-minute window, so nothing but
        the fence can account for the skip.
        """
        session = _session()

        killed, skipped_recent, skipped_fence_live, finalize = _run_cleanup(
            [session], live_ct=_RECORDED_CT
        )

        assert killed == 0
        assert skipped_fence_live == 1, (
            "the fence positively resolved a live process — that must be counted "
            "separately so an operator can see the gate acting"
        )
        assert skipped_recent == 0, "the skip came from the fence, not from recency"
        finalize.assert_not_called()

    def test_fence_is_checked_ahead_of_recency(self):
        """A fence-live session inside the recency window counts as a FENCE skip.

        If the order were reversed, the recency check would absorb it and the
        run summary would under-report the gate — the operator could not tell
        whether the fence was doing anything at all.
        """
        session = _session(updated_at_seconds_ago=60)

        _, skipped_recent, skipped_fence_live, _ = _run_cleanup([session], live_ct=_RECORDED_CT)

        assert skipped_fence_live == 1
        assert skipped_recent == 0

    def test_sub_tolerance_create_time_skew_still_counts_as_live(self):
        from agent.pid_fence import CREATE_TIME_TOLERANCE_S

        session = _session()

        killed, _, skipped_fence_live, _ = _run_cleanup(
            [session], live_ct=_RECORDED_CT + CREATE_TIME_TOLERANCE_S / 2
        )

        assert killed == 0
        assert skipped_fence_live == 1


class TestFenceDeadSession:
    def test_fence_dead_and_stale_is_killed_with_the_fence_reason(self):
        session = _session()

        killed, skipped_recent, skipped_fence_live, finalize = _run_cleanup([session], live_ct=None)

        assert killed == 1
        assert skipped_fence_live == 0
        assert skipped_recent == 0
        finalize.assert_called_once_with(
            session, "killed", reason=_FENCE_REASON, skip_checkpoint=True
        )

    def test_recycled_pid_is_treated_as_dead_not_live(self):
        """An alive pid under a different identity is not our process."""
        session = _session()

        killed, _, skipped_fence_live, finalize = _run_cleanup(
            [session], live_ct=_RECORDED_CT + 5000.0
        )

        assert skipped_fence_live == 0, (
            "a recycled pid must never protect a session — that is the defect, not the guard"
        )
        assert killed == 1
        assert finalize.call_args.kwargs["reason"] == _FENCE_REASON

    def test_fence_dead_but_recently_updated_is_still_spared(self):
        """The fence ADDS protection; it never subtracts it.

        A fence-verified-dead row inside the recency window falls through to the
        recency gate and is spared. Killing here would make ``/update`` more
        aggressive than before, which is not what this change is for.
        """
        session = _session(updated_at_seconds_ago=60)

        killed, skipped_recent, skipped_fence_live, finalize = _run_cleanup([session], live_ct=None)

        assert killed == 0, "a fence-dead verdict must not bypass the recency gate"
        assert skipped_recent == 1
        assert skipped_fence_live == 0
        finalize.assert_not_called()

    def test_fence_dead_but_young_by_created_at_is_still_spared(self):
        """Same asymmetry against the last-resort ``created_at`` gate."""
        session = _session(age_seconds=600, updated_at_seconds_ago=None)

        killed, _, _, finalize = _run_cleanup([session], live_ct=None)

        assert killed == 0
        finalize.assert_not_called()


class TestLegacyRowsKeepTheOldPath:
    def test_legacy_row_stale_is_killed_with_the_unverified_reason(self):
        """No recorded ``create_time`` → unknown → the reason must not overclaim.

        ``proc_create_time`` reports a live value here, so the pid genuinely
        exists; the row simply recorded nothing to compare it against.
        """
        session = _session(create_time=None)

        killed, skipped_recent, skipped_fence_live, finalize = _run_cleanup(
            [session], live_ct=_RECORDED_CT
        )

        assert killed == 1
        assert skipped_fence_live == 0, "an unfenced row cannot produce a fence verdict"
        finalize.assert_called_once_with(
            session, "killed", reason=_RECENCY_REASON, skip_checkpoint=True
        )

    def test_legacy_row_recent_is_skipped_and_counted_as_recent(self):
        session = _session(create_time=None, updated_at_seconds_ago=60)

        killed, skipped_recent, skipped_fence_live, finalize = _run_cleanup(
            [session], live_ct=_RECORDED_CT
        )

        assert killed == 0
        assert skipped_recent == 1
        assert skipped_fence_live == 0
        finalize.assert_not_called()

    def test_row_with_no_fence_at_all_takes_the_recency_path(self):
        session = _session(pid=None)

        killed, _, skipped_fence_live, finalize = _run_cleanup([session], live_ct=_RECORDED_CT)

        assert killed == 1
        assert skipped_fence_live == 0
        assert finalize.call_args.kwargs["reason"] == _RECENCY_REASON


class TestReasonStrings:
    def test_the_retracted_claim_is_gone_from_both_branches(self):
        """``"stale cleanup (no live process)"`` asserted something unchecked."""
        fenced_dead = _session()
        _, _, _, finalize = _run_cleanup([fenced_dead], live_ct=None)
        assert finalize.call_args.kwargs["reason"] == _FENCE_REASON

        legacy = _session(create_time=None)
        _, _, _, finalize = _run_cleanup([legacy], live_ct=_RECORDED_CT)
        assert finalize.call_args.kwargs["reason"] == _RECENCY_REASON

        assert "no live process" not in _FENCE_REASON + _RECENCY_REASON

    def test_the_fence_reason_says_what_verified_it(self):
        assert "fence verified" in _FENCE_REASON

    def test_the_recency_reason_admits_liveness_was_not_checked(self):
        assert "liveness unverified" in _RECENCY_REASON


class TestMixedBatchCounting:
    def test_each_session_lands_in_exactly_one_bucket(self):
        """Three rows, three outcomes, counted independently in one pass.

        Each row carries a distinct pid so the ``proc_create_time`` seam can
        answer differently per row, which is what makes the three buckets
        separable rather than three runs stitched together.
        """
        live = _session(session_id="live", chat_id="c1", pid=1001)
        dead = _session(session_id="dead", chat_id="c2", pid=1002)
        legacy_recent = _session(
            session_id="legacy",
            chat_id="c3",
            pid=1003,
            create_time=None,
            updated_at_seconds_ago=60,
        )

        from scripts.update.run import _cleanup_stale_sessions

        def mock_filter(status=None):
            return [live, dead, legacy_recent] if status == "running" else []

        with (
            patch("models.agent_session.AgentSession") as mock_as_class,
            patch("models.session_lifecycle.finalize_session") as finalize,
            patch(
                "agent.pid_fence.proc_create_time",
                side_effect=lambda pid: _RECORDED_CT if pid == 1001 else None,
            ),
        ):
            mock_as_class.query.filter.side_effect = mock_filter
            import agent.agent_session_queue as queue_module

            original = getattr(queue_module, "_active_workers", {})
            try:
                queue_module._active_workers = {}
                counts = _cleanup_stale_sessions(Path("/tmp"), age_minutes=120)
            finally:
                queue_module._active_workers = original

        assert counts == (1, 1, 1), "(killed, skipped_recent, skipped_fence_live)"
        assert finalize.call_count == 1
        assert finalize.call_args.args[0] is dead


class TestCallerUnpacksThreeValues:
    def test_run_update_unpacks_the_new_arity(self):
        """A two-value unpack raises, and ``/update`` swallows it into a WARN.

        The failure would be silent in production: the whole cleanup step is
        wrapped in ``try/except Exception`` that logs
        ``WARN: Session cleanup failed`` and continues, so no session would ever
        be cleaned and nothing would look broken.
        """
        from scripts.update import run as run_module

        source = inspect.getsource(run_module.run_update)
        assert (
            "stale_killed, skipped_recent, skipped_fence_live = _cleanup_stale_sessions(" in source
        ), "run_update must unpack all three return values"

    def test_function_signature_declares_a_three_tuple(self):
        from scripts.update.run import _cleanup_stale_sessions

        annotation = inspect.signature(_cleanup_stale_sessions).return_annotation
        assert annotation in ("tuple[int, int, int]", tuple[int, int, int])

    def test_run_update_logs_the_fence_skip_distinctly(self):
        """The operator-facing half of the arity change."""
        from scripts.update import run as run_module

        source = inspect.getsource(run_module.run_update)
        assert "fence verified" in source, (
            "the run summary must distinguish fence-live skips from recency "
            "skips, or the operator cannot see the gate working"
        )


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(pytest.main([__file__, "-v"]))
