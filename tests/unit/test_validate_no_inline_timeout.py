"""Unit tests for validate_no_inline_timeout.py hook validator (issue #1968, Task 4)."""

import sys
from pathlib import Path

# Hook scripts live in .claude/hooks/validators/
VALIDATORS_DIR = Path(__file__).resolve().parent.parent.parent / ".claude" / "hooks" / "validators"
if str(VALIDATORS_DIR) not in sys.path:
    sys.path.insert(0, str(VALIDATORS_DIR))


def import_validator():
    """Import the validator module."""
    import validate_no_inline_timeout

    return validate_no_inline_timeout


class TestFindViolationsFires:
    """The guard must FIRE (reject) on deliberately-violating fixtures."""

    def test_fires_on_subprocess_run_bare_int_literal(self):
        mod = import_validator()
        content = "import subprocess\nsubprocess.run(cmd, timeout=10)\n"
        violations = mod.find_violations(content, "example.py")
        assert len(violations) == 1
        assert "example.py:2" in violations[0]

    def test_fires_on_subprocess_popen_bare_literal(self):
        mod = import_validator()
        content = "import subprocess\nsubprocess.Popen(cmd, timeout=30.5)\n"
        violations = mod.find_violations(content, "example.py")
        assert len(violations) == 1

    def test_fires_on_requests_post_bare_literal(self):
        mod = import_validator()
        content = "import requests\nrequests.post(url, timeout=5)\n"
        violations = mod.find_violations(content, "example.py")
        assert len(violations) == 1

    def test_fires_on_multiline_call(self):
        mod = import_validator()
        content = (
            "import subprocess\n"
            "subprocess.run(\n"
            "    cmd,\n"
            "    capture_output=True,\n"
            "    timeout=10,\n"
            ")\n"
        )
        violations = mod.find_violations(content, "example.py")
        assert len(violations) == 1
        assert "example.py:5" in violations[0]

    def test_fires_on_negative_literal(self):
        mod = import_validator()
        content = "requests.get(url, timeout=-1)\n"
        violations = mod.find_violations(content, "example.py")
        assert len(violations) == 1


class TestFindViolationsPasses:
    """The guard must PASS (accept) on compliant fixtures."""

    def test_passes_settings_reference(self):
        mod = import_validator()
        content = "subprocess.run(cmd, timeout=settings.timeouts.git_subprocess_s)\n"
        violations = mod.find_violations(content, "example.py")
        assert violations == []

    def test_passes_named_constant_reference(self):
        mod = import_validator()
        content = "MY_NAMED_CONSTANT = 10\nsubprocess.run(cmd, timeout=MY_NAMED_CONSTANT)\n"
        violations = mod.find_violations(content, "example.py")
        assert violations == []

    def test_passes_lowercase_variable_reference(self):
        mod = import_validator()
        content = "requests.get(url, timeout=request_timeout)\n"
        violations = mod.find_violations(content, "example.py")
        assert violations == []

    def test_passes_with_allow_marker(self):
        mod = import_validator()
        content = "subprocess.run(cmd, timeout=10)  # timeout-guard: allow\n"
        violations = mod.find_violations(content, "example.py")
        assert violations == []

    def test_passes_unrelated_call(self):
        mod = import_validator()
        content = "asyncio.wait_for(coro, timeout=10)\n"
        violations = mod.find_violations(content, "example.py")
        assert violations == []


class TestFindViolationsEmptyMatchSet:
    """The guard must handle files with zero timeout literals without error."""

    def test_no_timeout_literals_at_all(self):
        mod = import_validator()
        content = "def add(a, b):\n    return a + b\n"
        violations = mod.find_violations(content, "example.py")
        assert violations == []

    def test_empty_file(self):
        mod = import_validator()
        violations = mod.find_violations("", "example.py")
        assert violations == []

    def test_calls_with_no_timeout_kwarg(self):
        mod = import_validator()
        content = "subprocess.run(cmd, capture_output=True)\n"
        violations = mod.find_violations(content, "example.py")
        assert violations == []


class TestActionableMessage:
    """The rejection message must point at file:line and suggest the fix."""

    def test_message_contains_file_and_line(self):
        mod = import_validator()
        content = "x = 1\nsubprocess.run(cmd, timeout=10)\n"
        violations = mod.find_violations(content, "tools/example.py")
        assert len(violations) == 1
        assert "tools/example.py:2" in violations[0]

    def test_message_suggests_settings_timeouts(self):
        mod = import_validator()
        content = "subprocess.run(cmd, timeout=10)\n"
        violations = mod.find_violations(content, "example.py")
        assert "settings.timeouts" in violations[0]

    def test_message_suggests_named_constant(self):
        mod = import_validator()
        content = "subprocess.run(cmd, timeout=10)\n"
        violations = mod.find_violations(content, "example.py")
        assert "named" in violations[0].lower() and "constant" in violations[0].lower()

    def test_message_mentions_allow_marker_escape_hatch(self):
        mod = import_validator()
        content = "subprocess.run(cmd, timeout=10)\n"
        violations = mod.find_violations(content, "example.py")
        assert mod.ALLOW_MARKER in violations[0]


class TestIsTestFile:
    """Test files are excluded from scanning."""

    def test_tests_dir_excluded(self):
        mod = import_validator()
        assert mod.is_test_file("tests/unit/test_foo.py")

    def test_test_prefix_excluded(self):
        mod = import_validator()
        assert mod.is_test_file("test_foo.py")

    def test_conftest_excluded(self):
        mod = import_validator()
        assert mod.is_test_file("tests/conftest.py")

    def test_fixtures_dir_excluded(self):
        mod = import_validator()
        assert mod.is_test_file("tools/fixtures/sample.py")

    def test_regular_source_file_not_excluded(self):
        mod = import_validator()
        assert not mod.is_test_file("agent/branch_manager.py")


class TestCliDirectInvocation:
    """The direct-invocation CLI path (used outside the git-commit hook trigger)."""

    def test_exits_nonzero_on_violation(self, tmp_path, capsys):
        mod = import_validator()
        f = tmp_path / "bad.py"
        f.write_text("subprocess.run(cmd, timeout=10)\n")
        import pytest

        with pytest.raises(SystemExit) as exc_info:
            mod._run_cli([str(f)])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert str(f) in captured.err
        assert "settings.timeouts" in captured.err

    def test_exits_zero_on_compliant_file(self, tmp_path):
        mod = import_validator()
        f = tmp_path / "good.py"
        f.write_text("subprocess.run(cmd, timeout=settings.timeouts.git_subprocess_s)\n")
        import pytest

        with pytest.raises(SystemExit) as exc_info:
            mod._run_cli([str(f)])
        assert exc_info.value.code == 0

    def test_exits_zero_on_file_with_no_timeouts(self, tmp_path):
        mod = import_validator()
        f = tmp_path / "empty.py"
        f.write_text("def add(a, b):\n    return a + b\n")
        import pytest

        with pytest.raises(SystemExit) as exc_info:
            mod._run_cli([str(f)])
        assert exc_info.value.code == 0

    def test_skips_test_files(self, tmp_path):
        mod = import_validator()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        f = test_dir / "test_bad.py"
        f.write_text("subprocess.run(cmd, timeout=10)\n")
        import pytest

        with pytest.raises(SystemExit) as exc_info:
            mod._run_cli([str(f)])
        assert exc_info.value.code == 0
