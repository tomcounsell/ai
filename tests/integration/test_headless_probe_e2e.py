"""Headless real-CLI probes for the session-runner turn-end + prime contracts.

Replacement for the deleted ``test_granite_ollama_e2e.py`` (#1924 Test Impact:
REPLACE) — keeps the headless turn-end and prime-resolution probe subtests,
drops the recorder / hook-fidelity / ollama-substrate subtests that died with
the PTY harness. The probes now run against the production subscription-auth
substrate (the only substrate post-cutover).

Placement + gating: lives under ``tests/integration/`` and self-gates on
``HEADLESS_PROBE_SMOKE=1`` AND the ``claude`` binary being on PATH. Each
probe spawns a real ``claude -p`` turn, so the smoke tests are opt-in — they
double as the canary for new ``claude`` binary releases (Risk 4: stream-json
event drift fails loudly here before it fails in production).

The env-contract test is always-on and deterministic: it proves the
production ``subscription_auth_env`` overlay blanks every third-party model
route so a shell exporting an ollama endpoint can never silently redirect a
role turn (G5).
"""

from __future__ import annotations

import os
import unittest

from agent.session_runner.role_driver import subscription_auth_env
from tests.unit.session_runner.headless_hook_probe import claude_binary_available

HEADLESS_PROBE_SMOKE = os.environ.get("HEADLESS_PROBE_SMOKE") == "1"

_PROBE_READY: bool = HEADLESS_PROBE_SMOKE and claude_binary_available()

_SKIP_REASON = (
    "Headless real-CLI probes are opt-in: set HEADLESS_PROBE_SMOKE=1 with the "
    "`claude` binary on PATH (each probe spawns a real subscription-auth turn)."
)


class TestSubscriptionEnvContract(unittest.TestCase):
    """Always-on: the subscription-auth env posture needs no real CLI to prove.

    ``HeadlessRoleDriver`` spawns every role turn with this overlay merged
    over ``os.environ`` — a stray third-party route (ollama base URL, metered
    API key) surviving into the child env would silently change the substrate.
    """

    def test_third_party_model_routes_are_blanked(self) -> None:
        base = {
            "ANTHROPIC_API_KEY": "sk-metered-key",
            "ANTHROPIC_BASE_URL": "http://localhost:11434",
            "ANTHROPIC_AUTH_TOKEN": "ollama",
        }
        env = subscription_auth_env(base=base)
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "")

    def test_oauth_token_forwarded_when_present(self) -> None:
        """The subscription OAuth token (when the worker env carries one) is
        forwarded into the overlay — subscription auth, not metered."""
        if not os.environ.get("CLAUDE_CODE_OAUTH_TOKEN"):
            self.skipTest("no CLAUDE_CODE_OAUTH_TOKEN in this environment")
        env = subscription_auth_env()
        self.assertEqual(env["CLAUDE_CODE_OAUTH_TOKEN"], os.environ["CLAUDE_CODE_OAUTH_TOKEN"])


@unittest.skipUnless(_PROBE_READY, _SKIP_REASON)
class TestHeadlessTurnEndProbe(unittest.TestCase):
    """Probe A: the Stop hook flushes a TURN_END envelope for a `claude -p`
    turn, and the stream-json result event is parseable — the two turn-end
    signals ``HeadlessRoleDriver`` reconciles."""

    def test_turn_end_envelope_and_result_event_land(self) -> None:
        from tests.unit.session_runner.headless_hook_probe import run_headless_turn_end_probe

        result = run_headless_turn_end_probe()

        self.assertEqual(
            result.returncode,
            0,
            f"claude -p exited nonzero; stderr tail: {result.stderr_tail}",
        )
        self.assertTrue(
            result.turn_end_landed,
            "no TURN_END envelope landed in the edge file — the hook-edge "
            f"turn-end signal is broken on this claude release. result={result}",
        )
        self.assertIsNotNone(
            result.result_event,
            "no stream-json result event parsed — the fallback turn-end "
            f"signal is broken on this claude release. result={result}",
        )
        # The envelope must be attributable to this session.
        self.assertEqual(result.turn_end_payload.get("session_id"), result.session_id)


@unittest.skipUnless(_PROBE_READY, _SKIP_REASON)
class TestPrimeResolutionProbe(unittest.TestCase):
    """Probe B: the PM prime slash command resolves under `claude -p` — the
    primed persona's routing-token convention surfaces in the reply."""

    def test_pm_prime_resolves_to_persona(self) -> None:
        from tests.unit.session_runner.headless_hook_probe import run_prime_resolution_probe

        result = run_prime_resolution_probe(role="pm")

        self.assertEqual(
            result.returncode,
            0,
            f"primed claude -p exited nonzero; stderr tail: {result.stderr_tail}",
        )
        self.assertTrue(
            result.routing_token_present,
            "no [/user]/[/complete] routing token in the primed reply — the "
            "prime slash command did not resolve under -p. "
            f"reply={result.result_text!r}",
        )
        # Formatting fidelity (token alone on its own line) is the production
        # parsing contract; report it loudly if the substrate drifts, but the
        # resolution oracle above is the hard gate.
        if not result.strict_token_line:
            print(
                "[headless-probe] persona loaded but token-line discipline "
                f"imperfect: {result.result_text!r}"
            )
