"""Tests for the valor-granite-loop CLI (PoC #1546).

The CLI is a thin wrapper around `Container.run` that parses args,
runs the container, writes the results JSON, and prints a one-line
summary. The tests patch `Container.run` to a known result and
verify the CLI's argument parsing, exit code mapping, and JSON
output shape.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.granite_container.container import ContainerResult
from tools.granite_interactive_tui_poc.cli import _build_arg_parser, main


def _fake_result(exit_reason: str = "pm_complete") -> ContainerResult:
    return ContainerResult(
        session_id="cli-test",
        user_message="hello",
        turns=[],
        exit_reason=exit_reason,
        exit_message="test",
    )


class TestArgParser(unittest.TestCase):
    """The argument parser accepts the documented flags."""

    def test_user_message_required(self) -> None:
        p = _build_arg_parser()
        with self.assertRaises(SystemExit):
            p.parse_args([])

    def test_defaults(self) -> None:
        p = _build_arg_parser()
        args = p.parse_args(["--user-message", "hi"])
        self.assertEqual(args.user_message, "hi")
        self.assertEqual(args.max_turns, 10)
        self.assertEqual(args.output, Path("./granite_poc_results.json"))
        self.assertIsNone(args.cwd)
        self.assertFalse(args.verbose)

    def test_overrides(self) -> None:
        p = _build_arg_parser()
        args = p.parse_args([
            "--user-message", "hi",
            "--max-turns", "5",
            "--output", "/tmp/results.json",
            "--cwd", "/tmp/cwd",
            "--pm-model", "gemma4:e2b",
            "--dev-model", "gemma4:e2b",
            "--verbose",
        ])
        self.assertEqual(args.max_turns, 5)
        self.assertEqual(args.output, Path("/tmp/results.json"))
        self.assertEqual(args.cwd, "/tmp/cwd")
        self.assertEqual(args.pm_model, "gemma4:e2b")
        self.assertEqual(args.dev_model, "gemma4:e2b")
        self.assertTrue(args.verbose)


class TestMainRejectsEmpty(unittest.TestCase):
    """The CLI rejects empty user messages with exit code 5."""

    def test_whitespace_only(self) -> None:
        rc = main(["--user-message", "  "])
        self.assertEqual(rc, 5)

    def test_empty_string(self) -> None:
        rc = main(["--user-message", ""])
        self.assertEqual(rc, 5)


class TestMainRunPath(unittest.TestCase):
    """The CLI runs the container and writes the results JSON."""

    def test_pm_complete_writes_json_and_exits_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with patch("agent.granite_container.container.Container.run") as mock_run:
                mock_run.return_value = _fake_result("pm_complete")
                rc = main([
                    "--user-message", "hello world",
                    "--output", str(out_path),
                ])
            self.assertEqual(rc, 0, f"expected exit 0, got {rc}")
            self.assertTrue(out_path.exists())
            payload = json.loads(out_path.read_text())
            self.assertEqual(payload["session_id"], "cli-test")
            self.assertEqual(payload["exit_reason"], "pm_complete")

    def test_pm_max_turns_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with patch("agent.granite_container.container.Container.run") as mock_run:
                mock_run.return_value = _fake_result("pm_max_turns")
                rc = main([
                    "--user-message", "hello",
                    "--output", str(out_path),
                ])
            self.assertEqual(rc, 1)

    def test_dev_hang_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with patch("agent.granite_container.container.Container.run") as mock_run:
                mock_run.return_value = _fake_result("dev_hang")
                rc = main([
                    "--user-message", "hello",
                    "--output", str(out_path),
                ])
            self.assertEqual(rc, 2)

    def test_pm_hang_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with patch("agent.granite_container.container.Container.run") as mock_run:
                mock_run.return_value = _fake_result("pm_hang")
                rc = main([
                    "--user-message", "hello",
                    "--output", str(out_path),
                ])
            self.assertEqual(rc, 2)

    def test_startup_unresolved_exits_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with patch("agent.granite_container.container.Container.run") as mock_run:
                mock_run.return_value = _fake_result("startup_unresolved")
                rc = main([
                    "--user-message", "hello",
                    "--output", str(out_path),
                ])
            self.assertEqual(rc, 3)

    def test_exception_exits_4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with patch("agent.granite_container.container.Container.run") as mock_run:
                mock_run.return_value = _fake_result("exception")
                rc = main([
                    "--user-message", "hello",
                    "--output", str(out_path),
                ])
            self.assertEqual(rc, 4)


class TestMainStdoutSummary(unittest.TestCase):
    """The CLI prints a one-line JSON summary to stdout."""

    def test_summary_emitted(self) -> None:
        import io
        from contextlib import redirect_stdout

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with patch("agent.granite_container.container.Container.run") as mock_run:
                mock_run.return_value = _fake_result("pm_complete")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    main([
                        "--user-message", "hello",
                        "--output", str(out_path),
                    ])
            line = buf.getvalue().strip()
            payload = json.loads(line)
            self.assertEqual(payload["exit_reason"], "pm_complete")
            self.assertEqual(payload["session_id"], "cli-test")
            self.assertEqual(payload["turns"], 0)
            self.assertIn("output_path", payload)


if __name__ == "__main__":
    unittest.main(verbosity=2)
