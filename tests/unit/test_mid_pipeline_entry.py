"""Mid-pipeline entry and the archived-plan escape hatch (#2851).

Two defects in the same seam — what makes a stage legitimately skippable.

**The archived-plan hole** (found during #2851 recon, not filed separately).
``skip_stage`` may record PLAN/CRITIQUE as ``skipped`` only when there is
verifiably nothing to critique, and its first precondition is "no plan document"
resolved via ``find_plan_path`` — which searches ``docs/plans/`` only. Once a
shipped lane's plan is archived to ``docs/archive/plans-completed/`` that lookup
returns ``None``, so the lane's CRITIQUE became **retroactively skippable**.
Measured on #2734 and #2741: both read ``plan_exists: false`` after their plans
moved. That is an undesigned hole straight through the verdict invariant (#2415)
the precondition exists to defend, and it is more dangerous than #2851's own
deadlock because it grants a false skip silently.

**Mid-pipeline entry detection.** A lane whose first dispatch is at a non-ISSUE
stage skipped its predecessors. The detector must key on **positive evidence** —
a plan document, or a recorded verdict with no corresponding marker — and never
on an empty stages map. ``_recover_stage_states_from_durable_signals`` runs
before the router, so an empty map at that point means recovery already failed
and cannot be told apart from a genuinely fresh issue. Refusing on emptiness
would reject every new lane at ISSUE.
"""

from __future__ import annotations

from pathlib import Path


class TestArchivedPlanIsNotSkippable:
    """Archiving a plan must not make its stages retroactively skippable."""

    def _write(self, root: Path, relparts: tuple[str, ...], name: str, issue: int) -> Path:
        d = root.joinpath(*relparts)
        d.mkdir(parents=True, exist_ok=True)
        p = d / name
        tracking = f"https://github.com/tomcounsell/ai/issues/{issue}"
        p.write_text(
            f"---\nstatus: Complete\ntracking: {tracking}\n---\n\n# Plan\n",
            encoding="utf-8",
        )
        return p

    def test_archived_plan_is_found(self, tmp_path, monkeypatch):
        from tools.lane_identity import find_archived_plan_path

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        self._write(tmp_path, ("docs", "archive", "plans-completed"), "shipped.md", 2734)
        assert find_archived_plan_path(2734) is not None

    def test_archived_plan_does_not_count_as_a_live_plan(self, tmp_path, monkeypatch):
        """The separation is the point: plan_exists / row 1 / G5 must not shift."""
        from tools.lane_identity import find_plan_path

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        (tmp_path / "docs" / "plans").mkdir(parents=True, exist_ok=True)
        self._write(tmp_path, ("docs", "archive", "plans-completed"), "shipped.md", 2734)
        assert find_plan_path(2734) is None, (
            "an archived plan must not read as a live plan — that would flip "
            "plan_exists and reroute row 1"
        )

    def test_archived_plan_refuses_the_skip(self, tmp_path, monkeypatch):
        """The hole itself: this was permitted before the fix."""
        from tools.sdlc_stage_marker import _skip_precondition_error

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        (tmp_path / "docs" / "plans").mkdir(parents=True, exist_ok=True)
        self._write(tmp_path, ("docs", "archive", "plans-completed"), "shipped.md", 2734)

        class _Ledger:
            stage_states_json = "{}"
            issue_number = 2734

        result = _skip_precondition_error("CRITIQUE", 2734, ledger=_Ledger())
        assert result is not None, "an archived plan must refuse the skip"
        code, msg = result
        assert code == "PLAN_EXISTS_NOT_SKIPPABLE"
        assert "archived plan" in msg

    def test_no_plan_anywhere_still_permits_the_skip(self, tmp_path, monkeypatch):
        """#2577's capability must survive: a genuine no-plan lane stays skippable."""
        from tools.sdlc_stage_marker import _skip_precondition_error

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        (tmp_path / "docs" / "plans").mkdir(parents=True, exist_ok=True)
        (tmp_path / "docs" / "archive" / "plans-completed").mkdir(parents=True, exist_ok=True)

        class _Ledger:
            stage_states_json = "{}"
            issue_number = 9999

        assert _skip_precondition_error("CRITIQUE", 9999, ledger=_Ledger()) is None

    def test_archive_lookup_failure_fails_closed(self, tmp_path, monkeypatch):
        """A probe that cannot answer must refuse, not assume no plan existed."""
        import tools.lane_identity as li
        from tools.sdlc_stage_marker import _skip_precondition_error

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        (tmp_path / "docs" / "plans").mkdir(parents=True, exist_ok=True)
        monkeypatch.setattr(
            li, "find_archived_plan_path", lambda n: (_ for _ in ()).throw(OSError("boom"))
        )

        class _Ledger:
            stage_states_json = "{}"
            issue_number = 4242

        result = _skip_precondition_error("CRITIQUE", 4242, ledger=_Ledger())
        assert result is not None and result[0] == "PLAN_EXISTS_NOT_SKIPPABLE"


class TestVerdictInvariantIsUntouched:
    """#2415 / #2554 coverage lives in ``test_pipeline_state_machine.py``.

    The plan requires proof that a verdict-less REVIEW/CRITIQUE still cannot be
    force-completed after this change. That proof already exists and is
    comprehensive — ``TestBackfillPredecessors`` covers the raise, the
    satisfied-invariant path, and the once-per-call bound. Re-implementing it
    here would fork the invariant's definition across two files, which is how a
    guard quietly stops guarding.

    This change adds a precondition rung to ``_skip_precondition_error`` and a
    new lookup in ``lane_identity``. It does not touch ``_backfill_predecessors``
    or ``skip_stage``, so the existing suite is the regression proof; this test
    pins that non-touching claim.
    """

    def test_skip_stage_and_backfill_are_unmodified_by_this_change(self):
        import subprocess

        changed = subprocess.run(
            ["git", "diff", "origin/main", "--", "agent/pipeline_state.py"],
            capture_output=True,
            text=True,
        ).stdout
        assert changed.strip() == "", (
            "agent/pipeline_state.py changed — the verdict invariant's own tests "
            "are no longer sufficient proof and this suite must be extended"
        )
