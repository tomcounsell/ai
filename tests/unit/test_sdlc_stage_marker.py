"""Unit tests for tools.sdlc_stage_marker.

Tests cover:
- write_marker with issue-number resolution (via shared find_session)
- write_marker auto-ensures a session on sessionless-but-issue-numbered writes
- CLI --issue-number argument parsing
- Backward compatibility (env var path still works)

The local `_find_session` resolver was deleted in #1558; stage_marker now
resolves through the shared `tools._sdlc_utils.find_session(..., ensure=True)`.
Resolver-level lookup behavior is covered by ``test_sdlc_utils.py``.
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

    write_marker now returns ``(result, exit_code)``:
    - degraded / success / idempotent → exit 0
    - genuine write failure (substrate present, session resolved) → exit 1
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
        """ABSENT: substrate probe fails → degraded marker, exit 0 (non-`ai` repo)."""
        from tools.sdlc_stage_marker import SUBSTRATE_ABSENT, write_marker

        with patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_ABSENT):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 0
        assert result["status"] == "degraded"
        assert "substrate absent" in result["reason"]
        assert result["stage"] == "PLAN"

    def test_present_no_session_emits_degraded_marker_exit_0_quiet(self):
        """PRESENT_NO_SESSION: substrate present but no session → degraded, exit 0, quiet."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}

        with (
            patch.dict(os.environ, clean_env, clear=True),
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=None),
        ):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 0
        assert result["status"] == "degraded"
        assert "no PM session" in result["reason"]

    def test_present_write_failed_exits_1_loud(self):
        """PRESENT_WRITE_FAILED: session resolved but state-machine raises → exit 1."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine", side_effect=RuntimeError("boom")),
        ):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 1
        assert result == {}

    def test_present_start_stage_rejected_exits_1(self):
        """PRESENT_WRITE_FAILED: start_stage raising ValueError (misorder) → exit 1."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_sm = MagicMock()
        mock_sm.start_stage.side_effect = ValueError("predecessor not completed")

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="REVIEW", status="in_progress")

        assert code == 1
        assert result == {}

    def test_fresh_plan_in_progress_backfills_and_persists(self):
        """First-write-at-PLAN acceptance (#1916): a fresh session (ISSUE=ready)
        must NOT be rejected — start_stage is called with
        backfill_predecessors=True, and a real PipelineStateMachine persists
        ISSUE -> completed, PLAN -> in_progress."""
        from agent.pipeline_state import PipelineStateMachine
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_session.stage_states = None
        mock_session.save = MagicMock()

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
        ):
            result, code = write_marker(stage="PLAN", status="in_progress")

        assert code == 0
        assert result == {"stage": "PLAN", "status": "in_progress"}
        sm = PipelineStateMachine(mock_session)
        assert sm.states["ISSUE"] == "completed"
        assert sm.states["PLAN"] == "in_progress"

    def test_plan_in_progress_with_failed_issue_still_exits_1(self):
        """Companion failure case: a genuinely `failed` predecessor is never
        backfilled — in_progress at PLAN still exits 1 loudly."""
        import json

        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps({"ISSUE": "failed"})
        mock_session.save = MagicMock()

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
        ):
            result, code = write_marker(stage="PLAN", status="in_progress")

        assert code == 1
        assert result == {}

    def test_fresh_plan_completed_backfills_issue_too(self):
        """Completed-path backfill (BLOCKER regression, #1916): on a fresh
        session (ISSUE=ready, PLAN=pending), a --status completed write for
        PLAN must persist ISSUE -> completed AND PLAN -> completed. Proves the
        standalone _backfill_predecessors helper is reached directly and the
        start_stage `current == "in_progress"` no-op does not gate it."""
        from agent.pipeline_state import PipelineStateMachine
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_session.stage_states = None
        mock_session.save = MagicMock()

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
        ):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        sm = PipelineStateMachine(mock_session)
        assert sm.states["ISSUE"] == "completed"
        assert sm.states["PLAN"] == "completed"

    def test_plan_completed_with_failed_issue_exits_1_unmutated(self):
        """Companion failure case: a `failed` predecessor on the completed
        path exits 1 and leaves state unmutated (PLAN never force-set to
        in_progress, ISSUE stays failed)."""
        import json

        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_session.stage_states = json.dumps({"ISSUE": "failed", "PLAN": "pending"})
        mock_session.save = MagicMock()

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
        ):
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 1
        assert result == {}
        mock_session.save.assert_not_called()

    def test_idempotent_already_completed_exit_0(self):
        """Idempotent already-completed path stays exit 0 (not loud)."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_sm = MagicMock()
        mock_sm.states = {"PLAN": "completed"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="PLAN", status="completed")

        mock_sm.complete_stage.assert_not_called()

    def test_successful_write_exit_0(self):
        """Happy path: session resolved, write succeeds → exit 0 + marker."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_sm = MagicMock()
        mock_sm.states = {"PLAN": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session) as find_mock,
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=941)

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        mock_sm.complete_stage.assert_called_once_with("PLAN")
        # #1558: write path resolves through the shared resolver with ensure=True.
        find_mock.assert_called_once_with(None, issue_number=941, ensure=True)

    def test_marker_lands_on_issue_session_under_divergent_env(self):
        """#1671/#1672: with VALOR_SESSION_ID pointing at a DIFFERENT session, a
        stage-marker write with --issue-number N lands on the issue-scoped
        session (resolved via the real find_session issue-first pass), not the
        divergent env session."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        issue_session = MagicMock(name="issue_session")
        captured = {}

        def _psm_factory(session):
            captured["session"] = session
            sm = MagicMock()
            sm.states = {"PLAN": "in_progress"}
            return sm

        env = {**os.environ, "VALOR_SESSION_ID": "parent-pm-divergent"}
        env.pop("AGENT_SESSION_ID", None)

        with (
            patch.dict(os.environ, env, clear=True),
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            # Real find_session runs; its issue-first pass resolves this.
            patch("tools._sdlc_utils.find_session_by_issue", return_value=issue_session),
            patch("agent.pipeline_state.PipelineStateMachine", side_effect=_psm_factory),
        ):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=1672)

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        # The marker write targeted the issue session, not the env one.
        assert captured["session"] is issue_session

    def test_sessionless_issue_numbered_write_auto_ensures(self):
        """A sessionless-but-issue-numbered write resolves through find_session
        with ensure=True, which auto-creates a PM session so the marker persists
        (#1558). Here we assert the resolver is invoked with ensure=True and that
        the returned (ensured) session drives a successful marker write."""
        from tools.sdlc_stage_marker import write_marker

        ensured = MagicMock()
        ensured.stage_states = "{}"

        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}

        with (
            patch.dict(os.environ, clean_env, clear=True),
            patch("tools.sdlc_stage_marker.find_session", return_value=ensured) as find_mock,
            patch("agent.pipeline_state.PipelineStateMachine") as mock_psm_cls,
        ):
            mock_psm = MagicMock()
            mock_psm.states = {}
            mock_psm_cls.return_value = mock_psm

            result, code = write_marker(stage="REVIEW", status="in_progress", issue_number=1558)

        assert code == 0
        assert result == {"stage": "REVIEW", "status": "in_progress"}
        find_mock.assert_called_once_with(None, issue_number=1558, ensure=True)

    def test_successful_write_renews_issue_lock(self):
        """Issue #1954: a stage-marker write is evidence of an in-progress
        BUILD/TEST/REVIEW-stage recurrence, so a successful write must renew
        the per-issue SDLC ownership lock via the shared
        renew_issue_lock_for_session() helper."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        mock_session = MagicMock()
        mock_session.issue_number = 1954
        mock_sm = MagicMock()
        mock_sm.states = {"PLAN": "in_progress"}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=mock_session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
            patch("models.session_lifecycle.touch_issue_lock") as mock_touch,
        ):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=1954)

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}
        mock_touch.assert_called_once()
        args, kwargs = mock_touch.call_args
        assert args[0] == 1954
        assert kwargs.get("ttl") is not None

    def test_degraded_no_session_does_not_touch_issue_lock(self):
        """No session resolved → nothing to renew; touch_issue_lock must not fire."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=None),
            patch("models.session_lifecycle.touch_issue_lock") as mock_touch,
        ):
            write_marker(stage="PLAN", status="completed", issue_number=1954)

        mock_touch.assert_not_called()


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
        strip = ("VALOR_SESSION_ID", "AGENT_SESSION_ID")
        clean_env = {k: v for k, v in os.environ.items() if k not in strip}
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
            ],
            capture_output=True,
            text=True,
            cwd=REPO_ROOT,
            env=clean_env,
        )
        assert result.returncode == 0
        output = json.loads(result.stdout.strip())
        # Output depends on substrate state (D7 tri-state contract):
        # - degraded marker if the substrate is absent or no session resolves
        #   for issue 99999 (the common case in CI),
        # - {"stage": "PLAN", "status": "completed"} if a session happens to
        #   exist. All are valid — the test verifies the CLI accepts
        #   --issue-number and produces well-formed JSON with exit 0.
        assert output.get("status") == "degraded" or output == {
            "stage": "PLAN",
            "status": "completed",
        }


class TestOwnershipGateMarker:
    """Tests for the ownership guard in write_marker.

    When issue_number is passed, the resolved session must own that issue
    or the write is refused with exit_code 1 and a stderr diagnostic.
    A MagicMock() session passes the ownership check (endswith returns a
    truthy Mock), so we use explicit session objects here to control ownership.
    """

    class _Session:
        """Minimal session that controls ownership attributes explicitly."""

        def __init__(self, *, issue_url=None, session_id="", message_text=""):
            self.issue_url = issue_url
            self.session_id = session_id
            self.message_text = message_text

    def _non_owning_session(self, issue_number=42):
        """Session that does NOT own issue_number via any predicate."""
        return self._Session(
            issue_url="https://github.com/x/y/issues/99",
            session_id="other-session",
            message_text="working on issue 99",
        )

    def _owning_session(self, issue_number=42, via="issue_url"):
        if via == "issue_url":
            return self._Session(issue_url=f"https://github.com/x/y/issues/{issue_number}")
        elif via == "message_text":
            return self._Session(
                issue_url=None,
                session_id="other-session",
                message_text=f"SDLC issue #{issue_number} needs fixing",
            )
        raise ValueError(via)

    def test_explicit_issue_non_owning_exits_1_no_write(self):
        """Non-owning session with issue_number → write refused, exit_code 1.

        write_marker returns {"error": "ownership_divert"} as a sentinel so
        main() can suppress the generic write-failure message on this path.
        """
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        session = self._non_owning_session(42)
        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=session),
        ):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=42)

        assert result == {"error": "ownership_divert"}
        assert code == 1

    def test_explicit_issue_owning_via_issue_url_exits_0(self):
        """Owning session via issue_url → write proceeds, exit_code 0."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        session = self._owning_session(42, via="issue_url")
        mock_sm = MagicMock()
        mock_sm.states = {}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=42)

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}

    def test_explicit_issue_owning_via_message_text_exits_0(self):
        """CRITICAL: predicate 3 (message_text) passes the ownership gate.

        A session with no issue_url and session_id != 'sdlc-local-42' but
        message_text containing 'issue #42' must be allowed to write (exit 0).
        This proves the third predicate is actually evaluated by the gate.
        """
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        session = self._owning_session(42, via="message_text")
        mock_sm = MagicMock()
        mock_sm.states = {}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            result, code = write_marker(stage="PLAN", status="completed", issue_number=42)

        assert code == 0
        assert result == {"stage": "PLAN", "status": "completed"}

    def test_no_issue_number_skips_gate(self):
        """Without issue_number, the ownership guard is bypassed entirely.

        A non-owning session is still allowed to write when no issue number
        is passed — the gate only fires when issue_number is not None.
        """
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        session = self._non_owning_session(42)
        mock_sm = MagicMock()
        mock_sm.states = {}

        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=session),
            patch("agent.pipeline_state.PipelineStateMachine", return_value=mock_sm),
        ):
            # No issue_number passed — gate must not fire.
            result, code = write_marker(stage="PLAN", status="completed")

        assert code == 0

    def test_divert_stderr_message(self, capsys):
        """Ownership gate fires: stderr must contain 'ownership guard' and the issue number."""
        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, write_marker

        session = self._non_owning_session(42)
        with (
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=session),
        ):
            write_marker(stage="PLAN", status="completed", issue_number=42)

        captured = capsys.readouterr()
        assert "ownership guard" in captured.err.lower()
        assert "42" in captured.err

    def test_main_divert_emits_only_ownership_message_not_generic(self, capsys):
        """CLI-level: main() on ownership-divert emits ONLY the guard diagnostic.

        Regression test for the finding that main() was unconditionally printing
        a second contradictory 'state-machine write was rejected or raised' line
        after write_marker had already printed the correct ownership diagnostic.
        The ownership-divert path must suppress the generic write-failure message.
        """
        import pytest

        from tools.sdlc_stage_marker import SUBSTRATE_PRESENT, main

        session = self._non_owning_session(42)
        test_args = [
            "sdlc_stage_marker",
            "--stage",
            "PLAN",
            "--status",
            "completed",
            "--issue-number",
            "42",
        ]
        with (
            patch("sys.argv", test_args),
            patch("tools.sdlc_stage_marker.probe_substrate", return_value=SUBSTRATE_PRESENT),
            patch("tools.sdlc_stage_marker.find_session", return_value=session),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()

        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        # The ownership guard message must be present
        assert "ownership guard" in captured.err.lower()
        assert "42" in captured.err
        # The contradictory generic write-failure message must NOT be present
        assert "state-machine write was rejected or raised" not in captured.err
        # stdout must be clean JSON with no sentinel keys leaking out
        assert json.loads(captured.out.strip()) == {}
