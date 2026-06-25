"""Unit tests for the per-tool timeout sub-loop (issue #1270).

Covers ``_classify_tool_tier``, ``_check_tool_timeout``, and
``_agent_session_tool_timeout_check`` (one tick of the sub-loop).

Tier semantics:
  - ``mcp__`` prefix -> "mcp" (120s budget)
  - {ToolSearch, Read, Glob, Grep, Edit, Write, NotebookEdit} -> "internal" (30s)
  - everything else -> "default" (300s)

The sub-loop:
  - Skips when ``TOOL_TIMEOUT_TIERS_DISABLED=1`` is set.
  - Re-reads the session immediately before transitioning (race mitigation).
  - Bumps the per-tier counter on the session row.
  - Increments ``{project_key}:session-health:tool_timeouts:{tier}`` Redis counter.
  - Routes through ``_apply_recovery_transition`` with reason_kind="tool_timeout".
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest

import agent.session_health as session_health
from agent.session_health import (
    HEARTBEAT_FRESHNESS_WINDOW,
    MID_RUN_QUIESCENCE_SECS,
    TOOL_TIMEOUT_DEFAULT_SEC,
    TOOL_TIMEOUT_INTERNAL_SEC,
    TOOL_TIMEOUT_MCP_SEC,
    _agent_session_tool_timeout_check,
    _check_tool_timeout,
    _classify_tool_tier,
    _pty_quiescent_long_enough,
    _tool_tier_budget,
)

# ---------------------------------------------------------------------------
# _classify_tool_tier
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "tool_name,expected",
    [
        ("Read", "internal"),
        ("Glob", "internal"),
        ("Grep", "internal"),
        ("Edit", "internal"),
        ("Write", "internal"),
        ("NotebookEdit", "internal"),
        ("ToolSearch", "internal"),
        ("mcp__foo", "mcp"),
        ("mcp__claude_ai_Gmail__create_draft", "mcp"),
        ("Bash", "default"),
        ("Task", "default"),
        ("Skill", "default"),
        ("WebFetch", "default"),
        ("UnknownTool", "default"),
        (None, "default"),
        ("", "default"),
    ],
)
def test_classify_tool_tier(tool_name, expected):
    assert _classify_tool_tier(tool_name) == expected


def test_tool_tier_budget_returns_configured_seconds():
    assert _tool_tier_budget("internal") == TOOL_TIMEOUT_INTERNAL_SEC
    assert _tool_tier_budget("mcp") == TOOL_TIMEOUT_MCP_SEC
    assert _tool_tier_budget("default") == TOOL_TIMEOUT_DEFAULT_SEC
    # Unknown tier falls back to default budget.
    assert _tool_tier_budget("nonsense") == TOOL_TIMEOUT_DEFAULT_SEC


# ---------------------------------------------------------------------------
# _check_tool_timeout
# ---------------------------------------------------------------------------


def _entry(tool_name, age_seconds: float | None):
    """Build a fake session row with the two fields the check reads."""
    last_at = (
        datetime.now(tz=UTC) - timedelta(seconds=age_seconds) if age_seconds is not None else None
    )
    return SimpleNamespace(current_tool_name=tool_name, last_tool_use_at=last_at)


def test_check_tool_timeout_returns_none_when_no_tool_in_flight():
    assert _check_tool_timeout(_entry(None, 999)) is None
    assert _check_tool_timeout(_entry("", 999)) is None


def test_check_tool_timeout_returns_none_when_last_tool_use_at_missing():
    """Legacy session pre-Pillar A: the field is None even though tool is set."""
    assert _check_tool_timeout(_entry("Bash", None)) is None


def test_check_tool_timeout_returns_none_under_internal_budget():
    # Budget is 30s; an age of 29s is still within budget.
    result = _check_tool_timeout(_entry("Read", TOOL_TIMEOUT_INTERNAL_SEC - 1))
    assert result is None


def test_check_tool_timeout_fires_over_internal_budget():
    result = _check_tool_timeout(_entry("Read", TOOL_TIMEOUT_INTERNAL_SEC + 1))
    assert result is not None
    tier, reason = result
    assert tier == "internal"
    assert "Read" in reason
    assert "internal" in reason
    assert str(TOOL_TIMEOUT_INTERNAL_SEC) in reason


def test_check_tool_timeout_fires_over_mcp_budget():
    result = _check_tool_timeout(_entry("mcp__foo", TOOL_TIMEOUT_MCP_SEC + 1))
    assert result is not None
    tier, reason = result
    assert tier == "mcp"
    assert "mcp__foo" in reason


def test_check_tool_timeout_fires_over_default_budget():
    result = _check_tool_timeout(_entry("Bash", TOOL_TIMEOUT_DEFAULT_SEC + 1))
    assert result is not None
    tier, reason = result
    assert tier == "default"
    assert "Bash" in reason


def test_check_tool_timeout_under_mcp_budget_for_mcp_tool():
    # Internal budget (30s) is tighter than MCP (120s); make sure an MCP tool
    # at age 60s does NOT fire (would fire if mis-classified as internal).
    result = _check_tool_timeout(_entry("mcp__foo", 60))
    assert result is None


def test_check_tool_timeout_handles_naive_datetime():
    """A timestamp without tzinfo must be treated as UTC, not raise."""
    aware = datetime.now(tz=UTC) - timedelta(seconds=TOOL_TIMEOUT_INTERNAL_SEC + 5)
    naive = aware.replace(tzinfo=None)
    entry = SimpleNamespace(current_tool_name="Read", last_tool_use_at=naive)
    result = _check_tool_timeout(entry)
    assert result is not None
    assert result[0] == "internal"


# ---------------------------------------------------------------------------
# _agent_session_tool_timeout_check (one tick of the sub-loop)
# ---------------------------------------------------------------------------


@pytest.fixture
def clean_active_sessions():
    """Snapshot/restore _active_sessions around each test."""
    from agent.session_state import _active_sessions

    saved = dict(_active_sessions)
    _active_sessions.clear()
    yield
    _active_sessions.clear()
    _active_sessions.update(saved)


def _fake_running_entry(
    *,
    sid: str = "sess-1",
    tool_name: str = "Bash",
    age_seconds: float = TOOL_TIMEOUT_DEFAULT_SEC + 5,
    project_key: str = "test-tool-timeout",
    initial_count: int = 0,
    # PTY-liveness fields (issue #1784). Default: SDK session (no PTY read loop).
    last_pty_read_loop_at: datetime | None = None,
    mid_run_quiescent_since: datetime | None = None,
):
    """Build a fake session row that LOOKS wedged on a default-tier tool.

    By default, ``last_pty_read_loop_at=None`` models an SDK/non-granite session —
    the PTY-liveness gate (issue #1784) escapes immediately on branch 2 and the
    age-only default-tier kill fires as before.

    To model a granite PTY session:
    - Set ``last_pty_read_loop_at`` to a recent datetime (within HEARTBEAT_FRESHNESS_WINDOW).
    - Set ``mid_run_quiescent_since`` to None (PTY painting) or to a datetime old enough
      to satisfy MID_RUN_QUIESCENCE_SECS (PTY quiescent long enough → kill eligible).
    """
    last_at = datetime.now(tz=UTC) - timedelta(seconds=age_seconds)
    counters = {
        "tool_timeout_count_internal": 0,
        "tool_timeout_count_mcp": 0,
        "tool_timeout_count_default": 0,
    }
    tier = _classify_tool_tier(tool_name)
    counters[f"tool_timeout_count_{tier}"] = initial_count
    saves: list[list[str]] = []

    def _save(update_fields=None, **_kw):
        saves.append(list(update_fields) if update_fields else [])

    return SimpleNamespace(
        agent_session_id=sid,
        id=sid,
        session_id=f"sid-{sid}",
        status="running",
        project_key=project_key,
        current_tool_name=tool_name,
        last_tool_use_at=last_at,
        worker_key="telegram-test-chat",
        is_project_keyed=False,
        priority=None,
        recovery_attempts=0,
        reprieve_count=0,
        response_delivered_at=None,
        started_at=datetime.now(tz=UTC) - timedelta(seconds=600),
        exit_returncode=0,
        last_pty_read_loop_at=last_pty_read_loop_at,
        mid_run_quiescent_since=mid_run_quiescent_since,
        save=_save,
        delete=lambda **_kw: None,
        _saves=saves,
        **counters,
    )


@pytest.mark.asyncio
async def test_subloop_kill_switch_short_circuits(monkeypatch, clean_active_sessions):
    """``TOOL_TIMEOUT_TIERS_DISABLED=1`` skips everything for the tick."""
    monkeypatch.setenv("TOOL_TIMEOUT_TIERS_DISABLED", "1")

    called = []

    def _filter_called(*_a, **_k):
        called.append(True)
        return []

    with patch.object(session_health, "_filter_hydrated_sessions", _filter_called):
        await _agent_session_tool_timeout_check()

    # The filter is called only inside the kill-switch-passing branch.
    assert called == []


@pytest.mark.asyncio
async def test_subloop_no_op_when_no_tool_in_flight(monkeypatch, clean_active_sessions):
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_running_entry(tool_name=None)

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health, "_apply_recovery_transition") as mock_transition,
    ):
        await _agent_session_tool_timeout_check()

    mock_transition.assert_not_called()
    # No counter was bumped.
    assert entry._saves == []


@pytest.mark.asyncio
async def test_subloop_skips_terminal_status(monkeypatch, clean_active_sessions):
    """Stale running-index entries with terminal hash status are skipped."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_running_entry()
    entry.status = "killed"

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health, "_apply_recovery_transition") as mock_transition,
    ):
        await _agent_session_tool_timeout_check()

    mock_transition.assert_not_called()


