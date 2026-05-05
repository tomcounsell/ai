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
    TOOL_TIMEOUT_DEFAULT_SEC,
    TOOL_TIMEOUT_INTERNAL_SEC,
    TOOL_TIMEOUT_MCP_SEC,
    _agent_session_tool_timeout_check,
    _check_tool_timeout,
    _classify_tool_tier,
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
):
    """Build a fake session row that LOOKS wedged on a default-tier tool."""
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
        worker_key="local-test",
        is_project_keyed=False,
        priority=None,
        recovery_attempts=0,
        reprieve_count=0,
        response_delivered_at=None,
        started_at=datetime.now(tz=UTC) - timedelta(seconds=600),
        exit_returncode=0,
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
