"""Unit tests for the graduated HeadlessRoleDriver (plan #1924, task 1).

Covers, in isolation with an injected ``harness_fn`` and a real (or fake)
``HookEdgeConsumer`` over a temp NDJSON edge file:

* Prime injection — BOTH branches (slash-command and append-system-prompt).
* Turn-end reconciliation — BOTH branches (a TURN_END envelope honored when
  present; the clean-exit ``result`` fallback when absent).
* Race 4 — a stale TURN_END from a prior sequential turn does not end the next.
* Hung subprocess — bounded-wait timeout kills + classifies the turn.
* Nonzero exit — the corruption exception propagates a structured TurnFailure.
* Empty result — hits the empty-output guard.
* G5 — the subprocess env carries the explicit subscription-auth posture.
"""

from __future__ import annotations

import json
import time

import pytest

from agent.session_runner.hook_edge import HookEdgeConsumer
from agent.session_runner.role_driver import (
    _PRIME_COMMAND_DIR,
    PRIME_PATH_APPEND,
    PRIME_PATH_SLASH,
    HeadlessRoleDriver,
)
from agent.session_runner.router import ExitReason


def _write_edge(edge_path, *, kind_event="Stop", session_id=None, ts=None):
    """Append one hook envelope to the NDJSON edge file (forwarder format)."""
    payload = {"hook_event_name": kind_event}
    if session_id:
        payload["session_id"] = session_id
        payload["transcript_path"] = f"/tmp/{session_id}.jsonl"
    envelope = {
        "event": kind_event,
        "payload": payload,
        "ts": ts if ts is not None else time.time(),
    }
    with open(edge_path, "a") as f:
        f.write(json.dumps(envelope) + "\n")


def _make_harness(reply="the reply", record=None):
    """Build an async fake harness_fn recording the kwargs it was called with."""

    async def _fake(message, working_dir, **kwargs):
        if record is not None:
            record.append({"message": message, "working_dir": working_dir, **kwargs})
        return reply

    return _fake


# --------------------------------------------------------------------------
# Prime injection — both branches
# --------------------------------------------------------------------------


async def test_prime_append_system_prompt_branch(tmp_path):
    """Default (append) path injects the prime body via system_prompt, leaves
    the message intact."""
    calls = []
    prime_dir = tmp_path / _PRIME_COMMAND_DIR
    prime_dir.mkdir(parents=True)
    (prime_dir / "prime-pm-role.md").write_text(
        "---\ndescription: x\n---\n\nYou are the PM persona body."
    )
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-1",
        working_dir=str(tmp_path),
        prime_path=PRIME_PATH_APPEND,
        project_root=str(tmp_path),
        harness_fn=_make_harness(record=calls),
    )
    await driver.run_turn("do the thing")
    assert calls[0]["message"] == "do the thing"  # message untouched
    assert "PM persona body" in (calls[0]["system_prompt"] or "")
    assert "description: x" not in (calls[0]["system_prompt"] or "")  # frontmatter stripped


async def test_prime_slash_command_branch(tmp_path):
    """Slash path prepends the role's /roles:prime-* command to the message,
    with no system_prompt."""
    calls = []
    driver = HeadlessRoleDriver(
        role="dev",
        session_id="sess-2",
        working_dir=str(tmp_path),
        prime_path=PRIME_PATH_SLASH,
        harness_fn=_make_harness(record=calls),
    )
    await driver.run_turn("build it")
    assert calls[0]["message"] == "/roles:prime-dev-role build it"
    assert calls[0]["system_prompt"] is None


async def test_prime_only_on_first_turn(tmp_path):
    """Priming happens once; the second turn sends the bare message + --resume."""
    calls = []
    driver = HeadlessRoleDriver(
        role="dev",
        session_id="sess-3",
        working_dir=str(tmp_path),
        prime_path=PRIME_PATH_SLASH,
        harness_fn=_make_harness(record=calls),
    )
    await driver.run_turn("first")
    await driver.run_turn("second")
    assert calls[0]["message"] == "/roles:prime-dev-role first"
    assert calls[1]["message"] == "second"  # no prime on turn 2


