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
        from unittest.mock import MagicMock, patch

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
        from unittest.mock import MagicMock, patch

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


if __name__ == "__main__":
    unittest.main(verbosity=2)
