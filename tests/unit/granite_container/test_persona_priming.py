"""Tests for the persona-priming slash commands.

The slash commands are markdown files under `.claude/commands/granite/`
that the TUI's slash-command mechanism parses at the TUI layer (F1 from
the F-probe). The body is invisible to the user/operator (F4); the only
substrate signal is "did the model respond correctly to a follow-up?"

This module tests what is testable about persona priming from inside
the substrate driver:
  - The slash-command files exist and have the expected shape.
  - The PM persona body enforces the `[/dev]/[/user]/[/complete]`
    prefix-token convention (the routing convention consumed by
    `granite_classifier.py`).
  - The Dev persona body instructs Dev to wait for the PM.
  - The `$ARGUMENTS` placeholder is present in both bodies (F2:
    model-side substitution).
  - A live smoke test (env-gated): spawn a single PTY, send
    `/granite:prime-pm-role hello`, confirm idle + a follow-up
    response.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

import pexpect

from agent.granite_container.pty_driver import PTYDriver, _default_substrate_model

# Compute REPO_ROOT from this test file's location, not a hardcoded path.
# A worktree-aware root would be `Path(__file__).resolve().parents[3]` (this
# file lives at <root>/tests/unit/granite_container/test_persona_priming.py).
# Using a hardcoded worktree path made the tests fail when the worktree
# was collapsed back into the main checkout (see the original PoC review).
REPO_ROOT = Path(__file__).resolve().parents[3]
PM_PRIME = REPO_ROOT / ".claude" / "commands" / "granite" / "prime-pm-role.md"
DEV_PRIME = REPO_ROOT / ".claude" / "commands" / "granite" / "prime-dev-role.md"


class TestSlashCommandFiles(unittest.TestCase):
    """The two slash-command files exist and are non-empty."""

    def test_pm_prime_exists(self) -> None:
        self.assertTrue(PM_PRIME.exists(), f"missing {PM_PRIME}")

    def test_dev_prime_exists(self) -> None:
        self.assertTrue(DEV_PRIME.exists(), f"missing {DEV_PRIME}")

    def test_pm_prime_has_arguments_placeholder(self) -> None:
        body = PM_PRIME.read_text()
        self.assertIn("$ARGUMENTS", body, "PM slash command must include $ARGUMENTS")

    def test_dev_prime_has_arguments_placeholder(self) -> None:
        body = DEV_PRIME.read_text()
        self.assertIn("$ARGUMENTS", body, "Dev slash command must include $ARGUMENTS")


class TestPmPrimeShape(unittest.TestCase):
    """The PM persona body enforces the prefix-token convention."""

    def setUp(self) -> None:
        self.body = PM_PRIME.read_text()

    def test_documents_dev_token(self) -> None:
        self.assertIn("[/dev]", self.body)

    def test_documents_user_token(self) -> None:
        self.assertIn("[/user]", self.body)

    def test_documents_complete_token(self) -> None:
        self.assertIn("[/complete]", self.body)

    def test_documents_classifier_regex(self) -> None:
        # The PM's prefix token is consumed by a deterministic regex
        # in `granite_classifier.py`. The PM body must quote that exact
        # regex so the persona is self-documenting AND the doc cannot
        # drift from the code (the nit-3 fix). We assert against the
        # live compiled pattern rather than a hardcoded string so any
        # future change to PREFIX_TOKEN_RE forces a doc update here.
        from agent.granite_container.granite_classifier import PREFIX_TOKEN_RE

        self.assertIn(PREFIX_TOKEN_RE.pattern, self.body)
        self.assertIn("deterministic", self.body.lower())

    def test_forbids_custom_pm_tools(self) -> None:
        # Invariant #7: PM has no custom tools (no `send_to_dev`, no
        # `reply_to_user`). The persona body should explicitly note
        # this so the model doesn't propose to add them.
        self.assertIn("send_to_dev", self.body)
        self.assertIn("reply_to_user", self.body)

    def test_instructs_no_code_writes(self) -> None:
        # The PM does not write code in the PoC; the developer does.
        self.assertIn("code", self.body.lower())


class TestDevPrimeShape(unittest.TestCase):
    """The Dev persona body instructs Dev to wait for the PM."""

    def setUp(self) -> None:
        self.body = DEV_PRIME.read_text()

    def test_instructs_to_wait(self) -> None:
        # The Dev persona's first action is to wait for the PM.
        self.assertIn("Wait", self.body)

    def test_no_prefix_token_convention(self) -> None:
        # Dev's output is summarized by granite, not classified. The
        # prefix-token convention is PM-only. The Dev body may
        # *reference* the concept (so the model knows about it), but
        # it must not *use* the tokens as routing outputs. The
        # convention is "first line of output is `[/dev]`"; Dev's
        # first line should never be a prefix token, so we check for
        # absence of the usage phrase.
        self.assertNotIn("begin every output", self.body.lower())

    def test_instructs_pipeline_ownership(self) -> None:
        # Dev owns the SDLC pipeline and runs /do-* skills directly;
        # the prime must mention the pipeline and the skill invocation pattern.
        self.assertIn("pipeline", self.body.lower())
        self.assertIn("/do-", self.body)


def _model_reachable() -> bool:
    """Whether the live smoke test may run.

    Gated on GRANITE_LIVE_SMOKE=1 (explicit operator opt-in) BEFORE any
    process is spawned. This function runs at module import time (it is
    a ``@skipUnless`` decorator argument), so without the gate, merely
    collecting this module spawned a real ``claude --print`` round-trip
    — and the old probe picked an *ollama* model name (gemma*) for a
    Claude-subscription binary, orphaning ~250MB ``claude`` processes
    (issue #1632 mode 3). The conftest spawn guard cannot intercept
    import-time spawns, hence the env gate here.
    """
    if os.environ.get("GRANITE_LIVE_SMOKE") != "1":
        return False
    if not shutil.which("claude"):
        return False
    try:
        # The PTY substrate is the Claude subscription (opus/sonnet
        # aliases), not ollama — probe with the driver's own default
        # substrate model, mirroring test_pty_driver's check.
        r = subprocess.run(
            [
                "claude",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                _default_substrate_model("dev"),
                "--print",
                "ping",
            ],
            capture_output=True,
            text=True,
            timeout=60,
        )
        return r.returncode == 0
    except Exception:
        return False


class TestPersonaPrimingSmokeEnvGated(unittest.TestCase):
    """Env-gated: spawn a single PTY, prime PM, confirm model responds.

    The PoC's persona-priming self-test sends
    `/granite:prime-pm-role hello` to a fresh TUI and waits for
    the model to reach idle. If the model responds, the priming
    worked; if the TUI rejects the slash command, the body is broken
    at the substrate layer (F1 violated). If the model doesn't
    respond, F2 substitution is broken in the env.
    """

    @unittest.skipUnless(
        _model_reachable(),
        "RESUME_SKIP model_unreachable — smoke gated on GRANITE_LIVE_SMOKE=1",
    )
    def test_pm_priming_round_trip(self) -> None:
        driver = PTYDriver(role="pm", cwd=str(REPO_ROOT))
        try:
            driver.spawn()
            initial = driver.read_until_idle(min_content_bytes=0, timeout_s=30.0)
            self.assertTrue(initial.saw_idle, f"initial idle failed: {initial.buffer[-200:]!r}")
            driver.write("/granite:prime-pm-role hello")
            primed = driver.read_until_idle(min_content_bytes=100, timeout_s=60.0)
            self.assertTrue(
                primed.saw_idle,
                f"PM did not reach idle after priming: {primed.buffer[-200:]!r}",
            )
        finally:
            driver.close(force=True)


class TestPrimeSessionUserMessageSeparation(unittest.TestCase):
    """Both PM and Dev primes now receive the user_message as $ARGUMENTS.

    PM: receives the message for immediate routing.
    Dev: receives the message as labeled background context (issue #1692).
    Dev must NOT act before the [/dev] relay — this is enforced by the prime
    text, not by withholding the message.
    """

    def test_pm_prime_write_carries_user_message(self) -> None:
        """PM prime writes slash_cmd + space + user_message to the PTY."""
        from unittest.mock import patch

        from agent.granite_container.container import PM_PRIME_SLASH_CMD, Container
        from agent.granite_container.pty_driver import PTYDriver

        user_msg = "implement the new feature"
        c = Container(user_message=user_msg, max_turns=1)
        pm_mock = MagicMock(spec=PTYDriver)
        pm_mock.read_until_idle.return_value = MagicMock(
            saw_idle=True, buffer="startup idle", idle_marker="bypass permissions on", elapsed_ms=0
        )

        with patch.object(c, "_spawn_pair"), patch.object(c, "_close_pair"):
            c._pm_pty = pm_mock
            c._dev_pty = MagicMock(spec=PTYDriver)
            c._prime_session(pm_mock, PM_PRIME_SLASH_CMD, include_user_message=True)

        write_arg = pm_mock.write.call_args.args[0]
        self.assertIn(
            user_msg, write_arg, f"PM prime write should contain user_message; got {write_arg!r}"
        )

    def test_dev_prime_write_carries_user_message_as_background_context(self) -> None:
        """Dev prime writes slash_cmd + user_message (background context, issue #1692).

        Dev receives the raw user prompt as labeled background context so it
        understands the task when the PM's [/dev] relay arrives. The prime text
        instructs Dev NOT to act until it receives the [/dev] relay.
        """
        from unittest.mock import patch

        from agent.granite_container.container import DEV_PRIME_SLASH_CMD, Container
        from agent.granite_container.pty_driver import PTYDriver

        user_msg = "implement the new feature"
        c = Container(user_message=user_msg, max_turns=1)
        dev_mock = MagicMock(spec=PTYDriver)
        dev_mock.read_until_idle.return_value = MagicMock(
            saw_idle=True, buffer="startup idle", idle_marker="bypass permissions on", elapsed_ms=0
        )

        with patch.object(c, "_spawn_pair"), patch.object(c, "_close_pair"):
            c._pm_pty = MagicMock(spec=PTYDriver)
            c._dev_pty = dev_mock
            c._prime_session(dev_mock, DEV_PRIME_SLASH_CMD, include_user_message=True)

        write_arg = dev_mock.write.call_args.args[0]
        self.assertIn(
            user_msg,
            write_arg,
            f"Dev prime write should contain user_message as background context "
            f"(issue #1692); got {write_arg!r}",
        )

    def test_dev_prime_file_instructs_wait_for_relay(self) -> None:
        """Dev prime file instructs Dev to wait for the [/dev] relay before acting.

        Since Dev now receives the user message as background context (issue #1692),
        the 'no-task-yet' guard lives in the prime text, not in message omission.
        """
        body = DEV_PRIME.read_text()
        # The updated persona says no task is present and instructs waiting.
        self.assertIn("No task", body, "Dev prime file should say 'No task yet'")
        # Must not tell Dev 'What the user said' (phrase from old PM prime style)
        self.assertNotIn("What the user said", body)
        # The background context section must exist
        self.assertIn(
            "Background context",
            body,
            "Dev prime should have a background context section",
        )


TEAMMATE_PRIME = REPO_ROOT / ".claude" / "commands" / "granite" / "prime-teammate-role.md"


class TestSessionTypePrimeSelection(unittest.TestCase):
    """Container picks the right PM prime command based on session_type.

    Blocker 1 from PR #1694 review: prime-teammate-role.md existed but was
    never invoked. Container now accepts session_type and calls
    _resolve_pm_prime_cmd() to select the appropriate command at run time.
    """

    def _make_idle_mock(self):  # type: ignore[return]

        m = MagicMock()
        m.read_until_idle.return_value = MagicMock(
            saw_idle=True,
            buffer="startup idle bypass permissions on",
            idle_marker="bypass permissions on",
            elapsed_ms=0,
        )
        return m

    def test_teammate_session_gets_teammate_prime(self) -> None:
        """Container with session_type='teammate' primes PM with the teammate prime."""
        from agent.granite_container.container import (
            TEAMMATE_PRIME_SLASH_CMD,
            _resolve_pm_prime_cmd,
        )

        self.assertEqual(_resolve_pm_prime_cmd("teammate"), TEAMMATE_PRIME_SLASH_CMD)

    def test_eng_session_gets_pm_prime(self) -> None:
        """Container with session_type='eng' primes PM with the standard PM prime."""
        from agent.granite_container.container import PM_PRIME_SLASH_CMD, _resolve_pm_prime_cmd

        self.assertEqual(_resolve_pm_prime_cmd("eng"), PM_PRIME_SLASH_CMD)

    def test_none_session_type_gets_pm_prime(self) -> None:
        """Container with session_type=None primes PM with the standard PM prime."""
        from agent.granite_container.container import PM_PRIME_SLASH_CMD, _resolve_pm_prime_cmd

        self.assertEqual(_resolve_pm_prime_cmd(None), PM_PRIME_SLASH_CMD)

    def test_container_teammate_prime_written_to_pty(self) -> None:
        """Container with session_type='teammate' writes the teammate prime to the PM PTY."""
        from unittest.mock import patch

        from agent.granite_container.container import TEAMMATE_PRIME_SLASH_CMD, Container
        from agent.granite_container.pty_driver import PTYDriver

        user_msg = "hello"
        c = Container(user_message=user_msg, max_turns=1, session_type="teammate")
        pm_mock = self._make_idle_mock()
        pm_mock.__class__ = PTYDriver  # pass isinstance check if any

        captured_writes: list[str] = []
        pm_mock.write.side_effect = lambda s: captured_writes.append(s)

        with patch.object(c, "_spawn_pair"), patch.object(c, "_close_pair"):
            c._pm_pty = pm_mock
            c._dev_pty = MagicMock(spec=PTYDriver)
            c._prime_session(pm_mock, TEAMMATE_PRIME_SLASH_CMD, include_user_message=True)

        self.assertTrue(
            any(TEAMMATE_PRIME_SLASH_CMD in w for w in captured_writes),
            f"Expected teammate prime in PM writes; got {captured_writes}",
        )

    def test_container_eng_prime_written_to_pty(self) -> None:
        """Container with session_type='eng' writes the standard PM prime to the PM PTY."""
        from unittest.mock import patch

        from agent.granite_container.container import PM_PRIME_SLASH_CMD, Container
        from agent.granite_container.pty_driver import PTYDriver

        user_msg = "hello"
        c = Container(user_message=user_msg, max_turns=1, session_type="eng")
        pm_mock = self._make_idle_mock()
        pm_mock.__class__ = PTYDriver

        captured_writes: list[str] = []
        pm_mock.write.side_effect = lambda s: captured_writes.append(s)

        with patch.object(c, "_spawn_pair"), patch.object(c, "_close_pair"):
            c._pm_pty = pm_mock
            c._dev_pty = MagicMock(spec=PTYDriver)
            c._prime_session(pm_mock, PM_PRIME_SLASH_CMD, include_user_message=True)

        self.assertTrue(
            any(PM_PRIME_SLASH_CMD in w for w in captured_writes),
            f"Expected PM prime in PM writes; got {captured_writes}",
        )

    def test_teammate_prime_file_exists(self) -> None:
        """The prime-teammate-role.md file exists and is non-empty."""
        self.assertTrue(TEAMMATE_PRIME.exists(), f"missing {TEAMMATE_PRIME}")
        self.assertGreater(TEAMMATE_PRIME.stat().st_size, 0, "prime-teammate-role.md is empty")


class _TwoPhasePexpectChild:
    """Fake pexpect child gated on `write()`, for testing `_prime_session`.

    `_prime_session` issues THREE `read_until_idle` calls against the SAME
    PTY: trust-dismiss + pre-write (both before any `write()`), then
    post-write (after `PTYDriver.write()` resets `_turn_text` and sends
    the slash command). A naive fake child that just yields a fixed list
    of chunks breaks here: `read_until_idle`'s inner loop keeps calling
    `read_nonblocking` back-to-back as long as chunks are non-empty (see
    the `if chunk: ... continue` branch), so the very first (trust-
    dismiss) call would drain the ENTIRE chunk list before the post-write
    read ever runs — starving it and hanging until its own multi-minute
    timeout.

    This fake stays silent (`pexpect.TIMEOUT`, "phase 1") until `send()`
    is called (which `PTYDriver.write()` calls), then starts yielding the
    post-write `phase2_chunks` one at a time ("phase 2"). Combined with
    pre-seeding `PTYDriver._turn_text` with an already-idle welcome frame,
    this lets the trust-dismiss/pre-write reads settle immediately on the
    pre-seeded text (phase 1, no chunks consumed) while the post-write
    read observes the real repaint sequence (phase 2).
    """

    def __init__(self, phase2_chunks: list[str]) -> None:
        self._phase2 = iter(phase2_chunks)
        self._armed = False

    def read_nonblocking(self, size: int, timeout: float) -> str:
        if not self._armed:
            raise pexpect.TIMEOUT("phase 1: silent until write()")
        try:
            return next(self._phase2)
        except StopIteration as exc:
            raise pexpect.TIMEOUT("phase 2: exhausted") from exc

    def send(self, text: str) -> None:
        self._armed = True

    def isalive(self) -> bool:
        return True


class _FreshnessWriter:
    """Stand-in for the bridge-adapter's freshness writer (#1843 Gap B).

    Mirrors its real contract: `last_pty_read_loop_at` is stamped
    unconditionally on every call; `last_pty_activity_at` is stamped only
    when the buffer differs from the previous call (diff-gated). Uses a
    monotonically-incrementing fake tick instead of wall-clock time so the
    test can assert ordering without real sleeps.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []
        self.last_pty_read_loop_at: float | None = None
        self.last_pty_activity_at: float | None = None
        self.activity_history: list[float] = []
        self._prev_buffer: object = object()  # sentinel, never equals a str
        self._tick = 0.0

    def __call__(self, buffer: str) -> None:
        self._tick += 1.0
        self.calls.append(buffer)
        self.last_pty_read_loop_at = self._tick
        if buffer != self._prev_buffer:
            self.last_pty_activity_at = self._tick
            self.activity_history.append(self._tick)
        self._prev_buffer = buffer


class _CountingClock:
    """Fake monotonic clock that always advances past the throttle window.

    `Container._pty_read_iteration_cb` is built with the real wall-clock
    default in production, throttled to <=1 stamp/sec (#1843 Gap B) so a
    real multi-minute prime doesn't write-storm Redis. A unit test that
    completes in well under a second would have every call but the first
    silently dropped by that same window. Substituting this clock (which
    always reports enough elapsed time to clear the window) lets the test
    assert on the underlying wiring/advancement behavior without asserting
    anything about the throttle itself (that has dedicated coverage in
    `test_pty_read_iteration_throttle.py`).
    """

    def __init__(self) -> None:
        self._t = 0.0

    def __call__(self) -> float:
        self._t += 2.0
        return self._t


class TestPrimeSessionStampsLivenessMidPrime(unittest.TestCase):
    """#1878 Part A: `_prime_session` must wire `on_read_iteration` so the
    #1792 `_prime_pty_alive()` kill-gate deferral actually engages.

    Uses a real `PTYDriver` with a mocked pexpect child that streams
    several distinct repaint frames (spinner ticks, then the final
    idle-bearing frame) so the per-iteration callback observes genuine
    mid-prime progression, not just a single stamp at the very end.
    """

    def _make_container_with_writer(self):  # type: ignore[return]
        from agent.granite_container.container import (
            PTY_READ_ITER_MIN_INTERVAL_S,
            Container,
            _throttle,
        )

        writer = _FreshnessWriter()
        c = Container(user_message="hello", max_turns=1, on_pty_read=writer)
        # Reuse the real _throttle wrapping the real _fire_pty_read_raw
        # (byte-identical wiring to production), only substituting a
        # clock that never suppresses a call — see _CountingClock.
        c._pty_read_iteration_cb = _throttle(
            c._fire_pty_read_raw, PTY_READ_ITER_MIN_INTERVAL_S, clock=_CountingClock()
        )
        return c, writer

    def test_stamps_advance_during_prime_not_only_at_the_end(self) -> None:
        chunk1 = "✳ Sprouting… (2s · esc to interrupt)\n"
        chunk2 = "✵ Brewing… (4s · esc to interrupt)\n"
        chunk3 = "⏺ Reading files...\n" + ("x" * 400) + "\n"
        chunk4 = (
            "⏺ [/user] "
            + ("filler response text " * 80)
            + "\n❯ \nbypass permissions on (shift+tab to cycle)\n"
        )
        self.assertGreater(
            len(chunk1) + len(chunk2) + len(chunk3) + len(chunk4),
            1500,
            "fixture must clear PRIME_POST_WRITE_MIN_CONTENT_BYTES",
        )

        with patch("agent.granite_container.pty_driver.QUIESCENCE_S", 0.02):
            c, writer = self._make_container_with_writer()
            pty = PTYDriver(role="pm")
            pty._child = _TwoPhasePexpectChild([chunk1, chunk2, chunk3, chunk4])
            # Pre-seed the welcome-frame idle text so the trust-dismiss and
            # pre-write reads (phase 1, before any write()) settle
            # immediately without consuming any of the post-write chunks.
            pty._turn_text = "bypass permissions on ❯ "

            with patch.object(c, "_spawn_pair"), patch.object(c, "_close_pair"):
                c._pm_pty = pty
                c._dev_pty = MagicMock(spec=PTYDriver)
                from agent.granite_container.container import PM_PRIME_SLASH_CMD

                c._prime_session(pty, PM_PRIME_SLASH_CMD, include_user_message=False)

        self.assertGreater(
            len(writer.calls),
            3,
            "on_read_iteration must fire many times across the prime, not once",
        )
        self.assertGreater(
            len(set(writer.calls)),
            1,
            "buffer content must change across polls (repaint), proving mid-prime "
            "progression rather than a single frozen snapshot",
        )
        self.assertGreater(
            len(writer.activity_history),
            1,
            "last_pty_activity_at must be re-stamped more than once during the "
            "prime window (not only once at the final frame)",
        )
        self.assertEqual(
            writer.last_pty_read_loop_at,
            writer._tick,
            "last_pty_read_loop_at must be the freshest stamp (unconditional every call)",
        )

    def test_raising_callback_does_not_abort_prime(self) -> None:
        """Regression: a raising on_read_iteration must not break `_prime_session`.

        `read_until_idle` already swallows exceptions from the callback
        (see `test_read_until_idle_per_iteration.py::
        test_raising_callback_does_not_break_read_loop`); this is the
        prime-level version of that assertion — the whole `_prime_session`
        call must still complete and reach idle.
        """
        call_count = {"n": 0}

        def _boom(_buffer: str) -> None:
            call_count["n"] += 1
            raise RuntimeError("on_read_iteration callback exploded")

        with patch("agent.granite_container.pty_driver.QUIESCENCE_S", 0.02):
            from agent.granite_container.container import PM_PRIME_SLASH_CMD, Container

            c = Container(user_message="hello", max_turns=1)
            c._pty_read_iteration_cb = _boom
            pty = PTYDriver(role="pm")
            pty._child = _TwoPhasePexpectChild(
                [
                    "✳ Sprouting… (2s · esc to interrupt)\n⏺ [/user] "
                    + ("filler response text " * 80)
                    + "\n❯ \nbypass permissions on (shift+tab to cycle)\n"
                    + ("y" * 400)
                ]
            )
            pty._turn_text = "bypass permissions on ❯ "

            with patch.object(c, "_spawn_pair"), patch.object(c, "_close_pair"):
                c._pm_pty = pty
                c._dev_pty = MagicMock(spec=PTYDriver)
                # Must not raise.
                c._prime_session(pty, PM_PRIME_SLASH_CMD, include_user_message=False)

        self.assertGreater(
            call_count["n"],
            0,
            "the raising callback should still have been invoked at least once",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
