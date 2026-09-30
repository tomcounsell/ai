"""Unit tests for the params-driven apply activation added to
reflections/memory/memory_decay_prune.py (issue #2203).

Covers:
- params={"apply": True} engages BOTH tiers when neither env var is set.
- An explicitly-set env var (true or false) overrides params in either
  direction (env-as-kill-switch precedence).
- Per-tier removal MECHANISM (issue #2203 BLOCKER): tier-1 (importance < 0.15)
  HARD-DELETES (a tombstone save() cannot persist below the 0.15 write floor);
  tier-2 (0.15 <= importance <= 1.0) TOMBSTONES via superseded_by + save().
- The BLOCKER end-to-end with REAL Popoto records: a seeded tier-1 record is
  genuinely ABSENT from Memory.query after apply, and prune_count equals the
  actual removals (no phantom count from a filtered save); a seeded tier-2
  record gains superseded_by and persists.
- The per-run cap and importance floor still apply under params-driven activation.
- prune_count increments via the real Redis gate-counter, keyed per-record by the
  record's own project_key, coalescing null/empty to DEFAULT_PROJECT_KEY.

Fast precedence/cap tests use a FakeMemory whose Memory.query.all() is patched.
The BLOCKER tests use REAL Popoto records (real delete()/save() against Redis)
so the write filter is genuinely exercised -- Memory.query.all() is patched to
return ONLY the seeded records so the reflection never touches the live corpus.
"""

from __future__ import annotations

import asyncio
import time as _time
import uuid
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from config.memory_defaults import DEFAULT_PROJECT_KEY


@pytest.fixture(autouse=True)
def _neutralize_prune_guardrail(monkeypatch):
    """Neutralize the corpus-collapse guardrail (issue #2438) by default.

    These tests exercise apply-param/env precedence and cap enforcement
    against tiny fake corpora, which the guardrail's fraction check would
    trip on its own. Guardrail-specific tests (if any) re-tighten locally.
    """
    from reflections.memory import memory_decay_prune

    monkeypatch.setattr(memory_decay_prune, "MAX_PRUNE_FRACTION", 1.0)
    monkeypatch.setattr(memory_decay_prune, "MAX_PRUNE_ABSOLUTE", 10_000)


# Content long/substantive enough to clear the #2201 write-gate content filter.
_GATE_OK_CONTENT = (
    "The deployment pipeline uses a blue-green strategy with health-check "
    "gating before cutover to the new revision."
)


class FakeMemory:
    """Minimal stand-in for a Memory record.

    The reflection reads plain attributes and calls delete() (tier-1) or
    save() (tier-2). delete()/save() set flags so the test can assert which
    removal mechanism ran per tier.
    """

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
        self.deleted = False

    def save(self):
        self.saved = True

    def delete(self):
        self.deleted = True


def _fixture_confidence(memory) -> float:
    c = getattr(memory, "confidence", None)
    return 0.5 if c is None else float(c)


def _run_with(memories, env, params=None):
    """Run the reflection with Memory.query.all() patched to return `memories`.

    delete()/save() are called on the objects themselves, so passing real
    Memory instances exercises real Redis removal while the query is isolated.
    """
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


def _get_counter(project_key: str, reason: str) -> int:
    from popoto.redis_db import POPOTO_REDIS_DB as _R

    val = _R.get(f"{project_key}:memory-gate:{reason}")
    return int(val) if val else 0


# --- params -> apply engages tier-2 only; tier-1 requires explicit env ----


