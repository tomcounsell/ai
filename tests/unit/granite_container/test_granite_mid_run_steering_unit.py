"""Unit tests for granite mid-run steering (issue #1779).

Covers the worker-side consumer of the steering channel that the bridge
already populates:

  - Part B: the ``[/dev:steer]`` (``dev_steer``) routing branch in
    ``Container._route_pm_classification`` — token strip, empty-after-strip
    nudge — plus the per-turn steering-poll behavior in the steady-state loop
    (fail-silent drain, empty drain, empty-text skip, happy-path injection).
  - Part C: the ``is_abort`` steering path — the user-facing
    ``STEER_ABORT_USER_MESSAGE`` is delivered through ``on_user_payload``
    BEFORE the loop breaks with ``exit_reason="steer_abort"``, and
    ``steer_abort`` is a clean granite exit.
  - Part D: the ``BridgeAdapter`` ``poll_steering`` closure — it drains the
    real Redis list and is fail-silent (returns ``[]`` + logs a warning when
    the drain raises).

Routing-logic tests use a plain in-process stub callback for ``poll_steering``
and ``MagicMock(spec=PTYDriver)`` PTY fakes (the same fakes the existing
container unit tests use) so we can assert what was written to each PTY. No
Redis and no real ``claude`` TUI spawn is required for the routing tier.

Part D drives the REAL adapter closure (captured via a Container constructor
spy) against the real ``agent.steering`` Redis queue — that is the seam that
catches a wiring regression a hand-rolled closure cannot.
"""

from __future__ import annotations

import asyncio
import logging
import tempfile
import unittest
from dataclasses import dataclass, field
from typing import Any
from unittest.mock import MagicMock, patch

from agent.granite_container.container import (
    PM_COMPLIANCE_NUDGE,
    PM_DEV_STEER_ACK,
    STEER_ABORT_USER_MESSAGE,
    STEER_HARNESS_SUFFIX,
    Container,
    ContainerResult,
)
from agent.granite_container.granite_classifier import (
    ClassificationResult,
    classify_pm_prefix,
)
from agent.granite_container.pty_driver import IdleResult, PTYDriver

# ---------------------------------------------------------------------------
# Shared fakes (mirror tests/unit/granite_container/test_container.py)
# ---------------------------------------------------------------------------


def _idle_result(buffer_text: str = "", saw_idle: bool = True) -> IdleResult:
    return IdleResult(
        saw_idle=saw_idle,
        buffer=buffer_text,
        idle_marker="bypass permissions on",
        elapsed_ms=10,
    )


def _mock_driver(session_id: str) -> MagicMock:
    mock = MagicMock(spec=PTYDriver)
    mock.read_until_idle.return_value = _idle_result("", saw_idle=True)
    mock.last_resume_uuid.return_value = None
    mock.isalive.return_value = True
    mock._session_id = session_id
    return mock


def _pm_writes(pm_mock: MagicMock) -> list[str]:
    """All string args written to the PM PTY across the run."""
    return [c.args[0] for c in pm_mock.write.call_args_list if c.args]


def _steering_writes(pm_mock: MagicMock) -> list[str]:
    """The subset of PM writes that are injected steering blocks."""
    return [w for w in _pm_writes(pm_mock) if "[Steering from" in w]


# ---------------------------------------------------------------------------
# Part B.1 — dev_steer routing branch (direct _route_pm_classification calls)
# ---------------------------------------------------------------------------


