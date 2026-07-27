"""Unit matrix for the pure per-tool budget evaluator (Fix #6, issue #1821).

``evaluate_tool_budget`` is PURE and SYNCHRONOUS — it returns a verdict only,
reading ``tool_call_count`` / ``total_cost_usd`` off the session. These tests
pin the ALLOW/DENY matrix and the fail-safe-on-missing-data behavior. They are
the Acceptance #2 unit core.

The module constants are read as globals at call time, so we override them with
``monkeypatch.setattr`` for deterministic thresholds.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from agent import tool_budget
from agent.tool_budget import BudgetVerdict, evaluate_tool_budget


@pytest.fixture(autouse=True)
def _deterministic_thresholds(monkeypatch):
    """Small, deterministic thresholds and the budget ENABLED by default."""
    monkeypatch.setattr(tool_budget, "MAX_TOOL_CALLS_PER_SESSION", 10)
    monkeypatch.setattr(tool_budget, "SESSION_COST_CAP_USD", 5.0)
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_ENABLED", True)


def _session(calls=0, cost=0.0):
    return SimpleNamespace(tool_call_count=calls, total_cost_usd=cost)


def test_under_budget_allows():
    assert evaluate_tool_budget(_session(calls=5, cost=1.0)).allow is True


@pytest.mark.parametrize("calls", [10, 11, 1000])
def test_tool_call_cap_denies(calls):
    v = evaluate_tool_budget(_session(calls=calls, cost=0.0))
    assert v.allow is False
    assert "tool-call budget" in v.reason


@pytest.mark.parametrize("cost", [5.0, 5.01, 100.0])
def test_cost_cap_denies(cost):
    v = evaluate_tool_budget(_session(calls=0, cost=cost))
    assert v.allow is False
    assert "cost cap" in v.reason


def test_none_session_allows():
    assert evaluate_tool_budget(None).allow is True


def test_none_fields_allow():
    """Missing/None counters must never produce a false deny."""
    assert evaluate_tool_budget(_session(calls=None, cost=None)).allow is True
    assert evaluate_tool_budget(SimpleNamespace()).allow is True


def test_disabled_always_allows(monkeypatch):
    monkeypatch.setattr(tool_budget, "TOOL_BUDGET_ENABLED", False)
    # Even a wildly over-budget session allows when the master switch is off.
    assert evaluate_tool_budget(_session(calls=10_000, cost=10_000.0)).allow is True


def test_verdict_dataclass_shape():
    v = BudgetVerdict(allow=False, reason="x")
    assert (v.allow, v.reason) == (False, "x")
    assert BudgetVerdict(allow=True).reason is None


class _FakeRedis:
    """Minimal Redis double for ``record_budget_trip`` side effects.

    Records ``incr`` totals per key and implements ``set(nx=...)`` dedup
    semantics (returns truthy only the first time a key is set) so the
    per-session dedup gate behaves as in production.
    """

    def __init__(self):
        self.counters: dict[str, int] = {}
        self._keys: set[str] = set()

    def incr(self, key):
        self.counters[key] = self.counters.get(key, 0) + 1
        return self.counters[key]

    def set(self, key, value, nx=False, ex=None):
        if nx and key in self._keys:
            return None
        self._keys.add(key)
        return True

    # never reached in these tests, but keep the surface honest
    def rpush(self, *a, **k):  # pragma: no cover
        return 1

    def expire(self, *a, **k):  # pragma: no cover
        return True


class _FakeSession:
    def __init__(self, session_id):
        self.session_id = session_id
        self.project_key = "testproj"
        self.tool_call_count = 999

    def save(self, **kwargs):  # budget_tripped flag write; no-op here
        return None


@pytest.fixture
def _fake_redis(monkeypatch):
    fake = _FakeRedis()
    import popoto.redis_db as redis_db

    monkeypatch.setattr(redis_db, "POPOTO_REDIS_DB", fake)
    return fake


def test_denied_calls_counts_every_deny_tripped_dedups_per_session(_fake_redis):
    """#1886: ``denied_calls`` increments on EVERY deny (before the per-session
    dedup gate); ``tripped`` increments once per session.

    Two denies for the same session ⇒ denied_calls == 2 but tripped == 1 — the
    exact ratio (mean wasted round-trips per denied session) the deferred
    deny-but-don't-halt decision depends on.
    """
    session = _FakeSession("sess-A")
    verdict = BudgetVerdict(allow=False, reason="per-session tool-call budget reached (999/1000)")

    tool_budget.record_budget_trip(session, verdict)
    tool_budget.record_budget_trip(session, verdict)

    assert _fake_redis.counters.get("testproj:tool-budget:denied_calls") == 2
    assert _fake_redis.counters.get("testproj:tool-budget:tripped") == 1


def test_denied_calls_counts_across_distinct_sessions(_fake_redis):
    """Distinct sessions each count once toward both counters."""
    verdict = BudgetVerdict(allow=False, reason="per-session cost cap reached ($50.00/$50.00)")

    tool_budget.record_budget_trip(_FakeSession("sess-A"), verdict)
    tool_budget.record_budget_trip(_FakeSession("sess-B"), verdict)

    assert _fake_redis.counters.get("testproj:tool-budget:denied_calls") == 2
    assert _fake_redis.counters.get("testproj:tool-budget:tripped") == 2


def test_denied_calls_counts_id_less_sessions_without_dedup_key(_fake_redis):
    """An id-less session bypasses the NX dedup gate (issue #1873 item 3) — its
    denies must still be counted on every call, never collapsed into a shared
    ``:None`` slot."""
    verdict = BudgetVerdict(allow=False, reason="per-session tool-call budget reached (999/1000)")
    idless = SimpleNamespace(project_key="testproj", tool_call_count=999, save=lambda **k: None)

    tool_budget.record_budget_trip(idless, verdict)
    tool_budget.record_budget_trip(idless, verdict)

    assert _fake_redis.counters.get("testproj:tool-budget:denied_calls") == 2
    # id-less path bypasses the dedup gate, so tripped counts every deny too
    assert _fake_redis.counters.get("testproj:tool-budget:tripped") == 2
