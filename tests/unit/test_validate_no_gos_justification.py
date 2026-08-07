"""Unit tests for validate_no_gos_justification.py hook validator.

Two families here.

The PUNT_PHRASES patterns: descriptive uses of a phrase must pass, deferral
constructions must fail (issue #1900 flagged the old bare \\bpost-merge\\b
pattern as a false-positive on every mention; #2682 flagged \\boperator will\\b
the same way).

Targeting and quoted context (#2682): the validator must judge only the file
the triggering Write named, and must not fire on deferral language a plan
*quotes* while recording its own critique.
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Hook scripts live in .claude/hooks/validators/
VALIDATORS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "validators"
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))


def import_validator():
    import validate_no_gos_justification

    return validate_no_gos_justification


def plan_with_body(body: str) -> str:
    """Wrap a body line in a minimal plan that has a valid No-Gos section."""
    return f"""\
## Problem

Something.

{body}

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.
"""


class TestPostMergeDescriptiveUsesPass:
    """Descriptive 'post-merge' mentions are not punts."""

    def test_post_merge_extraction_prose(self):
        mod = import_validator()
        assert not mod.line_is_punt("Post-merge memory extraction distills PR takeaways.")

    def test_runs_post_merge(self):
        mod = import_validator()
        assert not mod.line_is_punt("The merged-branch-cleanup reflection runs post-merge.")

    def test_post_merge_learning_path(self):
        mod = import_validator()
        assert not mod.line_is_punt("This documents the post-merge learning-extraction path.")

    def test_is_handled_post_merge_present_tense(self):
        mod = import_validator()
        assert not mod.line_is_punt("Plan migration is handled post-merge by the reflection.")

    def test_full_plan_with_descriptive_mention_validates(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text(plan_with_body("The post-merge hook fires automatically on issue close."))
        ok, message = mod.validate(str(plan))
        assert ok, message


class TestPostMergePuntConstructionsFail:
    """Deferral constructions around 'post-merge' are still punts."""

    def test_deferred_post_merge(self):
        mod = import_validator()
        assert mod.line_is_punt("Cleanup is deferred to post-merge.")

    def test_left_for_post_merge(self):
        mod = import_validator()
        assert mod.line_is_punt("The sweep is left for a post-merge pass.")

    def test_will_be_done_post_merge(self):
        mod = import_validator()
        assert mod.line_is_punt("The backfill will be done post-merge.")

    def test_to_be_fixed_post_merge(self):
        mod = import_validator()
        assert mod.line_is_punt("Known flake to be fixed post-merge.")

    def test_post_merge_follow_up(self):
        mod = import_validator()
        assert mod.line_is_punt("Post-merge follow-up: prune old plans.")

    def test_tagged_punt_still_passes_validate(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text(
            plan_with_body("[ORDERED] Deploy verification deferred to post-merge, human-gated.")
        )
        ok, message = mod.validate(str(plan))
        assert ok, message

    def test_untagged_punt_fails_validate(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text(plan_with_body("The backfill will be done post-merge."))
        ok, message = mod.validate(str(plan))
        assert not ok
        assert "unjustified deferrals" in message


class TestOtherPuntPhrasesUnchanged:
    """The surrounding patterns keep their behavior."""

    def test_operator_will(self):
        mod = import_validator()
        assert mod.line_is_punt("The operator will rotate the key.")

    def test_punted_to(self):
        mod = import_validator()
        assert mod.line_is_punt("Punted to the next sprint.")

    def test_missing_no_gos_section_fails(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("## Problem\n\nSomething.\n")
        ok, message = mod.validate(str(plan))
        assert not ok
        assert "missing a ## No-Gos section" in message


VALIDATOR = VALIDATORS_DIR / "validate_no_gos_justification.py"


def run_hook(payload: dict | str | None, cwd: Path) -> int:
    """Invoke the validator the way the harness does: JSON on stdin, no argv."""
    stdin = payload if isinstance(payload, str) else json.dumps(payload or {})
    proc = subprocess.run(
        [sys.executable, str(VALIDATOR)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=30,
    )
    return proc.returncode


def write_plan(root: Path, name: str, body: str) -> Path:
    plans = root / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    path = plans / name
    path.write_text(body)
    return path


BAD_PLAN = """\
# Other lane's plan

## No-Gos (Out of Scope)

