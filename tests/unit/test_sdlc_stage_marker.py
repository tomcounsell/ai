"""Unit tests for tools.sdlc_stage_marker.

Tests cover:
- write_marker's lease-based tri-state degradation contract (D7, rebuilt
  around the issue-keyed PipelineLedger for issue #2012 task 2)
- The peek -> resolve target_repo -> revalidate (non-peek) -> write sequence
- CLI --issue-number / --run-id argument parsing

There is no session in this path anymore: ``find_session``,
``session_owns_issue``, and the AgentSession-ownership guard were removed.
Ownership is decided SOLELY by the run_id-keyed issue lease
(``models.session_lifecycle.touch_issue_lock``).

Lease-helper patches here target ``tools._sdlc_utils``, the module that owns
the helpers, because ``tools.sdlc_stage_marker`` calls them through the
module object rather than snapshotting them into its own globals (#2637).
The binding shape itself is guarded in
``tests/unit/test_sdlc_lease_helper_binding.py``.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


class TestWriteMarker:
    """Tests for write_marker's tri-state degradation contract (D7).

    write_marker returns ``(result, exit_code)``:
    - degraded (Redis absent) / success / idempotent → exit 0
    - lease absent/foreign/repo-less, or a genuine write failure → exit 1
    """

    def test_rejects_invalid_stage(self):
        from tools.sdlc_stage_marker import write_marker

        result, code = write_marker(stage="BOGUS", status="completed")
        assert result == {}
        assert code == 0

    def test_rejects_invalid_status(self):
        from tools.sdlc_stage_marker import write_marker

        result, code = write_marker(stage="PLAN", status="bogus")
        assert result == {}
        assert code == 0

    def test_absent_substrate_emits_degraded_marker_exit_0(self):
        """ABSENT: Redis probe fails → degraded marker, exit 0 (quiet)."""
        from tools.sdlc_stage_marker import SUBSTRATE_ABSENT, write_marker

        with patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_ABSENT):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 0
        assert result["status"] == "degraded"
        assert "substrate absent" in result["reason"]
        assert result["stage"] == "PLAN"

    def test_missing_issue_number_hard_fails_lease_absent(self, capsys):
        """No --issue-number at all → LEASE_ABSENT, exit 1 (loud)."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        with patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT):
            result, code = write_marker(stage="PLAN", status="completed", run_id="run-1")

        assert code == 1
        assert result["error"] == "lease_absent"
        assert "LEASE_ABSENT" in capsys.readouterr().err

    def test_missing_run_id_hard_fails_lease_absent(self, capsys):
        """No run_id at all (Python API caller) → LEASE_ABSENT, exit 1 (loud)."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        with patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=1)

        assert code == 1
        assert result["error"] == "lease_absent"
        assert "LEASE_ABSENT" in capsys.readouterr().err

    def test_unheld_lease_hard_fails(self, capsys):
        """PRESENT_NO_SESSION's replacement: an unheld lock (no established
        lease for this run_id at all) is now LOUD, not a quiet no-op."""
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_touch = MagicMock(
            return_value=IssueLockResult(acquired=True, owner_session_id=None, owner_run_id=None)
        )

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=1959, run_id="run-1959"
            )

        assert code == 1
        assert result["error"] == "lease_absent"
        assert "LEASE_ABSENT" in capsys.readouterr().err

    def test_foreign_run_id_refused_issue_locked(self, capsys):
        """A foreign run holding the issue lock refuses the marker write with
        the ISSUE_LOCKED shape (exit 1) -- the owning run_id and session_id
        are surfaced. No PipelineStateMachine write is ever attempted."""
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=False,
                owner_session_id="other-session",
                owner_run_id="foreign-run",
            )
        )
        write_mock = MagicMock()

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_state.PipelineStateMachine", write_mock),
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=1955, run_id="intruder-run"
            )

        assert code == 1
        assert result["error"] == "issue_locked"
        assert result["reason"] == "ISSUE_LOCKED"
        assert result["owner_run_id"] == "foreign-run"
        assert result["owner_session_id"] == "other-session"
        write_mock.assert_not_called()  # no state-machine write attempted
        assert "ISSUE_LOCKED" in capsys.readouterr().err

    def test_target_repo_missing_hard_fails_never_writes(self, capsys):
        """Risk 5 (writer side): a valid lease with NO pinned target_repo
        must hard-fail and never construct a PipelineLedger key with a None
        component."""
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id="run-1", target_repo=None
            )
        )
        write_mock = MagicMock()

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_state.PipelineStateMachine", write_mock),
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=1960, run_id="run-1"
            )

        assert code == 1
        assert result["error"] == "target_repo_missing"
        write_mock.assert_not_called()
        assert "TARGET_REPO_MISSING" in capsys.readouterr().err

    def test_present_write_failed_exits_1_loud(self, capsys):
        """A resolved lease but a raising state machine construction → exit 1.

        Issue #2554: "loud" is now asserted rather than asserted-in-the-name.
        The blanket handler used to route the exception to logger.debug and
        return a bare `{}`, so a crashed write was indistinguishable from a
        silent no-op.
        """
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id="run-1", target_repo="o/r"
            )
        )

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch(
                "agent.pipeline_state.PipelineStateMachine.for_issue",
                side_effect=RuntimeError("boom"),
            ),
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=1, run_id="run-1"
            )

        assert code == 1
        assert result["reason"] == "STATE_MACHINE_RAISED"
        err = capsys.readouterr().err
        assert "STATE_MACHINE_RAISED" in err
        # The concrete exception must survive to the operator, not be summarized away.
        assert "RuntimeError" in err and "boom" in err

    def test_present_start_stage_rejected_exits_1(self, capsys):
        """start_stage raising ValueError (misorder) → exit 1.

        Issue #2554: the ValueError's message is the actionable part (it names
        the offending predecessor and the remedy) and used to be dropped at
        logger.debug. It must reach stderr.
        """
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id="run-1", target_repo="o/r"
            )
        )
        mock_sm = MagicMock()
        mock_sm.start_stage.side_effect = ValueError("predecessor not completed")

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
        ):
            result, code = write_marker(
                stage="REVIEW", status="in_progress", issue_number=1, run_id="run-1"
            )

        assert code == 1
        assert result["reason"] == "STATE_MACHINE_REJECTED"
        err = capsys.readouterr().err
        assert "STATE_MACHINE_REJECTED" in err
        assert "predecessor not completed" in err

    def test_fresh_plan_in_progress_backfills_and_persists(self):
        """First-write-at-PLAN acceptance (#1916): a fresh ledger (ISSUE=ready)
        must NOT be rejected — start_stage is called with
        backfill_predecessors=True against a real ledger-backed
        PipelineStateMachine."""
        from agent.pipeline_state import PipelineStateMachine
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id="run-1", target_repo="o/r"
            )
        )

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
        ):
            result, code = write_marker(
                stage="PLAN", status="in_progress", issue_number=194601, run_id="run-1"
            )

        assert code == 0
        assert result == {"stage": "PLAN", "status": "in_progress"}
        sm = PipelineStateMachine.for_issue("o/r", 194601)
        assert sm.states["ISSUE"] == "completed"
        assert sm.states["PLAN"] == "in_progress"

    def test_idempotent_already_completed_exit_0(self):
        """Idempotent already-completed path stays exit 0 and never
        re-validates the lease (no write needed)."""
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id="run-1", target_repo="o/r"
            )
        )
        mock_sm = MagicMock()
        mock_sm.states = {"PLAN": "completed"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=1, run_id="run-1"
            )

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        mock_sm.complete_stage.assert_not_called()
        # Idempotent no-op — only the initial peek touches the lock, never a
        # second (non-peek, revalidation) call.
        assert mock_touch.call_count == 1

    def test_successful_write_revalidates_lease_before_write(self):
        """TOCTOU close (Risk 5): the write must call touch_issue_lock a
        SECOND time (non-peek) with the resolved target_repo, immediately
        before the actual state-machine mutation -- not just the initial
        peek."""
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_touch = MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id="run-1954", target_repo="o/r"
            )
        )
        mock_sm = MagicMock()
        mock_sm.states = {"PLAN": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=1954, run_id="run-1954"
            )

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        mock_sm.complete_stage.assert_called_once_with("PLAN")
        # Two lock touches: the read-only ownership peek, then the
        # non-peek revalidation immediately before the write.
        assert mock_touch.call_count == 2
        peek_calls = [c for c in mock_touch.call_args_list if c.kwargs.get("peek")]
        revalidate_calls = [c for c in mock_touch.call_args_list if not c.kwargs.get("peek")]
        assert len(peek_calls) == 1
        assert len(revalidate_calls) == 1
        args, kwargs = revalidate_calls[0]
        assert args[0] == 1954
        assert args[1] == "run-1954"
        assert kwargs.get("target_repo") == "o/r"

    def test_lease_lost_between_peek_and_write_refuses(self, capsys):
        """The revalidation (non-peek) call fails -- a foreign run took the
        lease in the gap between peek and write. The write must be
        refused, never attempted."""
        from models.session_lifecycle import IssueLockResult
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        peek_result = IssueLockResult(
            acquired=True, owner_session_id="s", owner_run_id="run-1", target_repo="o/r"
        )
        revalidate_result = IssueLockResult(
            acquired=False, owner_session_id="other", owner_run_id="foreign-run"
        )
        mock_touch = MagicMock(side_effect=[peek_result, revalidate_result])
        mock_sm = MagicMock()
        mock_sm.states = {"PLAN": "pending"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", mock_touch),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
        ):
            result, code = write_marker(
                stage="PLAN", status="in_progress", issue_number=1, run_id="run-1"
            )

        assert code == 1
        assert result["error"] == "lease_lost"
        mock_sm.start_stage.assert_not_called()
        assert "ISSUE_LOCKED" in capsys.readouterr().err


class TestColdIssueMarkerSessionless:
    """A cold ``--stage ISSUE`` marker (no --run-id) must be written
    sessionlessly — never by fresh-minting a runnable ``sdlc-local-{N}``
    pipeline anchor from nothing.

    Regression for the incident where ``/do-issue`` filing an issue spawned a
    live-looking eng SDLC session (status=running, seeded with "Run the full
    SDLC pipeline") the instant the ISSUE completion marker was written, before
    any human decided to plan it. ``write_issue_marker_cold`` records the ledger
    marker via the issue lease directly, creating no AgentSession.
    """

    def _no_session_for(self, issue_number: int) -> bool:
        from models.agent_session import AgentSession

        rows = list(AgentSession.query.filter(session_id=f"sdlc-local-{issue_number}"))
        return len(rows) == 0

    def test_cold_issue_completed_writes_ledger_without_spawning_session(self):
        """The core acceptance: cold ISSUE completed persists ISSUE=completed to
        the ledger, creates NO sdlc-local session, and leaves the lease free."""
        from agent.pipeline_state import PipelineStateMachine
        from models.session_lifecycle import touch_issue_lock
        from tools.sdlc_stage_marker import write_issue_marker_cold

        issue = 970001
        assert self._no_session_for(issue)

        with (
            patch("tools._sdlc_utils._resolve_target_repo", return_value="o/r"),
        ):
            result, code = write_issue_marker_cold("completed", issue)

        assert code == 0
        assert result == {"stage": "ISSUE", "status": "completed"}
        # Ledger marker landed — sdlc-tool stage-query would show it.
        sm = PipelineStateMachine.for_issue("o/r", issue)
        assert sm.states["ISSUE"] == "completed"
        # No phantom pipeline anchor was spawned.
        assert self._no_session_for(issue)
        # Lease released — a later /do-plan session-ensure acquires cleanly.
        assert touch_issue_lock(issue, "probe", peek=True).acquired is True

    def test_cold_issue_in_progress_also_sessionless(self):
        """The in_progress marker (/do-issue Step 6) is likewise sessionless."""
        from agent.pipeline_state import PipelineStateMachine
        from tools.sdlc_stage_marker import write_issue_marker_cold

        issue = 970002
        with patch("tools._sdlc_utils._resolve_target_repo", return_value="o/r"):
            result, code = write_issue_marker_cold("in_progress", issue)

        assert code == 0
        assert result == {"stage": "ISSUE", "status": "in_progress"}
        sm = PipelineStateMachine.for_issue("o/r", issue)
        assert sm.states["ISSUE"] == "in_progress"
        assert self._no_session_for(issue)

    def test_cold_issue_writes_under_foreign_owner_when_lease_held(self):
        """When a live run already owns the lease, write under ITS run_id (the
        idempotent ISSUE marker still lands) and never release its lease."""
        from models.session_lifecycle import IssueLockResult
        from tools import sdlc_stage_marker

        held = IssueLockResult(
            acquired=False,
            owner_session_id="live-sess",
            owner_run_id="live-run",
            target_repo="o/r",
        )
        mock_write = MagicMock(return_value=({"stage": "ISSUE", "status": "completed"}, 0))
        mock_release = MagicMock()

        with (
            patch("tools._sdlc_utils._resolve_target_repo", return_value="o/r"),
            patch("models.session_lifecycle.touch_issue_lock", return_value=held),
            patch("models.session_lifecycle.release_issue_lock", mock_release),
            patch.object(sdlc_stage_marker, "write_marker", mock_write),
        ):
            result, code = sdlc_stage_marker.write_issue_marker_cold("completed", 970003)

        assert code == 0
        assert result == {"stage": "ISSUE", "status": "completed"}
        # Wrote under the live owner's identity, not a fresh mint.
        assert mock_write.call_args.kwargs["run_id"] == "live-run"
        # The foreign lease is not ours — never release it.
        mock_release.assert_not_called()

    def test_cold_issue_releases_own_lease_after_write(self):
        """When we acquire the free lease, we release it after writing so it
        never lingers to block the next run."""
        from models.session_lifecycle import IssueLockResult
        from tools import sdlc_stage_marker

        acquired = IssueLockResult(
            acquired=True, owner_session_id="s", owner_run_id="mine", target_repo="o/r"
        )
        mock_write = MagicMock(return_value=({"stage": "ISSUE", "status": "completed"}, 0))
        mock_release = MagicMock()

        with (
            patch("tools._sdlc_utils._resolve_target_repo", return_value="o/r"),
            patch("models.session_lifecycle.touch_issue_lock", return_value=acquired),
            patch("models.session_lifecycle.release_issue_lock", mock_release),
            patch.object(sdlc_stage_marker, "write_marker", mock_write),
        ):
            sdlc_stage_marker.write_issue_marker_cold("completed", 970004)

        # Released under the fresh run_id we acquired with.
        assert mock_release.call_count == 1
        assert mock_release.call_args.args[0] == 970004

    def test_cold_issue_no_issue_number_refuses(self):
        """No issue number → nothing to key the ledger on → RUN_ID_REQUIRED."""
        from tools.sdlc_stage_marker import write_issue_marker_cold

        result, code = write_issue_marker_cold("completed", None)
        assert code == 2
        assert result["error"] == "RUN_ID_REQUIRED"

    def test_main_routes_cold_issue_to_sessionless_not_heal(self):
        """main() sends a cold ISSUE marker to the sessionless writer and NEVER
        the self-heal (which would fresh-mint a session)."""
        from tools import sdlc_stage_marker

        mock_cold = MagicMock(return_value=({"stage": "ISSUE", "status": "completed"}, 0))
        mock_heal = MagicMock()
        argv = [
            "sdlc_stage_marker",
            "--stage",
            "ISSUE",
            "--status",
            "completed",
            "--issue-number",
            "970005",
        ]
        with (
            patch.object(sdlc_stage_marker, "write_issue_marker_cold", mock_cold),
            patch.object(sdlc_stage_marker, "heal_missing_run_id", mock_heal),
            patch.object(sys, "argv", argv),
        ):
            try:
                sdlc_stage_marker.main()
            except SystemExit as e:
                assert e.code == 0
        mock_cold.assert_called_once_with("completed", 970005)
        mock_heal.assert_not_called()

    def test_main_issue_with_run_id_skips_sessionless_path(self):
        """An ISSUE marker that carries --run-id is NOT cold: it bypasses the
        sessionless writer and uses the normal write_marker path unchanged."""
        from tools import sdlc_stage_marker

        mock_cold = MagicMock()
        mock_heal = MagicMock()
        mock_write = MagicMock(return_value=({"stage": "ISSUE", "status": "completed"}, 0))
        argv = [
            "sdlc_stage_marker",
            "--stage",
            "ISSUE",
            "--status",
            "completed",
            "--issue-number",
            "970007",
            "--run-id",
            "run-970007",
        ]
        with (
            patch.object(sdlc_stage_marker, "write_issue_marker_cold", mock_cold),
            patch.object(sdlc_stage_marker, "heal_missing_run_id", mock_heal),
            patch.object(sdlc_stage_marker, "write_marker", mock_write),
            patch.object(sys, "argv", argv),
        ):
            try:
                sdlc_stage_marker.main()
            except SystemExit:
                pass
        # run_id present → neither the cold sessionless writer nor the heal fire.
        mock_cold.assert_not_called()
        mock_heal.assert_not_called()
        assert mock_write.call_args.kwargs["run_id"] == "run-970007"

    def test_main_cold_non_issue_stage_still_heals(self):
        """A cold non-ISSUE marker (e.g. DOCS) keeps the normal self-heal path —
        the sessionless writer is scoped to the ISSUE stage only."""
        from tools import sdlc_stage_marker

        mock_cold = MagicMock()
        # Heal fails → RUN_ID_REQUIRED exit 2 (we only assert routing, not mint).
        mock_heal = MagicMock(return_value=None)
        argv = [
            "sdlc_stage_marker",
            "--stage",
            "DOCS",
            "--status",
            "completed",
            "--issue-number",
            "970006",
        ]
        with (
            patch.object(sdlc_stage_marker, "write_issue_marker_cold", mock_cold),
            patch.object(sdlc_stage_marker, "heal_missing_run_id", mock_heal),
            patch.object(sys, "argv", argv),
        ):
            try:
                sdlc_stage_marker.main()
            except SystemExit:
                pass
        mock_cold.assert_not_called()
        mock_heal.assert_called_once()


class TestReviewCompletedVerdictGate:
    """WS3c (#2062): the REVIEW ``completed`` marker is unwritable without a
    readable substrate verdict. A fork that posts a GitHub APPROVED but skips
    ``verdict record`` can no longer mark REVIEW completed -- the refusal
    leaves the no-verdict state the WS3b recovery row owns (re-dispatch
    /do-pr-review), never a deadlock."""

    @staticmethod
    def _live_lock(run_id="run-r"):
        from models.session_lifecycle import IssueLockResult

        return MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id=run_id, target_repo="o/r"
            )
        )

    def test_review_completed_with_no_readable_verdict_refuses_named(self, capsys):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"REVIEW": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._review_verdict_readable", return_value=False),
        ):
            result, code = write_marker(
                stage="REVIEW", status="completed", issue_number=2062, run_id="run-r"
            )

        assert code == 1
        assert result["error"] == "review_verdict_missing"
        assert result["reason"] == "REVIEW_VERDICT_MISSING"
        # The marker write must never happen.
        mock_sm.complete_stage.assert_not_called()
        # Named, observable stderr diagnostic (not a silent swallow).
        err = capsys.readouterr().err
        assert "REVIEW_VERDICT_MISSING" in err

    def test_review_completed_with_readable_verdict_and_artifact_writes(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"REVIEW": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._review_verdict_readable", return_value=True),
            patch("tools.sdlc_stage_marker._review_trailer_present", return_value=True),
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=True),
        ):
            result, code = write_marker(
                stage="REVIEW", status="completed", issue_number=2062, run_id="run-r"
            )

        assert code == 0
        assert result == {"stage": "REVIEW", "status": "completed"}
        mock_sm.complete_stage.assert_called_once_with("REVIEW")

    def test_review_completed_approved_without_trailer_refuses_named(self, capsys):
        """Issue #2193: an APPROVED verdict readable-but-trailerless previously
        slipped past the truthiness-only ``_review_verdict_readable`` check.
        The new ``_review_trailer_present`` conjunct refuses it by name."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"REVIEW": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._review_verdict_readable", return_value=True),
            patch("tools.sdlc_stage_marker._review_trailer_present", return_value=False),
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=True),
        ):
            result, code = write_marker(
                stage="REVIEW", status="completed", issue_number=2062, run_id="run-r"
            )

        assert code == 1
        assert result["error"] == "review_trailer_missing"
        assert result["reason"] == "REVIEW_TRAILER_MISSING"
        mock_sm.complete_stage.assert_not_called()
        assert "REVIEW_TRAILER_MISSING" in capsys.readouterr().err

    def test_review_completed_approved_with_trailer_writes(self):
        """Regression: an APPROVED verdict WITH a well-formed trailer still
        passes the gate exactly as the pre-#2193 cases did."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"REVIEW": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._review_verdict_readable", return_value=True),
            patch("tools.sdlc_stage_marker._review_trailer_present", return_value=True),
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=True),
        ):
            result, code = write_marker(
                stage="REVIEW", status="completed", issue_number=2062, run_id="run-r"
            )

        assert code == 0
        assert result == {"stage": "REVIEW", "status": "completed"}
        mock_sm.complete_stage.assert_called_once_with("REVIEW")

    def test_review_completed_with_verdict_but_no_artifact_refuses_named(self, capsys):
        """WS-D (#2124): a readable verdict is necessary but not sufficient — a
        fork that exited with judges in flight leaves no posted review artifact.
        The REVIEW completed marker is refused with REVIEW_ARTIFACT_MISSING."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"REVIEW": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._review_verdict_readable", return_value=True),
            patch("tools.sdlc_stage_marker._review_trailer_present", return_value=True),
            patch("tools.sdlc_stage_marker._review_artifact_posted", return_value=False),
        ):
            result, code = write_marker(
                stage="REVIEW", status="completed", issue_number=2062, run_id="run-r"
            )

        assert code == 1
        assert result["error"] == "review_artifact_missing"
        assert result["reason"] == "REVIEW_ARTIFACT_MISSING"
        mock_sm.complete_stage.assert_not_called()
        assert "REVIEW_ARTIFACT_MISSING" in capsys.readouterr().err

    def test_non_review_completed_never_consults_verdict(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"DOCS": "in_progress"}
        verdict_probe = MagicMock(return_value=False)

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._review_verdict_readable", verdict_probe),
        ):
            result, code = write_marker(
                stage="DOCS", status="completed", issue_number=2062, run_id="run-r"
            )

        assert code == 0
        verdict_probe.assert_not_called()
        mock_sm.complete_stage.assert_called_once_with("DOCS")

    def test_already_completed_review_stays_idempotent_exit_0(self):
        """An already-completed REVIEW marker (pre-fix state) is not
        retroactively refused -- the WS3b router recovery row owns the
        completed+no-verdict state; the idempotent no-op stays exit 0."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"REVIEW": "completed"}
        verdict_probe = MagicMock(return_value=False)

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._review_verdict_readable", verdict_probe),
        ):
            result, code = write_marker(
                stage="REVIEW", status="completed", issue_number=2062, run_id="run-r"
            )

        assert code == 0
        assert result == {"stage": "REVIEW", "status": "completed"}
        verdict_probe.assert_not_called()

    def test_review_in_progress_never_consults_verdict(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"REVIEW": "pending"}
        verdict_probe = MagicMock(return_value=False)

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._review_verdict_readable", verdict_probe),
        ):
            result, code = write_marker(
                stage="REVIEW", status="in_progress", issue_number=2062, run_id="run-r"
            )

        assert code == 0
        verdict_probe.assert_not_called()


class TestReviewVerdictReadable:
    """Direct tests of the _review_verdict_readable helper."""

    def test_true_when_verdict_record_present(self):
        from tools.sdlc_stage_marker import _review_verdict_readable

        record = MagicMock()
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": "APPROVED"}),
        ):
            assert _review_verdict_readable(2062) is True

    def test_false_when_verdict_empty(self):
        from tools.sdlc_stage_marker import _review_verdict_readable

        record = MagicMock()
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
            patch("tools.sdlc_verdict.get_verdict", return_value={}),
        ):
            assert _review_verdict_readable(2062) is False

    def test_false_when_record_unresolvable(self):
        from tools.sdlc_stage_marker import _review_verdict_readable

        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=None):
            assert _review_verdict_readable(2062) is False

    def test_false_on_error_fails_toward_refusal(self):
        """A read error fails CLOSED (not readable -> refusal): the invariant
        marker-completed => verdict-readable must hold even under errors; the
        WS3b recovery row owns the refused state."""
        from tools.sdlc_stage_marker import _review_verdict_readable

        with patch(
            "tools.sdlc_stage_query._resolve_issue_record",
            side_effect=RuntimeError("boom"),
        ):
            assert _review_verdict_readable(2062) is False


class TestReviewTrailerPresent:
    """Direct tests of the _review_trailer_present helper (#2193).

    Scoped strictly to the APPROVED path (Risk 1): non-APPROVED verdicts must
    pass through True regardless of trailer presence."""

    _WELL_FORMED = "APPROVED REVIEW_CONTEXT head_sha=" + "a" * 40
    _SHORT_SHA = "APPROVED REVIEW_CONTEXT head_sha=abc123"

    def test_true_when_approved_verdict_has_well_formed_trailer(self):
        from tools.sdlc_stage_marker import _review_trailer_present

        record = MagicMock()
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
            patch(
                "tools.sdlc_verdict.get_verdict",
                return_value={"verdict": self._WELL_FORMED},
            ),
        ):
            assert _review_trailer_present(2193) is True

    def test_false_when_approved_verdict_has_no_trailer(self):
        from tools.sdlc_stage_marker import _review_trailer_present

        record = MagicMock()
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": "APPROVED"}),
        ):
            assert _review_trailer_present(2193) is False

    def test_false_when_approved_verdict_has_malformed_short_sha_trailer(self):
        from tools.sdlc_stage_marker import _review_trailer_present

        record = MagicMock()
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
            patch(
                "tools.sdlc_verdict.get_verdict",
                return_value={"verdict": self._SHORT_SHA},
            ),
        ):
            assert _review_trailer_present(2193) is False

    @pytest.mark.parametrize(
        "verdict_text",
        [
            "CHANGES REQUESTED",
            "BLOCKED_ON_CONFLICT",
            "PR_CLOSED",
        ],
    )
    def test_true_pass_through_for_non_approved_verdicts_regardless_of_trailer(self, verdict_text):
        """Risk 1 mitigation: non-APPROVED verdicts are never subject to the
        trailer check, even though they legitimately carry no trailer."""
        from tools.sdlc_stage_marker import _review_trailer_present

        record = MagicMock()
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": verdict_text}),
        ):
            assert _review_trailer_present(2193) is True

    def test_false_when_record_unresolvable(self):
        from tools.sdlc_stage_marker import _review_trailer_present

        with patch("tools.sdlc_stage_query._resolve_issue_record", return_value=None):
            assert _review_trailer_present(2193) is False

    def test_false_on_error_fails_toward_refusal(self):
        """Mirrors _review_verdict_readable's exception-branch fail-closed
        behavior: any error refuses rather than passing the gate."""
        from tools.sdlc_stage_marker import _review_trailer_present

        with patch(
            "tools.sdlc_stage_query._resolve_issue_record",
            side_effect=RuntimeError("boom"),
        ):
            assert _review_trailer_present(2193) is False


class TestCritiqueCompletedVerdictGate:
    """WS-C (#2124): the CRITIQUE ``completed`` marker is unwritable without a
    readable substrate CRITIQUE verdict — the twin of the REVIEW WS3c gate. A
    fabricated critique that hands back READY TO BUILD but never records the
    verdict can no longer mark CRITIQUE completed; the refusal routes back to
    /do-plan-critique."""

    @staticmethod
    def _live_lock(run_id="run-c"):
        from models.session_lifecycle import IssueLockResult

        return MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id=run_id, target_repo="o/r"
            )
        )

    def test_critique_completed_with_no_readable_verdict_refuses_named(self, capsys):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"CRITIQUE": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._critique_verdict_readable", return_value=False),
        ):
            result, code = write_marker(
                stage="CRITIQUE", status="completed", issue_number=2124, run_id="run-c"
            )

        assert code == 1
        assert result["error"] == "critique_verdict_missing"
        assert result["reason"] == "CRITIQUE_VERDICT_MISSING"
        mock_sm.complete_stage.assert_not_called()
        assert "CRITIQUE_VERDICT_MISSING" in capsys.readouterr().err

    def test_critique_completed_with_readable_verdict_writes(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"CRITIQUE": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._critique_verdict_readable", return_value=True),
        ):
            result, code = write_marker(
                stage="CRITIQUE", status="completed", issue_number=2124, run_id="run-c"
            )

        assert code == 0
        assert result == {"stage": "CRITIQUE", "status": "completed"}
        mock_sm.complete_stage.assert_called_once_with("CRITIQUE")

    def test_already_completed_critique_stays_idempotent_exit_0(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"CRITIQUE": "completed"}
        verdict_probe = MagicMock(return_value=False)

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._critique_verdict_readable", verdict_probe),
        ):
            result, code = write_marker(
                stage="CRITIQUE", status="completed", issue_number=2124, run_id="run-c"
            )

        assert code == 0
        verdict_probe.assert_not_called()


class TestCritiqueVerdictReadable:
    """Direct tests of the _critique_verdict_readable helper."""

    def test_true_when_verdict_record_present(self):
        from tools.sdlc_stage_marker import _critique_verdict_readable

        record = MagicMock()
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
            patch("tools.sdlc_verdict.get_verdict", return_value={"verdict": "READY TO BUILD"}),
        ):
            assert _critique_verdict_readable(2124) is True

    def test_false_when_verdict_empty(self):
        from tools.sdlc_stage_marker import _critique_verdict_readable

        record = MagicMock()
        with (
            patch("tools.sdlc_stage_query._resolve_issue_record", return_value=record),
            patch("tools.sdlc_verdict.get_verdict", return_value={}),
        ):
            assert _critique_verdict_readable(2124) is False

    def test_false_on_error_fails_toward_refusal(self):
        from tools.sdlc_stage_marker import _critique_verdict_readable

        with patch(
            "tools.sdlc_stage_query._resolve_issue_record",
            side_effect=RuntimeError("boom"),
        ):
            assert _critique_verdict_readable(2124) is False

    def test_false_when_issue_number_missing(self):
        from tools.sdlc_stage_marker import _critique_verdict_readable

        assert _critique_verdict_readable(None) is False


class TestHelpersDelegateToSharedVerdictModule:
    """Issue #2305 defect 4: sdlc_stage_marker's three verdict-readability
    helpers must be thin delegates to the ONE implementation living in
    tools.sdlc_verdict, reused by both the direct write_marker completed-path
    gate and the PipelineStateMachine._backfill_predecessors scan-phase gate
    -- not duplicated logic that could drift apart."""

    def test_review_verdict_readable_delegates(self):
        from tools.sdlc_stage_marker import _review_verdict_readable

        with patch(
            "tools.sdlc_verdict._review_verdict_readable", return_value="sentinel"
        ) as mock_shared:
            assert _review_verdict_readable(2062) == "sentinel"
        mock_shared.assert_called_once_with(2062)

    def test_review_trailer_present_delegates(self):
        from tools.sdlc_stage_marker import _review_trailer_present

        with patch(
            "tools.sdlc_verdict._review_trailer_present", return_value="sentinel"
        ) as mock_shared:
            assert _review_trailer_present(2193) == "sentinel"
        mock_shared.assert_called_once_with(2193)

    def test_critique_verdict_readable_delegates(self):
        from tools.sdlc_stage_marker import _critique_verdict_readable

        with patch(
            "tools.sdlc_verdict._critique_verdict_readable", return_value="sentinel"
        ) as mock_shared:
            assert _critique_verdict_readable(2124) == "sentinel"
        mock_shared.assert_called_once_with(2124)


class TestReviewArtifactPosted:
    """Direct tests of the _review_artifact_posted helper (WS-D)."""

    def test_false_when_no_pr_resolves(self):
        from tools.sdlc_stage_marker import _review_artifact_posted

        with patch("tools.sdlc_stage_query._lookup_pr", return_value=None):
            assert _review_artifact_posted(2124, "o/r") is False

    def test_true_when_formal_review_present(self):
        from tools.sdlc_stage_marker import _review_artifact_posted

        rev = MagicMock(returncode=0, stdout='{"reviews": [{"state": "APPROVED"}]}')
        with (
            patch("tools.sdlc_stage_query._lookup_pr", return_value=55),
            patch("subprocess.run", return_value=rev),
        ):
            assert _review_artifact_posted(2124, "o/r") is True

    def test_issue_2539_resolves_pr_state_agnostically(self):
        """#2539: the probe must ask for ``state="all"``.

        Under the ``open`` default a merged PR resolves to None and the probe
        returned False at the lookup, before either artifact check ran -- so the
        REVIEW marker was unreachable for ANY merged PR, in exactly the state
        where the artifact is guaranteed to exist. Observed on #2518 / PR #2538,
        which carried 3 formal reviews and 2 ``## Review:`` comments while the
        probe reported False.
        """
        from tools.sdlc_stage_marker import _review_artifact_posted

        rev = MagicMock(returncode=0, stdout='{"reviews": [{"state": "APPROVED"}]}')
        with (
            patch("tools.sdlc_stage_query._lookup_pr", return_value=2538) as lookup,
            patch("subprocess.run", return_value=rev),
        ):
            assert _review_artifact_posted(2518, "o/r") is True

        assert lookup.call_args.kwargs["state"] == "all"

    def test_true_when_review_comment_present(self):
        from tools.sdlc_stage_marker import _review_artifact_posted

        def fake_run(cmd, *a, **k):
            if "pr" in cmd and "view" in cmd:
                return MagicMock(returncode=0, stdout='{"reviews": []}')
            # gh api comments count
            return MagicMock(returncode=0, stdout="1")

        with (
            patch("tools.sdlc_stage_query._lookup_pr", return_value=55),
            patch("subprocess.run", side_effect=fake_run),
        ):
            assert _review_artifact_posted(2124, "o/r") is True

    def test_false_when_no_review_and_no_comment(self):
        from tools.sdlc_stage_marker import _review_artifact_posted

        def fake_run(cmd, *a, **k):
            if "pr" in cmd and "view" in cmd:
                return MagicMock(returncode=0, stdout='{"reviews": []}')
            return MagicMock(returncode=0, stdout="0")

        with (
            patch("tools.sdlc_stage_query._lookup_pr", return_value=55),
            patch("subprocess.run", side_effect=fake_run),
        ):
            assert _review_artifact_posted(2124, "o/r") is False

    def test_false_on_error_fails_toward_refusal(self):
        from tools.sdlc_stage_marker import _review_artifact_posted

        with patch("tools.sdlc_stage_query._lookup_pr", side_effect=RuntimeError("boom")):
            assert _review_artifact_posted(2124, "o/r") is False

    def test_false_when_issue_number_missing(self):
        from tools.sdlc_stage_marker import _review_artifact_posted

        assert _review_artifact_posted(None, "o/r") is False


class TestCLI:
    """Tests for CLI argument parsing."""

    def test_help_shows_issue_number(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_stage_marker", "--help"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        assert result.returncode == 0
        assert "--issue-number" in result.stdout

    def test_no_args_exits_with_error(self):
        result = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_stage_marker"],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
        )
        # Missing required --stage and --status
        assert result.returncode != 0

    def test_with_issue_number_outputs_json(self):
        import popoto.redis_db as rdb

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}
        # Isolate the subprocess to the per-worker test Redis db -- unit tests
        # must never touch production Redis.
        kwargs = rdb.POPOTO_REDIS_DB.connection_pool.connection_kwargs
        clean_env["REDIS_URL"] = (
            f"redis://{kwargs.get('host') or 'localhost'}:"
            f"{kwargs.get('port') or 6379}/{kwargs.get('db', 1)}"
        )
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_stage_marker",
                "--stage",
                "PLAN",
                "--status",
                "completed",
                "--issue-number",
                "99999",
                "--run-id",
                "run-cli-test",
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=clean_env,
        )
        output = json.loads(result.stdout.strip())
        # No lease was ever established for issue 99999 under run-cli-test, but
        # the lock is FREE in this fresh test db. The #2144 self-heal
        # (maybe_heal_after_write) re-acquires the lease under the supplied
        # --run-id on a free lock -- the documented "stale --run-id + lapsed
        # lease resume" -- so the marker completes rather than hard-failing
        # LEASE_ABSENT. A "degraded" exit-0 is the acceptable fallback when
        # Redis itself is unreachable.
        assert result.returncode == 0, (
            f"stage-marker should self-heal on a free lock and complete; "
            f"rc={result.returncode}, output={output!r}"
        )
        assert output.get("status") in ("completed", "degraded"), (
            f"expected a healed 'completed' (or 'degraded' if Redis is down); got {output!r}"
        )


class TestMarkerWriteTelemetry:
    """Part 3 (issue #2451): write_marker increments a best-effort per-run
    ok/fail counter. Degraded (Redis-absent) writes are NOT counted as fail,
    and a counter-write failure never changes the marker outcome.
    """

    def test_record_telemetry_ok_on_success(self):
        from tools.sdlc_stage_marker import _record_marker_telemetry

        with patch("tools._sdlc_marker_telemetry.record_marker_write") as rec:
            _record_marker_telemetry(
                {"stage": "PLAN", "status": "completed"}, 0, "PLAN", 7, "run-1"
            )

        rec.assert_called_once()
        _, kwargs = rec.call_args
        assert kwargs["ok"] is True

    def test_record_telemetry_fail_on_nonzero(self):
        from tools.sdlc_stage_marker import _record_marker_telemetry

        with patch("tools._sdlc_marker_telemetry.record_marker_write") as rec:
            _record_marker_telemetry({"error": "lease_absent"}, 1, "PLAN", 7, "run-1")

        rec.assert_called_once()
        _, kwargs = rec.call_args
        assert kwargs["ok"] is False
        assert kwargs["stage"] == "PLAN"

    def test_record_telemetry_degraded_not_counted(self):
        from tools.sdlc_stage_marker import _record_marker_telemetry

        with patch("tools._sdlc_marker_telemetry.record_marker_write") as rec:
            _record_marker_telemetry({"status": "degraded"}, 0, "PLAN", 7, "run-1")

        rec.assert_not_called()

    def test_record_telemetry_invalid_stage_skipped(self):
        from tools.sdlc_stage_marker import _record_marker_telemetry

        with patch("tools._sdlc_marker_telemetry.record_marker_write") as rec:
            _record_marker_telemetry({}, 0, "BOGUS", 7, "run-1")

        rec.assert_not_called()

    def test_degraded_write_not_counted_as_fail_integration(self):
        from tools.sdlc_stage_marker import SUBSTRATE_ABSENT, write_marker

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_ABSENT),
            patch("tools._sdlc_marker_telemetry.record_marker_write") as rec,
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=1, run_id="run-1"
            )

        assert code == 0
        assert result["status"] == "degraded"
        rec.assert_not_called()

    def test_lease_absent_counted_as_fail_integration(self):
        # Force the LEASE_ABSENT fail path deterministically (independent of any
        # ambient Redis lease state left by sibling tests in a combined run).
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch(
                "tools._sdlc_utils.resolve_ledger_lease",
                return_value=(None, {"reason": "LEASE_ABSENT"}),
            ),
            patch("tools._sdlc_marker_telemetry.record_marker_write") as rec,
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=1, run_id="run-1"
            )

        assert code == 1
        assert result["error"] == "lease_absent"
        rec.assert_called_once()
        _, kwargs = rec.call_args
        assert kwargs["ok"] is False
        assert kwargs["stage"] == "PLAN"

    def test_counter_write_raise_does_not_change_outcome(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch(
                "tools._sdlc_utils.resolve_ledger_lease",
                return_value=(None, {"reason": "LEASE_ABSENT"}),
            ),
            patch(
                "tools._sdlc_marker_telemetry.record_marker_write",
                side_effect=RuntimeError("counter down"),
            ),
        ):
            result, code = write_marker(
                stage="PLAN", status="completed", issue_number=1, run_id="run-1"
            )

        # A raising counter must not perturb the marker's own outcome.
        assert code == 1
        assert result["error"] == "lease_absent"


class TestSkipPreconditions:
    """`--status skipped` (#2577): the disposition is VERIFIED, never asserted.

    `_skip_precondition_error` is the whole security surface of the new status.
    Every refusal is a named error and every probe fails closed.
    """

    def test_review_is_never_skippable(self):
        """The forge attempt this design exists to refuse: a `skipped` REVIEW
        would let a PR merge with no review at all."""
        from tools.sdlc_stage_marker import _skip_precondition_error

        reason, message = _skip_precondition_error("REVIEW", 2577)
        assert reason == "STAGE_NOT_SKIPPABLE"
        assert "merge_predicate" in message or "merge" in message

    @pytest.mark.parametrize(
        "stage", ["ISSUE", "BUILD", "TEST", "PATCH", "REVIEW", "DOCS", "MERGE"]
    )
    def test_only_plan_and_critique_pass_the_stage_check(self, stage):
        from tools.sdlc_stage_marker import _skip_precondition_error

        assert _skip_precondition_error(stage, 2577)[0] == "STAGE_NOT_SKIPPABLE"

    def test_existing_plan_document_refuses(self):
        """You cannot skip the CRITIQUE of an issue that has a plan."""
        from pathlib import Path

        from tools.sdlc_stage_marker import _skip_precondition_error

        with patch(
            "tools.lane_identity.find_plan_path", return_value=Path("docs/plans/real-plan.md")
        ):
            reason, message = _skip_precondition_error("CRITIQUE", 2577)
        assert reason == "PLAN_EXISTS_NOT_SKIPPABLE"
        assert "real-plan.md" in message

    def test_plan_lookup_error_fails_closed(self):
        """'Cannot confirm no plan exists' must never read as 'no plan exists'."""
        from tools.sdlc_stage_marker import _skip_precondition_error

        with patch("tools.lane_identity.find_plan_path", side_effect=RuntimeError("boom")):
            assert _skip_precondition_error("CRITIQUE", 2577)[0] == "PLAN_EXISTS_NOT_SKIPPABLE"

    def test_recorded_verdict_refuses(self):
        """A CRITIQUE that produced a verdict actually ran."""
        from tools.sdlc_stage_marker import _skip_precondition_error

        with (
            patch("tools.lane_identity.find_plan_path", return_value=None),
            patch(
                "tools.sdlc_stage_query._load_raw_states",
                return_value={"_verdicts": {"CRITIQUE": {"verdict": "READY TO BUILD"}}},
            ),
        ):
            reason, _ = _skip_precondition_error("CRITIQUE", 2577, ledger=MagicMock())
        assert reason == "STAGE_RAN_NOT_SKIPPABLE"

    def test_recorded_dispatch_refuses(self):
        from tools.sdlc_stage_marker import _skip_precondition_error

        with (
            patch("tools.lane_identity.find_plan_path", return_value=None),
            patch(
                "tools.sdlc_stage_query._load_raw_states",
                return_value={"_sdlc_dispatches": [{"skill": "/do-plan-critique"}]},
            ),
        ):
            reason, _ = _skip_precondition_error("CRITIQUE", 2577, ledger=MagicMock())
        assert reason == "STAGE_RAN_NOT_SKIPPABLE"

    def test_missing_ledger_fails_closed(self):
        from tools.sdlc_stage_marker import _skip_precondition_error

        with patch("tools.lane_identity.find_plan_path", return_value=None):
            assert _skip_precondition_error("CRITIQUE", 2577, ledger=None)[0] == (
                "STAGE_RAN_NOT_SKIPPABLE"
            )

    def test_unreadable_ledger_fails_closed(self):
        from tools.sdlc_stage_marker import _skip_precondition_error

        with (
            patch("tools.lane_identity.find_plan_path", return_value=None),
            patch("tools.sdlc_stage_query._load_raw_states", side_effect=RuntimeError("redis")),
        ):
            assert _skip_precondition_error("CRITIQUE", 2577, ledger=MagicMock())[0] == (
                "STAGE_RAN_NOT_SKIPPABLE"
            )

    def test_missing_issue_number_fails_closed(self):
        from tools.sdlc_stage_marker import _skip_precondition_error

        assert _skip_precondition_error("CRITIQUE", None)[0] == "STAGE_RAN_NOT_SKIPPABLE"

    def test_clean_off_pipeline_state_passes(self):
        from tools.sdlc_stage_marker import _skip_precondition_error

        with (
            patch("tools.lane_identity.find_plan_path", return_value=None),
            patch("tools.sdlc_stage_query._load_raw_states", return_value={}),
        ):
            assert _skip_precondition_error("CRITIQUE", 2577, ledger=MagicMock()) is None


class TestSkipMarkerWrite:
    """`write_marker(status="skipped")` end to end through the lease machinery."""

    @staticmethod
    def _live_lock(run_id="run-r"):
        from models.session_lifecycle import IssueLockResult

        return MagicMock(
            return_value=IssueLockResult(
                acquired=True, owner_session_id="s", owner_run_id=run_id, target_repo="o/r"
            )
        )

    def test_skipped_critique_writes(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"CRITIQUE": "pending"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._skip_precondition_error", return_value=None),
        ):
            result, code = write_marker(
                stage="CRITIQUE", status="skipped", issue_number=2577, run_id="run-r"
            )

        assert code == 0
        assert result == {"stage": "CRITIQUE", "status": "skipped"}
        mock_sm.skip_stage.assert_called_once()

    def test_skipped_review_refused_and_never_written(self, capsys):
        """The end-to-end forge attempt. No mock of the precondition here —
        this exercises the real closed-set check."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"REVIEW": "pending"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
        ):
            result, code = write_marker(
                stage="REVIEW", status="skipped", issue_number=2577, run_id="run-r"
            )

        assert code == 1
        assert result["reason"] == "STAGE_NOT_SKIPPABLE"
        mock_sm.skip_stage.assert_not_called()
        mock_sm.complete_stage.assert_not_called()
        assert "STAGE_NOT_SKIPPABLE" in capsys.readouterr().err

    def test_skip_refused_when_lease_lost_between_resolve_and_write(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"CRITIQUE": "pending"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._skip_precondition_error", return_value=None),
            patch("tools._sdlc_utils.revalidate_ledger_lease", return_value=False),
        ):
            result, code = write_marker(
                stage="CRITIQUE", status="skipped", issue_number=2577, run_id="run-r"
            )

        assert code == 1
        assert result["reason"] == "ISSUE_LOCKED"
        mock_sm.skip_stage.assert_not_called()

    def test_already_skipped_is_idempotent(self):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"CRITIQUE": "skipped"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
        ):
            result, code = write_marker(
                stage="CRITIQUE", status="skipped", issue_number=2577, run_id="run-r"
            )

        assert code == 0
        mock_sm.skip_stage.assert_not_called()

    def test_state_machine_refusal_is_reported_not_swallowed(self, capsys):
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_sm = MagicMock()
        mock_sm.states = {"CRITIQUE": "pending"}
        mock_sm.skip_stage.side_effect = ValueError("current status is 'completed'")

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("models.session_lifecycle.touch_issue_lock", self._live_lock()),
            patch("agent.pipeline_state.PipelineStateMachine.for_issue", return_value=mock_sm),
            patch("tools.sdlc_stage_marker._skip_precondition_error", return_value=None),
        ):
            result, code = write_marker(
                stage="CRITIQUE", status="skipped", issue_number=2577, run_id="run-r"
            )

        assert code == 1
        assert result["reason"] == "STAGE_RAN_NOT_SKIPPABLE"
        assert "STAGE_RAN_NOT_SKIPPABLE" in capsys.readouterr().err
