"""Unit tests for validate_verification_section.py hook validator.

Two families: the pure section parser, and targeting — the validator must judge
the file the hook payload named and no other (#2689).
"""

import json
import subprocess
import sys
from pathlib import Path

import pytest

# Hook scripts live in .claude/hooks/validators/ and import from .claude/hooks/hook_utils/
HOOKS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks"
VALIDATORS_DIR = HOOKS_DIR / "validators"
if str(HOOKS_DIR) not in sys.path:
    sys.path.insert(0, str(HOOKS_DIR))
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))


def import_validator():
    """Import the validator module."""
    import validate_verification_section

    return validate_verification_section


class TestExtractVerificationSection:
    """Tests for extracting the ## Verification section."""

    def test_extracts_section(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Test | `echo hi` | exit code 0 |

## Open Questions

None.
"""
        section = mod.extract_verification_section(content)
        assert section is not None
        assert "echo hi" in section

    def test_returns_none_when_missing(self):
        mod = import_validator()
        content = """\
## Problem

Something.

## Success Criteria

- [ ] Done
"""
        section = mod.extract_verification_section(content)
        assert section is None


class TestIsSectionComplete:
    """Tests for checking section completeness."""

    def test_valid_table(self):
        mod = import_validator()
        section = """\
| Check | Command | Expected |
|-------|---------|----------|
| Test | `echo hi` | exit code 0 |
"""
        is_complete, reason = mod.is_section_complete(section)
        assert is_complete is True

    def test_empty_section(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("")
        assert is_complete is False

    def test_no_data_rows(self):
        mod = import_validator()
        section = """\
| Check | Command | Expected |
|-------|---------|----------|
"""
        is_complete, reason = mod.is_section_complete(section)
        assert is_complete is False

    def test_placeholder_text(self):
        mod = import_validator()
        is_complete, reason = mod.is_section_complete("[TODO]")
        assert is_complete is False


class TestValidateVerificationSection:
    """Integration tests for the full validation function."""

    def test_valid_plan(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "test-plan.md"
        plan.write_text("""\
---
status: Planning
type: feature
---

# Test Plan

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |

## Success Criteria

- [ ] Done
""")
        success, msg = mod.validate_verification_section(str(plan))
        assert success is True

    def test_missing_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "test-plan.md"
        plan.write_text("""\
---
status: Planning
type: feature
---

# Test Plan

## Success Criteria

- [ ] Done
""")
        success, msg = mod.validate_verification_section(str(plan))
        assert success is False
        assert "missing" in msg.lower() or "VALIDATION FAILED" in msg

    def test_incomplete_section(self, tmp_path):
        mod = import_validator()
        plan = tmp_path / "test-plan.md"
        plan.write_text("""\
---
status: Planning
type: feature
---

# Test Plan

## Verification

TBD

## Success Criteria

- [ ] Done
""")
        success, msg = mod.validate_verification_section(str(plan))
        assert success is False


GOOD_PLAN = """\
# A plan

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest -q` | exit code 0 |
"""

BAD_PLAN = """\
# Other lane's plan

## Problem

Something, with no Verification section anywhere in it.
"""

VALIDATOR = VALIDATORS_DIR / "validate_verification_section.py"


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
    )


def _git(root: Path, *args: str) -> None:
    subprocess.run(
        [
            "git",
            "-c",
            "core.hooksPath=/dev/null",
            "-c",
            "user.email=test@example.com",
            "-c",
            "user.name=Test",
            *args,
        ],
        cwd=str(root),
        check=True,
        capture_output=True,
        timeout=30,
    )


def make_cross_lane_repo(root: Path) -> Path:
    """A repo whose docs/plans/ holds a TRACKED anchor plus another lane's plan.

    The tracked anchor is load-bearing, not decoration. With no tracked file
    under ``docs/plans/``, ``git status --porcelain docs/plans/`` collapses the
    whole directory to a single ``?? docs/plans/`` line whose path does not end
    in ``.md``. The deleted guesser's suffix filter dropped that line, returned
    None, and the validator exited 0 looking innocent — so a fixture without the
    anchor passes against the *unfixed* code and proves nothing (#2689, and the
    "Data prerequisite" note under Race 1 of the plan).
    """
    plans = root / "docs" / "plans"
    plans.mkdir(parents=True, exist_ok=True)
    (plans / "anchor.md").write_text(GOOD_PLAN)
    _git(root, "init", "-q")
    _git(root, "add", "docs/plans/anchor.md")
    _git(root, "commit", "-q", "-m", "anchor")
    # The other lane's in-progress plan: untracked, deficient, newest by mtime.
    (plans / "zzz-other-lane.md").write_text(BAD_PLAN)
    return root


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

    def test_cross_lane_write_is_not_judged(self, tmp_path):
        """The #2689 reproducer: another lane's bad plan is sitting right there."""
        make_cross_lane_repo(tmp_path)
        payload = {"tool_name": "Write", "tool_input": {"file_path": "docs/features/mine.md"}}
        proc = run_hook(payload, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "zzz-other-lane" not in proc.stderr

    def test_cross_lane_source_write_is_not_judged(self, tmp_path):
        make_cross_lane_repo(tmp_path)
        payload = {"tool_name": "Write", "tool_input": {"file_path": "tools/foo.py"}}
        assert run_hook(payload, cwd=tmp_path).returncode == 0

    def test_your_own_deficient_plan_is_still_blocked(self, tmp_path):
        """Fail-closed direction: the fix must not disarm the guard."""
        make_cross_lane_repo(tmp_path)
        (tmp_path / "docs" / "plans" / "mine.md").write_text(BAD_PLAN)
        payload = {"tool_input": {"file_path": "docs/plans/mine.md"}}
        proc = run_hook(payload, cwd=tmp_path)
        assert proc.returncode == 2
        assert "docs/plans/mine.md" in proc.stderr
        assert "zzz-other-lane" not in proc.stderr

    def test_your_own_compliant_plan_passes(self, tmp_path):
        make_cross_lane_repo(tmp_path)
        (tmp_path / "docs" / "plans" / "mine.md").write_text(GOOD_PLAN)
        proc = run_hook({"tool_input": {"file_path": "docs/plans/mine.md"}}, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr

    def test_absolute_payload_path_is_enforced(self, tmp_path):
        make_cross_lane_repo(tmp_path)
        plan = tmp_path / "docs" / "plans" / "mine.md"
        plan.write_text(BAD_PLAN)
        assert run_hook({"tool_input": {"file_path": str(plan)}}, cwd=tmp_path).returncode == 2

    @pytest.mark.parametrize("payload", [None, "", "not json", "{}", {"tool_input": {}}])
    def test_absent_or_malformed_input_passes_through(self, payload, tmp_path):
        make_cross_lane_repo(tmp_path)
        assert run_hook(payload, cwd=tmp_path).returncode == 0

    @pytest.mark.parametrize("raw", ["null", "[1,2]", '"str"', "42"])
    def test_non_dict_payload_exits_zero_without_a_traceback(self, raw, tmp_path):
        """Syntactically valid JSON that is not an object must not raise."""
        make_cross_lane_repo(tmp_path)
        proc = run_hook(raw, cwd=tmp_path)
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr

    def test_unresolvable_plan_path_does_not_block(self, tmp_path):
        make_cross_lane_repo(tmp_path)
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
        )
        assert proc.returncode == 2
        assert "does not exist" in proc.stderr

    def test_non_plan_path_passes_even_when_it_does_not_exist(self, tmp_path):
        """The scope filter must precede the existence check."""
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), "tools/does-not-exist.py"],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
        )
        assert proc.returncode == 0, proc.stderr
