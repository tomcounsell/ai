"""#1919 test intent, ported against the graduated hook_edge module.

The idle Notification "Claude is waiting for your input" fires after every
response; treating it as a needs-human edge both leaked boilerplate to the
user and preempted delivery of the real answer. The graduated
``agent.session_runner.hook_edge`` classifies Notifications content-aware:

* idle-boilerplate / permission-boilerplate / empty message → NO edge
* substantive message → ``needs_human``
* ``PreToolUse(AskUserQuestion)`` → ``needs_human`` (unchanged)
* boilerplate matching is conservative (exact / stable prefix, not substring)

And the driver-level reconciliation prefers a ``turn_end`` edge over a
``needs_human`` edge when both arrive in one poll batch (inverting the
ordering bug that swallowed the PM's ``[/user]`` answer).

Also covers the headless hook-settings surface: ``PermissionRequest`` hooks
are no longer registered (they do not fire under ``claude -p``).
"""

from __future__ import annotations

import json
import time

import pytest

from agent.session_runner.hook_edge import (
    CLAUDE_CODE_BOILERPLATE,
    NEEDS_HUMAN,
    TURN_END,
    HookEdgeConsumer,
    generate_hook_settings,
    is_boilerplate_notification,
)

IDLE_TEXT = CLAUDE_CODE_BOILERPLATE["idle_exact"]
PERMISSION_PREFIX = CLAUDE_CODE_BOILERPLATE["permission_prefix"]


def _envelope_line(event: str, ts: float | None = None, **payload) -> str:
    payload = {"hook_event_name": event, **payload}
    return json.dumps(
        {"ts": ts if ts is not None else time.time(), "event": event, "payload": payload}
    )


def _consumer_with(tmp_path, *lines):
    edge = tmp_path / "edges.ndjson"
    edge.write_text("".join(line + "\n" for line in lines))
    return HookEdgeConsumer(str(edge), session_id=None)


# --------------------------------------------------------------------------
# Content-aware Notification classification
# --------------------------------------------------------------------------


def test_idle_notification_is_liveness_only(tmp_path):
    """The exact idle boilerplate emits NO edge."""
    consumer = _consumer_with(tmp_path, _envelope_line("Notification", message=IDLE_TEXT))
    assert consumer.poll() == []


@pytest.mark.parametrize("message", [None, "", "   ", "\n\t"])
def test_empty_notification_is_liveness_only(tmp_path, message):
    """A Notification with a missing/empty/whitespace-only message emits NO edge."""
    payload = {} if message is None else {"message": message}
    consumer = _consumer_with(tmp_path, _envelope_line("Notification", **payload))
    assert consumer.poll() == []


def test_permission_boilerplate_notification_is_liveness_only(tmp_path):
    """Permission-phrasing boilerplate never becomes a needs_human edge."""
    consumer = _consumer_with(
        tmp_path, _envelope_line("Notification", message=f"{PERMISSION_PREFIX}Bash")
    )
    assert consumer.poll() == []


def test_substantive_notification_is_needs_human(tmp_path):
    """A Notification with a real, non-boilerplate message still classifies
    as needs_human (defensive: preserves genuine input-request Notifications)."""
    consumer = _consumer_with(
        tmp_path,
        _envelope_line("Notification", message="Which environment should I deploy to?"),
    )
    edges = consumer.poll()
    assert len(edges) == 1
    assert edges[0].kind == NEEDS_HUMAN


def test_boilerplate_match_is_conservative_not_substring(tmp_path):
    """A legitimate message that merely contains boilerplate-adjacent words
    (e.g. 'input') must still route as needs_human."""
    consumer = _consumer_with(
        tmp_path,
        _envelope_line("Notification", message="I need more input on the schema design"),
    )
    edges = consumer.poll()
    assert len(edges) == 1
    assert edges[0].kind == NEEDS_HUMAN


def test_ask_user_question_pretooluse_is_needs_human(tmp_path):
    """PreToolUse(AskUserQuestion) remains a needs_human edge, unchanged."""
    consumer = _consumer_with(tmp_path, _envelope_line("PreToolUse", tool_name="AskUserQuestion"))
    edges = consumer.poll()
    assert len(edges) == 1
    assert edges[0].kind == NEEDS_HUMAN


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        (IDLE_TEXT, True),
        (f"  {IDLE_TEXT}  ", True),  # surrounding whitespace tolerated
        (f"{PERMISSION_PREFIX}Read", True),
        (None, True),
        ("", True),
        ("Please choose a deployment target", False),
        (f"Summary: {IDLE_TEXT} was shown twice today", False),  # substring ≠ match
    ],
)
def test_is_boilerplate_notification_matrix(message, expected):
    assert is_boilerplate_notification(message) is expected