class TestDevSteerRouting(unittest.TestCase):
    """The ``[/dev:steer]`` reserved-suffix branch writes the token-stripped
    instruction to the Dev PTY and acks PM, without blocking on Dev idle."""

    def _container(self) -> tuple[Container, MagicMock, MagicMock]:
        c = Container(user_message="hello", max_turns=3)
        pm = _mock_driver("mock-session-pm")
        dev = _mock_driver("mock-session-dev")
        c._pm_pty = pm
        c._dev_pty = dev
        return c, pm, dev

    def test_single_line_payload_strips_token_before_dev_write(self) -> None:
        """A single-line ``[/dev:steer] fix the auth test`` payload (which the
        classifier returns with the token RETAINED) must reach the Dev PTY
        token-free; the literal ``[/dev:steer]`` must never be written to Dev."""
        c, pm, dev = self._container()
        result = ContainerResult(session_id="s1", user_message="hello")

        classification = classify_pm_prefix("[/dev:steer] fix the auth test")
        # Precondition: the classifier leaves the token in the payload — this is
        # exactly the leak the dev_steer branch must scrub before the Dev write.
        self.assertEqual(classification.harness, STEER_HARNESS_SUFFIX)
        self.assertIn("[/dev:steer]", classification.payload)

        outcome = c._route_pm_classification(classification, "", 0, result)

        # Write-and-continue: the loop does not break on a steer.
        self.assertFalse(outcome.should_break)

        # The Dev PTY received the cleaned instruction, token-stripped.
        dev.write.assert_called_once()
        dev_written = dev.write.call_args.args[0]
        self.assertNotIn("[/dev:steer]", dev_written)
        self.assertIn("fix the auth test", dev_written)

        # PM received the one-line continuation ack (Risk 3 — no pm_hang).
        self.assertIn(PM_DEV_STEER_ACK, _pm_writes(pm))

        # A dev_steer TurnRecord was appended.
        self.assertEqual(result.turns[-1].classification, "dev_steer")

    def test_strict_payload_already_token_free_reaches_dev(self) -> None:
        """The strict form (token alone on line 1) yields a token-free payload;
        the defensive strip is a no-op and the instruction reaches Dev intact."""
        c, pm, dev = self._container()
        result = ContainerResult(session_id="s2", user_message="hello")

        classification = classify_pm_prefix("[/dev:steer]\nfocus on the auth module")
        self.assertEqual(classification.harness, STEER_HARNESS_SUFFIX)

        outcome = c._route_pm_classification(classification, "", 0, result)

        self.assertFalse(outcome.should_break)
        dev.write.assert_called_once()
        dev_written = dev.write.call_args.args[0]
        self.assertNotIn("[/dev:steer]", dev_written)
        self.assertIn("focus on the auth module", dev_written)
        self.assertEqual(result.turns[-1].classification, "dev_steer")

    def test_token_only_payload_nudges_pm_and_skips_dev_write(self) -> None:
        """A payload that is JUST the token (non-empty before strip, empty after)
        is a no-op steer: PM gets the compliance nudge and Dev is NOT written to.

        Built directly as a ClassificationResult to exercise the dev_steer
        branch's empty-after-strip guard (the empty-``[/dev]`` guard upstream
        already catches a genuinely empty payload, so this guard is the
        belt-and-suspenders path for a token-only fallback payload)."""
        c, pm, dev = self._container()
        result = ContainerResult(session_id="s3", user_message="hello")

        classification = ClassificationResult(
            destination="dev",
            payload="[/dev:steer]",  # non-empty, but empty once the token is scrubbed
            compliance_miss=True,
            raw_first_line="[/dev:steer]",
            harness=STEER_HARNESS_SUFFIX,
        )

        outcome = c._route_pm_classification(classification, "", 0, result)

        self.assertFalse(outcome.should_break)
        # No Dev write for a no-op steer.
        dev.write.assert_not_called()
        # PM received the compliance nudge.
        self.assertIn(PM_COMPLIANCE_NUDGE, _pm_writes(pm))


# ---------------------------------------------------------------------------
# Full-run harness for the steady-state steering-poll behavior (Part B.2 + C)
# ---------------------------------------------------------------------------


def _run_container_with_poll(
    poll_steering,
    *,
    on_user_payload=None,
    max_turns: int = 1,
) -> tuple[ContainerResult, MagicMock, MagicMock]:
    """Drive ``Container.run`` with mocked PTYs to reach the steady-state loop.

    Every PM/Dev read returns an empty idle buffer and ``last_assistant_text``
    returns "" so PM classifies as ``unknown`` each turn — the run simply
    advances turns and exits ``pm_max_turns`` (unless the steering path breaks
    it first). The heavy spawn/prime/pkill machinery is patched out. Returns
    ``(result, pm_mock, dev_mock)``.
    """
    c = Container(
        user_message="hello",
        max_turns=max_turns,
        on_user_payload=on_user_payload,
        poll_steering=poll_steering,
    )
    pm = _mock_driver("mock-session-pm")
    dev = _mock_driver("mock-session-dev")
    pm.read_until_idle.side_effect = lambda **kw: _idle_result("", saw_idle=True)
    dev.read_until_idle.side_effect = lambda **kw: _idle_result("", saw_idle=True)

    with (
        patch.object(c, "_spawn_pair"),
        patch.object(c, "_close_pair"),
        patch.object(c, "_prime_session"),
        patch.object(c, "_close_pair_and_reap"),
        patch.object(c, "_run_wrapup_guard"),
        patch(
            "agent.granite_container.container.last_assistant_text",
            side_effect=lambda path, *, baseline_text_count=None: "",
        ),
    ):
        c._pm_pty = pm
        c._dev_pty = dev
        result = c.run()
    return result, pm, dev


