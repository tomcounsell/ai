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
    TOOL_TIMEOUT_DECLARED_GRACE_SEC,
    TOOL_TIMEOUT_DECLARED_MAX_SEC,
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


def test_tool_timeout_default_code_constant_is_300():
    """The CODE default for TOOL_TIMEOUT_DEFAULT_SEC is 300 (plan #1924, task 5).

    The 2026-07-06 stopgap bump to 3000 (4f9f929e — aimed at the PTY wedge
    fault) is reverted with the PTY teardown. Asserted at source level because
    the runtime value is env-overridable (the vault .env override is removed
    separately in task 7), so a runtime assert would be machine-dependent.
    """
    import inspect
    import re

    src = inspect.getsource(session_health)
    m = re.search(
        r'TOOL_TIMEOUT_DEFAULT_SEC = int\(os\.environ\.get\("TOOL_TIMEOUT_DEFAULT_SEC", (\d+)\)\)',
        src,
    )
    assert m is not None, "TOOL_TIMEOUT_DEFAULT_SEC definition not found in session_health source"
    assert m.group(1) == "300", f"code default must be 300, found {m.group(1)}"


def test_no_pty_wedge_machinery_in_session_health():
    """Inverse row: the PTY wedge/quiescence family is deleted (plan #1924)."""
    for name in (
        "_pty_quiescent_long_enough",
        "_is_granite_pty_session",
        "_prime_pty_alive",
        "_wedge_nudge_eligible",
        "_maybe_push_wedge_nudge",
        "_wedge_nudge_producer_tick",
        "_eval_mid_run_pty_stage1",
        "MID_RUN_QUIESCENCE_SECS",
        "NUDGE_WEDGE_THRESHOLD_S",
        "WEDGE_NUDGE_LATCH_TTL_S",
    ):
        assert not hasattr(session_health, name), f"{name} must not resurface"


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
# _check_tool_timeout — epoch scoping (#2002, mirrors #1979 delivery guard)
# ---------------------------------------------------------------------------


def _entry_anchored(
    tool_name,
    *,
    last_tool_age_seconds: float | None,
    anchor_age_seconds: float | None,
    anchor_field: str = "started_at",
):
    """Build a fake session row with an explicit run start anchor.

    ``last_tool_age_seconds`` / ``anchor_age_seconds`` are ages relative to now
    (larger = further in the past). The anchor lands on ``anchor_field``
    (``started_at`` or ``created_at``). Pass ``anchor_age_seconds=None`` to omit
    the anchor entirely (no-anchor legacy row).
    """
    now = datetime.now(tz=UTC)
    last_at = (
        now - timedelta(seconds=last_tool_age_seconds)
        if last_tool_age_seconds is not None
        else None
    )
    ns = SimpleNamespace(current_tool_name=tool_name, last_tool_use_at=last_at)
    if anchor_age_seconds is not None:
        setattr(ns, anchor_field, now - timedelta(seconds=anchor_age_seconds))
    return ns


def test_check_tool_timeout_skips_stale_pair_before_anchor():
    """The bug (#2002): a stale wedge pair carried over from a prior run — its
    ``last_tool_use_at`` predates the current run's ``started_at`` — must NOT
    fire even though it is far past budget."""
    # last_tool_use_at is 600s old (well past the 30s internal budget) but the
    # run only started 60s ago, so the pair belongs to a prior run.
    entry = _entry_anchored(
        "Read",
        last_tool_age_seconds=600,
        anchor_age_seconds=60,
    )
    assert _check_tool_timeout(entry) is None


def test_check_tool_timeout_fires_when_fresh_after_anchor():
    """A wedge whose ``last_tool_use_at`` falls after the run start anchor and is
    past budget still fires (the current run really is wedged)."""
    # Run started 120s ago; the tool stamp is 60s old (>= anchor) and past the
    # 30s internal budget.
    entry = _entry_anchored(
        "Read",
        last_tool_age_seconds=60,
        anchor_age_seconds=120,
    )
    result = _check_tool_timeout(entry)
    assert result is not None
    assert result[0] == "internal"


def test_check_tool_timeout_boundary_equal_anchor_fires():
    """Boundary: ``last_tool_use_at == anchor`` counts as current-run (``>=``)
    and fires when over budget."""
    now = datetime.now(tz=UTC)
    stamp = now - timedelta(seconds=TOOL_TIMEOUT_INTERNAL_SEC + 5)
    entry = SimpleNamespace(
        current_tool_name="Read",
        last_tool_use_at=stamp,
        started_at=stamp,  # exactly equal to the tool stamp
    )
    result = _check_tool_timeout(entry)
    assert result is not None
    assert result[0] == "internal"