@pytest.mark.asyncio
async def test_subloop_recovers_wedged_session_default_tier(monkeypatch, clean_active_sessions):
    """Default-tier wedge: counter bumps, Redis incr, recovery transition fires."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_running_entry(tool_name="Bash", age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 5)

    redis_calls: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            redis_calls.append(key)
            return 1

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_FakeRedis())

    async def _fake_transition(*_a, **kwargs):
        # Capture kwargs so we can assert reason_kind/reason wiring.
        _fake_transition.last_kwargs = kwargs
        return True

    _fake_transition.last_kwargs = {}

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _fake_transition),
    ):
        await _agent_session_tool_timeout_check()

    # Per-tier IntField was bumped
    assert entry.tool_timeout_count_default == 1
    assert ["tool_timeout_count_default"] in entry._saves
    # Project-tier Redis counter was INCR'd
    assert any(
        k == f"{entry.project_key}:session-health:tool_timeouts:default" for k in redis_calls
    )
    # Recovery transition was invoked with the right reason_kind
    assert _fake_transition.last_kwargs.get("reason_kind") == "tool_timeout"
    assert "tool-wedge" in _fake_transition.last_kwargs.get("reason", "")
    assert "Bash" in _fake_transition.last_kwargs.get("reason", "")


# ---------------------------------------------------------------------------
# PTY-liveness gate unit tests (issue #1784)
# ---------------------------------------------------------------------------


def _granite_entry(
    *,
    last_pty_read_loop_at_age_secs: float = 10,  # fresh by default
    mid_run_quiescent_since_age_secs: float | None = None,
):
    """Build a minimal fake session row that looks like a granite PTY session."""
    now = datetime.now(tz=UTC)
    loop_at = now - timedelta(seconds=last_pty_read_loop_at_age_secs)
    quiescent_since = (
        now - timedelta(seconds=mid_run_quiescent_since_age_secs)
        if mid_run_quiescent_since_age_secs is not None
        else None
    )
    return SimpleNamespace(
        last_pty_read_loop_at=loop_at,
        mid_run_quiescent_since=quiescent_since,
    )


def test_pty_quiescent_long_enough_kill_switch_wins_first():
    """Branch 1: MID_RUN_QUIESCENCE_SECS <= 0 restores age-only kill even when PTY fields set."""
    entry = _granite_entry(mid_run_quiescent_since_age_secs=None)  # painting
    now = datetime.now(tz=UTC)
    # Patch the module-level constant to 0.
    original = session_health.MID_RUN_QUIESCENCE_SECS
    try:
        session_health.MID_RUN_QUIESCENCE_SECS = 0
        result = _pty_quiescent_long_enough(entry, now)
    finally:
        session_health.MID_RUN_QUIESCENCE_SECS = original
    assert result is True, (
        "Kill-switch (MID_RUN_QUIESCENCE_SECS=0) must return True even when PTY is "
        "present and painting — age-only kill must be restored"
    )


def test_pty_quiescent_long_enough_sdk_escape():
    """Branch 2: last_pty_read_loop_at=None (SDK session) → True (age-only kill preserved)."""
    entry = SimpleNamespace(last_pty_read_loop_at=None, mid_run_quiescent_since=None)
    now = datetime.now(tz=UTC)
    assert _pty_quiescent_long_enough(entry, now) is True, (
        "SDK session (no PTY read loop) must return True — age-only kill must be preserved"
    )


def test_pty_quiescent_long_enough_stale_loop_escape():
    """Branch 2b: last_pty_read_loop_at is stale → True (dead read loop, treat as eligible)."""
    stale_age = HEARTBEAT_FRESHNESS_WINDOW + 10
    entry = _granite_entry(
        last_pty_read_loop_at_age_secs=stale_age,
        mid_run_quiescent_since_age_secs=None,  # PTY 'painting' per branch 3...
    )
    now = datetime.now(tz=UTC)
    # Despite mid_run_quiescent_since=None (branch 3 would defer), the stale loop
    # escape (branch 2b) must fire first and return True.
    assert _pty_quiescent_long_enough(entry, now) is True, (
        "Stale PTY read loop (> HEARTBEAT_FRESHNESS_WINDOW) must return True even when "
        "mid_run_quiescent_since=None — a dead read loop must not indefinitely block the kill"
    )


def test_pty_quiescent_long_enough_painting_defers_kill():
    """Branch 3: granite PTY present and fresh, mid_run_quiescent_since=None → False (defer)."""
    entry = _granite_entry(
        last_pty_read_loop_at_age_secs=5,  # fresh
        mid_run_quiescent_since_age_secs=None,  # painting
    )
    now = datetime.now(tz=UTC)
    assert _pty_quiescent_long_enough(entry, now) is False, (
        "Granite PTY session that is currently painting (mid_run_quiescent_since=None) "
        "must return False — the tool is alive, do NOT kill"
    )


def test_pty_quiescent_long_enough_quiescent_not_long_enough_defers():
    """Branch 4: granite quiescent but < MID_RUN_QUIESCENCE_SECS → False (defer)."""
    entry = _granite_entry(
        last_pty_read_loop_at_age_secs=5,
        mid_run_quiescent_since_age_secs=MID_RUN_QUIESCENCE_SECS - 10,  # not long enough
    )
    now = datetime.now(tz=UTC)
    assert _pty_quiescent_long_enough(entry, now) is False, (
        "Granite PTY quiescent but below MID_RUN_QUIESCENCE_SECS threshold must return False"
    )


def test_pty_quiescent_long_enough_quiescent_fires():
    """Branch 4: granite quiescent >= MID_RUN_QUIESCENCE_SECS → True (kill eligible)."""
    entry = _granite_entry(
        last_pty_read_loop_at_age_secs=5,
        mid_run_quiescent_since_age_secs=MID_RUN_QUIESCENCE_SECS + 10,  # long enough
    )
    now = datetime.now(tz=UTC)
    assert _pty_quiescent_long_enough(entry, now) is True, (
        "Granite PTY quiescent >= MID_RUN_QUIESCENCE_SECS must return True — wedge eligible"
    )


def test_pty_quiescent_long_enough_naive_datetime_handled():
    """Branch 4: naive mid_run_quiescent_since (no tzinfo) is normalized to UTC, no crash."""
    now = datetime.now(tz=UTC)
    naive_quiescent = (now - timedelta(seconds=MID_RUN_QUIESCENCE_SECS + 10)).replace(tzinfo=None)
    entry = SimpleNamespace(
        last_pty_read_loop_at=datetime.now(tz=UTC) - timedelta(seconds=5),
        mid_run_quiescent_since=naive_quiescent,
    )
    result = _pty_quiescent_long_enough(entry, now)
    assert result is True, "Naive datetime in mid_run_quiescent_since must be handled without crash"


# ---------------------------------------------------------------------------
# Sub-loop integration tests for the PTY-liveness gate (issue #1784)
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_subloop_sdk_default_tier_recovered_via_age_escape(
    monkeypatch, clean_active_sessions
):
    """SDK/non-granite session (last_pty_read_loop_at=None) overdue on default-tier tool IS
    recovered — the PTY-liveness gate does NOT disable the 300s kill for the SDK path.

    This is the critique-blocker regression guard: without branch 2 of
    _pty_quiescent_long_enough, SDK default tools would never be killed.
    """
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    # Default _fake_running_entry has last_pty_read_loop_at=None (SDK session).
    entry = _fake_running_entry(tool_name="Bash", age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 5)
    assert entry.last_pty_read_loop_at is None, "Fixture must model an SDK session"

    transition_called = []

    async def _capture(*_a, **kwargs):
        transition_called.append(kwargs)
        return True

    redis_calls: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            redis_calls.append(key)
            return 1

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_FakeRedis())

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _capture),
    ):
        await _agent_session_tool_timeout_check()

    assert len(transition_called) == 1, (
        "SDK default-tier session overdue past 300s MUST be recovered "
        "(PTY-liveness gate must not disable age-only kill for SDK path)"
    )
    assert transition_called[0].get("reason_kind") == "tool_timeout"


@pytest.mark.asyncio
async def test_subloop_granite_painting_default_tier_not_recovered(
    monkeypatch, clean_active_sessions
):
    """Granite PTY session whose tool is overdue but PTY is still painting is NOT killed.

    This is the core fix: a default-tier tool emitting PTY output past 300s must not
    be recovered. (mid_run_quiescent_since=None → painting → defer kill.)

    _eval_mid_run_pty_stage1 is patched to a no-op so it cannot alter
    mid_run_quiescent_since on the fake entry between the fixture setup and the gate check.
    """
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_running_entry(
        tool_name="Bash",
        age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 5,
        last_pty_read_loop_at=datetime.now(tz=UTC) - timedelta(seconds=5),  # fresh loop
        mid_run_quiescent_since=None,  # still painting
    )

    async def _should_not_be_called(*_a, **_kw):
        raise AssertionError("recovery fired on a live-painting granite PTY session")

    redis_calls: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            redis_calls.append(key)
            return 1

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_FakeRedis())

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _should_not_be_called),
        # Patch out stage-1 so it cannot mutate mid_run_quiescent_since on the fake entry.
        patch.object(session_health, "_eval_mid_run_pty_stage1", lambda *_a, **_kw: None),
    ):
        await _agent_session_tool_timeout_check()

    # The deferred-kill counter must have been incremented.
    deferred_keys = [k for k in redis_calls if "default_deferred" in k]
    assert deferred_keys, (
        "tool_timeouts:default_deferred Redis counter must be incremented when "
        "a live-painting granite PTY tool is deferred"
    )


@pytest.mark.asyncio
async def test_subloop_granite_quiescent_default_tier_recovered(monkeypatch, clean_active_sessions):
    """Granite PTY session overdue AND quiescent >= MID_RUN_QUIESCENCE_SECS IS recovered.

    _eval_mid_run_pty_stage1 is patched to a no-op so it cannot alter
    mid_run_quiescent_since on the fake entry between the fixture setup and the gate check.
    """
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    _quiescent_age = MID_RUN_QUIESCENCE_SECS + 10
    entry = _fake_running_entry(
        tool_name="Bash",
        age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 5,
        last_pty_read_loop_at=datetime.now(tz=UTC) - timedelta(seconds=5),  # fresh loop
        mid_run_quiescent_since=datetime.now(tz=UTC) - timedelta(seconds=_quiescent_age),
    )

    transition_called = []

    async def _capture(*_a, **kwargs):
        transition_called.append(kwargs)
        return True

    redis_calls: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            redis_calls.append(key)
            return 1

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_FakeRedis())

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _capture),
        # Patch out stage-1 so it cannot mutate mid_run_quiescent_since on the fake entry.
        patch.object(session_health, "_eval_mid_run_pty_stage1", lambda *_a, **_kw: None),
    ):
        await _agent_session_tool_timeout_check()

    assert len(transition_called) == 1, (
        "Granite PTY session overdue AND quiescent >= MID_RUN_QUIESCENCE_SECS MUST be recovered"
    )
    assert transition_called[0].get("reason_kind") == "tool_timeout"
    # The reason should include quiescence context.
    reason = transition_called[0].get("reason", "")
    assert "pty quiescent" in reason, (
        f"Reason string for granite kill must include quiescence context; got: {reason!r}"
    )


@pytest.mark.asyncio
async def test_subloop_gate_does_not_apply_to_internal_tier(monkeypatch, clean_active_sessions):
    """Internal-tier tools keep age-only kill; PTY liveness gate does NOT apply.

    Even on a granite PTY session that is 'painting', an overdue internal-tier tool
    must still be killed — the liveness gate is default-tier only.
    """
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    # Use a granite PTY session that is painting — yet the internal-tier tool must still die.
    entry = _fake_running_entry(
        tool_name="Read",
        age_seconds=TOOL_TIMEOUT_INTERNAL_SEC + 5,
        last_pty_read_loop_at=datetime.now(tz=UTC) - timedelta(seconds=5),  # fresh loop
        mid_run_quiescent_since=None,  # painting
    )

    transition_called = []

    async def _capture(*_a, **kwargs):
        transition_called.append(kwargs)
        return True

    redis_calls: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            redis_calls.append(key)
            return 1

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_FakeRedis())

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _capture),
        patch.object(session_health, "_eval_mid_run_pty_stage1", lambda *_a, **_kw: None),
    ):
        await _agent_session_tool_timeout_check()

    assert len(transition_called) == 1, (
        "Internal-tier wedge on a granite PTY session must still fire — "
        "the PTY-liveness gate must NOT apply to the internal tier"
    )
    assert transition_called[0].get("reason_kind") == "tool_timeout"


@pytest.mark.asyncio
async def test_subloop_gate_does_not_apply_to_mcp_tier(monkeypatch, clean_active_sessions):
    """MCP-tier tools keep age-only kill; PTY liveness gate does NOT apply."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_running_entry(
        tool_name="mcp__claude_ai_Gmail__create_draft",
        age_seconds=TOOL_TIMEOUT_MCP_SEC + 5,
        last_pty_read_loop_at=datetime.now(tz=UTC) - timedelta(seconds=5),
        mid_run_quiescent_since=None,  # painting
    )

    transition_called = []

    async def _capture(*_a, **kwargs):
        transition_called.append(kwargs)
        return True

    redis_calls: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            redis_calls.append(key)
            return 1

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_FakeRedis())

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _capture),
        patch.object(session_health, "_eval_mid_run_pty_stage1", lambda *_a, **_kw: None),
    ):
        await _agent_session_tool_timeout_check()

    assert len(transition_called) == 1, (
        "MCP-tier wedge on a granite PTY session must still fire — "
        "the PTY-liveness gate must NOT apply to the mcp tier"
    )


