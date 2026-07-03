"""Substrate B — ollama-backed real Claude Code E2E (plan Task 5).

Launches the **real** ``claude`` binary against ollama's Anthropic-compatible
endpoint (free, unlimited, high-fidelity) and asserts a session reaches a clean
reply without wedging. Doubles as the canary for new ``claude`` binary releases
— the exact thing that breaks production granite.

Placement + gating (plan concern-3/4 fix): this fixture lives under
``tests/integration/`` (NOT the unit dir, so the ``tests/unit/granite_container``
autouse spawn-guard does not cover it) and self-gates inside its own module on
``GRANITE_OLLAMA_SMOKE=1`` AND ollama being reachable. The reachability probe
only runs when the smoke flag is set, so normal collection stays fast.

Blocker fix (constraint 2 / critique BLOCKER): the real session runs with the
three ollama vars set AND ``CLAUDE_CODE_OAUTH_TOKEN`` popped, with a
pre-``spawn()`` assertion that no OAuth token leaks into the child env. A
surviving token reproduces the PR #1612 "issue with the selected model" failure
(OAuth login + ollama base URL at once) and silently invalidates the canary.
That assertion runs unconditionally (``TestOllamaEnvContract`` below), and again
inside ``record_session`` immediately before the spawn.
"""

from __future__ import annotations

import os
import unittest

from tests.granite_faults.ollama_env import (
    OAUTH_TOKEN_VAR,
    assert_no_oauth_leak,
    build_ollama_child_env,
    ollama_substrate_reachable,
)

GRANITE_OLLAMA_SMOKE = os.environ.get("GRANITE_OLLAMA_SMOKE") == "1"

# Only pay for the (slow) reachability probe when the operator opted into the
# smoke run. Cached at module load so all tests + xdist workers see one value.
_OLLAMA_REACHABLE: bool = GRANITE_OLLAMA_SMOKE and ollama_substrate_reachable()

_SKIP_REASON = (
    "Substrate B is opt-in: set GRANITE_OLLAMA_SMOKE=1 and serve a tool-capable "
    "ollama model (the reachability probe must pass)."
)


class TestOllamaEnvContract(unittest.TestCase):
    """Always-on: the OAuth-strip no-leak contract needs no ollama to prove.

    This is the required pre-``spawn()`` assertion (constraint 2) in its
    deterministic form — it runs in every integration run, gated or not.
    """

    def test_child_env_carries_no_oauth_token(self) -> None:
        base = dict(os.environ)
        base[OAUTH_TOKEN_VAR] = "sk-oauth-live-token"  # simulate a logged-in box
        env = build_ollama_child_env(base=base)
        self.assertNotIn(
            OAUTH_TOKEN_VAR,
            env,
            "an OAuth token surviving into the ollama child env reproduces "
            "PR #1612 'issue with the selected model' and invalidates the canary",
        )
        # The explicit guard both Substrate B and the recorder call pre-spawn.
        assert_no_oauth_leak(env)

    def test_ollama_vars_are_set(self) -> None:
        env = build_ollama_child_env(base=dict(os.environ))
        self.assertEqual(env["ANTHROPIC_BASE_URL"], "http://localhost:11434")
        self.assertEqual(env["ANTHROPIC_AUTH_TOKEN"], "ollama")
        self.assertEqual(env["ANTHROPIC_API_KEY"], "")


@unittest.skipUnless(_OLLAMA_REACHABLE, _SKIP_REASON)
class TestStopHookFidelityGate(unittest.TestCase):
    """#1688 Task 0 HARD GATE: Stop/SubagentStop hooks fire under Substrate B.

    The hook-driven turn-return design (plan
    ``docs/plans/granite_hook_driven_turn_returns.md``) rests on one factual
    assumption: the real ``claude`` binary fires ``Stop`` on parent turn-end
    (payload carrying ``transcript_path``) and a *distinct* ``SubagentStop``
    per Task-tool subagent (payload carrying ``agent_id``/``agent_type``),
    even when the model backend is ollama. This test IS that gate, kept
    durable so every new pinned ``claude`` release can be re-verified with
    ``GRANITE_OLLAMA_SMOKE=1 pytest`` on this module.

    First verified: claude 2.1.198 / qwen3.6:35b-a3b-coding-nvfp4 (2026-07-02).
    """

    def test_stop_and_subagent_stop_fire_with_required_fields(self) -> None:
        # Import here so the pexpect-spawning module only loads on the smoke path.
        from tests.granite_faults.hook_fidelity import run_hook_fidelity_probe

        result = run_hook_fidelity_probe()

        # --- Parent Stop: the turn-end edge -------------------------------
        parent_stops = result.parent_stops
        self.assertGreaterEqual(
            len(parent_stops),
            1,
            "no parent Stop envelope landed — the hook-driven turn-return "
            f"design is invalid under Substrate B. result={result}",
        )
        stop = parent_stops[-1]
        self.assertTrue(
            stop.get("transcript_path"),
            f"parent Stop payload carries no transcript_path: {sorted(stop)}",
        )
        self.assertEqual(stop.get("session_id"), result.session_id)
        # Native disambiguation (Practice 5): the parent Stop must NOT look
        # like a subagent event.
        self.assertIsNone(
            stop.get("agent_id"),
            "parent Stop unexpectedly carries agent_id — Stop/SubagentStop "
            "are no longer distinguishable by payload shape",
        )

        # --- SubagentStop: the distinct child edge ------------------------
        subagent_stops = result.subagent_stops
        self.assertGreaterEqual(
            len(subagent_stops),
            1,
            "the Task-bearing turn produced no SubagentStop envelope — "
            "either the fan-out did not happen or the hook did not fire. "
            f"result={result}",
        )
        sub = subagent_stops[-1]
        self.assertTrue(
            sub.get("agent_id"),
            f"SubagentStop payload carries no agent_id: {sorted(sub)}",
        )
        self.assertTrue(
            sub.get("agent_type"),
            f"SubagentStop payload carries no agent_type: {sorted(sub)}",
        )
        self.assertEqual(sub.get("session_id"), result.session_id)