- Rate limiting deferred to a follow-up issue.
"""


class TestTargetIsTheFileTheWriteNamed:
    """#2682: a lane must never be gated on a plan it did not touch."""

    def test_newest_plan_fallback_is_gone(self):
        mod = import_validator()
        assert not hasattr(mod, "find_newest_plan_file"), (
            "the git-status/mtime fallback is the defect; it must not come back"
        )

    def test_reads_file_path_from_tool_input(self):
        mod = import_validator()
        payload = {"tool_input": {"file_path": "docs/plans/x.md"}}
        assert mod.target_from_hook_input(payload) == "docs/plans/x.md"

    def test_reads_notebook_path(self):
        mod = import_validator()
        assert mod.target_from_hook_input({"tool_input": {"notebook_path": "a.ipynb"}}) == "a.ipynb"

    @pytest.mark.parametrize(
        "payload",
        [
            {},
            {"tool_input": None},
            {"tool_input": "nope"},
            {"tool_input": {}},
            {"tool_input": {"file_path": ""}},
        ],
    )
    def test_unresolvable_target_is_none_not_a_guess(self, payload):
        mod = import_validator()
        assert mod.target_from_hook_input(payload) is None

    @pytest.mark.parametrize("bogus", [123, {"a": 1}, ["x"], True])
    def test_non_string_path_is_rejected(self, bogus):
        """A malformed payload must not hand a non-path downstream."""
        mod = import_validator()
        assert mod.target_from_hook_input({"tool_input": {"file_path": bogus}}) is None

    def test_unrelated_write_passes_while_a_bad_plan_sits_in_docs_plans(self, tmp_path):
        """The exact shape that blocked Workstream F."""
        write_plan(tmp_path, "other-lane.md", BAD_PLAN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": "docs/features/mine.md"}}
        assert run_hook(payload, cwd=tmp_path) == 0

    def test_non_plan_source_file_passes(self, tmp_path):
        write_plan(tmp_path, "other-lane.md", BAD_PLAN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": "tools/foo.py"}}
        assert run_hook(payload, cwd=tmp_path) == 0

    def test_the_plan_you_actually_wrote_is_still_enforced(self, tmp_path):
        plan = write_plan(tmp_path, "mine.md", BAD_PLAN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(plan)}}
        assert run_hook(payload, cwd=tmp_path) == 2

    def test_relative_plan_path_is_enforced(self, tmp_path):
        write_plan(tmp_path, "mine.md", BAD_PLAN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": "docs/plans/mine.md"}}
        assert run_hook(payload, cwd=tmp_path) == 2

    @pytest.mark.parametrize("payload", [None, "", "not json", {"tool_input": {}}])
    def test_absent_or_malformed_input_passes_through(self, payload, tmp_path):
        write_plan(tmp_path, "other-lane.md", BAD_PLAN)
        assert run_hook(payload, cwd=tmp_path) == 0

    def test_unresolvable_plan_path_does_not_block(self, tmp_path):
        payload = {"tool_input": {"file_path": "docs/plans/never-written.md"}}
        assert run_hook(payload, cwd=tmp_path) == 0

    def test_non_plan_path_passes_even_when_it_does_not_exist(self, tmp_path):
        """The plan-path check must precede the existence check.

        Reversed, a Write to any non-plan path the hook cannot stat — a
        relative path resolved against a different cwd, a file in another
        worktree — exits 2 on a file the validator would never have judged.
        """
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), "tools/does-not-exist.py"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr


class TestQuotedCritiqueContentDoesNotFalsePositive:
    """A plan that records its own review must not fail for quoting it."""

    def _plan(self, quoted_block: str) -> str:
        return f"""\
## Problem

Something.

## Critique Findings

{quoted_block}

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan.
"""

    def test_table_row_quoting_a_punt_passes(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            self._plan("| CONCERN | The honest fix is a follow-up issue naming the records. |")
        )
        ok, message = mod.validate(str(plan))
        assert ok, message

    def test_blockquote_quoting_a_punt_passes(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(self._plan("> Critic noted the work was deferred to a follow-up issue."))
        ok, message = mod.validate(str(plan))
        assert ok, message

    def test_fenced_code_quoting_a_punt_passes(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(self._plan('```\nerror: "punted to the next sprint"\n```'))
        ok, message = mod.validate(str(plan))
        assert ok, message

    def test_table_row_inside_no_gos_still_fails(self, tmp_path):
        """No-Gos is where commitments live; the exemption must not reach it."""
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            "## Problem\n\nThing.\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "| Item | Disposition |\n|---|---|\n"
            "| Rate limiting | Punted to the next sprint. |\n"
        )
        ok, message = mod.validate(str(plan))
        assert not ok
        assert "unjustified deferrals" in message

    def test_punt_in_ordinary_prose_outside_no_gos_still_fails(self, tmp_path):
        """The original intent — punts hide outside No-Gos — is preserved."""
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(plan_with_body("The retry backoff will be done post-merge."))
        ok, message = mod.validate(str(plan))
        assert not ok

    def test_committed_watchdog_plan_validates(self):
        """The #2682 repro fixture: a real plan whose critique tables tripped it."""
        candidates = [
            REPO_ROOT / "docs" / "plans" / "watchdog-import-time-log-handler.md",
            REPO_ROOT / "docs" / "plans" / "completed" / "watchdog-import-time-log-handler.md",
        ]
        plan = next((p for p in candidates if p.exists()), None)
        if plan is None:
            pytest.skip("watchdog plan not present in this checkout")
        mod = import_validator()
        ok, message = mod.validate(str(plan))
        assert ok, message


class TestOperatorWillIsActionSenseOnly:
    """#2682: 'operator will see X' describes impact; it defers nothing."""

    @pytest.mark.parametrize(
        "line",
        [
            "**What an operator will see change**: one log copy instead of two.",
            "An operator will notice the duplicate lines are gone.",
            "The operator will observe a single formatted record.",
            "A human will read the same message in both files.",
            "The operator will no longer see two copies.",
        ],
    )
    def test_perception_sense_is_not_a_punt(self, line):
        mod = import_validator()
        assert not mod.line_is_punt(line)

    @pytest.mark.parametrize(
        "line",
        [
            "The operator will rotate the signing key.",
            "The operator will run the migration by hand.",
            "A human will apply the config change.",
            "The operator will need to click through the vendor UI.",
        ],
    )
    def test_action_sense_is_still_a_punt(self, line):
        mod = import_validator()
        assert mod.line_is_punt(line)
