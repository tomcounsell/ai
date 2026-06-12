"""Integration test for the granite container loop (PoC #1546).

This integration test runs the PoC end-to-end **through the
`valor-granite-loop` CLI**, exactly as the operator and the plan's
Agent Integration spec invoke it. Driving the registered entry
point (rather than importing `Container` directly) means this test
catches a missing `[project.scripts]` registration -- a class of
failure a direct in-process call is blind to.

It is **env-gated** on the `claude --print "ping"` prerequisite
(the same env check the substrate driver tests use). In a
non-reachable env, the test is *skipped* with a structured reason.

The test exercises:
  - The `valor-granite-loop` entry point resolves on PATH
  - A short end-to-end run with `--max-turns 3` (a short run that
    won't loop forever) writes a well-formed results JSON
  - The stdout summary JSON and the written results JSON shapes

It is **not** the full historical verdict (that lives in
docs/plans/completed/granite-interactive-tui-poc-results.md). It is a
regression guard that the container's loop runs to completion in a
model-reachable env, invoked the way the operator invokes it.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
import urllib.request
from pathlib import Path


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

# Exit codes the CLI maps from exit_reason. A best-effort PoC run may
# end on any of these; the test asserts shape, not a specific verdict.
_VALID_EXIT_CODES = {0, 1, 2, 3, 4}

# Keys the written results JSON (result_to_json -> asdict) must carry.
_RESULTS_KEYS = {
    "session_id",
    "user_message",
    "turns",
    "exit_reason",
    "total_pm_pty_bytes",
    "total_dev_pty_bytes",
    "parse_failures",
    "classification_compliance_misses",
}

# Keys the CLI prints to stdout as a one-line operator summary.
_SUMMARY_KEYS = {
    "session_id",
    "exit_reason",
    "turns",
    "classification_compliance_misses",
    "parse_failures",
    "total_pm_pty_bytes",
    "total_dev_pty_bytes",
    "output_path",
}


@unittest.skipUnless(
    _MODEL_REACHABLE,
    "RESUME_SKIP model_unreachable — integration test gated on `claude --print ping`",
)
class TestGraniteContainerIntegration(unittest.TestCase):
    """Env-gated end-to-end run driven through the registered CLI."""

    def test_cli_short_run_produces_results_json(self) -> None:
        """`valor-granite-loop` runs end-to-end and writes a well-formed results JSON."""
        # Blocker-1 guard: the entry point must be registered in
        # [project.scripts]. A direct Container() call cannot catch this.
        cli = shutil.which("valor-granite-loop")
        self.assertIsNotNone(
            cli,
            "valor-granite-loop entry point not on PATH — is it registered in "
            "[project.scripts] and the package installed (uv sync)?",
        )

        with tempfile.TemporaryDirectory() as td:
            out_path = Path(td) / "granite_poc_results.json"
            proc = subprocess.run(
                [
                    cli,
                    "--user-message",
                    "say hi in three words",
                    "--max-turns",
                    "3",
                    "--output",
                    str(out_path),
                    "--cwd",
                    td,
                ],
                capture_output=True,
                text=True,
                timeout=600,
            )

            # The run is best-effort; any mapped exit code is acceptable.
            self.assertIn(
                proc.returncode,
                _VALID_EXIT_CODES,
                f"unexpected exit code {proc.returncode}; stderr:\n{proc.stderr}",
            )

            # stdout carries a one-line summary JSON (last non-empty line).
            stdout_lines = [ln for ln in proc.stdout.splitlines() if ln.strip()]
            self.assertTrue(stdout_lines, f"CLI produced no stdout; stderr:\n{proc.stderr}")
            summary = json.loads(stdout_lines[-1])
            self.assertTrue(
                _SUMMARY_KEYS.issubset(summary),
                f"summary missing keys {_SUMMARY_KEYS - set(summary)}",
            )

            # The results JSON must exist and carry the full result shape.
            self.assertTrue(out_path.exists(), "CLI did not write the results JSON")
            payload = json.loads(out_path.read_text())
            self.assertTrue(
                _RESULTS_KEYS.issubset(payload),
                f"results JSON missing keys {_RESULTS_KEYS - set(payload)}",
            )
            self.assertIsNotNone(payload["session_id"])
            self.assertIn(
                payload["exit_reason"],
                {
                    "pm_complete",
                    "pm_max_turns",
                    "dev_hang",
                    "pm_hang",
                    "startup_unresolved",
                    "exception",
                },
            )
            self.assertIsInstance(payload["turns"], list)


if __name__ == "__main__":
    unittest.main(verbosity=2)