# --------------------------------------------------------------------------
# Turn-end reconciliation — both branches
# --------------------------------------------------------------------------


async def test_turn_end_prefers_hook_envelope(tmp_path):
    """When a TURN_END envelope lands (postdating the pre-spawn snapshot), it is
    the turn-end authority (source='hook_edge')."""
    edge = tmp_path / "edges.ndjson"
    edge.touch()
    consumer = HookEdgeConsumer(str(edge), session_id=None)

    # The fake harness writes a fresh TURN_END edge as its side effect (as a
    # real subprocess Stop hook would), then returns the reply.
    async def _harness(message, working_dir, **kwargs):
        _write_edge(edge, kind_event="Stop", ts=time.time() + 1)
        return "done"

    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-4",
        working_dir=str(tmp_path),
        consumer=consumer,
        harness_fn=_harness,
    )
    outcome = await driver.run_turn("go")
    assert outcome.turn_ended is True
    assert outcome.turn_end_source == "hook_edge"
    assert outcome.reply_text == "done"


async def test_turn_end_falls_back_to_clean_exit(tmp_path):
    """When no TURN_END envelope lands before the subprocess exits, the clean
    exit is the authoritative boundary (source='result')."""
    edge = tmp_path / "edges.ndjson"
    edge.touch()
    consumer = HookEdgeConsumer(str(edge), session_id=None)
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-5",
        working_dir=str(tmp_path),
        consumer=consumer,
        harness_fn=_make_harness(reply="clean"),
    )
    outcome = await driver.run_turn("go")
    assert outcome.turn_ended is True
    assert outcome.turn_end_source == "result"
    assert outcome.reply_text == "clean"


async def test_race4_stale_turn_end_does_not_end_next_turn(tmp_path):
    """A stale TURN_END from a prior turn (predating the pre-spawn snapshot) must
    NOT be honored as the next turn's boundary — the next turn falls back to its
    own clean exit (Race 4)."""
    edge = tmp_path / "edges.ndjson"
    edge.touch()
    consumer = HookEdgeConsumer(str(edge), session_id=None)

    # Turn 1: harness writes a fresh Stop → hook_edge.
    async def _h1(message, working_dir, **kwargs):
        _write_edge(edge, kind_event="Stop", ts=time.time() + 1)
        return "r1"

    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-6",
        working_dir=str(tmp_path),
        consumer=consumer,
        harness_fn=_h1,
    )
    o1 = await driver.run_turn("t1")
    assert o1.turn_end_source == "hook_edge"

    # Turn 2: harness writes NOTHING new. The pre-spawn snapshot drains any
    # residual edge; no fresh TURN_END → must fall back to clean exit, never
    # re-honor turn 1's Stop.
    driver._harness_fn = _make_harness(reply="r2")
    o2 = await driver.run_turn("t2")
    assert o2.turn_ended is True
    assert o2.turn_end_source == "result"


# --------------------------------------------------------------------------
# Failure classification
# --------------------------------------------------------------------------


async def test_hung_subprocess_is_killed_and_classified(tmp_path):
    """A harness call that never returns within the bounded wait is classified
    hung with a timeout exit_reason (not an unbounded block)."""
    import asyncio

    async def _never(message, working_dir, **kwargs):
        await asyncio.sleep(10)
        return "never"

    driver = HeadlessRoleDriver(
        role="dev",
        session_id="sess-7",
        working_dir=str(tmp_path),
        turn_timeout_s=0.2,
        harness_fn=_never,
    )
    outcome = await driver.run_turn("go")
    assert outcome.hung is True
    assert outcome.turn_ended is False
    assert outcome.failure is not None
    assert outcome.failure.reason is ExitReason.HEADLESS_TURN_TIMEOUT