@pytest.mark.asyncio
async def test_subloop_race_abort_when_tool_resumes_painting(monkeypatch, clean_active_sessions):
    """Race: tool resumes painting between iterator read and fresh re-read → abort recovery.

    Iterator row: quiescent long enough → gate passes, would kill.
    Fresh re-read: mid_run_quiescent_since cleared (PTY resumed painting) → gate defers.

    _eval_mid_run_pty_stage1 is patched to a no-op so it cannot mutate
    mid_run_quiescent_since on the fake stale entry.
    """
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)

    # Iterator row: overdue + quiescent long enough → gate says "kill eligible".
    _quiescent_age = MID_RUN_QUIESCENCE_SECS + 10
    stale = _fake_running_entry(
        tool_name="Bash",
        age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 5,
        last_pty_read_loop_at=datetime.now(tz=UTC) - timedelta(seconds=5),
        mid_run_quiescent_since=datetime.now(tz=UTC) - timedelta(seconds=_quiescent_age),
    )
    # Fresh re-read: PTY resumed painting — mid_run_quiescent_since cleared.
    fresh = _fake_running_entry(
        tool_name="Bash",
        age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 5,
        last_pty_read_loop_at=datetime.now(tz=UTC) - timedelta(seconds=2),
        mid_run_quiescent_since=None,  # resumed painting
    )

    async def _should_not_be_called(*_a, **_kw):
        raise AssertionError("recovery fired despite PTY resuming painting in the re-read")

    redis_calls: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            redis_calls.append(key)
            return 1

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_FakeRedis())

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[stale]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: fresh)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _should_not_be_called),
        patch.object(session_health, "_eval_mid_run_pty_stage1", lambda *_a, **_kw: None),
    ):
        await _agent_session_tool_timeout_check()

    # No kill counter should be bumped (aborted before that block).
    assert stale.tool_timeout_count_default == 0
    assert fresh.tool_timeout_count_default == 0


