"""Tests for happy path test runner."""

from tools.happy_path_runner import (
    RunSummary,
    ScriptResult,
    format_summary_table,
    run_all,
    run_script,
)

# ── format_summary_table ─────────────────────────────────────────────


class TestFormatSummaryTable:
    def test_empty_summary(self):
        summary = RunSummary()
        result = format_summary_table(summary)
        assert "No happy path scripts found" in result

    def test_all_passing(self):
        summary = RunSummary(
            total=2,
            passed=2,
            results=[
                ScriptResult(
                    script="login",
                    status="pass",
                    exit_code=0,
                    duration_seconds=1.5,
                ),
                ScriptResult(
                    script="checkout",
                    status="pass",
                    exit_code=0,
                    duration_seconds=2.3,
                ),
            ],
        )
        table = format_summary_table(summary)
        assert "PASS" in table
        assert "login" in table
        assert "checkout" in table
        assert "**Passed:** 2" in table

    def test_mixed_results(self):
        summary = RunSummary(
            total=3,
            passed=1,
            failed=1,
            errored=1,
            results=[
                ScriptResult(script="ok", status="pass", exit_code=0, duration_seconds=1.0),
                ScriptResult(
                    script="bad",
                    status="fail",
                    exit_code=1,
                    duration_seconds=2.0,
                    stderr="assertion failed",
                ),
                ScriptResult(
                    script="broken",
                    status="error",
                    exit_code=2,
                    duration_seconds=0.5,
                    stderr="rodney not found",
                ),
            ],
        )
        table = format_summary_table(summary)
        assert "FAIL" in table
        assert "ERROR" in table
        assert "assertion failed" in table


# ── run_script ───────────────────────────────────────────────────────


class TestRunScript:
    def test_passing_script(self, tmp_path):
        script = tmp_path / "pass.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        script.chmod(0o755)

        result = run_script(script)
        assert result.status == "pass"
        assert result.exit_code == 0

    def test_failing_script(self, tmp_path):
        script = tmp_path / "fail.sh"
        script.write_text("#!/bin/bash\nexit 1\n")
        script.chmod(0o755)

        result = run_script(script)
        assert result.status == "fail"
        assert result.exit_code == 1

    def test_error_script(self, tmp_path):
        script = tmp_path / "error.sh"
        script.write_text("#!/bin/bash\nexit 2\n")
        script.chmod(0o755)

        result = run_script(script)
        assert result.status == "error"
        assert result.exit_code == 2

    def test_timeout_handling(self, tmp_path):
        script = tmp_path / "slow.sh"
        script.write_text("#!/bin/bash\nsleep 60\n")
        script.chmod(0o755)

        result = run_script(script, timeout=1)
        assert result.status == "error"
        assert "timed out" in result.stderr

    def test_script_name_extracted(self, tmp_path):
        script = tmp_path / "login-flow.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        script.chmod(0o755)

        result = run_script(script)
        assert result.script == "login-flow"


# ── run_all ──────────────────────────────────────────────────────────


class TestRunAll:
    def test_empty_directory(self, tmp_path):
        summary = run_all(tmp_path)
        assert summary.total == 0
        assert summary.passed == 0

    def test_multiple_scripts(self, tmp_path):
        for name, code in [("a.sh", 0), ("b.sh", 0), ("c.sh", 1)]:
            script = tmp_path / name
            script.write_text(f"#!/bin/bash\nexit {code}\n")
            script.chmod(0o755)

        summary = run_all(tmp_path, evidence_dir=tmp_path / "evidence")
        assert summary.total == 3
        assert summary.passed == 2
        assert summary.failed == 1
        assert len(summary.results) == 3

    def test_non_sh_files_ignored(self, tmp_path):
        (tmp_path / "readme.txt").write_text("not a script")
        (tmp_path / "trace.json").write_text("{}")
        script = tmp_path / "test.sh"
        script.write_text("#!/bin/bash\nexit 0\n")
        script.chmod(0o755)

        summary = run_all(tmp_path)
        assert summary.total == 1