async def test_nonzero_exit_propagates_exit_reason(tmp_path):
    """A HarnessThinkingBlockCorruptionError (nonzero exit + corruption) is
    caught and surfaced as a structured TurnFailure, not swallowed. The
    exception text travels in ``detail``, never smuggled into the reason."""
    from agent.sdk_client import HarnessThinkingBlockCorruptionError

    async def _boom(message, working_dir, **kwargs):
        raise HarnessThinkingBlockCorruptionError("thinking block corrupted")

    driver = HeadlessRoleDriver(
        role="dev",
        session_id="sess-8",
        working_dir=str(tmp_path),
        harness_fn=_boom,
    )
    outcome = await driver.run_turn("go")
    assert outcome.turn_ended is False
    assert outcome.failure is not None
    assert outcome.failure.reason is ExitReason.HEADLESS_THINKING_CORRUPTION
    assert "corrupt" in outcome.failure.detail.lower()
    # Legacy wire format preserved for exit_message telemetry.
    assert str(outcome.failure) == "headless_thinking_corruption: thinking block corrupted"


async def test_empty_result_hits_empty_output_guard(tmp_path):
    """An empty reply hits the empty-output guard (failure set, not looped)."""
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-9",
        working_dir=str(tmp_path),
        harness_fn=_make_harness(reply=""),
    )
    outcome = await driver.run_turn("go")
    assert outcome.turn_ended is False
    assert outcome.failure is not None
    assert outcome.failure.reason is ExitReason.EMPTY_OUTPUT


async def test_binary_missing_classified(tmp_path):
    """The harness's inline binary-not-found marker is classified, not treated
    as a normal reply."""
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-10",
        working_dir=str(tmp_path),
        harness_fn=_make_harness(reply="Error: CLI harness not found — claude"),
    )
    outcome = await driver.run_turn("go")
    assert outcome.failure is not None
    assert outcome.failure.reason is ExitReason.HEADLESS_BINARY_MISSING


async def test_claude_session_id_capture(tmp_path, monkeypatch):
    """After a successful first turn the driver exposes the captured claude
    UUID + derived transcript path (feeds the four-scalar resume persistence)."""
    import agent.sdk_client as sdk

    monkeypatch.setattr(sdk, "_get_prior_session_uuid", lambda sid: "claude-uuid-xyz")
    driver = HeadlessRoleDriver(
        role="dev",
        session_id="sess-11",
        working_dir=str(tmp_path),
        prime_path=PRIME_PATH_APPEND,
        project_root=str(tmp_path),
        harness_fn=_make_harness(reply="ok"),
    )
    assert driver.claude_session_id is None
    assert driver.transcript_path is None
    await driver.run_turn("go")
    assert driver.claude_session_id == "claude-uuid-xyz"
    assert driver.transcript_path.endswith("claude-uuid-xyz.jsonl")
    assert ".claude/projects/" in driver.transcript_path


# --------------------------------------------------------------------------
# Resume-id assert-and-alarm (plan #2000 Task 2.2 / Task 2.1 probe finding)
# --------------------------------------------------------------------------


async def test_resume_id_stable_across_turns_no_alarm(tmp_path, caplog):
    """The now-expected case: --resume reuses the session id across turns.
    No drift log fires."""
    calls = []

    async def _stable_harness(message, working_dir, **kwargs):
        on_init = kwargs.get("on_init")
        if on_init is not None:
            on_init({"type": "system", "subtype": "init", "session_id": "stable-uuid"})
        calls.append(message)
        return "ok"

    driver = HeadlessRoleDriver(
        role="dev",
        session_id="sess-stable",
        working_dir=str(tmp_path),
        prime_path=PRIME_PATH_SLASH,
        harness_fn=_stable_harness,
    )
    with caplog.at_level("ERROR"):
        await driver.run_turn("first")
        await driver.run_turn("second")

    assert driver.claude_session_id == "stable-uuid"
    assert not any("claude session id drift" in r.message for r in caplog.records)