@pytest.mark.asyncio
async def test_subloop_internal_tier_classification(monkeypatch, clean_active_sessions):
    """A wedged Read tool produces an internal-tier counter bump."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_running_entry(tool_name="Read", age_seconds=TOOL_TIMEOUT_INTERNAL_SEC + 5)

    redis_calls: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            redis_calls.append(key)
            return 1

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_FakeRedis())

    async def _noop_transition(*_a, **_kw):
        return True

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _noop_transition),
    ):
        await _agent_session_tool_timeout_check()

    assert entry.tool_timeout_count_internal == 1
    assert entry.tool_timeout_count_mcp == 0
    assert entry.tool_timeout_count_default == 0
    assert any(k.endswith(":session-health:tool_timeouts:internal") for k in redis_calls)


@pytest.mark.asyncio
async def test_subloop_mcp_tier_classification(monkeypatch, clean_active_sessions):
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_running_entry(
        tool_name="mcp__claude_ai_Gmail__create_draft",
        age_seconds=TOOL_TIMEOUT_MCP_SEC + 5,
    )

    redis_calls: list[str] = []

    class _FakeRedis:
        def incr(self, key):
            redis_calls.append(key)
            return 1

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_FakeRedis())

    async def _noop_transition(*_a, **_kw):
        return True

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _noop_transition),
    ):
        await _agent_session_tool_timeout_check()

    assert entry.tool_timeout_count_mcp == 1
    assert any(k.endswith(":session-health:tool_timeouts:mcp") for k in redis_calls)


@pytest.mark.asyncio
async def test_subloop_aborts_recovery_when_re_read_shows_fresh_state(
    monkeypatch, clean_active_sessions
):
    """Race mitigation (Risk 2): if PostToolUse fires between read and re-read,
    abort the recovery for this tick.
    """
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)

    # First entry (the iterator's view) looks wedged.
    stale = _fake_running_entry(tool_name="Bash", age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 5)
    # Re-read returns a fresh entry: current_tool_name cleared by PostToolUse.
    fresh = _fake_running_entry(tool_name="Bash", age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 5)
    fresh.current_tool_name = None

    async def _should_not_be_called(*_a, **_kw):
        raise AssertionError("recovery transition called despite race")

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[stale]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: fresh)),
        patch.object(session_health, "_apply_recovery_transition", _should_not_be_called),
    ):
        await _agent_session_tool_timeout_check()

    # No counter bump on either entry (recovery aborted before bump).
    assert stale.tool_timeout_count_default == 0
    assert fresh.tool_timeout_count_default == 0


@pytest.mark.asyncio
async def test_subloop_aborts_when_re_read_returns_terminal(monkeypatch, clean_active_sessions):
    """If the re-read shows terminal status, abort — the session was killed elsewhere."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    stale = _fake_running_entry()
    fresh = _fake_running_entry()
    fresh.status = "killed"

    async def _should_not_be_called(*_a, **_kw):
        raise AssertionError("recovery transition called despite terminal re-read")

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[stale]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: fresh)),
        patch.object(session_health, "_apply_recovery_transition", _should_not_be_called),
    ):
        await _agent_session_tool_timeout_check()