def test_params_apply_true_engages_only_tier2_when_no_env_set(monkeypatch):
    """params={"apply": True} with no env vars set engages ONLY tier-2 (issue
    #2438): tier-2 tombstones via the params fallback, but tier-1 hard-delete
    stays dry-run because it no longer inherits `params.apply` -- it requires
    its own explicit MEMORY_DECAY_PRUNE_APPLY opt-in."""
    _clear_env(monkeypatch)
    tier1 = FakeMemory(memory_id="d1", importance=0.05, age_days=60, confidence=0.9)
    tier2 = FakeMemory(memory_id="n1", importance=1.0, age_days=30)

    result = _run_with([tier1, tier2], {}, params={"apply": True})

    # Tier-1 stays dry-run: params.apply alone no longer engages hard-delete.
    assert tier1.deleted is False
    assert tier1.superseded_by is None
    # Tier-2 still tombstones via the params fallback (reversible, unaffected).
    assert tier2.saved is True
    assert tier2.deleted is False
    assert tier2.superseded_by == "decay-prune-tier2"
    assert "0 deleted" in result["summary"]
    assert "1 tombstoned" in result["summary"]


def test_params_apply_true_plus_explicit_tier1_env_engages_both_tiers(monkeypatch):
    """Tier-1 hard-delete engages only with its OWN explicit env opt-in,
    alongside the params-driven tier-2 tombstone."""
    monkeypatch.delenv("MEMORY_NOISE_PRUNE_APPLY", raising=False)
    tier1 = FakeMemory(memory_id="d1b", importance=0.05, age_days=60, confidence=0.9)
    tier2 = FakeMemory(memory_id="n1b", importance=1.0, age_days=30)

    result = _run_with(
        [tier1, tier2],
        {"MEMORY_DECAY_PRUNE_APPLY": "true"},
        params={"apply": True},
    )

    assert tier1.deleted is True
    assert tier1.superseded_by is None
    assert tier2.saved is True
    assert tier2.superseded_by == "decay-prune-tier2"
    assert "1 deleted" in result["summary"]
    assert "1 tombstoned" in result["summary"]


def test_params_apply_false_or_absent_stays_dry_run(monkeypatch):
    """No params, no env -> dry-run, nothing removed."""
    _clear_env(monkeypatch)
    m = FakeMemory(memory_id="d2", importance=0.05, age_days=60, confidence=0.9)

    result = _run_with([m], {}, params=None)

    assert m.deleted is False
    assert m.saved is False
    assert m.superseded_by is None
    assert "DRY RUN" in result["summary"]


# --- env-as-kill-switch: overrides params in both directions --------------


def test_env_true_forces_apply_even_when_params_apply_false(monkeypatch):
    """Explicit env=true wins over params={"apply": False}."""
    monkeypatch.delenv("MEMORY_NOISE_PRUNE_APPLY", raising=False)
    m = FakeMemory(memory_id="d3", importance=0.05, age_days=60, confidence=0.9)

    result = _run_with([m], {"MEMORY_DECAY_PRUNE_APPLY": "true"}, params={"apply": False})

    assert m.deleted is True  # tier-1 hard-delete engaged by env
    assert "APPLIED" in result["summary"]


def test_env_false_forces_dry_run_even_when_params_apply_true(monkeypatch):
    """Explicit env=false wins over params={"apply": True} -- the emergency brake."""
    monkeypatch.delenv("MEMORY_NOISE_PRUNE_APPLY", raising=False)
    m = FakeMemory(memory_id="d4", importance=0.05, age_days=60, confidence=0.9)

    result = _run_with([m], {"MEMORY_DECAY_PRUNE_APPLY": "false"}, params={"apply": True})

    assert m.deleted is False
    assert m.superseded_by is None
    # tier-2 (noise) still engaged via params since its own env var is unset.
    assert "tier1=1" in result["summary"]