# --------------------------------------------------------------------------
# Driver reconciliation: turn_end wins the batch
# --------------------------------------------------------------------------


async def test_turn_end_preferred_over_needs_human_in_one_batch(tmp_path):
    """When a Stop (turn_end) and a substantive Notification (needs_human)
    are drained in the same poll batch, the turn_end wins and needs_human is
    suppressed — the completed turn's real answer is delivered, never
    preempted (#1919 ordering inversion)."""
    from agent.session_runner.role_driver import HeadlessRoleDriver

    edge = tmp_path / "edges.ndjson"
    edge.touch()
    consumer = HookEdgeConsumer(str(edge), session_id=None)

    async def _harness(message, working_dir, **kwargs):
        ts = time.time() + 1
        with open(edge, "a") as f:
            f.write(_envelope_line("Stop", ts=ts) + "\n")
            f.write(
                _envelope_line("Notification", ts=ts, message="Which repo should I use?") + "\n"
            )
        return "[/user] the real answer"

    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-1919",
        working_dir=str(tmp_path),
        consumer=consumer,
        harness_fn=_harness,
    )
    outcome = await driver.run_turn("go")
    assert outcome.turn_ended is True
    assert outcome.turn_end_source == "hook_edge"
    assert outcome.needs_human is None  # suppressed by the fresh turn_end
    assert outcome.reply_text == "[/user] the real answer"


async def test_needs_human_without_turn_end_still_surfaces(tmp_path):
    """A substantive needs_human edge with NO fresh turn_end in the batch is
    still surfaced on the outcome (the preference only applies within a
    batch that also carries a turn_end)."""
    from agent.session_runner.role_driver import HeadlessRoleDriver

    edge = tmp_path / "edges.ndjson"
    edge.touch()
    consumer = HookEdgeConsumer(str(edge), session_id=None)

    async def _harness(message, working_dir, **kwargs):
        with open(edge, "a") as f:
            f.write(
                _envelope_line(
                    "Notification", ts=time.time() + 1, message="Need a decision from you"
                )
                + "\n"
            )
        return "partial"

    driver = HeadlessRoleDriver(
        role="pm",
        session_id="sess-1919b",
        working_dir=str(tmp_path),
        consumer=consumer,
        harness_fn=_harness,
    )
    outcome = await driver.run_turn("go")
    assert outcome.needs_human is not None
    assert outcome.needs_human.kind == NEEDS_HUMAN
    assert outcome.turn_end_source == "result"  # clean-exit fallback


def test_stop_then_idle_notification_batch_yields_only_turn_end(tmp_path):
    """The #1919 evidence scenario at the consumer level: a Stop and an idle
    Notification drained in one batch yield exactly one edge — the turn_end.
    The idle Notification produces nothing to preempt with."""
    consumer = _consumer_with(
        tmp_path,
        _envelope_line("Stop", transcript_path="/tmp/t.jsonl"),
        _envelope_line("Notification", message=IDLE_TEXT),
    )
    edges = consumer.poll()
    assert [e.kind for e in edges] == [TURN_END]


# --------------------------------------------------------------------------
# Headless hook-settings surface
# --------------------------------------------------------------------------


def test_permission_request_hook_not_registered(tmp_path):
    """PermissionRequest hooks do not fire under ``claude -p`` — the
    generated per-session settings must not register them."""
    settings_path, _edge = generate_hook_settings(tmp_path, tmp_path / "e.ndjson")
    hooks = json.loads((tmp_path / "session_runner_hook_settings.json").read_text())["hooks"]
    assert "PermissionRequest" not in hooks
    # The protocol hooks the runner depends on are all still registered.
    for event in (
        "Stop",
        "SubagentStop",
        "Notification",
        "PreToolUse",
        "PreCompact",
        "SessionStart",
    ):
        assert event in hooks


def test_forwarder_command_points_into_session_runner_package(tmp_path):
    """The registered hook command invokes the graduated forwarder path."""
    settings_path, edge = generate_hook_settings(tmp_path, tmp_path / "e.ndjson")
    settings = json.loads(open(settings_path).read())
    command = settings["hooks"]["Stop"][0]["hooks"][0]["command"]
    assert "agent/session_runner/hook_forwarder.py" in command
    assert command.endswith(f'"{edge}"')