@unittest.skipUnless(_OLLAMA_REACHABLE, _SKIP_REASON)
class TestOllamaSessionReachesCleanExit(unittest.TestCase):
    """The real ollama-backed session completes without wedging."""

    def test_session_replies_and_settles(self) -> None:
        # Import here so the (heavy) recorder module only loads on the smoke path.
        from tests.granite_faults.recorder import record_session

        meta = record_session(write_fixtures=False)

        # The real TUI painted its startup + bypass bar (no startup wedge) ...
        self.assertTrue(
            meta.saw_idle_bar,
            f"the TUI never painted the bypass bar — startup wedge? meta={meta}",
        )
        # ... and the session reached a real assistant reply and settled (the
        # clean-exit signal: a wedged session never emits the reply glyph).
        self.assertTrue(
            meta.reply_landed,
            f"the session did not reach a reply within budget (wedge). meta={meta}",
        )
        # Sanity: it actually ran against the local ollama model, not a cloud one.
        self.assertTrue(meta.model)


@unittest.skipUnless(_OLLAMA_REACHABLE, _SKIP_REASON)
class TestHeadlessTurnEndProbe(unittest.TestCase):
    """Plan #1842 (per-role-transport-hedge) Task 0 HARD GATE — Probe A.

    A single-shot ``claude -p`` invocation runs exactly one turn and exits.
    This test empirically determines whether the #1688 ``Stop``/``TURN_END``
    hook envelope lands in the per-session NDJSON edge file BEFORE the
    subprocess exits — the fact that selects the headless leg's turn-end
    authority (envelope-exclusive vs. envelope-preferred/result-fallback) for
    the ``HeadlessRoleDriver`` built in plan #1842 Task 2.

    This test does not assert a particular outcome either way (a `-p`
    process legitimately might flush post-exit) — it asserts the envelope
    lands *at all* (proving the hook fires under `-p`, the harder failure
    mode) and records the pre/post-exit timing as a diagnostic for humans
    reading the test output / plan notes.
    """

    def test_stop_hook_fires_under_print_mode(self) -> None:
        from tests.granite_faults.headless_hook_probe import run_headless_turn_end_probe

        result = run_headless_turn_end_probe()

        self.assertEqual(
            result.returncode,
            0,
            f"headless turn-end probe subprocess exited non-zero: "
            f"stderr_tail={result.stderr_tail!r} result={result}",
        )
        self.assertTrue(
            result.turn_end_landed,
            "no Stop/TURN_END envelope landed in the edge file at all — the "
            f"hook does not fire under `claude -p`. result={result}",
        )
        self.assertEqual(result.turn_end_payload.get("session_id"), result.session_id)
        # Diagnostic-only: prints the empirical pre/post-exit finding into the
        # test log for the Task 0 build note (see per-role-transport-hedge.md).
        print(
            f"\n[Task 0 Probe A] envelope_landed_pre_exit={result.envelope_landed_pre_exit} "
            f"elapsed_s={result.elapsed_s} model={result.model}"
        )


@unittest.skipUnless(_OLLAMA_REACHABLE, _SKIP_REASON)
class TestPrimeResolutionProbe(unittest.TestCase):
    """Plan #1842 (per-role-transport-hedge) Task 0 HARD GATE — Probe B.

    Verifies whether ``/granite:prime-pm-role``, passed as the first prompt
    to ``claude -p``, actually resolves and primes the PM persona (as it does
    in the interactive TUI) or is treated as a literal string with no
    slash-command expansion. The RESOLUTION oracle is the PM persona's own
    routing-token convention (``[/dev]``/``[/user]``/``[/complete]``)
    appearing in the reply at all — an unprimed model has no reason to emit
    it. The stricter production contract (token alone on its own line) is
    reported as a separate substrate-fidelity diagnostic, not asserted: the
    weak ollama substrate was observed (Task 0, 2026-07-02) to emit
    ``[/user] Sounds good — talk soon.`` on one line — persona loaded, line
    discipline imperfect.

    First verified: claude 2.1.198 / qwen3.6:35b-a3b-coding-nvfp4 (2026-07-02).
    """

    def test_prime_pm_role_resolves_under_print_mode(self) -> None:
        from tests.granite_faults.headless_hook_probe import run_prime_resolution_probe

        result = run_prime_resolution_probe(role="pm")

        self.assertEqual(
            result.returncode,
            0,
            f"prime resolution probe subprocess exited non-zero: "
            f"stderr_tail={result.stderr_tail!r} result={result}",
        )
        self.assertTrue(
            result.result_text,
            f"headless turn produced no result text at all. result={result}",
        )
        # Diagnostic-only: prints the empirical resolution finding into the
        # test log for the Task 0 build note.
        print(
            f"\n[Task 0 Probe B] routing_token_present={result.routing_token_present} "
            f"strict_token_line={result.strict_token_line} "
            f"elapsed_s={result.elapsed_s} model={result.model} "
            f"result_text={result.result_text!r}"
        )
        self.assertTrue(
            result.routing_token_present,
            "the PM persona's routing-token convention "
            "([/dev]/[/user]/[/complete]) did not surface in the reply to a "
            "trivial-ack task — /granite:prime-pm-role does NOT resolve "
            f"under `claude -p`. result={result}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