def test_check_tool_timeout_no_anchor_legacy_fires():
    """Legacy row with neither ``started_at`` nor ``created_at`` preserves the
    always-evaluate behavior (fires over budget) — matches #1979's fallback."""
    entry = _entry_anchored(
        "Read",
        last_tool_age_seconds=TOOL_TIMEOUT_INTERNAL_SEC + 5,
        anchor_age_seconds=None,
    )
    result = _check_tool_timeout(entry)
    assert result is not None
    assert result[0] == "internal"


def test_check_tool_timeout_falls_back_to_created_at_anchor():
    """When ``started_at`` is absent, ``created_at`` is the anchor — a stale pair
    predating ``created_at`` is skipped."""
    entry = _entry_anchored(
        "Read",
        last_tool_age_seconds=600,
        anchor_age_seconds=60,
        anchor_field="created_at",
    )
    assert _check_tool_timeout(entry) is None


def test_check_tool_timeout_garbage_anchor_treated_as_legacy():
    """A non-datetime/garbage anchor ⇒ ``_ts`` returns None ⇒ no-anchor legacy
    path ⇒ evaluation proceeds (never crashes)."""
    entry = SimpleNamespace(
        current_tool_name="Read",
        last_tool_use_at=datetime.now(tz=UTC) - timedelta(seconds=TOOL_TIMEOUT_INTERNAL_SEC + 5),
        started_at="not-a-datetime",
        created_at=None,
    )
    result = _check_tool_timeout(entry)
    assert result is not None
    assert result[0] == "internal"


def test_check_tool_timeout_naive_stale_pair_skipped():
    """A naive (tz-less) ``last_tool_use_at`` predating the anchor is still
    epoch-scoped correctly (routed through ``_ts`` normalization), not fired."""
    now = datetime.now(tz=UTC)
    naive_stale = (now - timedelta(seconds=600)).replace(tzinfo=None)
    entry = SimpleNamespace(
        current_tool_name="Read",
        last_tool_use_at=naive_stale,
        started_at=now - timedelta(seconds=60),
    )
    assert _check_tool_timeout(entry) is None


# ---------------------------------------------------------------------------
# _check_tool_timeout — declared-timeout resolution (issue #2145)
# ---------------------------------------------------------------------------


def _entry_declared(tool_name, age_seconds, declared):
    """`_entry` plus the declared-timeout field the #2145 raise reads."""
    e = _entry(tool_name, age_seconds)
    e.current_tool_timeout_s = declared
    return e


def test_declared_below_tier_keeps_tier_budget():
    """A declared timeout SMALLER than the tier never lowers the budget."""
    result = _check_tool_timeout(_entry_declared("Bash", TOOL_TIMEOUT_DEFAULT_SEC + 1, 10.0))
    assert result is not None
    assert result[0] == "default"


def test_declared_above_tier_protects_within_declared_budget():
    """The incident scenario: 600s-declared Bash call at age 400s (over the
    300s tier, inside its own budget) must NOT be wedged."""
    assert _check_tool_timeout(_entry_declared("Bash", 400, 600.0)) is None


def test_declared_above_tier_fires_after_declared_plus_grace():
    age = 600 + TOOL_TIMEOUT_DECLARED_GRACE_SEC + 5
    result = _check_tool_timeout(_entry_declared("Bash", age, 600.0))
    assert result is not None
    tier, reason = result
    assert tier == "default"
    assert "declared" in reason
    assert str(600 + TOOL_TIMEOUT_DECLARED_GRACE_SEC) in reason


def test_declared_above_cap_is_capped_never_disables_detection():
    """An absurd declared value (24h) raises the budget only to cap + grace."""
    over = TOOL_TIMEOUT_DECLARED_MAX_SEC + TOOL_TIMEOUT_DECLARED_GRACE_SEC + 5
    assert _check_tool_timeout(_entry_declared("Bash", over, 86400.0)) is not None
    under = TOOL_TIMEOUT_DECLARED_MAX_SEC + TOOL_TIMEOUT_DECLARED_GRACE_SEC - 5
    assert _check_tool_timeout(_entry_declared("Bash", under, 86400.0)) is None


@pytest.mark.parametrize("bad", [None, 0, -5, "600", True, float("nan")])
def test_declared_invalid_values_fall_back_to_tier(bad):
    """None / non-positive / string / bool / NaN → tier budget unchanged."""
    result = _check_tool_timeout(_entry_declared("Bash", TOOL_TIMEOUT_DEFAULT_SEC + 1, bad))
    assert result is not None
    tier, reason = result
    assert tier == "default"
    assert "declared" not in reason


def test_declared_attribute_absent_uses_tier():
    """Legacy rows without the field keep today's exact behavior."""
    result = _check_tool_timeout(_entry("Bash", TOOL_TIMEOUT_DEFAULT_SEC + 1))
    assert result is not None
    assert "declared" not in result[1]


