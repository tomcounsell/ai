"""Unit tests for validate_no_module_scope_env.py and its shared AST detector.

Issue #2866, slice 0. The guard and `scripts/scan_module_scope_env.py` share one
detector implementation, so these tests cover both: the AST module-scope vs.
`def`/`class`-body distinction (the thing a regex cannot express), the allowlist
escape hatch, the actionable message, and both entry points.
"""

import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
VALIDATORS_DIR = REPO_ROOT / ".claude" / "hooks" / "validators"
SCRIPTS_DIR = REPO_ROOT / "scripts"
for _d in (VALIDATORS_DIR, SCRIPTS_DIR):
    if str(_d) not in sys.path:
        sys.path.insert(0, str(_d))


def import_validator():
    """Import the validator module."""
    import validate_no_module_scope_env

    return validate_no_module_scope_env


def import_scan():
    """Import the census script (the detector's home module)."""
    import scan_module_scope_env

    return scan_module_scope_env


class TestFindViolationsFires:
    """The guard must FIRE (reject) on deliberately-violating fixtures."""

    @pytest.mark.parametrize(
        ("content", "expected_line"),
        [
            ('import os\nDEBUG = os.environ.get("DEBUG")\n', 2),
            ('import os\nDEBUG = os.getenv("DEBUG", "0")\n', 2),
            ('import os\nos.environ.setdefault("REDIS_URL", "redis://x")\n', 2),
            ('import os\nos.environ.pop("REDIS_URL", None)\n', 2),
        ],
    )
    def test_fires_on_each_covered_function(self, content, expected_line):
        mod = import_validator()
        violations = mod.find_violations(content, "example.py")
        assert len(violations) == 1
        assert f"example.py:{expected_line}" in violations[0]

    def test_fires_inside_module_level_if(self):
        """A module-level `if` body still executes at import time."""
        mod = import_validator()
        content = 'import os\nif True:\n    X = os.environ.get("X")\n'
        violations = mod.find_violations(content, "example.py")
        assert len(violations) == 1
        assert "example.py:3" in violations[0]

    @pytest.mark.parametrize(
        "content",
        [
            'import os\ntry:\n    X = os.getenv("X")\nexcept KeyError:\n    X = None\n',
            'import os\nfor _ in range(1):\n    X = os.getenv("X")\n',
            'import os\nwhile False:\n    X = os.getenv("X")\n',
        ],
    )
    def test_fires_inside_other_module_level_control_flow(self, content):
        mod = import_validator()
        assert len(mod.find_violations(content, "example.py")) == 1

    def test_fires_once_per_call_site(self):
        mod = import_validator()
        content = 'import os\nA = os.environ.get("A")\nB = os.getenv("B")\n'
        assert len(mod.find_violations(content, "example.py")) == 2

    def test_fires_on_multiline_call(self):
        mod = import_validator()
        content = 'import os\nA = os.environ.get(\n    "A",\n    "default",\n)\n'
        violations = mod.find_violations(content, "example.py")
        assert len(violations) == 1
        assert "example.py:2" in violations[0]


class TestFindViolationsPasses:
    """The guard must PASS (accept) on compliant fixtures."""

    def test_passes_read_inside_function_body(self):
        mod = import_validator()
        content = 'import os\n\n\ndef get_debug():\n    return os.environ.get("DEBUG")\n'
        assert mod.find_violations(content, "example.py") == []

    def test_passes_read_inside_async_function_body(self):
        mod = import_validator()
        content = 'import os\n\n\nasync def get_debug():\n    return os.getenv("DEBUG")\n'
        assert mod.find_violations(content, "example.py") == []

    def test_passes_read_inside_class_body(self):
        mod = import_validator()
        content = 'import os\n\n\nclass C:\n    DEBUG = os.environ.get("DEBUG")\n'
        assert mod.find_violations(content, "example.py") == []

    def test_passes_read_inside_method_body(self):
        mod = import_validator()
        content = 'import os\n\n\nclass C:\n    def go(self):\n        return os.getenv("X")\n'
        assert mod.find_violations(content, "example.py") == []

    def test_passes_with_allow_marker(self):
        mod = import_validator()
        content = f'import os\nX = os.environ.get("X")  # {mod.ALLOW_MARKER}\n'
        assert mod.find_violations(content, "example.py") == []

    def test_passes_settings_reference(self):
        mod = import_validator()
        content = "from config.settings import settings\nX = settings.timeouts.git_subprocess_s\n"
        assert mod.find_violations(content, "example.py") == []

    def test_passes_unrelated_get_call(self):
        mod = import_validator()
        content = 'CONFIG = {}\nX = CONFIG.get("X")\n'
        assert mod.find_violations(content, "example.py") == []

    def test_passes_bare_environ_subscript(self):
        """`os.environ["X"]` is a different (raising) shape, deliberately uncovered."""
        mod = import_validator()
        content = 'import os\nX = os.environ["X"]\n'
        assert mod.find_violations(content, "example.py") == []


class TestFindViolationsEmptyMatchSet:
    """Files with no env reads (or that do not parse) must not error."""

    @pytest.mark.parametrize(
        "content",
        [
            "",
            "def add(a, b):\n    return a + b\n",
            "def broken(:\n",  # unparseable — a syntax error is not this guard's job
        ],
    )
    def test_no_violations_and_no_raise(self, content):
        mod = import_validator()
        assert mod.find_violations(content, "example.py") == []


