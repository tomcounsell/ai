"""Tests asserting the worker startup wires `validate_agent_files()`.

Background: `validate_agent_files()` was originally only called from the
Telegram bridge's `main()`. The worker is the actual session execution
engine (per CLAUDE.md: "sole session execution engine"), so worker-only
deployments — or boots where the worker comes up before the bridge —
silently lost the early-warning hook for missing agent definition files.

These tests guard against regressing back to the bridge-only call site.
"""

from __future__ import annotations

import inspect
from pathlib import Path
from unittest.mock import patch

import worker.__main__ as worker_main

REPO_ROOT = Path(__file__).resolve().parents[2]


class TestWorkerCallsValidateAgentFiles:
    """Static + behavioural assertions that worker main() invokes the validator."""

    def test_worker_main_source_imports_validate_agent_files(self):
        """The worker's main() function must reference validate_agent_files.

        A static-grep test rather than a full process spawn — sufficient to
        prevent the bridge/worker asymmetry from re-emerging.
        """
        source = inspect.getsource(worker_main.main)
        assert "validate_agent_files" in source, (
            "worker.__main__.main() must call validate_agent_files() — "
            "the worker is the session execution engine and must surface "
            "missing agent files at startup, not just the bridge."
        )

    def test_worker_main_file_contains_validate_agent_files(self):
        """Defence-in-depth: also assert the call exists in the on-disk module."""
        worker_main_path = REPO_ROOT / "worker" / "__main__.py"
        text = worker_main_path.read_text(encoding="utf-8")
        assert "validate_agent_files" in text, (
            "worker/__main__.py must import and call validate_agent_files()."
        )

    def test_worker_main_invokes_validator_at_startup(self):
        """Behavioural test: running main() up to the run-loop hits the validator.

        We patch `_load_projects` to return an empty list so main() exits
        early before launching any real worker work. The validator is
        called *before* `_load_projects`, so the mock should still be hit.
        """
        with (
            patch.object(worker_main, "_parse_args") as parse_args,
            patch.object(worker_main, "_configure_logging"),
            patch.object(worker_main, "_load_projects", return_value=[]),
            patch(
                "agent.agent_definitions.validate_agent_files",
                return_value=[],
            ) as validate_mock,
        ):
            parse_args.return_value = type("Args", (), {"project": None, "dry_run": False})()
            try:
                worker_main.main()
            except SystemExit:
                # main() exits after _load_projects returns an empty list.
                pass
        assert validate_mock.called, (
            "worker.__main__.main() must call validate_agent_files() before entering the run loop."
        )
