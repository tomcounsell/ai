"""Integration tests for granite mid-run steering (issue #1779).

These drive the REAL granite ``Container`` against real PTYs (real ``claude``
TUIs + Ollama) — no mocking of PTY writes — exactly as the worker does in
production via ``BridgeAdapter``. They are **env-gated** on the same
``claude --print ping`` + Ollama reachability check the existing
``test_granite_container_loop.py`` uses; in a non-reachable env they skip with
a structured reason.

Two paths are exercised:

  - **Part 1 (bridge → PM):** a steering message pushed to the real Redis list
    ``steering:{session_id}`` is drained by the container's ``poll_steering``
    callback at a steady-state turn boundary and injected into the PM PTY — the
    PM transcript then contains the steering text.
  - **Part 2 (PM → Dev):** when the PM emits ``[/dev:steer] <text>`` the
    container writes the **token-stripped** text to the Dev PTY immediately and
    the run does NOT exit ``pm_hang``/``dev_hang`` (the PM is acked so it keeps
    producing turns).

These are additive — the existing loop test is untouched.
"""

from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
import unittest
import urllib.request
import uuid
from pathlib import Path

from agent.steering import (
    clear_steering_queue,
    pop_all_steering_messages,
    push_steering_message,
)


def _model_reachable() -> bool:
    """Same env check as the loop integration test; inlined to avoid cross-import."""
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


# Cached at module load so all xdist workers see the same value.
_MODEL_REACHABLE: bool = _model_reachable()

_NON_HANG_EXITS = {"pm_hang", "dev_hang"}


@unittest.skipUnless(
    _MODEL_REACHABLE,
    "RESUME_SKIP model_unreachable — integration test gated on `claude --print ping` + Ollama",
)
class TestGraniteMidRunSteeringIntegration(unittest.TestCase):
    """Env-gated end-to-end steering tests against real PTYs."""

    def test_part1_bridge_steering_injected_into_pm_transcript(self) -> None:
        """A message pushed to ``steering:{session_id}`` mid-run is drained by the
        container and injected into the PM PTY; the PM transcript shows it."""
        from agent.granite_container.container import Container

        session_id = "local-steer-" + uuid.uuid4().hex[:12]
        marker = f"STEERMARKER_{uuid.uuid4().hex[:8]}"
        clear_steering_queue(session_id)
        # Pre-push so the message is waiting when the container drains the queue
        # at its first steady-state turn boundary (atomic LPOP — race-free).
        push_steering_message(session_id, f"Please note: {marker}", "TestOperator")
        self.addCleanup(clear_steering_queue, session_id)

        with tempfile.TemporaryDirectory() as td:
            container = Container(
                # A delegation task keeps PM in the steady-state loop (it routes
                # to Dev rather than terminating at the prime-turn relay), so the
                # steering drain at the turn boundary is reached.
                user_message=(
                    "Delegate to the developer: ask them to run `echo hello` and "
                    "report the output back. Then summarize for the user."
                ),
                cwd=td,
                max_turns=5,
                # Mirror the BridgeAdapter closure: drain the real Redis list.
                poll_steering=lambda: pop_all_steering_messages(session_id),
            )
            result = container.run()

            # The container must have reached the steady-state loop (where the
            # steering drain lives). A startup failure is an environment problem,
            # not a steering assertion — surface it clearly.
            self.assertNotIn(
                result.exit_reason,
                {"startup_unresolved", "exception"},
                f"run did not reach steady state: {result.exit_reason} / {result.exit_message}",
            )

            # The queue was drained by the container (not left for us).
            self.assertEqual(
                pop_all_steering_messages(session_id),
                [],
                "steering queue was not drained by the container",
            )

            # The PM transcript must contain the injected steering text.
            self.assertIsNotNone(result.pm_transcript_path, "no PM transcript path on result")
            pm_path = Path(result.pm_transcript_path)
            self.assertTrue(pm_path.exists(), f"PM transcript missing: {pm_path}")
            transcript = pm_path.read_text(errors="replace")
            self.assertIn(
                marker,
                transcript,
                "steering marker was not injected into the PM transcript",
            )

    def test_part2_dev_steer_no_hang_and_token_stripped(self) -> None:
        """When PM emits ``[/dev:steer]`` the run does not hang, and if a
        dev_steer turn fired the Dev transcript is token-free."""
        from agent.granite_container.container import Container

        with tempfile.TemporaryDirectory() as td:
            container = Container(
                user_message=(
                    "You are steering a developer. As your VERY FIRST reply, send a "
                    "mid-task correction to the developer using the steer prefix. "
                    "Reply with EXACTLY this line and nothing else:\n"
                    "[/dev:steer] focus on the auth module, skip the migration"
                ),
                cwd=td,
                max_turns=4,
            )
            result = container.run()

            # The key Risk-3 guarantee: a [/dev:steer] turn must NOT leave PM
            # hanging on an empty idle read (the PM_DEV_STEER_ACK prevents it).
            # This holds whether or not the model complied with the prefix.
            self.assertNotIn(
                result.exit_reason,
                _NON_HANG_EXITS,
                f"run exited {result.exit_reason} (a hang) after the steer prompt: "
                f"{result.exit_message}",
            )

            # If the model complied and a dev_steer turn fired, the Dev transcript
            # must be token-free (the literal [/dev:steer] must never reach Dev).
            dev_steer_turns = [t for t in result.turns if t.classification == "dev_steer"]
            if dev_steer_turns and result.dev_transcript_path:
                dev_path = Path(result.dev_transcript_path)
                if dev_path.exists():
                    dev_transcript = dev_path.read_text(errors="replace")
                    self.assertNotIn(
                        "[/dev:steer]",
                        dev_transcript,
                        "the literal [/dev:steer] routing token leaked into the Dev transcript",
                    )


if __name__ == "__main__":
    unittest.main(verbosity=2)