@pytest.mark.asyncio
async def test_subloop_redis_failure_does_not_block_recovery(monkeypatch, clean_active_sessions):
    """A Redis ``incr`` failure must not prevent the IntField bump or transition."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_running_entry(tool_name="Bash", age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 5)

    class _BoomRedis:
        def incr(self, key):
            raise ConnectionError("simulated Redis outage")

    fake_redis_module = SimpleNamespace(POPOTO_REDIS_DB=_BoomRedis())

    transition_called = []

    async def _capture(*_a, **kwargs):
        transition_called.append(kwargs)
        return True

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.dict("sys.modules", {"popoto.redis_db": fake_redis_module}),
        patch.object(session_health, "_apply_recovery_transition", _capture),
    ):
        await _agent_session_tool_timeout_check()

    # Counter still bumped despite Redis failure.
    assert entry.tool_timeout_count_default == 1
    # Recovery transition still invoked.
    assert len(transition_called) == 1


# ---------------------------------------------------------------------------
# Regression tests for issue #1762: stale wedge fields after tool_timeout
# recovery causing immediate re-trigger on the next sub-loop tick.
# ---------------------------------------------------------------------------


def _make_requeue_entry(
    *,
    sid: str = "sess-rq",
    tool_name: str = "bash",
    age_seconds: float = TOOL_TIMEOUT_DEFAULT_SEC + 100,
    recovery_attempts: int = 0,
    project_key: str = "test-tool-timeout-rq",
    exit_returncode: int = 0,
    extra_context: dict | None = None,
):
    """Build a fake session row in the 'running' state that looks tool-wedged,
    matching the shape expected by ``_apply_recovery_transition``.

    The timestamp is intentionally STALE (past the budget) so that
    ``_check_tool_timeout`` fires before the reset and returns None afterward.
    """
    last_at = datetime.now(tz=UTC) - timedelta(seconds=age_seconds)
    saves: list[dict] = []

    def _save(update_fields=None, **_kw):
        saves.append({"update_fields": list(update_fields) if update_fields else []})

    return SimpleNamespace(
        agent_session_id=sid,
        id=sid,
        session_id=f"sid-{sid}",
        status="running",
        project_key=project_key,
        current_tool_name=tool_name,
        last_tool_use_at=last_at,
        worker_key="telegram-test-chat",
        is_project_keyed=False,
        priority=None,
        recovery_attempts=recovery_attempts,
        reprieve_count=0,
        response_delivered_at=None,
        started_at=datetime.now(tz=UTC) - timedelta(seconds=600),
        exit_returncode=exit_returncode,
        extra_context=extra_context or {},
        message_text="original request",
        chat_id="c-test",
        telegram_message_id=0,
        claude_pid=None,
        claude_session_uuid=None,
        scheduled_at=None,
        last_turn_at=None,
        tool_timeout_count_internal=0,
        tool_timeout_count_mcp=0,
        tool_timeout_count_default=0,
        save=_save,
        delete=lambda **_kw: None,
        push_steering_message=lambda *a, **kw: None,
        _saves=saves,
    )


@pytest.mark.asyncio
async def test_recovery_requeue_clears_wedge_fields_issue1762():
    """Regression: after a tool_timeout recovery-requeue, ``current_tool_name``
    and ``last_tool_use_at`` must both be None so the next sub-loop tick does
    NOT re-detect a wedge from stale data (issue #1762).

    CRITICAL fixture detail: ``last_tool_use_at`` is set to a STALE timestamp
    (400s past the 300s default budget), NOT None.  A None timestamp causes
    ``_check_tool_timeout`` to short-circuit early and the test would pass even
    against the un-patched code, making it vacuous.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.session_health import _apply_recovery_transition

    # recovery_attempts=0 → below MAX so the requeue 'else' branch fires.
    entry = _make_requeue_entry(
        tool_name="bash",
        age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 100,
        recovery_attempts=0,
    )

    # Sanity: pre-recovery the entry looks wedged.
    pre_check = _check_tool_timeout(entry)
    assert pre_check is not None, (
        "Pre-condition failed: entry must look wedged before recovery "
        "(stale last_tool_use_at required — None would short-circuit and make "
        "the whole test vacuous against unfixed code)"
    )

    def _fake_finalize(e, status, reason="", **kw):
        e.status = status

    def _fake_transition(e, status, reason="", **kw):
        e.status = status

    with (
        patch("agent.session_health._tier2_reprieve_signal", return_value=None),
        patch("agent.session_health._confirm_subprocess_dead") as mock_kill,
        patch("agent.session_health._increment_subprocess_kill_counter"),
        patch("agent.session_health._is_memory_tight", return_value=False),
        patch("agent.session_health.asyncio.get_running_loop") as mock_loop,
        patch(
            "agent.session_health._deliver_tool_timeout_degraded_notice",
            new_callable=AsyncMock,
        ),
        patch(
            "agent.session_health._deliver_deferred_self_draft_fallback",
            new_callable=AsyncMock,
        ),
        patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
        patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
        patch("models.session_lifecycle.StatusConflictError", Exception),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("agent.session_health._active_events", {}),
        patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
        patch("agent.steering.reset_self_draft_attempts", return_value=None),
        patch("agent.session_telemetry.record_telemetry_event", return_value=None),
        patch("agent.session_telemetry.finalize_session", return_value=None),
    ):
        from agent.session_health import SubprocessKillResult

        mock_kill.return_value = SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        mock_loop.return_value.run_in_executor = AsyncMock(
            return_value=SubprocessKillResult(confirmed_dead=True, signal_sent=False)
        )

        result = await _apply_recovery_transition(
            entry,
            reason="tool-wedge: bash exceeded 300s default budget",
            reason_kind="tool_timeout",
            handle=None,
            worker_key="telegram-test-chat",
        )

    assert result is True, "Recovery transition must return True (transition fired)"

    # The fix: both fields must be cleared on the requeue path.
    assert entry.current_tool_name is None, (
        "current_tool_name must be None after requeue recovery (issue #1762); "
        f"got {entry.current_tool_name!r}"
    )
    assert entry.last_tool_use_at is None, (
        "last_tool_use_at must be None after requeue recovery (issue #1762); "
        f"got {entry.last_tool_use_at!r}"
    )

    # Both cleared fields must appear in the save call's update_fields.
    all_saved_fields = {f for s in entry._saves for f in s.get("update_fields", [])}
    assert "current_tool_name" in all_saved_fields, (
        "current_tool_name must be included in update_fields on the requeue save"
    )
    assert "last_tool_use_at" in all_saved_fields, (
        "last_tool_use_at must be included in update_fields on the requeue save"
    )

    # Simulate the next sub-loop tick: _check_tool_timeout must now return None.
    post_check = _check_tool_timeout(entry)
    assert post_check is None, (
        "After recovery, _check_tool_timeout must return None on the next tick "
        "(stale wedge signal must not re-trigger); the fix clears current_tool_name "
        "and last_tool_use_at so the None-tool-name guard short-circuits early"
    )


@pytest.mark.asyncio
async def test_genuine_second_wedge_still_finalizes_at_max_attempts():
    """Fail-fast preserved: a session that wedges, recovers cleanly, then
    wedges again on a NEW tool with a NEW timestamp still exhausts
    MAX_RECOVERY_ATTEMPTS and is finalized as 'failed'.

    This guards against the fix accidentally making genuinely stuck sessions
    immortal by suppressing the MAX_RECOVERY_ATTEMPTS cap.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.session_health import MAX_RECOVERY_ATTEMPTS, _apply_recovery_transition

    # First wedge: recovery_attempts=0 → requeue path.
    entry = _make_requeue_entry(
        tool_name="bash",
        age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 100,
        recovery_attempts=0,
    )

    def _fake_finalize(e, status, reason="", **kw):
        e.status = status

    def _fake_transition(e, status, reason="", **kw):
        e.status = status

    degraded_calls: list = []

    from agent.session_health import SubprocessKillResult

    _kill_ok = SubprocessKillResult(confirmed_dead=True, signal_sent=False)

    # --- Recovery 1: first wedge, requeue ---
    with (
        patch("agent.session_health._tier2_reprieve_signal", return_value=None),
        patch("agent.session_health._confirm_subprocess_dead", return_value=_kill_ok),
        patch("agent.session_health._increment_subprocess_kill_counter"),
        patch("agent.session_health._is_memory_tight", return_value=False),
        patch("agent.session_health.asyncio.get_running_loop") as mock_loop1,
        patch(
            "agent.session_health._deliver_tool_timeout_degraded_notice",
            side_effect=lambda *a, **kw: degraded_calls.append(a),
        ),
        patch(
            "agent.session_health._deliver_deferred_self_draft_fallback",
            new_callable=AsyncMock,
        ),
        patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
        patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
        patch("models.session_lifecycle.StatusConflictError", Exception),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("agent.session_health._active_events", {}),
        patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
        patch("agent.steering.reset_self_draft_attempts", return_value=None),
        patch("agent.session_telemetry.record_telemetry_event", return_value=None),
        patch("agent.session_telemetry.finalize_session", return_value=None),
    ):
        mock_loop1.return_value.run_in_executor = AsyncMock(return_value=_kill_ok)
        await _apply_recovery_transition(
            entry,
            reason="tool-wedge: bash exceeded default budget (attempt 1)",
            reason_kind="tool_timeout",
            handle=None,
            worker_key="telegram-test-chat",
        )

    # After recovery 1: fields cleared, status → pending, attempts=1.
    assert entry.current_tool_name is None
    assert entry.last_tool_use_at is None
    assert entry.recovery_attempts == 1

    # Simulate the session resuming: new tool arrives on a fresh (but stale) timestamp.
    # Use "Read" (capitalized — it's in _INTERNAL_TOOL_NAMES) with an age just past the
    # internal budget (30s). The default-tier budget is 300s so lowercase "read" would
    # NOT be detected as wedged here; capitalize to hit the 30s internal tier.
    entry.current_tool_name = "Read"
    entry.last_tool_use_at = datetime.now(tz=UTC) - timedelta(seconds=TOOL_TIMEOUT_INTERNAL_SEC + 5)
    entry.status = "running"

    # Verify the second wedge IS detectable (the new timestamp triggers the budget check).
    second_check = _check_tool_timeout(entry)
    assert second_check is not None, (
        "Second wedge on a new tool/timestamp must be detectable by _check_tool_timeout"
    )

    # --- Recovery 2: second wedge, now at MAX_RECOVERY_ATTEMPTS → finalize as failed ---
    # Set recovery_attempts to MAX-1 so that incrementing inside _apply_recovery_transition
    # crosses the threshold.
    entry.recovery_attempts = MAX_RECOVERY_ATTEMPTS - 1

    with (
        patch("agent.session_health._tier2_reprieve_signal", return_value=None),
        patch("agent.session_health._confirm_subprocess_dead", return_value=_kill_ok),
        patch("agent.session_health._increment_subprocess_kill_counter"),
        patch("agent.session_health._is_memory_tight", return_value=False),
        patch("agent.session_health.asyncio.get_running_loop") as mock_loop2,
        patch(
            "agent.session_health._deliver_tool_timeout_degraded_notice",
            side_effect=lambda *a, **kw: degraded_calls.append(a),
        ),
        patch(
            "agent.session_health._deliver_deferred_self_draft_fallback",
            new_callable=AsyncMock,
        ),
        patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
        patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
        patch("models.session_lifecycle.StatusConflictError", Exception),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("agent.session_health._active_events", {}),
        patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
        patch("agent.steering.reset_self_draft_attempts", return_value=None),
        patch("agent.session_telemetry.record_telemetry_event", return_value=None),
        patch("agent.session_telemetry.finalize_session", return_value=None),
    ):
        mock_loop2.return_value.run_in_executor = AsyncMock(return_value=_kill_ok)
        await _apply_recovery_transition(
            entry,
            reason="tool-wedge: read exceeded internal budget (attempt 2)",
            reason_kind="tool_timeout",
            handle=None,
            worker_key="telegram-test-chat",
        )

    # The session must be finalized as failed — not silently left in pending.
    assert entry.status == "failed", (
        f"A genuinely double-wedged session must finalize as 'failed' at MAX_RECOVERY_ATTEMPTS; "
        f"got status={entry.status!r}.  The fix must not suppress the MAX cap."
    )


@pytest.mark.asyncio
async def test_requeue_proceeds_when_save_raises_issue1762():
    """Best-effort: if the save of cleared wedge fields raises, the requeue to
    'pending' still proceeds and the exception is logged (not silently swallowed
    or allowed to abort the recovery).

    This validates that the error path in the fix (save failure inside the
    requeue branch) doesn't block the session from being set back to pending.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.session_health import _apply_recovery_transition

    transition_calls: list[str] = []

    def _fake_transition(e, status, reason="", **kw):
        e.status = status
        transition_calls.append(status)

    save_calls: list[str] = []

    def _boom_save(update_fields=None, **_kw):
        save_calls.append("boom")
        raise OSError("simulated save failure")

    entry = _make_requeue_entry(
        tool_name="bash",
        age_seconds=TOOL_TIMEOUT_DEFAULT_SEC + 100,
        recovery_attempts=0,
    )
    entry.save = _boom_save

    from agent.session_health import SubprocessKillResult

    _kill_ok = SubprocessKillResult(confirmed_dead=True, signal_sent=False)

    with (
        patch("agent.session_health._tier2_reprieve_signal", return_value=None),
        patch("agent.session_health._confirm_subprocess_dead", return_value=_kill_ok),
        patch("agent.session_health._increment_subprocess_kill_counter"),
        patch("agent.session_health._is_memory_tight", return_value=False),
        patch("agent.session_health.asyncio.get_running_loop") as mock_loop,
        patch(
            "agent.session_health._deliver_tool_timeout_degraded_notice",
            new_callable=AsyncMock,
        ),
        patch(
            "agent.session_health._deliver_deferred_self_draft_fallback",
            new_callable=AsyncMock,
        ),
        patch("models.session_lifecycle.finalize_session"),
        patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
        patch("models.session_lifecycle.StatusConflictError", Exception),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("agent.session_health._active_events", {}),
        patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
        patch("agent.steering.reset_self_draft_attempts", return_value=None),
        patch("agent.session_telemetry.record_telemetry_event", return_value=None),
        patch("agent.session_telemetry.finalize_session", return_value=None),
    ):
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=_kill_ok)
        # Must not raise even when save blows up.
        result = await _apply_recovery_transition(
            entry,
            reason="tool-wedge: bash timed out",
            reason_kind="tool_timeout",
            handle=None,
            worker_key="telegram-test-chat",
        )

    # Save must have been attempted (exception was caught, not short-circuited).
    assert save_calls, "save() must be called even if it later raises"
    # The requeue (transition_status → pending) must still fire despite the save failure.
    assert "pending" in transition_calls, (
        f"transition_status must still send the session to 'pending' even when "
        f"the wedge-field save raises; transition_calls={transition_calls}"
    )
    assert result is True, "Recovery must return True even when the save fails"


