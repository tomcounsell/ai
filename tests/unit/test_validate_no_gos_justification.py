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

from tests.db_claim import subprocess_env

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

# Hook scripts live in .claude/hooks/validators/ and import from .claude/hooks/hook_utils/
HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
VALIDATORS_DIR = HOOKS_DIR / "validators"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
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
        env=subprocess_env(),
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

    def test_process_cwd_never_selects_the_target(self, tmp_path):
        """The hook process's cwd must not decide which file gets judged.

        Lane B holds a COMPLIANT plan at the same relative path lane A's
        deficient one occupies, and the hook process starts in lane B. The
        payload names lane A's file by absolute path and carries no ``cwd``
        key — the shape ``.opencode/plugins/valor-bridge.ts`` actually sends.

        A validator that resolves the target against its own cwd reads lane B's
        compliant plan and exits 0, clearing a write it never opened. The three
        sibling section validators pin this via the shared ``cross_lane_repo``
        fixture; this one keeps its own two-lane setup because its other
        targeting tests are already ``tmp_path``-based.
        """
        lane_a, lane_b = tmp_path / "laneA", tmp_path / "laneB"
        deficient = write_plan(lane_a, "mine.md", BAD_PLAN)
        write_plan(lane_b, "mine.md", plan_with_body("Nothing notable."))

        proc = subprocess.run(
            [sys.executable, str(VALIDATOR)],
            input=json.dumps({"tool_name": "Write", "tool_input": {"file_path": str(deficient)}}),
            capture_output=True,
            text=True,
            cwd=str(lane_b),
            timeout=30,
            env=subprocess_env(),
        )
        assert proc.returncode == 2
        assert str(deficient) in proc.stderr, "the refusal must cite lane A's plan, not lane B's"

    @pytest.mark.parametrize("payload", [None, "", "not json", {"tool_input": {}}])
    def test_absent_or_malformed_input_passes_through(self, payload, tmp_path):
        write_plan(tmp_path, "other-lane.md", BAD_PLAN)
        assert run_hook(payload, cwd=tmp_path) == 0

    def test_unresolvable_plan_path_does_not_block(self, tmp_path):
        payload = {"tool_input": {"file_path": "docs/plans/never-written.md"}}
        assert run_hook(payload, cwd=tmp_path) == 0

    @pytest.mark.parametrize(
        "path",
        ["docs/plans_archive/old.md", "docs/plansomething/x.md", "docs/plans/helper.py"],
    )
    def test_lookalike_and_wrong_extension_paths_are_out_of_scope(self, path, tmp_path):
        """The scope filter is anchored on a path segment and on the extension.

        The unanchored predicate this replaced (``"docs/plans" not in path``)
        exits 2 on every one of these: a sibling archive directory, a prefix
        lookalike, and a Python helper that has no business carrying a No-Gos
        section at all.
        """
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(BAD_PLAN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": path}}
        assert run_hook(payload, cwd=tmp_path) == 0

    def test_hook_derived_non_plan_path_passes_even_when_it_does_not_exist(self, tmp_path):
        """The scope filter must precede the existence check.

        Reversed, a Write to any non-plan path the hook cannot stat — a
        relative path resolved against a different cwd, a file in another
        worktree — exits 2 on a file the validator would never have judged.
        """
        payload = {"tool_name": "Write", "tool_input": {"file_path": "tools/does-not-exist.py"}}
        assert run_hook(payload, cwd=tmp_path) == 0

    def test_explicit_cli_path_bypasses_the_scope_filter(self, tmp_path):
        """Family-wide rule: an argv path was named on purpose, so it is judged.

        ``validate_file_contains.py`` has always worked this way. The section
        validators used to run the scope filter first, so the same flag shape
        meant the opposite thing: an operator-supplied path outside
        ``docs/plans`` was silently ignored instead of checked.
        """
        outside = tmp_path / "outside.md"
        outside.write_text(BAD_PLAN)
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), str(outside)],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
            env=subprocess_env(),
        )
        assert proc.returncode == 2
        assert str(outside) in proc.stderr


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

    @pytest.mark.parametrize("trailing_newline", [True, False])
    def test_table_row_inside_no_gos_still_fails(self, tmp_path, trailing_newline):
        """No-Gos is where commitments live; the exemption must not reach it.

        The no-trailing-newline variant pins the ``+ 1`` in
        ``no_gos_line_span``. ``end`` is computed by counting newlines, so when
        No-Gos is the last section and the file does not end in one, the final
        punt line contributes no newline and falls outside the scanned range
        without it — the guard silently returns clean on exactly the section it
        exists to police. Trailing newlines are an editor detail no plan author
        controls, so both shapes must fail.
        """
        mod = import_validator()
        plan = tmp_path / "p.md"
        content = (
            "## Problem\n\nThing.\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "| Item | Disposition |\n|---|---|\n"
            "| Rate limiting | Punted to the next sprint. |\n"
        )
        plan.write_text(content if trailing_newline else content.rstrip("\n"))
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