def test_tiers_resolve_independently_against_their_own_env_var(monkeypatch):
    """Each tier's own explicit env var wins regardless of params or the
    other tier's setting: tier-1 opts in via its own env var while tier-2 is
    forced off by ITS own explicit env var, even though params says apply."""
    m1 = FakeMemory(memory_id="d5", importance=0.05, age_days=60, confidence=0.9)
    m2 = FakeMemory(memory_id="n5", importance=1.0, age_days=30)

    result = _run_with(
        [m1, m2],
        {"MEMORY_DECAY_PRUNE_APPLY": "true", "MEMORY_NOISE_PRUNE_APPLY": "false"},
        params={"apply": True},
    )

    # tier-1 opts in via its own explicit env var.
    assert m1.deleted is True
    # tier-2 forced off by its own explicit env var, regardless of params.
    assert m2.saved is False
    assert m2.superseded_by is None
    assert "APPLIED" in result["summary"]


def test_tier1_unset_env_stays_dry_run_even_with_params_apply(monkeypatch):
    """Tier-1 with NO env var set stays dry-run under params={"apply": True}
    (issue #2438) -- the asymmetry that motivated decoupling hard-delete from
    the shared params fallback."""
    _clear_env(monkeypatch)
    m1 = FakeMemory(memory_id="d5b", importance=0.05, age_days=60, confidence=0.9)

    result = _run_with([m1], {}, params={"apply": True})

    assert m1.deleted is False
    assert "0 deleted" in result["summary"]


# --- cap + importance floor still respected under params activation -------


def test_cap_respected_under_params_activation(monkeypatch):
    """MAX_PRUNE_PER_RUN still caps the union when tier-1 is engaged via its
    own explicit env var (tier-1 no longer engages via params alone, #2438)."""
    from reflections.memory import memory_decay_prune

    _clear_env(monkeypatch)
    with patch.object(memory_decay_prune, "MAX_PRUNE_PER_RUN", 3):
        records = [
            FakeMemory(memory_id=f"d{i}", importance=0.05, age_days=60, confidence=0.9)
            for i in range(5)
        ]
        result = _run_with(records, {"MEMORY_DECAY_PRUNE_APPLY": "true"}, params={"apply": True})

    removed = sum(1 for m in records if m.deleted)
    assert removed == 3
    assert "3 deleted" in result["summary"]


def test_importance_floor_exempts_high_importance_records(monkeypatch):
    """importance >= IMPORTANCE_EXEMPT_THRESHOLD is never removed, even under params apply."""
    _clear_env(monkeypatch)
    vip = FakeMemory(memory_id="vip", importance=8.0, age_days=100)

    _run_with([vip], {}, params={"apply": True})

    assert vip.deleted is False
    assert vip.saved is False
    assert vip.superseded_by is None


# --- BLOCKER: real records genuinely leave the corpus, no phantom count ----


def _run_real(memories, params, env=None):
    """Run against REAL Memory instances: Memory.query.all() is patched to return
    only the seeded records, but delete()/save() hit real Redis (write filter
    genuinely exercised)."""
    from reflections.memory import memory_decay_prune

    fake_memory_cls = MagicMock()
    fake_memory_cls.query.all.return_value = memories

    with (
        patch.dict("os.environ", env or {}, clear=False),
        patch("models.memory.Memory", fake_memory_cls),
    ):
        return asyncio.run(memory_decay_prune.run(params=params))


def test_tier1_real_record_hard_deleted_absent_from_query_no_phantom(monkeypatch):
    """BLOCKER: a seeded tier-1 record (importance < 0.15) is hard-deleted -- absent
    from Memory.query afterward -- and prune_count increments by exactly the number
    actually removed (never a phantom from a filtered tombstone save)."""
    from models.memory import Memory

    _clear_env(monkeypatch)
    pk = "test-2203-tier1-blocker"
    mid = uuid.uuid4().hex
    # Seed at a gate-passing importance so it persists, then drive tier-1
    # classification via Python attributes (importance < 0.15, aged, never accessed).
    m = Memory(
        memory_id=mid,
        content=_GATE_OK_CONTENT,
        importance=0.5,
        project_key=pk,
        access_count=0,
    )
    assert m.save()
    m.importance = 0.10
    m.created_at = datetime.now(UTC) - timedelta(days=60)

    before = _get_counter(pk, "prune_count")
    try:
        # Tier-1 hard-delete requires its own explicit env opt-in (#2438) --
        # params={"apply": True} alone is no longer sufficient.
        result = _run_real([m], params={"apply": True}, env={"MEMORY_DECAY_PRUNE_APPLY": "true"})

        # Genuinely gone from the corpus (hard-delete, not a no-op tombstone save).
        assert Memory.query.filter(memory_id=mid).all() == []
        # prune_count increments by exactly one -- no phantom.
        assert _get_counter(pk, "prune_count") == before + 1
        assert "1 deleted" in result["summary"]
    finally:
        for leftover in Memory.query.filter(memory_id=mid).all():
            leftover.delete()


