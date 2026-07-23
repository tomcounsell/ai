"""Unit tests for the params-driven apply activation added to
reflections/memory/memory_decay_prune.py (issue #2203).

Covers:
- params={"apply": True} engages BOTH tiers when neither env var is set.
- An explicitly-set env var (true or false) overrides params in either
  direction (env-as-kill-switch precedence).
- Both tiers tombstone (set superseded_by + save()) rather than delete on
  the apply path.
- The per-run cap and importance floor still apply under params-driven
  activation.
- prune_count increments via the real Redis gate-counter, keyed per-record
  by the record's own project_key, coalescing null/empty to
  DEFAULT_PROJECT_KEY.

Follows the FakeMemory fixture pattern already established in
tests/unit/test_memory_decay_prune.py (Memory.query.all() is patched; the
reflection's tier-selection logic only reads plain attributes + save()).
Real Redis (POPOTO_REDIS_DB) is used for the counter assertions -- no mocks
on that path, per this repo's testing philosophy.
"""

from __future__ import annotations

import asyncio
import time as _time
from unittest.mock import MagicMock, patch

import pytest

from config.memory_defaults import DEFAULT_PROJECT_KEY


class FakeMemory:
    """Minimal stand-in for a Memory record (the reflection only reads attrs + save())."""

    def __init__(
        self,
        *,
        memory_id: str,
        importance: float,
        access_count: int = 0,
        confidence: float | None = 0.5,
        age_days: float = 100.0,
        superseded_by=None,
        content: str = "noise",
        project_key: str | None = "test-decay-prune-apply-params",
    ):
        self.memory_id = memory_id
        self.importance = importance
        self.access_count = access_count
        self.confidence = confidence
        self.created_at = _time.time() - (age_days * 86400)
        self.superseded_by = superseded_by
        self.superseded_by_rationale = None
        self.content = content
        self.project_key = project_key
        self.saved = False

    def save(self):
        self.saved = True


def _fixture_confidence(memory) -> float:
    c = getattr(memory, "confidence", None)
    return 0.5 if c is None else float(c)


def _run_with(memories, env, params=None):
    """Run the reflection with Memory.query.all() patched to return `memories`."""
    from reflections.memory import memory_decay_prune

    fake_memory_cls = MagicMock()
    fake_memory_cls.query.all.return_value = memories

    with (
        patch.dict("os.environ", env, clear=False),
        patch("models.memory.Memory", fake_memory_cls),
        patch(
            "reflections.memory.memory_decay_prune._live_confidence",
            _fixture_confidence,
        ),
    ):
        return asyncio.run(memory_decay_prune.run(params=params))


def _clear_env(monkeypatch):
    monkeypatch.delenv("MEMORY_DECAY_PRUNE_APPLY", raising=False)
    monkeypatch.delenv("MEMORY_NOISE_PRUNE_APPLY", raising=False)


# --- params -> apply engages both tiers -----------------------------------


def test_params_apply_true_engages_both_tiers_when_no_env_set(monkeypatch):
    """params={"apply": True} with no env vars set tombstones both tier-1 and tier-2."""
    _clear_env(monkeypatch)
    tier1 = FakeMemory(memory_id="d1", importance=0.05, age_days=60, confidence=0.9)
    tier2 = FakeMemory(memory_id="n1", importance=1.0, age_days=30)

    result = _run_with([tier1, tier2], {}, params={"apply": True})

    assert tier1.saved is True
    assert tier1.superseded_by == "decay-prune-tier1"
    assert tier2.saved is True
    assert tier2.superseded_by == "decay-prune-tier2"
    assert "APPLIED" in result["summary"]
    assert "2 tombstoned" in result["summary"]


def test_params_apply_false_or_absent_stays_dry_run(monkeypatch):
    """No params, no env -> dry-run, nothing tombstoned."""
    _clear_env(monkeypatch)
    m = FakeMemory(memory_id="d2", importance=0.05, age_days=60, confidence=0.9)

    result = _run_with([m], {}, params=None)

    assert m.saved is False
    assert m.superseded_by is None
    assert "DRY RUN" in result["summary"]


# --- env-as-kill-switch: overrides params in both directions --------------


def test_env_true_forces_apply_even_when_params_apply_false(monkeypatch):
    """Explicit env=true wins over params={"apply": False}."""
    monkeypatch.delenv("MEMORY_NOISE_PRUNE_APPLY", raising=False)
    m = FakeMemory(memory_id="d3", importance=0.05, age_days=60, confidence=0.9)

    result = _run_with([m], {"MEMORY_DECAY_PRUNE_APPLY": "true"}, params={"apply": False})

    assert m.saved is True
    assert m.superseded_by == "decay-prune-tier1"
    assert "APPLIED" in result["summary"]


