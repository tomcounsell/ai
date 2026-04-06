"""Integration tests for the happy path testing pipeline.

Tests the trace-to-script generation pipeline end-to-end using real files
(no mocks). Validates that parse_trace() and generate_script() produce
correct Rodney shell scripts from trace JSON input.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path

import pytest

from tools.happy_path_generator import generate_script
from tools.happy_path_schema import parse_trace, validate_trace_file

# Path to example trace committed in the repo
EXAMPLE_TRACE_PATH = Path(__file__).parent.parent / "happy-paths" / "traces" / "example-homepage.json"


class TestTraceToScriptGeneration:
    """Test the full trace-to-script generation pipeline with real files."""

    def test_example_trace_loads_and_validates(self):
        """Example trace JSON loads and passes schema validation."""
        assert EXAMPLE_TRACE_PATH.exists(), f"Example trace missing: {EXAMPLE_TRACE_PATH}"

        data = json.loads(EXAMPLE_TRACE_PATH.read_text())
        is_valid, errors = validate_trace_file(data)

        assert is_valid, f"Trace validation failed: {errors}"

    def test_example_trace_parses_correctly(self):
        """parse_trace() returns a Trace with expected fields."""
        data = json.loads(EXAMPLE_TRACE_PATH.read_text())
        trace = parse_trace(data)

        assert trace.name == "example-homepage"
        assert trace.url == "https://example.com"
        assert len(trace.steps) == 4
        assert trace.steps[0].action == "navigate"
        assert trace.steps[0].url == "https://example.com"
        assert trace.steps[1].action == "assert"
        assert trace.steps[1].type == "title_equals"

    def test_generate_script_produces_valid_output(self, tmp_path):
        """generate_script() creates a valid shell script from the example trace."""
        data = json.loads(EXAMPLE_TRACE_PATH.read_text())
        trace = parse_trace(data)

        output_path = tmp_path / "example-homepage.sh"
        result = generate_script(trace, output_path)

        assert result is True
        assert output_path.exists()

        content = output_path.read_text()
        # Script should have a shebang
        assert content.startswith("#!/usr/bin/env bash")
        # Script should use strict mode
        assert "set -euo pipefail" in content
        # Script should contain rodney commands
        assert "rodney open" in content
        assert "rodney assert" in content
        # Script should reference example.com
        assert "example.com" in content
        # Script should have a PASS message
        assert "PASS: example-homepage" in content

    def test_generate_script_is_syntactically_valid(self, tmp_path):
        """Generated script passes bash -n syntax check."""
        data = json.loads(EXAMPLE_TRACE_PATH.read_text())
        trace = parse_trace(data)

        output_path = tmp_path / "example-homepage.sh"
        generate_script(trace, output_path)

        result = subprocess.run(
            ["bash", "-n", str(output_path)],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0, f"Syntax error: {result.stderr}"

    def test_empty_steps_produces_no_script(self, tmp_path):
        """Trace with empty steps array produces no script (generator returns False)."""
        data = {
            "name": "empty-test",
            "url": "https://example.com",
            "steps": [],
        }
        trace = parse_trace(data)
        output_path = tmp_path / "empty-test.sh"
        result = generate_script(trace, output_path)

        assert result is False
        assert not output_path.exists()

    def test_navigate_only_trace_produces_valid_script(self, tmp_path):
        """Trace with only navigate steps produces a valid script."""
        data = {
            "name": "navigate-only",
            "url": "https://example.com",
            "steps": [
                {"action": "navigate", "url": "https://example.com"},
                {"action": "navigate", "url": "https://example.com/about"},
            ],
        }
        trace = parse_trace(data)
        output_path = tmp_path / "navigate-only.sh"
        result = generate_script(trace, output_path)

        assert result is True
        content = output_path.read_text()
        assert "rodney open 'https://example.com'" in content
        assert "rodney open 'https://example.com/about'" in content

    def test_committed_script_matches_generation(self, tmp_path):
        """The committed example script matches what the generator produces."""
        committed_script = Path(__file__).parent.parent / "happy-paths" / "scripts" / "example-homepage.sh"
        if not committed_script.exists():
            pytest.skip("Committed example script not found")

        data = json.loads(EXAMPLE_TRACE_PATH.read_text())
        trace = parse_trace(data)

        output_path = tmp_path / "example-homepage.sh"
        generate_script(trace, output_path)

        assert output_path.read_text() == committed_script.read_text()


class TestRodneyExecution:
    """Tests that require Rodney to be installed. Skipped if unavailable."""

    @pytest.fixture(autouse=True)
    def _require_rodney(self):
        if not shutil.which("rodney"):
            pytest.skip("Rodney not installed")

    def test_rodney_version(self):
        """Rodney binary reports a version string."""
        result = subprocess.run(
            ["rodney", "--version"],
            capture_output=True,
            text=True,
            timeout=10,
        )
        assert result.returncode == 0
        assert len(result.stdout.strip()) > 0
