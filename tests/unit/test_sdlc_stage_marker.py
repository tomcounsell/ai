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
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

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

    def test_present_write_failed_exits_1_loud(self):
        """A resolved lease but a raising state machine construction → exit 1."""
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
        assert result == {}

    def test_present_start_stage_rejected_exits_1(self):
        """start_stage raising ValueError (misorder) → exit 1."""
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
        assert result == {}

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
        # No lease was ever established for issue 99999 under run-cli-test in
        # this fresh test Redis db, so this always hard-fails LEASE_ABSENT
        # (exit 1) unless Redis itself is unreachable (exit 0, degraded).
        if result.returncode == 0:
            assert output.get("status") == "degraded"
        else:
            assert result.returncode == 1
            assert output.get("reason") == "LEASE_ABSENT"
