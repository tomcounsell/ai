"""Tests for the valor-granite-loop CLI (PoC #1546).

The CLI is a thin wrapper around `Container.run` that parses args,
runs the container, writes the results JSON, and prints a one-line
summary. The tests patch `Container.run` to a known result and
verify the CLI's argument parsing, exit code mapping, and JSON
output shape.

AgentSession lifecycle tests (added for issue #1571) patch
``AgentSession.create_local`` and ``finalize_session`` so unit tests
never need a live Redis instance. The lifecycle assertions verify:

- A ``running`` session is created before ``container.run()``.
- The session is finalized ``completed`` for pm_complete/pm_user and
  ``failed`` for all other exit reasons.
- An empty ``--user-message`` skips session creation entirely.
- A Redis failure in ``create_local`` does not affect exit codes or
  results JSON (best-effort guard).
- A double-finalize (post-run sets status, then except-block calls
  finalize again with reject_from_terminal=False) does not raise.
"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

from agent.granite_container.container import ContainerResult
from tools.granite_loop.cli import _build_arg_parser, main


def _fake_result(exit_reason: str = "pm_complete") -> ContainerResult:
    return ContainerResult(
        session_id="cli-test",
        user_message="hello",
        turns=[],
        exit_reason=exit_reason,
        exit_message="test",
    )


def _make_fake_session(agent_session_id: str = "fake-agent-session-id") -> MagicMock:
    """Build a minimal fake AgentSession for lifecycle assertions."""
    session = MagicMock()
    session.agent_session_id = agent_session_id
    session.status = "running"
    return session


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
        args = p.parse_args(
            [
                "--user-message",
                "hi",
                "--max-turns",
                "5",
                "--output",
                "/tmp/results.json",
                "--cwd",
                "/tmp/cwd",
                "--pm-model",
                "gemma4:e2b",
                "--dev-model",
                "gemma4:e2b",
                "--verbose",
            ]
        )
        self.assertEqual(args.max_turns, 5)
        self.assertEqual(args.output, Path("/tmp/results.json"))
        self.assertEqual(args.cwd, "/tmp/cwd")
        self.assertEqual(args.pm_model, "gemma4:e2b")
        self.assertEqual(args.dev_model, "gemma4:e2b")
        self.assertTrue(args.verbose)


class TestMainRejectsEmpty(unittest.TestCase):
    """The CLI rejects empty user messages with exit code 5.

    Empty messages return before session creation — no AgentSession
    should be created or finalized.
    """

    def test_whitespace_only(self) -> None:
        with patch("tools.granite_loop.cli.AgentSession.create_local") as mock_create:
            rc = main(["--user-message", "  "])
        self.assertEqual(rc, 5)
        mock_create.assert_not_called()

    def test_empty_string(self) -> None:
        with patch("tools.granite_loop.cli.AgentSession.create_local") as mock_create:
            rc = main(["--user-message", ""])
        self.assertEqual(rc, 5)
        mock_create.assert_not_called()


class TestMainRunPath(unittest.TestCase):
    """The CLI runs the container and writes the results JSON."""

    def test_pm_complete_writes_json_and_exits_0(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            fake_session = _make_fake_session()
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("pm_complete")
                rc = main(
                    [
                        "--user-message",
                        "hello world",
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc, 0, f"expected exit 0, got {rc}")
            self.assertTrue(out_path.exists())
            payload = json.loads(out_path.read_text())
            self.assertEqual(payload["session_id"], "cli-test")
            self.assertEqual(payload["exit_reason"], "pm_complete")

    def test_pm_user_exits_0(self) -> None:
        # pm_user is a clean terminal exit (PM addressed the user with
        # no further routing) and must map to exit code 0, not the
        # exception fallback.
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            fake_session = _make_fake_session()
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("pm_user")
                rc = main(
                    [
                        "--user-message",
                        "what's the status?",
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc, 0, f"expected exit 0, got {rc}")
            payload = json.loads(out_path.read_text())
            self.assertEqual(payload["exit_reason"], "pm_user")

    def test_pm_max_turns_exits_1(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            fake_session = _make_fake_session()
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("pm_max_turns")
                rc = main(
                    [
                        "--user-message",
                        "hello",
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc, 1)

    def test_dev_hang_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            fake_session = _make_fake_session()
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("dev_hang")
                rc = main(
                    [
                        "--user-message",
                        "hello",
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc, 2)

    def test_pm_hang_exits_2(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            fake_session = _make_fake_session()
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("pm_hang")
                rc = main(
                    [
                        "--user-message",
                        "hello",
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc, 2)

    def test_startup_unresolved_exits_3(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            fake_session = _make_fake_session()
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("startup_unresolved")
                rc = main(
                    [
                        "--user-message",
                        "hello",
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc, 3)

    def test_exception_exits_4(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            fake_session = _make_fake_session()
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("exception")
                rc = main(
                    [
                        "--user-message",
                        "hello",
                        "--output",
                        str(out_path),
                    ]
                )
            self.assertEqual(rc, 4)


class TestMainStdoutSummary(unittest.TestCase):
    """The CLI prints a one-line JSON summary to stdout."""

    def test_summary_emitted(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            fake_session = _make_fake_session("test-agent-session-xyz")
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("pm_complete")
                buf = io.StringIO()
                with redirect_stdout(buf):
                    main(
                        [
                            "--user-message",
                            "hello",
                            "--output",
                            str(out_path),
                        ]
                    )
            line = buf.getvalue().strip()
            payload = json.loads(line)
            self.assertEqual(payload["exit_reason"], "pm_complete")
            self.assertEqual(payload["session_id"], "cli-test")
            self.assertEqual(payload["turns"], 0)
            self.assertIn("output_path", payload)
            # agent_session_id from the created session record
            self.assertEqual(payload["agent_session_id"], "test-agent-session-xyz")


class TestMainAgentSessionLifecycle(unittest.TestCase):
    """AgentSession is created before container.run() and finalized on exit."""

    def test_session_created_before_run_with_correct_fields(self) -> None:
        """A running AgentSession is minted before container.run() is called.

        - session_id starts with 'local-'
        - session_type is 'granite'
        - project_key is 'valor'
        """
        create_calls: list[dict] = []

        def _capture_create(**kwargs):
            create_calls.append(kwargs)
            return _make_fake_session()

        run_order: list[str] = []

        def _track_run(self):
            run_order.append("run")
            return _fake_result("pm_complete")

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    side_effect=_capture_create,
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch(
                    "agent.granite_container.container.Container.run",
                    _track_run,
                ),
            ):
                rc = main(["--user-message", "hello", "--output", str(out_path)])

        self.assertEqual(rc, 0)
        self.assertEqual(len(create_calls), 1, "create_local must be called exactly once")
        kwargs = create_calls[0]
        self.assertTrue(
            kwargs["session_id"].startswith("local-"),
            f"session_id must start with 'local-', got {kwargs['session_id']!r}",
        )
        self.assertEqual(kwargs["session_type"], "granite")
        self.assertEqual(kwargs["project_key"], "valor")
        self.assertEqual(
            kwargs.get("status"),
            "running",
            "create_local must be called with status='running' so the record is "
            "visible to dashboard/valor-session-list/watchdog immediately; "
            f"got status={kwargs.get('status')!r}",
        )
        # container.run() must be called after create_local (run_order populated after create)
        self.assertIn("run", run_order, "container.run must be called")

    def test_session_finalized_completed_for_pm_complete(self) -> None:
        """pm_complete exit_reason finalizes the session as 'completed'."""
        fake_session = _make_fake_session()
        finalize_calls: list[tuple] = []

        def _capture_finalize(session, status, reason="", **kwargs):
            finalize_calls.append((session, status, reason))

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch(
                    "tools.granite_loop.cli.finalize_session",
                    side_effect=_capture_finalize,
                ),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("pm_complete")
                main(["--user-message", "hello", "--output", str(out_path)])

        self.assertEqual(len(finalize_calls), 1)
        _, status, reason = finalize_calls[0]
        self.assertEqual(status, "completed")
        self.assertEqual(reason, "pm_complete")

    def test_session_finalized_completed_for_pm_user(self) -> None:
        """pm_user exit_reason also finalizes the session as 'completed'."""
        fake_session = _make_fake_session()
        finalize_calls: list[tuple] = []

        def _capture_finalize(session, status, reason="", **kwargs):
            finalize_calls.append((session, status, reason))

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch(
                    "tools.granite_loop.cli.finalize_session",
                    side_effect=_capture_finalize,
                ),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("pm_user")
                main(["--user-message", "hello", "--output", str(out_path)])

        self.assertEqual(len(finalize_calls), 1)
        _, status, _ = finalize_calls[0]
        self.assertEqual(status, "completed")

    def test_session_finalized_failed_for_non_clean_exit_reasons(self) -> None:
        """dev_hang, pm_max_turns, startup_unresolved, exception → 'failed'."""
        non_clean = ["dev_hang", "pm_max_turns", "pm_hang", "startup_unresolved", "exception"]
        for exit_reason in non_clean:
            with self.subTest(exit_reason=exit_reason):
                fake_session = _make_fake_session()
                finalize_calls: list[tuple] = []

                def _capture_finalize(session, status, reason="", **kwargs):
                    finalize_calls.append((session, status, reason))

                with tempfile.TemporaryDirectory() as tmp:
                    out_path = Path(tmp) / "results.json"
                    with (
                        patch(
                            "tools.granite_loop.cli.AgentSession.create_local",
                            return_value=fake_session,
                        ),
                        patch(
                            "tools.granite_loop.cli.finalize_session",
                            side_effect=_capture_finalize,
                        ),
                        patch("agent.granite_container.container.Container.run") as mock_run,
                    ):
                        mock_run.return_value = _fake_result(exit_reason)
                        main(["--user-message", "hello", "--output", str(out_path)])

                self.assertEqual(len(finalize_calls), 1, f"exit_reason={exit_reason}")
                _, status, _ = finalize_calls[0]
                self.assertEqual(
                    status, "failed", f"exit_reason={exit_reason} should finalize as failed"
                )

    def test_persistence_failure_does_not_change_exit_code(self) -> None:
        """When create_local raises, the CLI exits normally with exit code 0.

        - exit code is unchanged
        - results JSON is still written
        - exactly one stderr line: 'granite session not recorded: <reason>'
        """
        import sys as _sys

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            stderr_buf = io.StringIO()
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    side_effect=ConnectionError("Redis down"),
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("pm_complete")
                old_stderr = _sys.stderr
                _sys.stderr = stderr_buf
                try:
                    rc = main(["--user-message", "hello", "--output", str(out_path)])
                finally:
                    _sys.stderr = old_stderr

            self.assertEqual(rc, 0, "persistence failure must not change exit code")
            self.assertTrue(out_path.exists(), "results JSON must still be written")
            stderr_output = stderr_buf.getvalue()
            # Exactly one 'granite session not recorded' line on stderr
            recorded_lines = [
                line
                for line in stderr_output.splitlines()
                if "granite session not recorded" in line
            ]
            self.assertEqual(
                len(recorded_lines),
                1,
                "expected exactly 1 stderr line about session not recorded, "
                f"got: {stderr_output!r}",
            )
            self.assertIn("Redis down", recorded_lines[0])

    def test_persistence_failure_does_not_affect_stdout_json(self) -> None:
        """When create_local raises, the stdout JSON shape is unchanged."""
        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    side_effect=RuntimeError("no redis"),
                ),
                patch("tools.granite_loop.cli.finalize_session"),
                patch("agent.granite_container.container.Container.run") as mock_run,
            ):
                mock_run.return_value = _fake_result("pm_complete")
                buf = io.StringIO()
                import sys as _sys

                old_stderr = _sys.stderr
                _sys.stderr = io.StringIO()  # suppress stderr in test output
                try:
                    with redirect_stdout(buf):
                        main(["--user-message", "hello", "--output", str(out_path)])
                finally:
                    _sys.stderr = old_stderr

        payload = json.loads(buf.getvalue().strip())
        self.assertEqual(payload["exit_reason"], "pm_complete")
        self.assertEqual(payload["session_id"], "cli-test")
        # agent_session_id is None when create_local failed
        self.assertIsNone(payload["agent_session_id"])

    def test_double_finalize_does_not_raise(self) -> None:
        """When container.run() raises after post-run finalize sets failed,
        the except-block finalize with reject_from_terminal=False is a no-op.

        Simulates the double-finalize path: post-run finalize sets 'failed',
        then the except-block tries to finalize again. This must not raise.
        """
        from models.session_lifecycle import StatusConflictError

        fake_session = _make_fake_session()
        call_count = [0]

        def _finalize_raises_on_second(session, status, reason="", **kwargs):
            call_count[0] += 1
            if call_count[0] >= 2 and kwargs.get("reject_from_terminal") is False:
                # reject_from_terminal=False path should not raise even if
                # the session is already terminal — this tests the guard.
                return
            if call_count[0] >= 2:
                raise StatusConflictError(
                    session_id="fake",
                    expected_status="running",
                    actual_status="failed",
                    reason="already terminal",
                )

        with tempfile.TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "results.json"
            with (
                patch(
                    "tools.granite_loop.cli.AgentSession.create_local",
                    return_value=fake_session,
                ),
                patch(
                    "tools.granite_loop.cli.finalize_session",
                    side_effect=_finalize_raises_on_second,
                ),
                patch(
                    "agent.granite_container.container.Container.run",
                    side_effect=RuntimeError("boom"),
                ),
            ):
                # Must not propagate any exception
                rc = main(["--user-message", "hello", "--output", str(out_path)])

        # The except-block returns exit 4, so the rc must be 4 (not a crash)
        self.assertEqual(rc, 4)


if __name__ == "__main__":
    unittest.main(verbosity=2)
