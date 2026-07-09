"""Regression tests for issue #1980 — the stale-UUID fallback must not clobber
a valid ``result`` event.

Root cause: ``get_response_via_harness`` (``agent/sdk_client.py``) ran a
destructive fresh-session retry whenever a resumed (``--resume``) subprocess
exited non-zero, WITHOUT checking whether that subprocess had already emitted a
``result`` event. A resumed wrap-up turn that produced a valid ``[/complete]``
completion and then exited non-zero had its good ``result_text`` overwritten by
the empty output of the fresh retry, so ``get_response_via_harness`` returned
``""``. ``HeadlessRoleDriver.run_turn``'s ``if not reply:`` guard then set
``exit_reason="empty_output"`` and the wrap-up guard delivered the canned
``OPERATOR_TERMINAL_MESSAGE`` instead of the real answer.

The fix gates the fallback on the true ``result_event_fired`` boolean (captured
from the primary invocation's ``on_exit_status`` callback), NOT on
``result_text is None`` — which is imprecise because ``_run_harness_subprocess``
returns a non-None string in BOTH the result-event branch (A) and the
accumulated-partial-text branch (B). The tests below pin all four branches.

``_run_harness_subprocess`` return tuple (8-tuple):
    (result_text, session_id_from_harness, returncode, usage, cost_usd,
     stderr_snippet, num_turns, tool_call_count)
Its ``on_exit_status(returncode, result_event_fired)`` callback is the ONLY
precise signal for "a result event fired." The fakes below invoke it faithfully.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

VALID_UUID = "36514af3-c4e9-455d-9087-f5850101990e"

COMPLETION_TEXT = (
    "Merged and verified. `origin/main` now has the hotfix at the top.\n\n"
    "[/complete]\nSend Reminders hotfix shipped to main — PR #604 merged."
)


def _make_fake_run(responses):
    """Build a fake ``_run_harness_subprocess`` that replays ``responses``.

    Each item is a dict with keys: ``result_text``, ``returncode``, ``fired``,
    and optional ``session_id`` / ``num_turns`` / ``tool_calls``. The fake
    invokes the passed ``on_exit_status(returncode, fired)`` callback exactly as
    the real helper does (``sdk_client.py`` line ~3089) before returning the
    8-tuple. It records the number of invocations on ``.calls``.
    """
    state = {"i": 0, "calls": 0}

    async def _fake(cmd, working_dir, proc_env, *, on_exit_status=None, **_kw):
        spec = responses[state["i"]] if state["i"] < len(responses) else responses[-1]
        state["i"] += 1
        state["calls"] += 1
        rc = spec["returncode"]
        fired = spec["fired"]
        if on_exit_status is not None:
            on_exit_status(rc, fired)
        return (
            spec["result_text"],
            spec.get("session_id"),
            rc,
            None,  # usage
            None,  # cost_usd
            None,  # stderr_snippet
            spec.get("num_turns", 0),
            spec.get("tool_calls", 0),
        )

    _fake.state = state  # type: ignore[attr-defined]
    return _fake


# ---------------------------------------------------------------------------
# get_response_via_harness — the four return branches of _run_harness_subprocess
# ---------------------------------------------------------------------------


class TestStaleUuidFallbackGate:
    """The fallback fires iff no ``result`` event fired on the primary turn."""

    @pytest.mark.asyncio
    async def test_branch_a_result_event_kept_on_nonzero_exit(self):
        """BRANCH A (the bug): a resumed subprocess emits a valid result event
        and then exits non-zero. The completion must be returned verbatim and
        the destructive fallback must NOT fire."""
        from agent.sdk_client import get_response_via_harness

        fake = _make_fake_run(
            [
                # Primary: valid result event, but non-zero exit afterward.
                {
                    "result_text": COMPLETION_TEXT,
                    "returncode": 1,
                    "fired": True,
                    "session_id": VALID_UUID,
                },
                # A fallback, IF it wrongly fired, would return empty — the
                # pre-fix clobber. Its presence lets us prove it is NOT reached.
                {"result_text": "", "returncode": 0, "fired": False},
            ]
        )
        with patch("agent.sdk_client._run_harness_subprocess", new=AsyncMock(side_effect=fake)):
            reply = await get_response_via_harness(
                message="wrap it up",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                prior_uuid=VALID_UUID,
                full_context_message="full context for a cold retry",
            )

        assert reply == COMPLETION_TEXT, "valid completion must survive a post-turn non-zero exit"
        assert fake.state["calls"] == 1, "fallback must NOT fire when a result event fired"

    @pytest.mark.asyncio
    async def test_branch_a_empty_result_event_is_genuinely_empty(self):
        """BRANCH A with an empty result event (``""``): a result event fired but
        carried no text. The fallback must NOT fire (a fired result event is the
        completion signal) and the empty string is returned — OPERATOR_TERMINAL_
        MESSAGE stays reserved for a genuinely empty PM turn (acceptance #3)."""
        from agent.sdk_client import get_response_via_harness

        fake = _make_fake_run(
            [
                {"result_text": "", "returncode": 1, "fired": True, "session_id": VALID_UUID},
                {"result_text": "SHOULD-NOT-APPEAR", "returncode": 0, "fired": True},
            ]
        )
        with patch("agent.sdk_client._run_harness_subprocess", new=AsyncMock(side_effect=fake)):
            reply = await get_response_via_harness(
                message="wrap it up",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                prior_uuid=VALID_UUID,
                full_context_message="full context",
            )

        assert reply == "", "an empty result event is a genuinely empty turn"
        assert fake.state["calls"] == 1, "fallback must NOT fire when a result event fired"

    @pytest.mark.asyncio
    async def test_branch_b_partial_text_no_result_event_still_fallbacks(self):
        """BRANCH B: no result event, but partial streamed text accumulated, then
        a non-zero exit. This is a crashed subprocess worth a fresh retry — the
        fallback MUST still fire (no regression of pre-existing recovery)."""
        from agent.sdk_client import get_response_via_harness

        fake = _make_fake_run(
            [
                # Primary: partial accumulated text, NO result event, crash exit.
                {"result_text": "partial streamed text", "returncode": 1, "fired": False},
                # Fallback (fresh session) recovers with a full answer.
                {
                    "result_text": "fresh full answer",
                    "returncode": 0,
                    "fired": True,
                    "session_id": VALID_UUID,
                },
            ]
        )
        with patch("agent.sdk_client._run_harness_subprocess", new=AsyncMock(side_effect=fake)):
            reply = await get_response_via_harness(
                message="wrap it up",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                prior_uuid=VALID_UUID,
                full_context_message="full context",
            )

        assert reply == "fresh full answer", "BRANCH B must still recover via the fallback"
        assert fake.state["calls"] == 2, "fallback MUST fire when no result event fired"

    @pytest.mark.asyncio
    async def test_branch_c_stale_uuid_no_output_still_fallbacks(self):
        """BRANCH C: a genuinely stale UUID — ``claude --resume`` errors before
        producing any output (no result event, no accumulated text, non-zero
        exit). The fallback MUST still fire (stale-UUID recovery preserved). This
        runs mocked so it exercises the gate in binary-free CI, unlike the live
        ``test_stale_uuid_triggers_fallback`` integration test."""
        from agent.sdk_client import get_response_via_harness

        fake = _make_fake_run(
            [
                {"result_text": None, "returncode": 1, "fired": False},
                {
                    "result_text": "recovered fresh",
                    "returncode": 0,
                    "fired": True,
                    "session_id": VALID_UUID,
                },
            ]
        )
        with patch("agent.sdk_client._run_harness_subprocess", new=AsyncMock(side_effect=fake)):
            reply = await get_response_via_harness(
                message="wrap it up",
                working_dir="/tmp",
                env={"AGENT_SESSION_ID": "x"},
                prior_uuid=VALID_UUID,
                full_context_message="full context",
            )

        assert reply == "recovered fresh", "BRANCH C (stale UUID) must recover via the fallback"
        assert fake.state["calls"] == 2, "fallback MUST fire for a genuinely stale UUID"


# ---------------------------------------------------------------------------
# End-to-end through HeadlessRoleDriver.run_turn — the delivered outcome
# ---------------------------------------------------------------------------


class TestRunTurnPropagatesCompletion:
    """The real get_response_via_harness → HeadlessRoleDriver.run_turn path must
    surface the completion (non-empty reply_text, not the empty-output guard) so
    the wrap-up guard delivers the real text, not OPERATOR_TERMINAL_MESSAGE."""

    @pytest.mark.asyncio
    async def test_run_turn_returns_completion_on_nonzero_exit_with_result(self, tmp_path):
        from agent.session_runner.role_driver import HeadlessRoleDriver

        fake = _make_fake_run(
            [
                {
                    "result_text": COMPLETION_TEXT,
                    "returncode": 1,
                    "fired": True,
                    "session_id": VALID_UUID,
                },
                {"result_text": "", "returncode": 0, "fired": False},
            ]
        )
        driver = HeadlessRoleDriver(
            role="pm",
            session_id="test-1980-e2e",
            working_dir=str(tmp_path),
            project_root=str(tmp_path),
            full_context_message="full context for a cold retry",
            # harness_fn=None → uses the real get_response_via_harness, so the
            # #1980 gate is exercised end-to-end.
        )
        # Ride --resume so prior_uuid is set (the fallback only runs on resume).
        driver.seed_resume(VALID_UUID)

        with patch("agent.sdk_client._run_harness_subprocess", new=AsyncMock(side_effect=fake)):
            outcome = await driver.run_turn("send your wrap-up now")

        assert outcome.reply_text == COMPLETION_TEXT, "real completion must reach run_turn's return"
        assert outcome.exit_reason != "empty_output", "must NOT hit the empty-output guard"
        assert fake.state["calls"] == 1, "fallback must not have clobbered the completion"

    @pytest.mark.asyncio
    async def test_floor_triggered_resume_preserves_completion_for_attribution(self, tmp_path):
        """C7 (#1917): the crash-recovery deterministic floor drives ``--resume``
        against sessions that just died mid-tool-call — exactly the conditions
        that produce a resumed wrap-up turn emitting a valid result event and
        then exiting non-zero (#1980/#1985). The floor triggers ``resume_session``,
        which the worker services as a ``--resume`` turn; that resumed turn must
        return the valid completion so Phase-1 outcome attribution records
        ``recovered`` rather than mis-recording a crash and re-crash.

        This reuses the branch-A fixtures (result event fired + non-zero exit)
        via the real run_turn path with a seeded resume UUID — the harness code
        is trigger-agnostic, so the floor-resume path inherits the #1980 gate.
        """
        from agent.session_runner.role_driver import HeadlessRoleDriver

        fake = _make_fake_run(
            [
                # Resumed wrap-up turn: valid completion, then a post-turn
                # non-zero exit (the mid-tool-wedge death shape the floor rescues).
                {
                    "result_text": COMPLETION_TEXT,
                    "returncode": 1,
                    "fired": True,
                    "session_id": VALID_UUID,
                },
                # A destructive fresh retry would clobber the completion — it must
                # never be reached.
                {"result_text": "SHOULD-NOT-APPEAR", "returncode": 0, "fired": False},
            ]
        )
        driver = HeadlessRoleDriver(
            role="eng",
            session_id="test-1917-floor-resume",
            working_dir=str(tmp_path),
            project_root=str(tmp_path),
            full_context_message="full context for a cold retry",
        )
        # The floor's resume_session drives --resume on the persisted UUID.
        driver.seed_resume(VALID_UUID)

        with patch("agent.sdk_client._run_harness_subprocess", new=AsyncMock(side_effect=fake)):
            outcome = await driver.run_turn("continue")

        assert outcome.reply_text == COMPLETION_TEXT, (
            "floor-triggered --resume must preserve the valid completion so "
            "outcome attribution records 'recovered', not a crash"
        )
        assert outcome.exit_reason != "empty_output"
        assert fake.state["calls"] == 1, "the completion must not be clobbered by a fresh retry"
