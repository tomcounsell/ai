"""Hooks write Pillar A liveness fields on every tool boundary (issue #1172).

The PreToolUse hook sets ``current_tool_name`` and bumps ``last_tool_use_at``
on the matching ``AgentSession`` (resolved via the ``AGENT_SESSION_ID`` env).
The PostToolUse hook clears ``current_tool_name`` and bumps the timestamp
again. Both writes are wrapped in try/except — Redis failures must NOT crash
the hook.

A per-session 5s in-memory cooldown bounds Redis write rate under tight tool
loops. The cooldown is best-effort: writes are coalesced, never reordered.

Issue #1843 (Gap A) added a SECOND path that writes the same fields: the
**CLI hooks** (``.claude/hooks/pre_tool_use.py`` / ``post_tool_use.py``) that
the runner's ``claude -p`` subprocesses run. Those hooks resolve the
AgentSession via the on-disk sidecar when ``AGENT_SESSION_ID`` is unset in
the child env, not via ``agent.hooks.liveness_writers.record_tool_boundary``.
The test below covers that CLI-hook path.
"""

from __future__ import annotations

import asyncio
import json
import shutil
import sys
from datetime import datetime
from pathlib import Path

import pytest

from models.agent_session import AgentSession, SessionType

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def _write_agent_session_sidecar(cli_session_id: str, agent_session_id: str) -> None:
    """Write the minimal ``agent_session.json`` sidecar the CLI hooks read.

    Mirrors ``hook_utils.memory_bridge.save_agent_session_sidecar`` but
    without importing the popoto-heavy module.
    """
    sidecar_dir = REPO_ROOT / "data" / "sessions" / cli_session_id
    sidecar_dir.mkdir(parents=True, exist_ok=True)
    (sidecar_dir / "agent_session.json").write_text(
        json.dumps({"agent_session_id": agent_session_id})
    )


@pytest.fixture
def liveness_session(monkeypatch):
    """Create a PM session and expose its session_id via AGENT_SESSION_ID."""
    s = AgentSession.create(
        project_key="test-liveness-hooks",
        chat_id="x",
        session_type=SessionType.ENG,
        message_text="x",
        sender_name="x",
        session_id=f"liveness-hooks-{id(monkeypatch)}",
        working_dir="/tmp",
    )
    monkeypatch.setenv("AGENT_SESSION_ID", s.session_id)
    yield s
    try:
        s.delete()
    except Exception:
        pass


def _reset_cooldown():
    from agent.hooks import liveness_writers

    liveness_writers._reset_cooldown_for_tests()


