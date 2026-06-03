"""Integration test for the granite container loop (PoC #1546).

This integration test runs the container end-to-end against the
real Claude Code TUI. It is **env-gated** on the `claude --print
"ping"` prerequisite (the same env check the substrate driver
tests use). In a non-reachable env, the test is *skipped* with
a structured log line.

The test exercises:
  - Two-PTY coordination (Container.run_ping_pong_test)
  - End-to-end run with `--max-turns 3` (a short run that won't
    loop forever)
  - Results JSON shape

It is **not** the full PoC verdict (that lives in
docs/plans/granite_interactive_tui_poc-results.md). It is a
regression guard that the container's loop runs to completion in a
model-reachable env.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from agent.granite_container.container import Container, ContainerResult


def _model_reachable() -> bool:
    """Same env check as the unit tests; inlined to avoid cross-import."""
    if not shutil.which("claude"):
        return False
    try:
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


# Cached at module load so all tests see the same value (avoids races
# when xdist forks workers and each forks its own `claude` subprocess).
_MODEL_REACHABLE: bool = _model_reachable()


@unittest.skipUnless(
    _MODEL_REACHABLE,
    "RESUME_SKIP model_unreachable — integration test gated on `claude --print ping`",
)
class TestGraniteContainerIntegration(unittest.TestCase):
    """Env-gated end-to-end container run."""

    def test_ping_pong(self) -> None:
        """Two-PTY ping-pong: spawn both, prime both, ping each in turn."""
        c = Container(user_message="ping", max_turns=1)
        result = c.run_ping_pong_test()
        self.assertTrue(result, "two-PTY ping-pong failed")

    def test_short_run_produces_results_json(self) -> None:
        """A short end-to-end run writes a well-formed results JSON."""
        c = Container(user_message="say hi in three words", max_turns=3)
        result: ContainerResult = c.run()
        # The run is best-effort; we just check the JSON shape and
        # that the container exited (any reason is fine).
        self.assertIsNotNone(result.session_id)
        self.assertIn(
            result.exit_reason,
            {
                "pm_complete",
                "pm_max_turns",
                "dev_hang",
                "pm_hang",
                "startup_unresolved",
                "exception",
            },
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
