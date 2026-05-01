"""Integration tests for the SDLC next-skill dispatch CLI (issue #1216).

Drives ``tools.sdlc_next_skill.decide()`` directly against fixture
stage_states and asserts that the JSON output matches what
``agent.sdlc_router.decide_next_dispatch()`` produces for the same inputs.

This is the regression net for Phase 2: if the CLI wrapper and the core
function ever diverge, these tests catch it.

No Redis required — the tests mock out session lookup so decide() falls
through to the dispatch algorithm with the supplied stage_states.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

from agent.sdlc_router import (
    Blocked,
    Dispatch,
    decide_next_dispatch,
)
from tools.sdlc_next_skill import decide

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_enriched(stage_states: dict, meta: dict | None = None) -> dict:
    """Build a fake query_enriched() return value."""
    default_meta = {
        "patch_cycle_count": 0,
        "critique_cycle_count": 0,
        "latest_critique_verdict": None,
        "latest_review_verdict": None,
        "revision_applied": False,
        "pr_number": None,
        "pr_merge_state": None,
        "ci_all_passing": None,
        "same_stage_dispatch_count": 0,
        "last_dispatched_skill": None,
    }
    if meta:
        default_meta.update(meta)
    return {"stages": stage_states, "_meta": default_meta}


def _decide_with_fixture(stage_states: dict, meta: dict | None = None) -> dict:
    """Call tools.sdlc_next_skill.decide() with mocked session lookup."""
    enriched = _make_enriched(stage_states, meta)
    with (
        patch("tools.sdlc_next_skill._resolve_enriched", return_value=enriched),
        patch("tools.sdlc_next_skill._build_context", return_value={}),
    ):
        return decide(issue_number=None, session_id=None)


def _core_decide(stage_states: dict, meta: dict | None = None) -> dict:
    """Call agent.sdlc_router.decide_next_dispatch() directly for comparison."""
    m = meta or {}
    result = decide_next_dispatch(stage_states, m, {})
    if isinstance(result, Dispatch):
        return {
            "skill": result.skill,
            "reason": result.reason,
            "row_id": result.row_id,
            "dispatched": True,
        }
    elif isinstance(result, Blocked):
        return {"blocked": True, "reason": result.reason, "guard_id": result.guard_id}
    return {"error": "unknown", "dispatched": False}


# ---------------------------------------------------------------------------
# Parity tests: CLI matches core function for canonical states
# ---------------------------------------------------------------------------


class TestNextSkillParity:
    """For each canonical state, assert decide() matches decide_next_dispatch()."""

    def _assert_parity(self, stage_states: dict, meta: dict | None = None):
        cli = _decide_with_fixture(stage_states, meta)
        core = _core_decide(stage_states, meta)
        assert cli.get("skill") == core.get("skill"), (
            f"Skill mismatch for state {stage_states}:\n  CLI: {cli}\n  Core: {core}"
        )
        assert cli.get("blocked") == core.get("blocked"), (
            f"Blocked mismatch for state {stage_states}:\n  CLI: {cli}\n  Core: {core}"
        )

    def test_empty_states_routes_to_plan(self):
        """Row 1: empty stage_states → /do-plan."""
        self._assert_parity({})

    def test_issue_completed_routes_to_plan(self):
        """Row 1: ISSUE completed, no plan → /do-plan."""
        self._assert_parity({"ISSUE": "completed"})

    def test_plan_completed_routes_to_critique(self):
        """Row 2: PLAN completed, CRITIQUE pending → /do-plan-critique."""
        self._assert_parity({"ISSUE": "completed", "PLAN": "completed"})

    def test_critique_needs_revision_routes_to_plan(self):
        """Row 3: CRITIQUE failed/needs revision → /do-plan."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "failed",
            "_verdicts": {"CRITIQUE": {"verdict": "NEEDS REVISION", "recorded_at": "2026-01-01"}},
        }
        self._assert_parity(states, {"latest_critique_verdict": "NEEDS REVISION"})

    def test_critique_ready_no_branch_routes_to_build(self):
        """Row 4a: CRITIQUE READY TO BUILD, no PR → /do-build."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "_verdicts": {"CRITIQUE": {"verdict": "READY TO BUILD", "recorded_at": "2026-01-01"}},
        }
        self._assert_parity(states, {"latest_critique_verdict": "READY TO BUILD"})

    def test_test_failed_routes_to_patch(self):
        """Row 6: TEST failed → /do-patch."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "failed",
        }
        self._assert_parity(states)

    def test_pr_exists_no_review_routes_to_review(self):
        """Row 7: PR exists, REVIEW pending → /do-pr-review."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
        }
        self._assert_parity(states, {"pr_number": 42})

    def test_review_approved_no_docs_routes_to_docs(self):
        """Row 9: REVIEW approved, DOCS not done → /do-docs."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "completed",
            "_verdicts": {"REVIEW": {"verdict": "APPROVED", "recorded_at": "2026-01-01"}},
        }
        self._assert_parity(
            states,
            {
                "pr_number": 42,
                "latest_review_verdict": "APPROVED",
            },
        )

    def test_all_stages_complete_routes_to_merge(self):
        """Row 10: all stages complete → /do-merge."""
        states = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "completed",
            "DOCS": "completed",
            "_verdicts": {"REVIEW": {"verdict": "APPROVED", "recorded_at": "2026-01-01"}},
        }
        self._assert_parity(
            states,
            {
                "pr_number": 42,
                "latest_review_verdict": "APPROVED",
            },
        )

    def test_oscillation_guard_g4_fires(self):
        """G4: same-skill repeated 3+ times → Blocked."""
        from agent.sdlc_router import MAX_SAME_STAGE_DISPATCHES, build_stage_snapshot

        states: dict = {"ISSUE": "completed"}
        # Inject dispatch history with 3 identical dispatches
        snapshot = build_stage_snapshot(states, {"pr_number": None})
        history = [
            {"skill": "/do-plan", "at": "2026-01-01T00:00:00", "stage_snapshot": snapshot}
        ] * MAX_SAME_STAGE_DISPATCHES
        states["_sdlc_dispatches"] = history

        meta = {
            "same_stage_dispatch_count": MAX_SAME_STAGE_DISPATCHES,
            "last_dispatched_skill": "/do-plan",
        }
        self._assert_parity(states, meta)