class _OneShot:
    """A poll_steering stub that yields a payload once, then drains empty."""

    def __init__(self, messages: list[dict]) -> None:
        self._messages = messages
        self.calls = 0

    def __call__(self) -> list[dict]:
        self.calls += 1
        if self.calls == 1:
            return list(self._messages)
        return []


class TestSteadyStateSteeringPoll(unittest.TestCase):
    """The per-turn steering drain at the top of the steady-state loop."""

    def test_empty_drain_writes_no_steering_block(self) -> None:
        """``poll_steering`` returning ``[]`` injects nothing — the no-steering
        turn behaves as before (no ``[Steering from …]`` write to PM)."""
        poll = _OneShot([])  # always returns []
        result, pm, _dev = _run_container_with_poll(poll)

        self.assertEqual(_steering_writes(pm), [])
        self.assertEqual(result.exit_reason, "pm_max_turns")
        self.assertGreaterEqual(poll.calls, 1)

    def test_raising_poll_does_not_crash_loop(self) -> None:
        """A ``poll_steering`` callback that raises is treated as an empty drain;
        the loop continues and exits cleanly (fail-silent, like ``on_turn``)."""

        def _boom() -> list[dict]:
            raise RuntimeError("redis exploded")

        result, pm, _dev = _run_container_with_poll(_boom)

        # No crash: run reached a normal terminal state.
        self.assertEqual(result.exit_reason, "pm_max_turns")
        # Nothing injected.
        self.assertEqual(_steering_writes(pm), [])

    def test_empty_text_message_is_skipped(self) -> None:
        """A drained message with whitespace-only ``text`` is skipped — not
        written to the PM PTY as a steering block."""
        poll = _OneShot([{"text": "   ", "sender": "Tom", "is_abort": False}])
        result, pm, _dev = _run_container_with_poll(poll)

        self.assertEqual(_steering_writes(pm), [])
        self.assertEqual(result.exit_reason, "pm_max_turns")

    def test_valid_message_is_injected_into_pm_pty(self) -> None:
        """Positive control: a valid drained message is written to the PM PTY as
        a ``[Steering from {sender}]: {text}`` block (so the negative cases above
        are meaningful)."""
        poll = _OneShot([{"text": "check the staging deploy", "sender": "Tom", "is_abort": False}])
        result, pm, _dev = _run_container_with_poll(poll)

        steering = _steering_writes(pm)
        self.assertEqual(len(steering), 1)
        self.assertIn("[Steering from Tom]", steering[0])
        self.assertIn("check the staging deploy", steering[0])
        self.assertEqual(result.exit_reason, "pm_max_turns")


# ---------------------------------------------------------------------------
# Part C — abort path
# ---------------------------------------------------------------------------


class TestSteerAbort(unittest.TestCase):
    """An ``is_abort`` drained message delivers the fixed user-facing string
    BEFORE breaking, and exits ``steer_abort`` (a clean granite exit)."""

    def test_abort_delivers_user_message_before_break(self) -> None:
        delivered: list[str] = []

        def _on_user(payload: str) -> None:
            delivered.append(payload)

        poll = _OneShot([{"text": "stop", "sender": "Tom", "is_abort": True}])
        result, _pm, _dev = _run_container_with_poll(poll, on_user_payload=_on_user, max_turns=3)

        # The user-facing abort string was delivered exactly once via the
        # on_user_payload channel — and nothing double-delivered (the wrap-up
        # guard must NOT also fire for steer_abort).
        self.assertEqual(delivered, [STEER_ABORT_USER_MESSAGE])
        self.assertEqual(STEER_ABORT_USER_MESSAGE, "Session stopped at your request.")

        # Clean controlled termination.
        self.assertEqual(result.exit_reason, "steer_abort")
        self.assertTrue(result.user_facing_routed)

    def test_steer_abort_is_a_clean_granite_exit(self) -> None:
        """``steer_abort`` must be classified as a controlled (clean) exit, not a
        REACTION_ERROR. Assert set membership directly — do NOT route through
        ``_is_non_clean_granite_exit`` (whose name/return polarity is inverted)."""
        from agent.session_executor import _CLEAN_GRANITE_EXIT_REASONS

        self.assertIn("steer_abort", _CLEAN_GRANITE_EXIT_REASONS)