def test_pre_tool_use_sets_current_tool_name(liveness_session):
    from agent.hooks.pre_tool_use import pre_tool_use_hook

    _reset_cooldown()

    asyncio.run(
        pre_tool_use_hook(
            input_data={"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}},
            tool_use_id="tool-use-1",
            context=None,
        )
    )

    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert len(refreshed) == 1
    assert refreshed[0].current_tool_name == "Read"
    assert refreshed[0].last_tool_use_at is not None


def test_budget_check_allows_and_liveness_still_fires(liveness_session, monkeypatch):
    """Issue #1821 (Fix #6): the SDK PreToolUse hook gained a per-tool budget
    check at the TOP. For an under-budget session it must be a no-op ALLOW and
    the #1172 liveness write must still fire — the budget check is a coordinated
    addition, not a behavior change for the common path.
    """
    from agent import tool_budget
    from agent.hooks.pre_tool_use import pre_tool_use_hook

    # Deterministic, generous cap; the fixture session has tool_call_count=0.
    monkeypatch.setattr(tool_budget, "MAX_TOOL_CALLS_PER_SESSION", 1000)
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_ENABLED", True)
    _reset_cooldown()

    result = asyncio.run(
        pre_tool_use_hook(
            input_data={"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}},
            tool_use_id="budget-allow-1",
            context=None,
        )
    )

    # Budget allowed (no block) and the liveness field still landed.
    assert "decision" not in result
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name == "Read"
    assert refreshed[0].last_tool_use_at is not None


def test_post_tool_use_clears_current_tool_name(liveness_session):
    from agent.hooks.post_tool_use import post_tool_use_hook
    from agent.hooks.pre_tool_use import pre_tool_use_hook

    _reset_cooldown()

    asyncio.run(
        pre_tool_use_hook(
            input_data={"tool_name": "Bash", "tool_input": {"command": "ls"}},
            tool_use_id="tool-use-2",
            context=None,
        )
    )
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name == "Bash"

    _reset_cooldown()
    asyncio.run(
        post_tool_use_hook(
            input_data={"tool_name": "Bash", "tool_input": {"command": "ls"}},
            tool_use_id="tool-use-2",
            context=None,
        )
    )
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name is None
    assert refreshed[0].last_tool_use_at is not None


def test_hook_silently_no_ops_without_agent_session_id(monkeypatch):
    """No AGENT_SESSION_ID env → write helper silently returns False."""
    from agent.hooks.liveness_writers import record_tool_boundary

    monkeypatch.delenv("AGENT_SESSION_ID", raising=False)
    _reset_cooldown()
    # Should not raise even with no env var present.
    assert record_tool_boundary(tool_name="Read", clear=False) is False


def test_hook_redis_failure_does_not_crash(liveness_session, monkeypatch):
    """A Popoto/Redis save failure must not propagate out of the writer."""
    from agent.hooks import liveness_writers

    _reset_cooldown()

    def _boom(*_args, **_kwargs):
        raise RuntimeError("simulated Redis outage")

    monkeypatch.setattr(liveness_writers, "_save_tool_boundary", _boom)

    # Returns False but does not raise.
    assert liveness_writers.record_tool_boundary(tool_name="Edit", clear=False) is False


def test_clear_true_bypasses_cooldown_window(liveness_session):
    """Issue #1270: PostToolUse (``clear=True``) must NOT be coalesced by the
    cooldown — otherwise a fast PreToolUse->PostToolUse pair within the 5s window
    leaves ``current_tool_name`` populated and the per-tool timeout sub-loop
    sees a false-positive wedge condition.
    """
    from agent.hooks.liveness_writers import record_tool_boundary

    _reset_cooldown()

    # PreToolUse fires (clear=False) — sets current_tool_name=Read, pumps cooldown.
    assert record_tool_boundary(tool_name="Read", clear=False) is True
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name == "Read"

    # PostToolUse fires immediately (clear=True). Without the bypass, the 5s
    # cooldown would coalesce this into a no-op and current_tool_name would
    # stay populated. With the bypass, the field clears.
    assert record_tool_boundary(tool_name="Read", clear=True) is True
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name is None


def test_clear_false_still_respects_cooldown(liveness_session):
    """Companion to the above: rapid-fire PreToolUse calls remain coalesced."""
    from agent.hooks.liveness_writers import record_tool_boundary

    _reset_cooldown()

    # First PreToolUse fires.
    assert record_tool_boundary(tool_name="Read", clear=False) is True
    # Second PreToolUse within the cooldown window is suppressed.
    assert record_tool_boundary(tool_name="Edit", clear=False) is False
    # current_tool_name reflects only the first write.
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name == "Read"


def test_cli_hook_writes_current_tool_name_and_datetime(liveness_session):
    """Issue #1843 (Gap A): the CLI PreToolUse hook (used by granite's PM/Dev
    PTY children, where ``AGENT_SESSION_ID`` is unset) resolves the
    AgentSession via the on-disk sidecar and stamps ``current_tool_name`` /
    ``last_tool_use_at`` directly — NOT via ``record_tool_boundary``.
    """
    hooks_dir = str(REPO_ROOT / ".claude" / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    import pre_tool_use as cli_pre_tool_use

    cli_session_id = f"cli-liveness-{liveness_session.id}"
    sidecar_dir = REPO_ROOT / "data" / "sessions" / cli_session_id
    _write_agent_session_sidecar(cli_session_id, liveness_session.agent_session_id)
    try:
        cli_pre_tool_use._record_tool_start(
            {
                "session_id": cli_session_id,
                "tool_name": "Bash",
                "tool_input": {"command": "ls"},
            }
        )

        refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
        assert len(refreshed) == 1
        assert refreshed[0].current_tool_name == "Bash"
        # Regression guard (CONCERN 4): must be a real datetime, not time.time().
        assert isinstance(refreshed[0].last_tool_use_at, datetime)
    finally:
        shutil.rmtree(sidecar_dir, ignore_errors=True)


# ---------------------------------------------------------------------------
# Declared-timeout capture (issue #2145)
# ---------------------------------------------------------------------------


def test_pre_tool_use_bash_declared_timeout_captured(liveness_session):
    """A Bash call declaring `timeout` (MILLISECONDS) lands as seconds."""
    from agent.hooks.pre_tool_use import pre_tool_use_hook

    _reset_cooldown()
    asyncio.run(
        pre_tool_use_hook(
            input_data={
                "tool_name": "Bash",
                "tool_input": {"command": "sleep 500", "timeout": 600000},
            },
            tool_use_id="declared-1",
            context=None,
        )
    )
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name == "Bash"
    assert refreshed[0].current_tool_timeout_s == 600.0


def test_pre_tool_use_non_bash_declared_timeout_none(liveness_session):
    """Non-Bash tools never carry a declared timeout."""
    from agent.hooks.pre_tool_use import pre_tool_use_hook

    _reset_cooldown()
    asyncio.run(
        pre_tool_use_hook(
            input_data={"tool_name": "Read", "tool_input": {"file_path": "/etc/hosts"}},
            tool_use_id="declared-2",
            context=None,
        )
    )
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_timeout_s is None


@pytest.mark.parametrize("bad", [None, 0, -5, "600000", True])
def test_extract_declared_timeout_rejects_malformed(bad):
    from agent.hooks.pre_tool_use import _extract_declared_timeout_s

    assert _extract_declared_timeout_s("Bash", {"command": "ls", "timeout": bad}) is None


def test_extract_declared_timeout_happy_path():
    from agent.hooks.pre_tool_use import _extract_declared_timeout_s

    assert _extract_declared_timeout_s("Bash", {"timeout": 600000}) == 600.0
    assert _extract_declared_timeout_s("Bash", {"timeout": 30000}) == 30.0
    assert _extract_declared_timeout_s("Bash", {"command": "ls"}) is None
    assert _extract_declared_timeout_s("Read", {"timeout": 600000}) is None
    assert _extract_declared_timeout_s("Bash", "not-a-dict") is None


def test_post_tool_use_clears_declared_timeout(liveness_session):
    """PostToolUse clears the declared timeout with the tool name — the pair
    can never split-brain."""
    from agent.hooks.post_tool_use import post_tool_use_hook
    from agent.hooks.pre_tool_use import pre_tool_use_hook

    _reset_cooldown()
    asyncio.run(
        pre_tool_use_hook(
            input_data={
                "tool_name": "Bash",
                "tool_input": {"command": "ls", "timeout": 120000},
            },
            tool_use_id="declared-3",
            context=None,
        )
    )
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_timeout_s == 120.0

    _reset_cooldown()
    asyncio.run(
        post_tool_use_hook(
            input_data={"tool_name": "Bash", "tool_input": {"command": "ls"}},
            tool_use_id="declared-3",
            context=None,
        )
    )
    refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
    assert refreshed[0].current_tool_name is None
    assert refreshed[0].current_tool_timeout_s is None


def test_cli_hook_captures_declared_timeout(liveness_session):
    """The CLI PreToolUse hook (headless runner's `claude -p` children — the
    #2145 incident's actual capture path) also stamps the declared timeout."""
    hooks_dir = str(REPO_ROOT / ".claude" / "hooks")
    if hooks_dir not in sys.path:
        sys.path.insert(0, hooks_dir)
    import pre_tool_use as cli_pre_tool_use

    cli_session_id = f"cli-declared-{liveness_session.id}"
    sidecar_dir = REPO_ROOT / "data" / "sessions" / cli_session_id
    _write_agent_session_sidecar(cli_session_id, liveness_session.agent_session_id)
    try:
        cli_pre_tool_use._record_tool_start(
            {
                "session_id": cli_session_id,
                "tool_name": "Bash",
                "tool_input": {"command": "pytest tests/", "timeout": 600000},
            }
        )
        refreshed = AgentSession.query.filter(session_id=liveness_session.session_id)
        assert refreshed[0].current_tool_timeout_s == 600.0
    finally:
        shutil.rmtree(sidecar_dir, ignore_errors=True)
