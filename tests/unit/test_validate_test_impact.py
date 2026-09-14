"""Unit tests for validate_test_impact_section.py hook validator.

Two families: the pure section parser, and targeting — the validator must judge
the file the hook payload named and no other (#2689).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

from tests.db_claim import subprocess_env

# Hook scripts live in .claude/hooks/validators/ and import from .claude/hooks/hook_utils/
HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
VALIDATORS_DIR = HOOKS_DIR / "validators"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))


def import_validator():
    """Import the validator module."""
    import validate_test_impact_section

    return validate_test_impact_section


class TestExtractTestImpactSection:
    """Tests for extracting the ## Test Impact section."""

    def test_extracts_section(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Test Impact

- [ ] `tests/unit/test_foo.py::test_bar` — UPDATE: new assertion

## Rabbit Holes

None.
"""
        section = mod.extract_test_impact_section(content)
        assert section is not None
        assert "UPDATE" in section

    def test_returns_none_when_missing(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Rabbit Holes

None.
"""
        section = mod.extract_test_impact_section(content)
        assert section is None

    def test_extracts_section_at_end_of_file(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Test Impact

No existing tests affected — this is greenfield with no prior coverage.
"""
        section = mod.extract_test_impact_section(content)
        assert section is not None
        assert "greenfield" in section


class TestIsSectionComplete:
    """Tests for checking section completeness."""

    def test_accepts_checklist_with_dispositions(self):
        mod = import_validator()
        content = (
            "- [ ] `tests/unit/test_foo.py::test_bar` — UPDATE: new return value\n"
            "- [ ] `tests/unit/test_baz.py::test_qux` — DELETE: removed feature"
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete
        assert "disposition" in reason.lower()

    def test_accepts_single_disposition(self):
        mod = import_validator()
        content = "- [ ] `tests/unit/test_foo.py::test_bar` — REPLACE: rewrite for new API contract"
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete

    def test_accepts_no_tests_affected_with_justification(self):
        mod = import_validator()
        content = (
            "No existing tests affected — this is a greenfield feature with no prior test coverage."
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete
        assert "no existing tests affected" in reason.lower()

    def test_rejects_no_tests_affected_without_justification(self):
        mod = import_validator()
        content = "No existing tests affected"
        is_complete, reason = mod.is_section_complete(content)
        assert not is_complete
        assert "too brief" in reason.lower()

    def test_rejects_empty_section(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("")
        assert not is_complete
        assert "empty" in reason.lower()

    def test_rejects_placeholder_tbd(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("TBD")
        assert not is_complete
        assert "placeholder" in reason.lower()

    def test_rejects_placeholder_dots(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("...")
        assert not is_complete
        assert "placeholder" in reason.lower()

    def test_rejects_placeholder_bracket(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("[Fill in later]")
        assert not is_complete
        assert "placeholder" in reason.lower()

    def test_rejects_too_brief_content(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("Some short text.")
        assert not is_complete
        assert "too brief" in reason.lower()

    def test_accepts_checklists_without_dispositions_if_substantive(self):
        mod = import_validator()
        content = (
            "- [ ] `tests/unit/test_foo.py` needs to be updated for the new interface changes\n"
            "- [ ] `tests/integration/test_bar.py` should be modified to use new endpoint"
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete
        assert "checklist" in reason.lower() or "items" in reason.lower()

    def test_case_insensitive_dispositions(self):
        mod = import_validator()
        content = (
            "- [ ] `tests/unit/test_foo.py::test_bar`"
            " — update: new assertion value for changed output"
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete

    def test_checked_items_accepted(self):
        mod = import_validator()
        content = (
            "- [x] `tests/unit/test_foo.py::test_bar`"
            " — UPDATE: new assertion value for changed output"
        )
        is_complete, reason = mod.is_section_complete(content)
        assert is_complete


class TestValidateTestImpactSection:
    """Tests for the full validation function."""

    def test_passes_valid_plan(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("""\
## Problem

Something.

## Test Impact

- [ ] `tests/unit/test_foo.py::test_bar` — UPDATE: assert new return value

## Rabbit Holes

None.
""")
        success, message = mod.validate_test_impact_section(str(plan))
        assert success

    def test_fails_missing_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("""\
## Problem

Something.

## Rabbit Holes

None.
""")
        success, message = mod.validate_test_impact_section(str(plan))
        assert not success
        assert "missing" in message.lower()

    def test_fails_empty_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("""\
## Problem

Something.

## Test Impact

## Rabbit Holes

None.
""")
        success, message = mod.validate_test_impact_section(str(plan))
        assert not success

    def test_fails_nonexistent_file(self):
        mod = import_validator()
        success, message = mod.validate_test_impact_section("/nonexistent/path.md")
        assert not success
        assert "failed to read" in message.lower()

    def test_passes_exemption_plan(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("""\
## Problem

Something.

## Test Impact

No existing tests affected — this is a greenfield feature with no prior test coverage.

## Rabbit Holes

None.
""")
        success, message = mod.validate_test_impact_section(str(plan))
        assert success


GOOD_PLAN = """\
# A plan

## Test Impact

- [ ] tests/unit/test_thing.py — UPDATE: add the targeting class
- [ ] tests/unit/test_other.py — DELETE: the behavior it pins is gone
"""

BAD_PLAN = """\
# Other lane's plan

## Problem

Something, with no Test Impact section anywhere in it.
"""

VALIDATOR = VALIDATORS_DIR / "validate_test_impact_section.py"


def run_hook(payload: dict | str | None, cwd: Path) -> subprocess.CompletedProcess:
    """Invoke the validator the way the harness does: JSON on stdin, no argv."""
    stdin = payload if isinstance(payload, str) else json.dumps(payload or {})
    return subprocess.run(
        [sys.executable, str(VALIDATOR)],
        input=stdin,
        capture_output=True,
        text=True,
        cwd=str(cwd),
        timeout=30,
        env=subprocess_env(),
    )


@pytest.fixture
def cross_lane(cross_lane_repo, tmp_path):
    """The shared #2689 reproducer, wired with this module's plan bodies."""
    return cross_lane_repo(tmp_path, GOOD_PLAN, BAD_PLAN)


class TestTargetIsTheFileTheHookNamed:
    """#2689: a lane must never be gated on a plan doc it did not touch."""

    def test_newest_plan_fallback_is_gone(self):
        mod = import_validator()
        assert not hasattr(mod, "find_newest_plan_file"), (
            "the git-status/mtime fallback is the defect; it must not come back"
        )

    def test_target_comes_from_the_payload(self):
        mod = import_validator()
        payload = {"tool_input": {"file_path": "docs/plans/x.md"}}
        assert mod.target_from_hook_input(payload) == "docs/plans/x.md"

    def test_cross_lane_write_is_not_judged(self, cross_lane, tmp_path):
        """The #2689 reproducer: another lane's bad plan is sitting right there."""
        payload = {"tool_name": "Write", "tool_input": {"file_path": "docs/features/mine.md"}}
        proc = run_hook(payload, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "zzz-other-lane" not in proc.stderr

    def test_cross_lane_source_write_is_not_judged(self, cross_lane, tmp_path):
        payload = {"tool_name": "Write", "tool_input": {"file_path": "tools/foo.py"}}
        assert run_hook(payload, cwd=tmp_path).returncode == 0

    def test_your_own_deficient_plan_is_still_blocked(self, cross_lane, tmp_path):
        """Fail-closed direction: the fix must not disarm the guard."""
        (tmp_path / "docs" / "plans" / "mine.md").write_text(BAD_PLAN)
        payload = {"tool_input": {"file_path": "docs/plans/mine.md"}}
        proc = run_hook(payload, cwd=tmp_path)
        assert proc.returncode == 2
        assert "docs/plans/mine.md" in proc.stderr
        assert "zzz-other-lane" not in proc.stderr

    def test_your_own_compliant_plan_passes(self, cross_lane, tmp_path):
        (tmp_path / "docs" / "plans" / "mine.md").write_text(GOOD_PLAN)
        proc = run_hook({"tool_input": {"file_path": "docs/plans/mine.md"}}, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr

    def test_absolute_payload_path_is_enforced(self, cross_lane, tmp_path):
        plan = tmp_path / "docs" / "plans" / "mine.md"
        plan.write_text(BAD_PLAN)
        assert run_hook({"tool_input": {"file_path": str(plan)}}, cwd=tmp_path).returncode == 2

    @pytest.mark.parametrize("payload", [None, "", "not json", "{}", {"tool_input": {}}])
    def test_absent_or_malformed_input_passes_through(self, payload, cross_lane, tmp_path):
        assert run_hook(payload, cwd=tmp_path).returncode == 0

    @pytest.mark.parametrize("raw", ["null", "[1,2]", '"str"', "42"])
    def test_non_dict_payload_exits_zero_without_a_traceback(self, raw, cross_lane, tmp_path):
        """Syntactically valid JSON that is not an object must not raise."""
        proc = run_hook(raw, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr

    def test_unresolvable_plan_path_does_not_block(self, cross_lane, tmp_path):
        payload = {"tool_input": {"file_path": "docs/plans/never-written.md"}}
        assert run_hook(payload, cwd=tmp_path).returncode == 0

    def test_explicit_cli_path_that_names_nothing_is_reported(self, tmp_path):
        """An argv path is a user error worth reporting; a payload path is not."""
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), "docs/plans/does-not-exist.md"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
            env=subprocess_env(),
        )
        assert proc.returncode == 2
        assert "does not exist" in proc.stderr

    @pytest.mark.parametrize(
        "path",
        ["docs/plans_archive/old.md", "docs/plansomething/x.md", "docs/plans/helper.py"],
    )
    def test_lookalike_and_wrong_extension_paths_are_out_of_scope(self, path, cross_lane, tmp_path):
        """The scope filter is anchored on a path segment and on the extension.

        The unanchored predicate this replaced (``"docs/plans" not in path``)
        exits 2 on every one of these: a sibling archive directory, a prefix
        lookalike, and a Python helper that has no business carrying a plan
        section at all.
        """
        target = tmp_path / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(BAD_PLAN)
        proc = run_hook({"tool_name": "Write", "tool_input": {"file_path": path}}, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr

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
