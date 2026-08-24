"""Unit tests for validate_file_contains.py hook validator.

This is the registered `PostToolUse` / `Write` guard that enforces the presence
of `## Success Criteria`, `## Update System`, `## Agent Integration`, and
`## Test Impact` in plan docs (manifest.toml `validate_file_contains`).

Both directions matter and both are asserted here. Fail-open is the dangerous
regression: if the port broke the enforcing path, every one of those presence
checks would silently stop firing while the hook kept exiting 0 and looking
healthy. So every "passes through" test has a matching "still blocks" twin.
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
    import validate_file_contains

    return validate_file_contains


# The exact strings the manifest registers this hook with.
REGISTERED_CONTAINS = [
    "## Success Criteria",
    "## Update System",
    "## Agent Integration",
    "## Test Impact",
]
CONTAINS_ARGS = [arg for s in REGISTERED_CONTAINS for arg in ("--contains", s)]

COMPLIANT_PLAN = """\
# A plan

## Success Criteria

- [ ] Done.

## Update System

No update system changes required.

## Agent Integration

No agent integration required.

## Test Impact

- [ ] tests/unit/test_thing.py — UPDATE
"""

# Missing ## Agent Integration and ## Test Impact.
DEFICIENT_PLAN = """\
# Other lane's plan

## Success Criteria

- [ ] Done.

## Update System

No update system changes required.
"""


class TestCheckContains:
    """The pure content predicate."""

    def test_all_present(self, tmp_path):
        mod = import_validator()
        f = tmp_path / "p.md"
        f.write_text(COMPLIANT_PLAN)
        all_found, found, missing = mod.check_contains(str(f), REGISTERED_CONTAINS)
        assert all_found is True
        assert missing == []
        assert len(found) == 4

    def test_reports_exactly_what_is_missing(self, tmp_path):
        mod = import_validator()
        f = tmp_path / "p.md"
        f.write_text(DEFICIENT_PLAN)
        all_found, _found, missing = mod.check_contains(str(f), REGISTERED_CONTAINS)
        assert all_found is False
        assert missing == ["## Agent Integration", "## Test Impact"]

    def test_match_is_case_insensitive(self, tmp_path):
        mod = import_validator()
        f = tmp_path / "p.md"
        f.write_text("## success criteria\n")
        all_found, _found, _missing = mod.check_contains(str(f), ["## Success Criteria"])
        assert all_found is True

    def test_unreadable_file_reports_everything_missing(self, tmp_path):
        mod = import_validator()
        all_found, found, missing = mod.check_contains(str(tmp_path / "gone.md"), ["## X"])
        assert all_found is False
        assert found == []
        assert missing == ["## X"]


class TestInScope:
    """`--directory` / `--extension` are the scope filter, nothing more.

    The validator uses the shared anchored predicate from ``hook_target``; these
    cases pin the pairing this hook is actually registered with. The predicate's
    own exhaustive coverage lives in ``test_hook_target.py``.
    """

    @pytest.mark.parametrize(
        "target,expected",
        [
            ("docs/plans/a.md", True),
            ("docs/plans/critiques/a.md", True),
            ("docs\\plans\\a.md", True),
            ("/abs/checkout/docs/plans/a.md", True),
            ("docs/features/a.md", False),
            ("docs/plans/a.py", False),
            ("tools/foo.py", False),
            ("docs/plansomething/a.md", False),
            # Archived plans are history, not live plans (#2878).
            ("docs/archive/plans-completed/a.md", False),
            ("docs/plans_archive/old.md", False),
            ("a.md", False),
        ],
    )
    def test_scope(self, target, expected):
        mod = import_validator()
        assert mod.in_scope(target, "docs/plans", ".md") is expected

    def test_extension_without_a_dot_is_accepted(self):
        mod = import_validator()
        assert mod.in_scope("docs/plans/a.md", "docs/plans", "md") is True

    def test_trailing_slash_on_directory_is_tolerated(self):
        mod = import_validator()
        assert mod.in_scope("docs/plans/a.md", "docs/plans/", ".md") is True


class TestCwdIsNeverAnInputToTargetSelection:
    """The payload's path is judged as given, never rewritten against a cwd."""

    def test_normalize_target_is_gone(self):
        """Rewriting an absolute payload path to a cwd-relative one was the defect.

        It made the payload's ``cwd`` key mandatory for an absolute path to be in
        scope (silently failing open when the harness sends none), and reduced the
        target to a relative path that the read then resolved against the *hook
        process's* cwd — a different lane's checkout.
        """
        mod = import_validator()
        assert not hasattr(mod, "normalize_target"), (
            "cwd-relative rewriting reintroduces the cross-lane defect"
        )


