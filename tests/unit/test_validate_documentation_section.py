"""Unit tests for validate_documentation_section.py hook validator.

Two families: the pure section parser (``extract_documentation_section`` /
``is_section_complete``), and targeting — the validator must judge the file the
hook payload named and no other (#2689).
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
    import validate_documentation_section

    return validate_documentation_section


class TestExtractDocumentationSection:
    """Tests for extracting the ## Documentation section."""

    def test_extracts_section(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Documentation

- [ ] Write docs/features/thing.md
- [ ] Add the README index row

## Verification

None.
"""
        section = mod.extract_documentation_section(content)
        assert section is not None
        assert "docs/features/thing.md" in section
        assert "Verification" not in section

    def test_extracts_trailing_section_at_end_of_file(self):
        """``\\Z`` closes the match when Documentation is the last section."""
        mod = import_validator()
        content = "## Problem\n\nSomething.\n\n## Documentation\n\n- [ ] a\n- [ ] b\n"
        section = mod.extract_documentation_section(content)
        assert section == "- [ ] a\n- [ ] b"

    def test_returns_none_when_missing(self):
        mod = import_validator()
        assert mod.extract_documentation_section("## Problem\n\nSomething.\n") is None

    def test_subsection_headings_stay_inside(self):
        mod = import_validator()
        content = "## Documentation\n\n### Feature Documentation\n\n- [ ] a\n- [ ] b\n"
        section = mod.extract_documentation_section(content)
        assert "### Feature Documentation" in section


class TestIsSectionComplete:
    """Tests for checking section completeness."""

    def test_empty_section(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("")
        assert is_complete is False
        assert "empty" in reason.lower()

    def test_two_checklist_items_pass(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("- [ ] a\n- [ ] b")
        assert is_complete is True
        assert "2" in reason

    def test_single_checklist_item_is_not_enough(self):
        mod = import_validator()
        section = "- [ ] Write the one doc that this change needs, and nothing else at all."
        is_complete, _reason = mod.is_section_complete(section)
        assert is_complete is False

    def test_explicit_no_docs_needed_with_explanation_passes(self):
        mod = import_validator()
        section = (
            "No documentation changes needed: this is an internal refactor of a "
            "private helper with no user-visible surface."
        )
        is_complete, reason = mod.is_section_complete(section)
        assert is_complete is True
        assert "no documentation needed" in reason.lower()

    def test_bare_no_docs_needed_without_explanation_fails(self):
        """The exemption requires an explanation, not just the magic phrase."""
        mod = import_validator()
        is_complete, _reason = mod.is_section_complete("No documentation needed.")
        assert is_complete is False

    @pytest.mark.parametrize("placeholder", ["[TODO]", "TBD", "TODO", "...", "[fill this in]"])
    def test_placeholder_text_fails(self, placeholder):
        mod = import_validator()
        is_complete, _reason = mod.is_section_complete(placeholder)
        assert is_complete is False

    def test_long_prose_without_checklists_fails(self):
        mod = import_validator()
        section = (
            "We will document this properly at some point, in a way that is "
            "definitely longer than fifty characters but names no actual task."
        )
        is_complete, reason = mod.is_section_complete(section)
        assert is_complete is False
        assert "checklist" in reason.lower()


GOOD_PLAN = """\
# A plan

## Documentation

### Feature Documentation
- [ ] Create `docs/features/thing.md`
- [ ] Add the `docs/features/README.md` index row
"""

BAD_PLAN = """\
# Other lane's plan

## Problem

Something, with no Documentation section anywhere in it.
"""


class TestValidateDocumentationSection:
    """The file-level validation function."""

    def test_valid_plan(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text(GOOD_PLAN)
        success, msg = mod.validate_documentation_section(str(plan))
        assert success is True, msg

    def test_missing_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text(BAD_PLAN)
        success, msg = mod.validate_documentation_section(str(plan))
        assert success is False
        assert "missing a ## Documentation section" in msg

    def test_incomplete_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "plan.md"
        plan.write_text("# A plan\n\n## Documentation\n\nTBD\n")
        success, msg = mod.validate_documentation_section(str(plan))
        assert success is False
        assert "incomplete" in msg.lower()

    def test_unreadable_file_is_reported_not_raised(self, tmp_path):
        mod = import_validator()
        success, msg = mod.validate_documentation_section(str(tmp_path / "nope.md"))
        assert success is False
        assert "Failed to read file" in msg


VALIDATOR = VALIDATORS_DIR / "validate_documentation_section.py"


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
        payload = {"tool_name": "Write", "tool_input": {"file_path": "docs/plans/mine.md"}}
        proc = run_hook(payload, cwd=tmp_path)
        assert proc.returncode == 2
        assert "docs/plans/mine.md" in proc.stderr
        assert "zzz-other-lane" not in proc.stderr

    def test_your_own_compliant_plan_passes(self, cross_lane, tmp_path):
        (tmp_path / "docs" / "plans" / "mine.md").write_text(GOOD_PLAN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": "docs/plans/mine.md"}}
        proc = run_hook(payload, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr

    def test_absolute_payload_path_is_enforced(self, cross_lane, tmp_path):
        plan = tmp_path / "docs" / "plans" / "mine.md"
        plan.write_text(BAD_PLAN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(plan)}}
        assert run_hook(payload, cwd=tmp_path).returncode == 2

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