def test_env_false_forces_dry_run_even_when_params_apply_true(monkeypatch):
    """Explicit env=false wins over params={"apply": True} -- the emergency brake."""
    monkeypatch.delenv("MEMORY_NOISE_PRUNE_APPLY", raising=False)
    m = FakeMemory(memory_id="d4", importance=0.05, age_days=60, confidence=0.9)

    result = _run_with([m], {"MEMORY_DECAY_PRUNE_APPLY": "false"}, params={"apply": True})

    assert m.saved is False
    assert m.superseded_by is None
    # tier-2 (noise) still engaged via params since its own env var is unset.
    assert "tier1=1" in result["summary"]


def test_tiers_resolve_independently_against_their_own_env_var(monkeypatch):
    """One tier's env var can force it off while params engages the other tier."""
    m1 = FakeMemory(memory_id="d5", importance=0.05, age_days=60, confidence=0.9)
    m2 = FakeMemory(memory_id="n5", importance=1.0, age_days=30)

    result = _run_with(
        [m1, m2],
        {"MEMORY_NOISE_PRUNE_APPLY": "false"},
        params={"apply": True},
    )

    # tier-1 has no env override -> falls back to params (apply)
    assert m1.saved is True
    assert m1.superseded_by == "decay-prune-tier1"
    # tier-2 forced off by its own explicit env var, regardless of params
    assert m2.saved is False
    assert m2.superseded_by is None
    assert "APPLIED" in result["summary"]


# --- tombstone-first: no delete() call anywhere on the apply path ---------


def test_apply_path_never_calls_delete(monkeypatch):
    """FakeMemory intentionally has no delete() method -- if the apply path ever
    called it, this test would raise AttributeError."""
    _clear_env(monkeypatch)
    tier1 = FakeMemory(memory_id="d6", importance=0.05, age_days=60, confidence=0.9)
    tier2 = FakeMemory(memory_id="n6", importance=1.0, age_days=30)

    assert not hasattr(tier1, "delete")
    assert not hasattr(tier2, "delete")

    result = _run_with([tier1, tier2], {}, params={"apply": True})
    assert result["status"] == "ok"


# --- cap + importance floor still respected under params activation -------


def test_cap_respected_under_params_activation(monkeypatch):
    """MAX_PRUNE_PER_RUN still caps the union when activation comes from params."""
    from reflections.memory import memory_decay_prune

    _clear_env(monkeypatch)
    with patch.object(memory_decay_prune, "MAX_PRUNE_PER_RUN", 3):
        records = [
            FakeMemory(memory_id=f"d{i}", importance=0.05, age_days=60, confidence=0.9)
            for i in range(5)
        ]
        result = _run_with(records, {}, params={"apply": True})

    tombstoned = sum(1 for m in records if m.saved)
    assert tombstoned == 3
    assert "3 tombstoned" in result["summary"]


def test_importance_floor_exempts_high_importance_records(monkeypatch):
    """importance >= IMPORTANCE_EXEMPT_THRESHOLD is never tombstoned, even under params apply."""
    _clear_env(monkeypatch)
    vip = FakeMemory(memory_id="vip", importance=8.0, age_days=100)

    _run_with([vip], {}, params={"apply": True})

    assert vip.saved is False
    assert vip.superseded_by is None


# --- prune_count counter, per-record project_key coalescing ---------------


def _get_counter(project_key: str, reason: str) -> int:
    from popoto.redis_db import POPOTO_REDIS_DB as _R

    val = _R.get(f"{project_key}:memory-gate:{reason}")
    return int(val) if val else 0


def test_prune_count_increments_for_named_project_key(monkeypatch):
    _clear_env(monkeypatch)
    pk = "test-decay-prune-counter-named"
    before = _get_counter(pk, "prune_count")

    m = FakeMemory(memory_id="pk1", importance=0.05, age_days=60, confidence=0.9, project_key=pk)
    _run_with([m], {}, params={"apply": True})

    after = _get_counter(pk, "prune_count")
    assert after == before + 1


def test_prune_count_coalesces_null_project_key_to_default(monkeypatch):
    """A record with project_key=None must increment the DEFAULT_PROJECT_KEY counter,
    not silently vanish under a null-keyed Redis key."""
    _clear_env(monkeypatch)
    before = _get_counter(DEFAULT_PROJECT_KEY, "prune_count")

    m = FakeMemory(memory_id="pk2", importance=0.05, age_days=60, confidence=0.9, project_key=None)
    _run_with([m], {}, params={"apply": True})

    after = _get_counter(DEFAULT_PROJECT_KEY, "prune_count")
    assert after == before + 1


def test_prune_count_coalesces_empty_string_project_key_to_default(monkeypatch):
    _clear_env(monkeypatch)
    before = _get_counter(DEFAULT_PROJECT_KEY, "prune_count")

    m = FakeMemory(memory_id="pk3", importance=0.05, age_days=60, confidence=0.9, project_key="")
    _run_with([m], {}, params={"apply": True})

    after = _get_counter(DEFAULT_PROJECT_KEY, "prune_count")
    assert after == before + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