async def test_resume_id_drift_logs_error_and_adopts_new_id(tmp_path, caplog):
    """If a future CLI ever forks the session id under plain --resume (the
    pre-#2000 assumption), the driver still adopts the new id (so --resume
    keeps working) but now logs an error-level drift alarm instead of
    silently expecting it."""

    async def _forking_harness(message, working_dir, **kwargs):
        on_init = kwargs.get("on_init")
        sid = "uuid-turn-1" if "first" in message else "uuid-turn-2-forked"
        if on_init is not None:
            on_init({"type": "system", "subtype": "init", "session_id": sid})
        return "ok"

    driver = HeadlessRoleDriver(
        role="dev",
        session_id="sess-drift",
        working_dir=str(tmp_path),
        prime_path=PRIME_PATH_SLASH,
        harness_fn=_forking_harness,
    )
    with caplog.at_level("ERROR"):
        await driver.run_turn("first turn")
        assert driver.claude_session_id == "uuid-turn-1"
        await driver.run_turn("second turn")

    # Still adopts the newly-observed id (resume must keep working even if
    # a future CLI regresses to forking behavior).
    assert driver.claude_session_id == "uuid-turn-2-forked"
    assert driver.transcript_path.endswith("uuid-turn-2-forked.jsonl")
    drift_records = [r for r in caplog.records if "claude session id drift" in r.message]
    assert len(drift_records) == 1
    assert drift_records[0].levelname == "ERROR"


# --------------------------------------------------------------------------
# Driver-seam: stdout stream drives liveness even in a toolless window
# (issue #1935, CRITIQUE pass 1 Concern 4)
# --------------------------------------------------------------------------


async def test_toolless_stdout_window_fires_on_stdout_event(tmp_path):
    """Deterministic proof that the real stream (not just Element 1's unit
    test stamping last_stdout_at directly) fires on_stdout_event during a
    toolless window: a fake harness that emits `init` then assistant stdout
    lines with NO tool-call event must still drive the on_stdout_event
    callback the same way sdk_client.py's real dispatch does
    (`_run_harness_subprocess`, one call per non-empty stdout line)."""

    async def _toolless_streaming_harness(message, working_dir, **kwargs):
        on_init = kwargs.get("on_init")
        on_stdout_event = kwargs.get("on_stdout_event")
        if on_init is not None:
            on_init({"type": "system", "subtype": "init", "session_id": "claude-uuid-toolless"})
        # Simulate several stdout lines of assistant reasoning/output with NO
        # tool_use content block anywhere — the exact toolless-turn shape
        # that used to be silently invisible to session-health.
        for _ in range(3):
            if on_stdout_event is not None:
                on_stdout_event()
        return "final toolless reply"

    stdout_events = []
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-toolless",
        working_dir=str(tmp_path),
        harness_fn=_toolless_streaming_harness,
        on_stdout_event=lambda: stdout_events.append(1),
    )
    outcome = await driver.run_turn("go")
    assert outcome.reply_text == "final toolless reply"
    assert len(stdout_events) >= 1, (
        "the stdout stream must drive on_stdout_event even when no tool "
        "boundary ever fires during the turn"
    )


# --------------------------------------------------------------------------
# G5 — explicit subscription-auth env injection
# --------------------------------------------------------------------------


async def test_subprocess_env_pins_subscription_auth(tmp_path, monkeypatch):
    """The harness env overlay strips ANTHROPIC_API_KEY (blanked so the
    overlay overrides an inherited value), blanks the endpoint overrides, and
    carries CLAUDE_CODE_OAUTH_TOKEN from the vault-loaded process env."""
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "oauth-token-abc")
    calls = []
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-g5",
        working_dir=str(tmp_path),
        env={"AGENT_SESSION_ID": "sess-g5", "ANTHROPIC_API_KEY": "sk-leaked"},
        harness_fn=_make_harness(record=calls),
    )
    await driver.run_turn("go")
    env = calls[0]["env"]
    assert env["CLAUDE_CODE_OAUTH_TOKEN"] == "oauth-token-abc"
    assert env["ANTHROPIC_API_KEY"] == ""  # explicit strip, never inherited
    assert env["ANTHROPIC_BASE_URL"] == ""
    assert env["ANTHROPIC_AUTH_TOKEN"] == ""
    assert env["AGENT_SESSION_ID"] == "sess-g5"  # caller overlay preserved