class TestChangedLineScoping:
    """The hook path only litigates lines the commit actually touches."""

    def test_reports_only_changed_lines(self):
        mod = import_validator()
        content = 'import os\nA = os.getenv("A")\nB = os.getenv("B")\n'
        violations = mod.find_violations(content, "example.py", changed_lines={3})
        assert len(violations) == 1
        assert "example.py:3" in violations[0]

    def test_empty_changed_set_reports_nothing(self):
        mod = import_validator()
        content = 'import os\nA = os.getenv("A")\n'
        assert mod.find_violations(content, "example.py", changed_lines=set()) == []

    def test_none_changed_set_reports_everything(self):
        mod = import_validator()
        content = 'import os\nA = os.getenv("A")\nB = os.getenv("B")\n'
        assert len(mod.find_violations(content, "example.py", changed_lines=None)) == 2


class TestActionableMessage:
    """The rejection message must point at file:line and suggest the fix."""

    def _one(self):
        mod = import_validator()
        content = 'import os\nX = os.environ.get("MY_KEY")\n'
        violations = mod.find_violations(content, "tools/example.py")
        assert len(violations) == 1
        return mod, violations[0]

    def test_message_contains_file_and_line(self):
        _mod, message = self._one()
        assert "tools/example.py:2" in message

    def test_message_names_the_key(self):
        _mod, message = self._one()
        assert "MY_KEY" in message

    def test_message_suggests_config_settings(self):
        _mod, message = self._one()
        assert "config/settings.py" in message

    def test_message_mentions_allow_marker_escape_hatch(self):
        mod, message = self._one()
        assert mod.ALLOW_MARKER in message


class TestIsTestFile:
    """Test files are excluded from scanning."""

    @pytest.mark.parametrize(
        ("path", "expected"),
        [
            ("tests/unit/test_foo.py", True),
            ("test_foo.py", True),
            ("tests/conftest.py", True),
            ("tools/fixtures/sample.py", True),
            ("agent/branch_manager.py", False),
            # A pytest tmp_path dir named after the test function must not
            # false-positive via substring matching.
            ("/tmp/pytest-of-x/test_something0/bad.py", False),
        ],
    )
    def test_classification(self, path, expected):
        mod = import_validator()
        assert mod.is_test_file(path) is expected


class TestCliDirectInvocation:
    """The direct-invocation CLI path (whole-file, no git required)."""

    def test_exits_nonzero_on_violation(self, tmp_path, capsys):
        mod = import_validator()
        f = tmp_path / "bad.py"
        f.write_text('import os\nX = os.environ.get("X")\n')
        with pytest.raises(SystemExit) as exc_info:
            mod._run_cli([str(f)])
        assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert str(f) in captured.err
        assert "config/settings.py" in captured.err

    def test_exits_zero_on_compliant_file(self, tmp_path):
        mod = import_validator()
        f = tmp_path / "good.py"
        f.write_text('import os\n\n\ndef go():\n    return os.environ.get("X")\n')
        with pytest.raises(SystemExit) as exc_info:
            mod._run_cli([str(f)])
        assert exc_info.value.code == 0

    def test_skips_test_files(self, tmp_path):
        mod = import_validator()
        test_dir = tmp_path / "tests"
        test_dir.mkdir()
        f = test_dir / "test_bad.py"
        f.write_text('import os\nX = os.environ.get("X")\n')
        with pytest.raises(SystemExit) as exc_info:
            mod._run_cli([str(f)])
        assert exc_info.value.code == 0


class TestFindViolationForCommand:
    """The dispatcher predicate only engages on `git commit`."""

    @pytest.mark.parametrize("command", ["", "ls -la", "git status", "git diff --cached"])
    def test_returns_none_for_non_commit_commands(self, command):
        mod = import_validator()
        assert mod.find_violation_for_command(command) is None


class TestSharedDetector:
    """The guard and the census must be the same implementation (#2866)."""

    def test_guard_imports_the_scan_detector(self):
        guard = import_validator()
        scan = import_scan()
        assert guard.ALLOW_MARKER == scan.ALLOW_MARKER
        assert guard.is_test_file is scan.is_test_file

    def test_detector_reports_func_and_key(self):
        scan = import_scan()
        calls = scan.find_module_scope_env_calls('import os\nX = os.getenv("K", "d")\n', "e.py")
        assert len(calls) == 1
        assert calls[0].func == "os.getenv"
        assert calls[0].key == "K"
        assert calls[0].allowed is False

    def test_detector_flags_allowlisted_without_dropping_it(self):
        """The census keeps allowlisted sites (the guard is what filters them)."""
        scan = import_scan()
        content = f'import os\nX = os.getenv("K")  # {scan.ALLOW_MARKER}\n'
        calls = scan.find_module_scope_env_calls(content, "e.py")
        assert len(calls) == 1
        assert calls[0].allowed is True

    def test_repo_census_is_a_monotonic_ratchet(self):
        """The census over git-tracked *.py must never exceed the #2866 baseline.

        Exact baseline at slice 0 (commit 22cb19025): 72 modules / 190 call
        sites, of which 2 are allowlisted bootstrap gates. Asserted as an upper
        bound rather than an equality because slices 1-9 exist precisely to
        drive these numbers down — an equality assert would fail on every
        successful migration commit. Growth in either number is the regression
        this issue is about, and fails here.
        """
        scan = import_scan()
        result = scan.scan_repo(REPO_ROOT, include_tests=False)
        assert result.module_count <= 72, f"module-scope env reads grew: {result.module_count}"
        assert result.call_count <= 190, f"module-scope env reads grew: {result.call_count}"

    def test_only_bootstrap_sites_are_allowlisted(self):
        """The escape hatch stays narrow: only the pre-config launcher gates."""
        scan = import_scan()
        result = scan.scan_repo(REPO_ROOT, include_tests=False)
        allowed = {c.filename for c in result.calls if c.allowed}
        assert allowed <= {"worker/__main__.py", "bridge/telegram_bridge.py"}
        assert all(c.key == "VALOR_LAUNCHD" for c in result.calls if c.allowed)