# ---------------------------------------------------------------------------
# Output schema tests
# ---------------------------------------------------------------------------


class TestNextSkillOutputSchema:
    """Verify the JSON output schema matches the contract documented in the tool."""

    def test_dispatch_result_has_required_keys(self):
        """A dispatched result must have skill, reason, row_id, dispatched keys."""
        result = _decide_with_fixture({})
        # Row 1 should fire for empty stage_states
        assert "skill" in result, f"'skill' key missing: {result}"
        assert "reason" in result, f"'reason' key missing: {result}"
        assert result.get("dispatched") is True, f"'dispatched' not True: {result}"

    def test_blocked_result_has_blocked_key(self):
        """A blocked result must have the 'blocked' key set to True."""
        from agent.sdlc_router import MAX_SAME_STAGE_DISPATCHES, build_stage_snapshot

        states: dict = {"ISSUE": "completed"}
        snapshot = build_stage_snapshot(states, {"pr_number": None})
        history = [
            {"skill": "/do-plan", "at": "2026-01-01T00:00:00", "stage_snapshot": snapshot}
        ] * MAX_SAME_STAGE_DISPATCHES
        states["_sdlc_dispatches"] = history

        meta = {
            "same_stage_dispatch_count": MAX_SAME_STAGE_DISPATCHES,
            "last_dispatched_skill": "/do-plan",
        }
        result = _decide_with_fixture(states, meta)
        assert result.get("blocked") is True, f"Expected blocked=True: {result}"
        assert "reason" in result, f"'reason' key missing from blocked result: {result}"

    def test_error_result_does_not_raise(self):
        """When session lookup fails completely, decide() returns error dict without raising."""
        with (
            patch("tools.sdlc_next_skill._resolve_enriched", side_effect=RuntimeError("DB down")),
        ):
            result = decide(issue_number=None, session_id=None)
        assert "error" in result, f"Expected error key in result: {result}"
        assert result.get("dispatched") is False, f"Expected dispatched=False: {result}"


# ---------------------------------------------------------------------------
# CLI subprocess tests (drives sdlc-tool next-skill end-to-end)
# ---------------------------------------------------------------------------


class TestNextSkillCLISubprocess:
    """Drive the module as a subprocess to verify the CLI contract."""

    def _run_cli(self, args: list[str]) -> tuple[int, dict]:
        """Run ``python -m tools.sdlc_next_skill`` with args, return (exit_code, parsed_json)."""
        proc = subprocess.run(
            [sys.executable, "-m", "tools.sdlc_next_skill"] + args,
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=15,
        )
        try:
            data = json.loads(proc.stdout.strip() or "{}")
        except json.JSONDecodeError:
            data = {"parse_error": proc.stdout}
        return proc.returncode, data

    def test_missing_args_returns_exit_2(self):
        """No --issue-number or --session-id → exit code 2 with error JSON."""
        rc, data = self._run_cli([])
        assert rc == 2, f"Expected exit 2, got {rc}: {data}"
        assert "error" in data

    def test_format_pretty_produces_indented_json(self):
        """--format pretty must produce indented JSON (with newlines)."""
        # We don't have a real session — use a fake session-id so the tool
        # falls through to default empty states (exit 0, /do-plan dispatch).
        proc = subprocess.run(
            [
                sys.executable,
                "-m",
                "tools.sdlc_next_skill",
                "--session-id",
                "nonexistent-id-xyz",
                "--format",
                "pretty",
            ],
            capture_output=True,
            text=True,
            cwd=str(REPO_ROOT),
            timeout=15,
        )
        # The tool should exit 0 even when session is not found (empty states → Row 1)
        assert proc.returncode in (0, 1), f"Unexpected exit code {proc.returncode}: {proc.stdout}"
        # Pretty format should have at least one newline (indented JSON)
        assert "\n" in proc.stdout, f"Expected indented JSON but got: {proc.stdout!r}"
