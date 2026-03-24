"""Unit tests for reflections pre-flight checks."""

import os
from unittest.mock import patch

from scripts.reflections import STEP_PREREQUISITES, preflight_check


class TestPreflightCheck:
    def test_no_requirements_returns_empty(self):
        assert preflight_check(None) == []
        assert preflight_check({}) == []

    def test_existing_file_passes(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("content")
        failures = preflight_check({"files": [str(f)]})
        assert failures == []

    def test_missing_file_fails(self):
        failures = preflight_check({"files": ["/nonexistent/path/file.txt"]})
        assert len(failures) == 1
        assert "Required file missing" in failures[0]

    def test_existing_command_passes(self):
        # python should always exist
        failures = preflight_check({"commands": ["python3"]})
        assert failures == []

    def test_missing_command_fails(self):
        failures = preflight_check({"commands": ["this_command_does_not_exist_xyz"]})
        assert len(failures) == 1
        assert "Required command not found" in failures[0]

    def test_env_var_set_passes(self):
        with patch.dict(os.environ, {"TEST_PREFLIGHT_VAR": "value"}):
            failures = preflight_check({"env_vars": ["TEST_PREFLIGHT_VAR"]})
        assert failures == []

    def test_env_var_unset_fails(self):
        with patch.dict(os.environ, {}, clear=True):
            failures = preflight_check({"env_vars": ["DEFINITELY_NOT_SET_XYZ"]})
        assert len(failures) == 1
        assert "Required env var not set" in failures[0]

    def test_multiple_failures_combined(self):
        failures = preflight_check(
            {
                "files": ["/nonexistent1", "/nonexistent2"],
                "commands": ["nonexistent_cmd_xyz"],
            }
        )
        assert len(failures) == 3

    def test_step_prerequisites_well_formed(self):
        """Verify STEP_PREREQUISITES dict has valid structure."""
        for step_num, reqs in STEP_PREREQUISITES.items():
            assert isinstance(step_num, int)
            assert isinstance(reqs, dict)
            for key in reqs:
                assert key in ("files", "commands", "env_vars")
                assert isinstance(reqs[key], list)
