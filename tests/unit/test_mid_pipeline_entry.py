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

import json
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
        """#2577's capability must survive: a genuine no-plan lane stays skippable.

        Uses a REAL ``PipelineLedger``, not a duck-typed double. This is the one
        assertion in this class that expects a **permit**, and
        ``tools.sdlc_stage_query._load_raw_states`` selects its field by
        ``isinstance`` — so a double reads ``{}`` and the permit would pass
        vacuously, proving nothing about the verdict/dispatch rungs it is meant
        to clear. The refusal assertions above are safe with doubles because
        ``{}`` produces a refusal too, but this one is not.
        """
        from agent.pipeline_ledger import PipelineLedger
        from tools.sdlc_stage_marker import _skip_precondition_error

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        (tmp_path / "docs" / "plans").mkdir(parents=True, exist_ok=True)
        (tmp_path / "docs" / "archive" / "plans-completed").mkdir(parents=True, exist_ok=True)

        ledger = PipelineLedger(
            ledger_key="tomcounsell/ai:9999",
            issue_number=9999,
            stage_states_json=json.dumps({}),
        )
        assert _skip_precondition_error("CRITIQUE", 9999, ledger=ledger) is None

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


class TestMidPipelineEntryDetector:
    """The detector: turn a generic refusal into a named missing signal.

    The refusal itself (``STATE_MACHINE_REJECTED``) already exists and is
    correct. What was missing is that it is undiagnosable — it does not say
    "this lane entered mid-pipeline and skipped its predecessors". These tests
    pin the positive-evidence predicate, and in particular that a fresh lane is
    never flagged.
    """

    def _plan(self, root: Path, relparts: tuple[str, ...], issue: int) -> Path:
        d = root.joinpath(*relparts)
        d.mkdir(parents=True, exist_ok=True)
        p = d / f"lane-{issue}.md"
        p.write_text(
            f"---\ntracking: https://github.com/tomcounsell/ai/issues/{issue}\n---\n\n# Plan\n",
            encoding="utf-8",
        )
        return p

    def test_verdict_without_marker_is_detected_and_names_the_signal(self, tmp_path, monkeypatch):
        """The #2734/#2741 shape: APPROVED REVIEW verdict while REVIEW sits pending."""
        from tools.sdlc_stage_marker import _mid_pipeline_entry_diagnostic

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        self._plan(tmp_path, ("docs", "plans"), 2734)

        raw = {
            "REVIEW": "pending",
            "_verdicts": {"REVIEW": {"verdict": "APPROVED"}},
            "_sdlc_dispatches": [{"skill": "/do-pr-review", "stage": "REVIEW"}],
        }
        msg = _mid_pipeline_entry_diagnostic(2734, raw)
        assert msg is not None, "a verdict recorded with no marker must be detected"
        assert "MID_PIPELINE_ENTRY" in msg
        assert "REVIEW" in msg and "verdict" in msg
        assert "PLAN" in msg and "plan document" in msg

    def test_fresh_lane_with_empty_stages_map_is_not_detected(self, tmp_path, monkeypatch):
        """The round-2 blocker. Emptiness alone must never fire.

        ``_recover_stage_states_from_durable_signals`` already ran and failed by
        the time anything downstream sees ``{}``, so an empty map cannot be told
        apart from a genuinely fresh issue. Firing here would flag every new lane.
        """
        from tools.sdlc_stage_marker import _mid_pipeline_entry_diagnostic

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        (tmp_path / "docs" / "plans").mkdir(parents=True, exist_ok=True)

        assert _mid_pipeline_entry_diagnostic(4242, {}) is None
        assert _mid_pipeline_entry_diagnostic(4242, {"ISSUE": "in_progress"}) is None

    def test_normally_progressing_lane_is_not_detected(self, tmp_path, monkeypatch):
        """Markers written in order, verdicts backed by markers — nothing missing."""
        from tools.sdlc_stage_marker import _mid_pipeline_entry_diagnostic

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        self._plan(tmp_path, ("docs", "plans"), 2636)

        raw = {
            "ISSUE": "completed",
            "PLAN": "completed",
            "CRITIQUE": "completed",
            "BUILD": "completed",
            "TEST": "completed",
            "REVIEW": "in_progress",
            "_verdicts": {"CRITIQUE": {"verdict": "READY TO BUILD (NO CONCERNS)"}},
            "_sdlc_dispatches": [
                {"skill": "/do-plan", "stage": "PLAN"},
                {"skill": "/do-pr-review", "stage": "REVIEW"},
            ],
        }
        assert _mid_pipeline_entry_diagnostic(2636, raw) is None

    def test_archived_plan_lane_is_still_detected(self, tmp_path, monkeypatch):
        """The plan having been archived is still evidence that PLAN applied."""
        from tools.sdlc_stage_marker import _mid_pipeline_entry_diagnostic

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        (tmp_path / "docs" / "plans").mkdir(parents=True, exist_ok=True)
        archived = self._plan(tmp_path, ("docs", "archive", "plans-completed"), 2741)

        raw = {
            "REVIEW": "pending",
            "PATCH": "completed",
            "_verdicts": {},
            "_sdlc_dispatches": [{"skill": "/do-pr-review", "stage": "REVIEW"}],
        }
        msg = _mid_pipeline_entry_diagnostic(2741, raw)
        assert msg is not None, "an archived plan is still evidence PLAN was owed"
        assert str(archived) in msg

    def test_plan_lookup_failure_does_not_fabricate_a_diagnostic(self, tmp_path, monkeypatch):
        """The detector is observability: it must never raise into the refusal path."""
        import tools.lane_identity as li
        from tools.sdlc_stage_marker import _mid_pipeline_entry_diagnostic

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        monkeypatch.setattr(li, "find_plan_path", lambda n: (_ for _ in ()).throw(OSError("boom")))
        monkeypatch.setattr(
            li, "find_archived_plan_path", lambda n: (_ for _ in ()).throw(OSError("boom"))
        )

        raw = {"REVIEW": "pending", "_verdicts": {"REVIEW": {"verdict": "APPROVED"}}}
        msg = _mid_pipeline_entry_diagnostic(2734, raw)
        assert msg is not None and "REVIEW" in msg

    def test_ledger_read_path_produces_the_diagnostic(self, tmp_path, monkeypatch):
        """Exercise the exact call the refusal path makes, ledger read included."""
        import json

        from tools.sdlc_stage_marker import _mid_pipeline_entry_for_ledger

        monkeypatch.setenv("SDLC_TARGET_REPO", str(tmp_path))
        self._plan(tmp_path, ("docs", "plans"), 2734)

        from agent.pipeline_ledger import PipelineLedger

        # A real (unsaved) PipelineLedger: `_load_raw_states` picks its field by
        # `isinstance`, so a duck-typed double would silently read `{}` and the
        # detector would look correct while never seeing the state.
        ledger = PipelineLedger(
            ledger_key="tomcounsell/ai:2734",
            issue_number=2734,
            stage_states_json=json.dumps(
                {
                    "REVIEW": "pending",
                    "_verdicts": {"REVIEW": {"verdict": "APPROVED"}},
                    "_sdlc_dispatches": [{"skill": "/do-pr-review", "stage": "REVIEW"}],
                }
            ),
        )

        msg = _mid_pipeline_entry_for_ledger(2734, ledger)
        assert msg is not None and "MID_PIPELINE_ENTRY" in msg

    def test_unreadable_ledger_yields_no_diagnostic_rather_than_raising(self):
        """A detector that raised would replace a correct refusal with a crash."""
        from tools.sdlc_stage_marker import _mid_pipeline_entry_for_ledger

        assert _mid_pipeline_entry_for_ledger(2734, object()) is None

    def test_refusal_message_carries_the_diagnostic(self):
        """The wiring: the generic refusal must surface the specific signal."""
        import inspect

        import tools.sdlc_stage_marker as m

        src = inspect.getsource(m._write_marker_impl)
        assert "_mid_pipeline_entry_for_ledger" in src, (
            "STATE_MACHINE_REJECTED must name mid-pipeline entry when it is the cause"
        )


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