# ---------------------------------------------------------------------------
# Part D — BridgeAdapter poll_steering closure (real Redis, fail-silent)
# ---------------------------------------------------------------------------


@dataclass
class _FakeSession:
    session_id: str = "unit-steer-adapter-session"
    chat_id: int = 4242
    telegram_message_id: int = 99
    session_events: list = field(default_factory=list)


def _capture_adapter_poll_closure(session_id: str):
    """Run a BridgeAdapter with mocked spawn + a Container spy and return the
    real ``poll_steering`` closure it built (bound to ``session_id``)."""
    from agent.granite_container import bridge_adapter as ba
    from agent.granite_container.pty_pool import PTYPool

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".json")
    tmp.close()
    pool = PTYPool(pool_size=1, pid_registry_path=tmp.name)
    session = _FakeSession(session_id=session_id)
    seen: dict[str, Any] = {}

    def _fake_container(**kwargs):
        seen.update(kwargs)
        container = MagicMock()
        result = MagicMock()
        result.exit_reason = "pm_complete"
        result.exit_message = "done"
        result.turns = []
        result.classification_compliance_misses = 0
        result.user_facing_routed = False
        result.transcript_fallback_count = 0
        container.run = lambda: result
        return container

    with (
        patch("agent.granite_container.pty_pool.PTYDriver.spawn", lambda self: None),
        patch.object(ba, "Container", side_effect=_fake_container),
    ):
        asyncio.run(pool.initialize(cwd="/tmp"))
        adapter = ba.BridgeAdapter(
            agent_session=session,
            project_key="t",
            transport="telegram",
            pool=pool,
            resolve_callbacks=lambda p, t: (None, None),
        )
        asyncio.run(adapter.run("hello", "/tmp"))

    closure = seen.get("poll_steering")
    assert closure is not None, "BridgeAdapter did not pass poll_steering to Container"
    return closure


class TestBridgeAdapterPollClosure(unittest.TestCase):
    """The closure delegates to ``agent.steering.pop_all_steering_messages`` and
    is fail-silent (returns ``[]`` + logs a warning when the drain raises)."""

    def setUp(self) -> None:
        import agent.steering as steering

        self._steering = steering
        self._sid = "unit-steer-adapter-session"
        steering.clear_steering_queue(self._sid)

    def tearDown(self) -> None:
        self._steering.clear_steering_queue(self._sid)

    def test_closure_drains_real_queued_messages(self) -> None:
        """Happy path: a message pushed to ``steering:{session_id}`` is returned
        by the closure. This is the regression guard for the wiring (a missing
        import would silently make the closure always return ``[]``)."""
        closure = _capture_adapter_poll_closure(self._sid)
        self._steering.clear_steering_queue(self._sid)
        self._steering.push_steering_message(self._sid, "fix the auth test", "Tom")

        drained = closure()

        self.assertEqual(len(drained), 1)
        self.assertEqual(drained[0]["text"], "fix the auth test")
        self.assertEqual(drained[0]["sender"], "Tom")
        self.assertFalse(drained[0]["is_abort"])

    def test_closure_returns_empty_and_logs_when_drain_raises(self) -> None:
        """Fail-silent: when ``pop_all_steering_messages`` raises, the closure
        returns ``[]`` and logs a warning (observable, not a silent pass)."""
        closure = _capture_adapter_poll_closure(self._sid)
        self._steering.push_steering_message(self._sid, "ignored", "Tom")

        def _boom(_sid):
            raise RuntimeError("redis down")

        with (
            patch(
                "agent.granite_container.bridge_adapter.pop_all_steering_messages",
                side_effect=_boom,
            ),
            self.assertLogs("agent.granite_container.bridge_adapter", level=logging.WARNING) as cm,
        ):
            drained = closure()

        self.assertEqual(drained, [])
        self.assertTrue(
            any("poll_steering drain failed" in line for line in cm.output),
            f"expected a 'poll_steering drain failed' warning, got: {cm.output}",
        )


if __name__ == "__main__":
    unittest.main(verbosity=2)