class TestFenceMarkersFollowCommonMark:
    """A fence closes only on a same-type marker at least as long (#2682).

    A bare open/close toggle desyncs on the first nested marker and stays
    desynced for the rest of the document, in the dangerous direction: the
    exemption evaporates and an unrelated agent's write gets blocked.
    """

    def test_four_backtick_wrapper_around_three_backtick_block_passes(self, tmp_path):
        """Documenting markdown-in-markdown must not leak its content."""
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            "# Plan\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "Nothing deferred — every relevant item is in scope for this plan.\n\n"
            "## Notes\n\n"
            "Documenting how to write a fenced block:\n\n"
            "````\n"
            "```\n"
            "- Rate limiting deferred to a follow-up issue\n"
            "```\n"
            "````\n"
        )
        ok, message = mod.validate(str(plan))
        assert ok, message

    def test_backtick_fence_nested_in_tilde_fence_does_not_desync(self, tmp_path):
        """A different-type marker inside a fence is content, not a delimiter."""
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            "# Plan\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "Nothing deferred — every relevant item is in scope for this plan.\n\n"
            "## Notes\n\n"
            "~~~\n"
            "```\n"
            "- Rate limiting deferred to a follow-up issue\n"
            "```\n"
            "~~~\n"
        )
        ok, message = mod.validate(str(plan))
        assert ok, message

    def test_ordinary_fence_still_closes_and_later_punts_still_fail(self, tmp_path):
        """The fix must not turn every fence into an open-ended exemption."""
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            "# Plan\n\n"
            "## Design\n\n"
            "```\n"
            "punted to the next sprint\n"
            "```\n\n"
            "The retry backoff will be done post-merge.\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "Nothing deferred — every relevant item is in scope for this plan.\n"
        )
        ok, message = mod.validate(str(plan))
        assert not ok
        assert "will be done post-merge" in message

    def test_indented_fence_still_opens(self, tmp_path):
        """The ``^\\s*`` allowance is deliberate; list-nested fences count."""
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            "# Plan\n\n"
            "## Design\n\n"
            "- Example:\n\n"
            "      ```\n"
            "      punted to the next sprint\n"
            "      ```\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "Nothing deferred — every relevant item is in scope for this plan.\n"
        )
        ok, message = mod.validate(str(plan))
        assert ok, message

    def test_inline_backticks_do_not_open_a_fence(self, tmp_path):
        """A mid-line code span does not open a fence (the ``^`` anchor).

        Scope note: this pins the mid-line case only. A line-initial span of
        three or more backticks (a line beginning ```foo```) does open a fence
        that never closes, swallowing later punts. That is pre-existing, in the
        fail-open direction, and out of scope here. Line-initial spans *shorter*
        than a fence are pinned by
        ``test_line_initial_short_code_span_does_not_open_a_fence``.
        """
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            "# Plan\n\n"
            "## Design\n\n"
            "Call ```foo``` then ```bar```.\n\n"
            "The retry backoff will be done post-merge.\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "Nothing deferred — every relevant item is in scope for this plan.\n"
        )
        ok, message = mod.validate(str(plan))
        assert not ok
        assert "will be done post-merge" in message

    @pytest.mark.parametrize("ticks", ["`", "``"])
    def test_line_initial_short_code_span_does_not_open_a_fence(self, tmp_path, ticks):
        """A fence needs three markers; one or two are an inline code span.

        Pins the ``{3,}`` floor in ``FENCE``. Relaxed to ``{1,}``, a line that
        merely *begins* with a code span — ```flag` enables it.``, ordinary
        plan prose — opens a fence that nothing ever closes, exempting every
        later line to EOF and disarming the validator for the whole document.
        """
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            "# Plan\n\n"
            "## Design\n\n"
            f"{ticks}flag{ticks} enables it.\n\n"
            "The retry backoff will be done post-merge.\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "Nothing deferred — every relevant item is in scope for this plan.\n"
        )
        ok, message = mod.validate(str(plan))
        assert not ok
        assert "will be done post-merge" in message

    def test_longer_closing_marker_still_closes_the_fence(self, tmp_path):
        """CommonMark: a ``` fence closes on a marker at least as long.

        Pins the ``>=`` in the close comparison. Tightened to ``==``, the
        fence would never close and the punt below it would be swallowed.
        """
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            "# Plan\n\n"
            "## Design\n\n"
            "```\n"
            "example\n"
            "````\n\n"
            "The retry backoff will be done post-merge.\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "Nothing deferred — every relevant item is in scope for this plan.\n"
        )
        ok, message = mod.validate(str(plan))
        assert not ok
        assert "will be done post-merge" in message

    def test_mismatched_fence_markers_fail_open_into_no_gos(self, tmp_path):
        """Decision on the record: mismatched markers exempt to EOF.

        A ````-opened block "closed" by a shorter ``` marker stays open for
        the rest of the document, and that reach extends into ``## No-Gos``,
        where the quoted-context exemption deliberately does not apply — so an
        unjustified punt there goes uncaught. This is the ACCEPTED fail-open
        direction, taken knowingly: fail-open can never block a lane, and
        CommonMark correctness is worth more than catching a malformed
        document. Asserting PASS documents the trade rather than endorsing it.
        """
        mod = import_validator()
        plan = tmp_path / "p.md"
        plan.write_text(
            "# Plan\n\n"
            "## Design\n\n"
            "````\n"
            "example\n"
            "```\n\n"
            "## No-Gos (Out of Scope)\n\n"
            "- Rate limiting deferred to a follow-up issue.\n"
        )
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
            # An adverb between "will" and the perception verb must not defeat
            # the exemption — impact prose is written this way constantly.
            "The operator will simply see one log line.",
            "The operator will then see the change.",
            "An operator will now observe a single record.",
            "The human will eventually receive the digest.",
            "The operator will still find both entries.",
            "A human will already know the outcome.",
            # "no longer" exempts any following verb, not just perception ones.
            "The operator will no longer rotate the key.",
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
            # The adverb slot admits ``\w+ly``, which over-matches verbs ending
            # in "ly". "apply" must stay a punt: it is not a perception verb,
            # and neither is the "the" that follows it.
            "The operator will apply the patch.",
            "The operator will supply the credentials.",
            # An adverb in front of a real action is still a punt.
            "The operator will manually rotate the signing key.",
        ],
    )
    def test_action_sense_is_still_a_punt(self, line):
        mod = import_validator()
        assert mod.line_is_punt(line)