def test_declared_stale_pair_still_epoch_scoped():
    """A prior-run declared value rides the wedge pair — the epoch gate scopes
    it out before the budget math (#2002 pattern)."""
    now = datetime.now(tz=UTC)
    entry = SimpleNamespace(
        current_tool_name="Bash",
        last_tool_use_at=now - timedelta(seconds=1000),
        current_tool_timeout_s=600.0,
        started_at=now - timedelta(seconds=60),
    )
    assert _check_tool_timeout(entry) is None


def test_declared_on_internal_tier_also_raises():
    """The raise is tier-agnostic: max(tier, declared+grace) applies to any
    tool that somehow carries a declared timeout."""
    age = TOOL_TIMEOUT_INTERNAL_SEC + 10
    assert _check_tool_timeout(_entry_declared("Read", age, 120.0)) is None
    fires = 120 + TOOL_TIMEOUT_DECLARED_GRACE_SEC + 5
    assert _check_tool_timeout(_entry_declared("Read", fires, 120.0)) is not None


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
        worker_key="telegram-test-chat",
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


# ---------------------------------------------------------------------------
# Regression test for issue #2042: the tool-timeout sub-loop is a fifth,
# independent surface that could transition a non-executable ledger anchor
# row (created by ``sdlc-tool session-ensure``). Ledger rows never get a
# claude_pid, turn_count, log_path, or claude_session_uuid, so once their
# started_at ages past NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS
# the D0 never-started branch would otherwise transition them via
# _apply_recovery_transition -- exactly the destructive behavior #2042
# exists to prevent.
# ---------------------------------------------------------------------------


def _fake_never_started_ledger_entry(**overrides):
    """Build a fake running session row that LOOKS never-started AND is a
    ledger anchor: no tool in flight, no per-turn/per-stream liveness fields,
    no own-progress sticky fields, and started_at far enough in the past to
    trip ``_never_started_past_grace``."""
    from agent.session_stall_classifier import (
        NEVER_STARTED_CONFIRM_MARGIN_SECS,
        NEVER_STARTED_GRACE_SECS,
    )

    age_seconds = NEVER_STARTED_GRACE_SECS + NEVER_STARTED_CONFIRM_MARGIN_SECS + 30
    started_at = datetime.now(tz=UTC) - timedelta(seconds=age_seconds)
    saves: list[list[str]] = []

    def _save(update_fields=None, **_kw):
        saves.append(list(update_fields) if update_fields else [])

    defaults = dict(
        agent_session_id="ledger-never-started-1",
        id="ledger-never-started-1",
        session_id="sdlc-local-9042",
        status="running",
        project_key="test-tool-timeout-ledger",
        current_tool_name=None,
        last_tool_use_at=None,
        last_turn_at=None,
        last_stdout_at=None,
        worker_key="test-tool-timeout-ledger",
        is_project_keyed=False,
        priority=None,
        recovery_attempts=0,
        reprieve_count=0,
        response_delivered_at=None,
        started_at=started_at,
        created_at=started_at,
        turn_count=0,
        log_path=None,
        claude_session_uuid=None,
        claude_pid=None,
        exit_returncode=None,
        is_ledger=True,
        save=_save,
        delete=lambda **_kw: None,
        _saves=saves,
    )
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


@pytest.mark.asyncio
async def test_subloop_skips_ledger_anchor_never_started(monkeypatch, clean_active_sessions):
    """An is_ledger=True entry that would otherwise trip the D0 never-started
    branch (no claude_pid/turn_count/log_path/claude_session_uuid, started_at
    past grace) must be skipped entirely -- no re-read, no recovery
    transition, no counter/save mutation."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_never_started_ledger_entry()

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id") as mock_get_by_id,
        patch.object(session_health, "_apply_recovery_transition") as mock_transition,
    ):
        await _agent_session_tool_timeout_check()

    mock_transition.assert_not_called()
    mock_get_by_id.assert_not_called()
    assert entry._saves == []


@pytest.mark.asyncio
async def test_subloop_non_ledger_never_started_entry_is_still_recovered(
    monkeypatch, clean_active_sessions
):
    """Control case: an otherwise-identical entry with is_ledger=False (or
    missing) IS recovered by the D0 never-started branch -- proves the new
    guard is scoped to ledger rows only, not a blanket suppression of the D0
    path."""
    monkeypatch.delenv("TOOL_TIMEOUT_TIERS_DISABLED", raising=False)
    entry = _fake_never_started_ledger_entry(is_ledger=False)

    async def _fake_transition(*_a, **kwargs):
        _fake_transition.last_kwargs = kwargs
        return True

    _fake_transition.last_kwargs = {}

    with (
        patch.object(session_health.AgentSession.query, "filter", return_value=[entry]),
        patch.object(session_health, "_filter_hydrated_sessions", lambda x: list(x)),
        patch.object(session_health.AgentSession, "get_by_id", classmethod(lambda cls, sid: entry)),
        patch.object(session_health, "_apply_recovery_transition", _fake_transition),
    ):
        await _agent_session_tool_timeout_check()

    assert _fake_transition.last_kwargs.get("reason_kind") == "no_progress"
