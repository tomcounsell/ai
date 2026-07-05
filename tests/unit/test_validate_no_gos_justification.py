"""Unit tests for validate_no_gos_justification.py hook validator.

Focused on the PUNT_PHRASES post-merge patterns: descriptive uses of the
hyphenated phrase must pass, deferral constructions must fail (issue #1900
review flagged the old bare \\bpost-merge\\b pattern as a false-positive on
every mention).
"""

import sys
from pathlib import Path

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