def test_tier2_real_record_tombstoned_persists(monkeypatch):
    """A seeded tier-2 record (0.15 <= importance <= 1.0, aged, baseline confidence)
    gains superseded_by via a persisting save() and increments prune_count."""
    from models.memory import Memory

    _clear_env(monkeypatch)
    pk = "test-2203-tier2-tombstone"
    mid = uuid.uuid4().hex
    m = Memory(
        memory_id=mid,
        content=_GATE_OK_CONTENT,
        importance=0.5,
        project_key=pk,
        access_count=0,
    )
    assert m.save()
    # Age it past NOISE_PRUNE_AGE_DAYS so it qualifies for tier-2.
    m.created_at = datetime.now(UTC) - timedelta(days=30)

    before = _get_counter(pk, "prune_count")
    try:
        result = _run_real([m], params={"apply": True})

        refetched = Memory.query.filter(memory_id=mid).all()
        assert len(refetched) == 1  # tombstoned, not deleted -- still present
        assert refetched[0].superseded_by == "decay-prune-tier2"
        assert _get_counter(pk, "prune_count") == before + 1
        assert "1 tombstoned" in result["summary"]
    finally:
        for leftover in Memory.query.filter(memory_id=mid).all():
            leftover.delete()


# --- prune_count counter, per-record project_key coalescing ---------------


def test_prune_count_increments_for_named_project_key(monkeypatch):
    _clear_env(monkeypatch)
    pk = "test-decay-prune-counter-named"
    before = _get_counter(pk, "prune_count")

    # Tier-1 hard-delete requires its own explicit env opt-in (#2438).
    m = FakeMemory(memory_id="pk1", importance=0.05, age_days=60, confidence=0.9, project_key=pk)
    _run_with([m], {"MEMORY_DECAY_PRUNE_APPLY": "true"}, params={"apply": True})

    after = _get_counter(pk, "prune_count")
    assert after == before + 1


def test_prune_count_coalesces_null_project_key_to_default(monkeypatch):
    """A record with project_key=None must increment the DEFAULT_PROJECT_KEY counter,
    not silently vanish under a null-keyed Redis key."""
    _clear_env(monkeypatch)
    before = _get_counter(DEFAULT_PROJECT_KEY, "prune_count")

    m = FakeMemory(memory_id="pk2", importance=0.05, age_days=60, confidence=0.9, project_key=None)
    _run_with([m], {"MEMORY_DECAY_PRUNE_APPLY": "true"}, params={"apply": True})

    after = _get_counter(DEFAULT_PROJECT_KEY, "prune_count")
    assert after == before + 1


def test_prune_count_coalesces_empty_string_project_key_to_default(monkeypatch):
    _clear_env(monkeypatch)
    before = _get_counter(DEFAULT_PROJECT_KEY, "prune_count")

    m = FakeMemory(memory_id="pk3", importance=0.05, age_days=60, confidence=0.9, project_key="")
    _run_with([m], {"MEMORY_DECAY_PRUNE_APPLY": "true"}, params={"apply": True})

    after = _get_counter(DEFAULT_PROJECT_KEY, "prune_count")
    assert after == before + 1


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