class TestValidate:
    """The file-level check, decoupled from argv and stdin."""

    def test_compliant_file_passes(self, tmp_path):
        mod = import_validator()
        f = tmp_path / "p.md"
        f.write_text(COMPLIANT_PLAN)
        success, msg = mod.validate(str(f), REGISTERED_CONTAINS)
        assert success is True, msg

    def test_deficient_file_fails_and_names_what_is_missing(self, tmp_path):
        mod = import_validator()
        f = tmp_path / "p.md"
        f.write_text(DEFICIENT_PLAN)
        success, msg = mod.validate(str(f), REGISTERED_CONTAINS)
        assert success is False
        assert "## Agent Integration" in msg
        assert "## Test Impact" in msg
        assert str(f) in msg

    def test_no_required_strings_is_a_pass(self, tmp_path):
        mod = import_validator()
        f = tmp_path / "p.md"
        f.write_text("anything")
        success, _msg = mod.validate(str(f), [])
        assert success is True


VALIDATOR = VALIDATORS_DIR / "validate_file_contains.py"


def run_hook(payload: dict | str | None, cwd: Path, *args: str) -> subprocess.CompletedProcess:
    """Invoke the validator the way the harness does: JSON on stdin, plus flags."""
    stdin = payload if isinstance(payload, str) else json.dumps(payload or {})
    return subprocess.run(
        [sys.executable, str(VALIDATOR), "-d", "docs/plans", "-e", ".md", *args],
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
    return cross_lane_repo(tmp_path, COMPLIANT_PLAN, DEFICIENT_PLAN)


class TestTargetIsTheFileTheHookNamed:
    """#2689: a lane must never be gated on a plan doc it did not touch."""

    def test_newest_file_fallback_is_gone(self):
        mod = import_validator()
        for gone in (
            "find_newest_file",
            "find_newest_plan_file",
            "get_git_new_files",
            "get_recent_files",
            "get_git_committed_files",
            "get_committed_file_content",
        ):
            assert not hasattr(mod, gone), f"{gone} is the defect; it must not come back"

    def test_max_age_flag_is_gone(self, tmp_path):
        """The knob only meant something while mtime selected the target."""
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), "--max-age", "300"],
            input="{}",
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
            env=subprocess_env(),
        )
        assert proc.returncode == 2
        assert "unrecognized arguments" in proc.stderr

    def test_target_comes_from_the_payload(self):
        mod = import_validator()
        payload = {"tool_input": {"file_path": "docs/plans/x.md"}}
        assert mod.target_from_hook_input(payload) == "docs/plans/x.md"

    def test_cross_lane_write_is_not_judged(self, cross_lane, tmp_path):
        """The #2689 reproducer: another lane's deficient plan is sitting right there."""
        payload = {"tool_name": "Write", "tool_input": {"file_path": "docs/features/mine.md"}}
        proc = run_hook(payload, tmp_path, *CONTAINS_ARGS)
        assert proc.returncode == 0, proc.stderr
        assert "zzz-other-lane" not in proc.stderr

    def test_cross_lane_source_write_is_not_judged(self, cross_lane, tmp_path):
        payload = {"tool_name": "Write", "tool_input": {"file_path": "tools/foo.py"}}
        assert run_hook(payload, tmp_path, *CONTAINS_ARGS).returncode == 0

    def test_out_of_scope_extension_exits_early(self, cross_lane, tmp_path):
        """A .py write inside docs/plans/ is still out of scope."""
        payload = {"tool_input": {"file_path": "docs/plans/helper.py"}}
        assert run_hook(payload, tmp_path, *CONTAINS_ARGS).returncode == 0

    def test_your_own_deficient_plan_is_still_blocked(self, cross_lane, tmp_path):
        """Fail-closed direction. This is the assertion that keeps the guard armed."""
        (tmp_path / "docs" / "plans" / "mine.md").write_text(DEFICIENT_PLAN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": "docs/plans/mine.md"}}
        proc = run_hook(payload, tmp_path, *CONTAINS_ARGS)
        assert proc.returncode == 2
        assert "docs/plans/mine.md" in proc.stderr
        assert "## Agent Integration" in proc.stderr
        assert "zzz-other-lane" not in proc.stderr

    @pytest.mark.parametrize("required", REGISTERED_CONTAINS)
    def test_each_registered_section_is_individually_enforced(self, required, cross_lane, tmp_path):
        """Drop exactly one required heading; the hook must still fire."""
        body = "\n".join(line for line in COMPLIANT_PLAN.splitlines() if line.strip() != required)
        (tmp_path / "docs" / "plans" / "mine.md").write_text(body + "\n")
        payload = {"tool_input": {"file_path": "docs/plans/mine.md"}}
        proc = run_hook(payload, tmp_path, *CONTAINS_ARGS)
        assert proc.returncode == 2, proc.stdout
        assert required in proc.stderr

    def test_your_own_compliant_plan_passes(self, cross_lane, tmp_path):
        (tmp_path / "docs" / "plans" / "mine.md").write_text(COMPLIANT_PLAN)
        payload = {"tool_input": {"file_path": "docs/plans/mine.md"}}
        proc = run_hook(payload, tmp_path, *CONTAINS_ARGS)
        assert proc.returncode == 0, proc.stderr

    def test_absolute_payload_path_with_cwd_is_enforced(self, cross_lane, tmp_path):
        plan = tmp_path / "docs" / "plans" / "mine.md"
        plan.write_text(DEFICIENT_PLAN)
        payload = {"cwd": str(tmp_path), "tool_input": {"file_path": str(plan)}}
        assert run_hook(payload, tmp_path, *CONTAINS_ARGS).returncode == 2

    def test_absolute_payload_path_with_cwd_passes_when_compliant(self, cross_lane, tmp_path):
        plan = tmp_path / "docs" / "plans" / "mine.md"
        plan.write_text(COMPLIANT_PLAN)
        payload = {"cwd": str(tmp_path), "tool_input": {"file_path": str(plan)}}
        proc = run_hook(payload, tmp_path, *CONTAINS_ARGS)
        assert proc.returncode == 0, proc.stderr

    def test_absolute_payload_path_without_cwd_is_enforced(self, cross_lane, tmp_path):
        """No `cwd` key at all — the shape `.opencode/plugins/valor-bridge.ts` sends.

        While the validator rewrote the absolute path relative to `payload["cwd"]`,
        an absent key left the absolute path unrewritten, `startswith("docs/plans/")`
        was false, and the hook exited 0 without ever reading the file: a silent
        fail-open on the exact writes it exists to police.
        """
        plan = tmp_path / "docs" / "plans" / "mine.md"
        plan.write_text(DEFICIENT_PLAN)
        payload = {"tool_name": "Write", "tool_input": {"file_path": str(plan)}}
        proc = run_hook(payload, tmp_path, *CONTAINS_ARGS)
        assert proc.returncode == 2, proc.stdout
        assert "## Agent Integration" in proc.stderr

    def test_payload_cwd_diverging_from_the_process_cwd_judges_the_payloads_file(
        self, cross_lane_repo, tmp_path
    ):
        """Two lanes, two checkouts: the file the payload named is the file judged.

        Lane A holds the deficient plan the payload points at; lane B holds a
        *compliant* plan at the same relative path and is where the hook process
        happens to start. Reducing the target to `docs/plans/p.md` made both the
        existence check and the content read resolve against lane B, so the hook
        reported success for a file it never opened.
        """
        lane_a = tmp_path / "lane-a"
        lane_b = tmp_path / "lane-b"
        for lane in (lane_a, lane_b):
            lane.mkdir()
            cross_lane_repo(lane, COMPLIANT_PLAN, DEFICIENT_PLAN)
        (lane_a / "docs" / "plans" / "p.md").write_text(DEFICIENT_PLAN)
        (lane_b / "docs" / "plans" / "p.md").write_text(COMPLIANT_PLAN)

        payload = {
            "cwd": str(lane_a),
            "tool_name": "Write",
            "tool_input": {"file_path": str(lane_a / "docs" / "plans" / "p.md")},
        }
        proc = run_hook(payload, lane_b, *CONTAINS_ARGS)
        assert proc.returncode == 2, proc.stdout
        assert "lane-a" in proc.stderr
        assert "contains all" not in proc.stdout

    def test_no_target_and_no_positional_file_exits_zero(self, cross_lane, tmp_path):
        """A bare invocation guesses nothing — it has nothing to judge."""
        proc = run_hook({}, tmp_path, *CONTAINS_ARGS)
        assert proc.returncode == 0, proc.stderr

    @pytest.mark.parametrize("payload", [None, "", "not json", "{}", {"tool_input": {}}])
    def test_absent_or_malformed_input_passes_through(self, payload, cross_lane, tmp_path):
        assert run_hook(payload, tmp_path, *CONTAINS_ARGS).returncode == 0

    @pytest.mark.parametrize("raw", ["null", "[1,2]", '"str"', "42"])
    def test_non_dict_payload_exits_zero_without_a_traceback(self, raw, cross_lane, tmp_path):
        """Syntactically valid JSON that is not an object must not raise."""
        proc = run_hook(raw, tmp_path, *CONTAINS_ARGS)
        assert proc.returncode == 0, proc.stderr
        assert "Traceback" not in proc.stderr

    def test_unresolvable_plan_path_does_not_block(self, cross_lane, tmp_path):
        payload = {"tool_input": {"file_path": "docs/plans/never-written.md"}}
        assert run_hook(payload, tmp_path, *CONTAINS_ARGS).returncode == 0

    def test_explicit_cli_path_that_names_nothing_is_reported(self, tmp_path):
        """An argv path is a user error worth reporting; a payload path is not."""
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), "docs/plans/does-not-exist.md", *CONTAINS_ARGS],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
            env=subprocess_env(),
        )
        assert proc.returncode == 2
        assert "does not exist" in proc.stderr

    def test_explicit_cli_path_is_checked_directly(self, tmp_path):
        """An argv path bypasses the scope filter: it was named on purpose."""
        f = tmp_path / "elsewhere.md"
        f.write_text(DEFICIENT_PLAN)
        proc = subprocess.run(
            [sys.executable, str(VALIDATOR), str(f), *CONTAINS_ARGS],
            capture_output=True,
            text=True,
            cwd=str(tmp_path),
            timeout=30,
            env=subprocess_env(),
        )
        assert proc.returncode == 2
        assert "## Test Impact" in proc.stderr
