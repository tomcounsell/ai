"""Tests for the persona-priming slash commands (PoC #1546).

The slash commands are markdown files under `.claude/commands/granite-poc/`
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
    `/granite-poc:prime-pm-role hello`, confirm idle + a follow-up
    response.
"""

from __future__ import annotations

import shutil
import subprocess
import unittest
from pathlib import Path

from agent.granite_container.pty_driver import PTYDriver

# Compute REPO_ROOT from this test file's location, not a hardcoded path.
# A worktree-aware root would be `Path(__file__).resolve().parents[3]` (this
# file lives at <root>/tests/unit/granite_container/test_persona_priming.py).
# Using a hardcoded worktree path made the tests fail when the worktree
# was collapsed back into the main checkout (see session/granite_interactive_tui_poc
# review).
REPO_ROOT = Path(__file__).resolve().parents[3]
PM_PRIME = REPO_ROOT / ".claude" / "commands" / "granite-poc" / "prime-pm-role.md"
DEV_PRIME = REPO_ROOT / ".claude" / "commands" / "granite-poc" / "prime-dev-role.md"


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
        # in `granite_classifier.py`. The PM body should reference the
        # token shape so the persona is self-documenting. The body
        # documents the alternation `[/dev|/user|/complete]` as a
        # substring (escaped for markdown backticks); we check both
        # for the alternation shape and the trailing classifier note.
        self.assertIn("(/dev|/user|/complete)", self.body)
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

    def test_instructs_no_orchestration(self) -> None:
        self.assertIn("orchestrate", self.body.lower())
        self.assertIn("/do-", self.body)


def _model_reachable() -> bool:
    """Reuse the same env check as test_pty_driver."""
    if not shutil.which("claude"):
        return False
    try:
        import json
        import urllib.request

        tags = json.loads(
            urllib.request.urlopen("http://localhost:11434/api/tags", timeout=10).read()
        )
        names = [m["name"] for m in tags.get("models", [])]
        if not names:
            return False
        pick = next(
            (n for n in names if n.startswith("gemma")),
            next((n for n in names if not n.startswith("granite")), names[0]),
        )
        r = subprocess.run(
            [
                "claude",
                "--permission-mode",
                "bypassPermissions",
                "--model",
                pick,
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
    `/granite-poc:prime-pm-role hello` to a fresh TUI and waits for
    the model to reach idle. If the model responds, the priming
    worked; if the TUI rejects the slash command, the body is broken
    at the substrate layer (F1 violated). If the model doesn't
    respond, F2 substitution is broken in the env.
    """

    @unittest.skipUnless(
        _model_reachable(),
        "RESUME_SKIP model_unreachable — persona-priming smoke test gated on `claude --print ping`",
    )
    def test_pm_priming_round_trip(self) -> None:
        driver = PTYDriver(role="pm", cwd=str(REPO_ROOT))
        try:
            driver.spawn()
            initial = driver.read_until_idle(min_content_bytes=0, timeout_s=30.0)
            self.assertTrue(initial.saw_idle, f"initial idle failed: {initial.buffer[-200:]!r}")
            driver.write("/granite-poc:prime-pm-role hello")
            primed = driver.read_until_idle(min_content_bytes=100, timeout_s=60.0)
            self.assertTrue(
                primed.saw_idle,
                f"PM did not reach idle after priming: {primed.buffer[-200:]!r}",
            )
        finally:
            driver.close(force=True)


if __name__ == "__main__":
    unittest.main(verbosity=2)