@pytest.mark.asyncio
async def test_degraded_notice_fires_on_genuine_post_recovery_exhaustion_issue1762():
    """Degraded-notice regression: when a session genuinely exhausts
    MAX_RECOVERY_ATTEMPTS after a clean first recovery, the degraded notice
    must still fire.  The fix clears wedge fields on the requeue path; this
    test confirms that clearing those fields does NOT suppress the notice on
    the true-failure path that follows.
    """
    from unittest.mock import AsyncMock, MagicMock, patch

    from agent.session_health import MAX_RECOVERY_ATTEMPTS, _apply_recovery_transition

    degraded_calls: list = []

    def _fake_finalize(e, status, reason="", **kw):
        e.status = status

    def _fake_transition(e, status, reason="", **kw):
        e.status = status

    # recovery_attempts already at MAX_RECOVERY_ATTEMPTS-1 so the NEXT increment
    # crosses the threshold and takes the 'failed' branch (not the requeue branch).
    entry = _make_requeue_entry(
        tool_name="mcp__slow_service",
        age_seconds=TOOL_TIMEOUT_MCP_SEC + 10,
        recovery_attempts=MAX_RECOVERY_ATTEMPTS - 1,
    )

    from agent.session_health import SubprocessKillResult

    _kill_ok = SubprocessKillResult(confirmed_dead=True, signal_sent=False)

    with (
        patch("agent.session_health._tier2_reprieve_signal", return_value=None),
        patch("agent.session_health._confirm_subprocess_dead", return_value=_kill_ok),
        patch("agent.session_health._increment_subprocess_kill_counter"),
        patch("agent.session_health._is_memory_tight", return_value=False),
        patch("agent.session_health.asyncio.get_running_loop") as mock_loop,
        patch(
            "agent.session_health._deliver_tool_timeout_degraded_notice",
            AsyncMock(side_effect=lambda *a, **kw: degraded_calls.append(a)),
        ),
        patch(
            "agent.session_health._deliver_deferred_self_draft_fallback",
            new_callable=AsyncMock,
        ),
        patch("models.session_lifecycle.finalize_session", side_effect=_fake_finalize),
        patch("models.session_lifecycle.transition_status", side_effect=_fake_transition),
        patch("models.session_lifecycle.StatusConflictError", Exception),
        patch("agent.agent_session_queue._ensure_worker"),
        patch("agent.session_health._active_events", {}),
        patch("popoto.redis_db.POPOTO_REDIS_DB", MagicMock()),
        patch("agent.steering.reset_self_draft_attempts", return_value=None),
        patch("agent.session_telemetry.record_telemetry_event", return_value=None),
        patch("agent.session_telemetry.finalize_session", return_value=None),
    ):
        mock_loop.return_value.run_in_executor = AsyncMock(return_value=_kill_ok)
        await _apply_recovery_transition(
            entry,
            reason="tool-wedge: mcp__slow_service exceeded budget (final attempt)",
            reason_kind="tool_timeout",
            handle=None,
            worker_key="telegram-test-chat",
        )

    assert entry.status == "failed", (
        f"Session must finalize as 'failed' at MAX_RECOVERY_ATTEMPTS; got {entry.status!r}"
    )
    assert degraded_calls, (
        "``_deliver_tool_timeout_degraded_notice`` must fire on the genuine "
        "post-recovery exhaustion path (issue #1762 fix must not suppress it)"
    )
