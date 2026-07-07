"""End-to-end runner dispatch test (plan #1924, task 4).

Proves the executor actually dispatches through the REAL
``agent/session_runner/`` stack — ``_execute_agent_session`` →
``SessionRunner`` → ``HeadlessRoleDriver`` → harness — with only the harness
function faked (no real ``claude -p`` subprocess is spawned). This is the
test that guards against the PR #1848 B1 class of defect: a fully built +
unit-tested execution leg that is never actually wired into dispatch.

Covered end-to-end:

* The PM turn is dispatched through ``HeadlessRoleDriver`` with the prime
  slash command prepended on the first turn, ``metered=True``, ``role="pm"``.
* The subscription-auth env posture (G5) AND the executor's per-session env
  (SESSION_TYPE, AGENT_SESSION_ID) both reach the harness subprocess env.
* A ``[/user]`` PM reply routes through the ``SessionRunnerAdapter`` delivery
  callback to the registered bridge send callback (transport-keyed per the
  repo convention) and marks ``user_facing_routed``.
* Capture-at-init (Race 5): the stream-json ``system/init`` session id is
  persisted onto the AgentSession's four resume scalars mid-run.
* The terminal ``exit_reason`` lands on the AgentSession via
  ``publish_exit_summary``.
"""

from __future__ import annotations

import tempfile
import uuid
from datetime import UTC, datetime
from unittest.mock import patch

import pytest

from models.agent_session import AgentSession

pytestmark = pytest.mark.sdlc


def _make_session(message_text: str = "ship the fix") -> AgentSession:
    return AgentSession.create(
        session_id=f"runner-e2e-{uuid.uuid4().hex[:12]}",
        session_type="eng",
        project_key="test",
        working_dir="/tmp",
        status="pending",
        chat_id="999",
        message_text=message_text,
        sender_name="tester",
        created_at=datetime.now(tz=UTC),
        turn_count=0,
        tool_call_count=0,
    )


def _worktree_path() -> str:
    import os

    base = tempfile.mkdtemp()
    wt_path = os.path.join(base, ".worktrees", "e2e-slot")
    os.makedirs(wt_path, exist_ok=True)
    return wt_path


@pytest.mark.asyncio
async def test_executor_dispatches_through_real_runner_to_delivery(redis_test_db, tmp_path):
    """Full dispatch: executor → SessionRunner → HeadlessRoleDriver → fake
    harness → ``[/user]`` payload → registered send callback."""
    from agent.session_executor import _execute_agent_session

    session = _make_session()
    session.status = "running"
    session.save(update_fields=["status"])

    claude_uuid = str(uuid.uuid4())
    harness_calls: list[dict] = []

    async def _fake_harness(message, working_dir, **kwargs):
        harness_calls.append({"message": message, "working_dir": working_dir, **kwargs})
        # Simulate the stream-json system/init event the real harness parses
        # (capture-at-init, Race 5) — carries the new claude session id.
        on_init = kwargs.get("on_init")
        if on_init is not None:
            on_init({"session_id": claude_uuid, "version": "9.9.9-test"})
        return "[/user] Deployment ready: all tests green."

    delivered: list[tuple] = []

    def _spy_send(chat_id, payload, reply_to, agent_session):
        # Sync send callback: the adapter delivers on the calling thread and
        # treats the return as a confirmed delivery.
        delivered.append((chat_id, payload, reply_to))

    async def _spy_react(chat_id, message_id, emoji):
        pass

    wt_path = _worktree_path()
    with (
        patch("agent.sdk_client.get_response_via_harness", _fake_harness),
        patch(
            "agent.agent_session_queue._resolve_callbacks",
            return_value=(_spy_send, _spy_react),
        ),
        # Keep hook-channel provisioning out of the repo data dir.
        patch(
            "agent.session_runner.adapter._hook_edge_base_dir",
            return_value=str(tmp_path / "hook_edges"),
        ),
        patch("agent.worktree_manager.get_or_create_worktree", return_value=wt_path),
        patch("agent.worktree_manager.verify_worktree_branch", return_value=None),
    ):
        await _execute_agent_session(session)

    # -- The harness was reached through the real driver ---------------------
    assert len(harness_calls) == 1, "expected exactly one PM turn"
    call = harness_calls[0]
    # First-turn persona priming via the PM prime slash command.
    assert call["message"].startswith("/roles:prime-pm-role"), (
        f"first turn must be prime-prefixed, got: {call['message'][:80]!r}"
    )
    # The executor's harness turn input (with the original message) rides
    # behind the prime.
    assert "ship the fix" in call["message"]
    assert call["role"] == "pm"
    assert call["metered"] is True
    # Subprocesses run in their own process group (Race 2 / D4 killpg scope).
    assert call["start_new_session"] is True

    # -- Env posture: G5 subscription auth + executor session env ------------
    env = call["env"]
    assert env["ANTHROPIC_API_KEY"] == "", "API key must be blanked (G5 subscription auth)"
    assert env.get("SESSION_TYPE") == "eng", "executor session_env must reach the subprocess"
    assert env.get("AGENT_SESSION_ID") == session.agent_session_id

    # -- Delivery: [/user] payload reached the registered callback -----------
    assert delivered, "the [/user] payload never reached the send callback"
    chat_id, payload, _reply_to = delivered[0]
    assert "Deployment ready: all tests green." in payload
    assert str(chat_id) == "999"

    # -- Terminal persistence (publish_exit_summary + capture-at-init) -------
    reloaded = AgentSession.query.filter(session_id=session.session_id).all()[0]
    assert reloaded.exit_reason == "pm_user"
    assert bool(reloaded.user_facing_routed) is True
    # Four-scalar resume persistence: the init event's session id + cwd +
    # version were captured mid-run (Race 5).
    assert reloaded.claude_session_uuid == claude_uuid
    assert reloaded.runner_cwd == wt_path
    assert reloaded.claude_version == "9.9.9-test"


@pytest.mark.asyncio
async def test_runner_error_turn_never_reports_completed(redis_test_db, tmp_path):
    """A turn whose subprocess dies produces exit_reason='error' and a
    persona-safe user message — never a clean 'pm_complete' (the #1916 class)."""
    from agent.session_executor import _execute_agent_session

    session = _make_session()
    session.status = "running"
    session.save(update_fields=["status"])

    async def _crashing_harness(message, working_dir, **kwargs):
        raise RuntimeError("subprocess died mid-turn")

    delivered: list[str] = []

    def _spy_send(chat_id, payload, reply_to, agent_session):
        delivered.append(payload)

    async def _spy_react(chat_id, message_id, emoji):
        pass

    wt_path = _worktree_path()
    with (
        patch("agent.sdk_client.get_response_via_harness", _crashing_harness),
        patch(
            "agent.agent_session_queue._resolve_callbacks",
            return_value=(_spy_send, _spy_react),
        ),
        patch(
            "agent.session_runner.adapter._hook_edge_base_dir",
            return_value=str(tmp_path / "hook_edges"),
        ),
        patch("agent.worktree_manager.get_or_create_worktree", return_value=wt_path),
        patch("agent.worktree_manager.verify_worktree_branch", return_value=None),
    ):
        await _execute_agent_session(session)

    reloaded = AgentSession.query.filter(session_id=session.session_id).all()[0]
    assert reloaded.exit_reason == "error", (
        f"a dead subprocess must classify as 'error', got {reloaded.exit_reason!r}"
    )
    # A persona-safe user-facing message was still delivered (never silence).
    assert delivered, "the error path must deliver a user-facing message"
    assert "subprocess died mid-turn" not in delivered[0], (
        "raw internal error text must never reach the user"
    )
