"""
Integration tests for TOOL_NAME.

These tests use real TOOL_NAME functionality.
Run with: pytest tools/TOOL_NAME/tests/ -v
"""

import subprocess

import pytest


def run_cmd(*args: str, timeout: int = 30) -> subprocess.CompletedProcess:
    """Run a TOOL_NAME command and return the result."""
    cmd = ["COMMAND_NAME", *args]
    return subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


class TestInstallation:
    """Verify TOOL_NAME is properly installed."""

    def test_version(self):
        """TOOL_NAME should respond to version command."""
        result = run_cmd("--version")
        assert result.returncode == 0
        assert result.stdout.strip()

    def test_help(self):
        """TOOL_NAME should respond to help command."""
        result = run_cmd("--help")
        assert result.returncode == 0


class TestCoreWorkflow:
    """Test the primary use case."""

    def test_basic_operation(self):
        """TOOL_NAME performs its main function."""
        # TODO: Implement actual test
        pass


class TestErrorHandling:
    """Test graceful failure modes."""

    def test_invalid_input(self):
        """TOOL_NAME handles bad input gracefully."""
        # TODO: Implement actual test
        pass