async def test_subprocess_env_without_vault_token(tmp_path, monkeypatch):
    """Absent CLAUDE_CODE_OAUTH_TOKEN, the overlay still strips API-key auth
    and does not invent a token key."""
    monkeypatch.delenv("CLAUDE_CODE_OAUTH_TOKEN", raising=False)
    calls = []
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-g5b",
        working_dir=str(tmp_path),
        harness_fn=_make_harness(record=calls),
    )
    await driver.run_turn("go")
    env = calls[0]["env"]
    assert "CLAUDE_CODE_OAUTH_TOKEN" not in env
    assert env["ANTHROPIC_API_KEY"] == ""


@pytest.mark.parametrize("prime_path", [PRIME_PATH_APPEND, PRIME_PATH_SLASH])
async def test_role_kwarg_always_set(tmp_path, prime_path):
    """Every headless turn calls the harness with its role (blocker 1/2).
    Schema diet (#1927): the `metered` flag was dead plumbing — it routed
    nowhere once `accumulate_session_tokens` collapsed to a single `total_*`
    write path — and has been removed entirely from the harness-adapter
    contract (`TurnRequest`, `get_response_via_harness`). `role` remains
    threaded through, so this call-shape guard stays valid for it."""
    calls = []
    prime_dir = tmp_path / _PRIME_COMMAND_DIR
    prime_dir.mkdir(parents=True)
    (prime_dir / "prime-pm-role.md").write_text("body")
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-12",
        working_dir=str(tmp_path),
        prime_path=prime_path,
        project_root=str(tmp_path),
        harness_fn=_make_harness(record=calls),
    )
    await driver.run_turn("go")
    assert calls[0]["role"] == "pm"
    assert "metered" not in calls[0]


# --------------------------------------------------------------------------
# Nonzero exit without a result event (PR #1930 review, A5 — residual #1916)
# --------------------------------------------------------------------------


def _status_harness(reply, returncode, result_event_fired):
    """Fake harness that reports the subprocess exit status via the
    ``on_exit_status`` callback (as get_response_via_harness does)."""

    async def _fake(message, working_dir, **kwargs):
        on_exit_status = kwargs.get("on_exit_status")
        if on_exit_status is not None:
            on_exit_status(returncode, result_event_fired)
        return reply

    return _fake


async def test_nonzero_exit_without_result_event_is_not_a_clean_turn(tmp_path):
    """A subprocess that exits nonzero WITHOUT a ``result`` event but WITH
    partial streamed text must be classified as a failed turn, not a clean
    ``turn_end_source="result"`` turn."""
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-a5",
        working_dir=str(tmp_path),
        harness_fn=_status_harness("partial streamed text", 1, False),
    )
    outcome = await driver.run_turn("go")
    assert outcome.failure is not None
    assert outcome.failure.reason is ExitReason.HEADLESS_NONZERO_EXIT_NO_RESULT
    assert outcome.turn_ended is False


async def test_nonzero_exit_with_result_event_stays_clean(tmp_path):
    """A result event is the protocol's completion signal — a nonzero exit
    AFTER it does not invalidate the turn."""
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-a5b",
        working_dir=str(tmp_path),
        harness_fn=_status_harness("real result", 1, True),
    )
    outcome = await driver.run_turn("go")
    assert outcome.failure is None
    assert outcome.turn_ended is True
    assert outcome.turn_end_source == "result"


async def test_zero_exit_without_result_event_stays_clean(tmp_path):
    """Accumulated-text fallback on a CLEAN exit remains a valid turn."""
    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-a5c",
        working_dir=str(tmp_path),
        harness_fn=_status_harness("accumulated text", 0, False),
    )
    outcome = await driver.run_turn("go")
    assert outcome.failure is None
    assert outcome.turn_ended is True
