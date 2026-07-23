"""Unit tests for reflections/memory/memory_decay_prune.py.

Covers the original decay tier (tier-1) and the issue #1822 extraction-noise
tier (tier-2): non-overlap, the dedicated MEMORY_NOISE_PRUNE_APPLY gate, union
dedup, the 14-day boundary, the confidence ≈ 0.5 epsilon, and the shared
MAX_PRUNE_PER_RUN cap.
"""

from __future__ import annotations

import time as _time
from unittest.mock import MagicMock, patch

import pytest


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
        project_key: str | None = "test-decay-prune",
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

    @property
    def deleted(self) -> bool:
        """Back-compat test helper: True once tombstoned (superseded_by set + saved)."""
        return self.saved and bool(self.superseded_by)


def _fixture_confidence(memory) -> float:
    """Live-confidence stand-in for tests.

    Production reads the ConfidenceField companion hash via
    ``_live_confidence``; FakeMemory has no companion hash, so tests patch the
    helper to read the fixture's ``confidence`` attribute (None → 0.5 baseline).
    """
    c = getattr(memory, "confidence", None)
    return 0.5 if c is None else float(c)


def _run_with(memories, env):
    """Run the reflection with Memory.query.all() patched to return `memories`."""
    import asyncio

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
        return asyncio.run(memory_decay_prune.run())


# --- Tier-2 candidate selection ------------------------------------------------


def test_tier2_baseline_noise_is_candidate_dry_run():
    """importance=1.0, access_count=0, confidence=0.5, old → tier-2 candidate (dry-run)."""
    m = FakeMemory(memory_id="noise1", importance=1.0, age_days=30)
    result = _run_with(
        [m], {"MEMORY_DECAY_PRUNE_APPLY": "false", "MEMORY_NOISE_PRUNE_APPLY": "false"}
    )
    assert result["status"] == "ok"
    assert "tier2=1" in result["summary"]
    assert m.deleted is False  # dry-run never deletes


def test_tier2_applies_with_its_own_gate():
    """MEMORY_NOISE_PRUNE_APPLY=true deletes tier-2; MEMORY_DECAY_PRUNE_APPLY stays off."""
    m = FakeMemory(memory_id="noise2", importance=1.0, age_days=30)
    result = _run_with(
        [m], {"MEMORY_DECAY_PRUNE_APPLY": "false", "MEMORY_NOISE_PRUNE_APPLY": "true"}
    )
    assert m.deleted is True
    assert "1 tombstoned" in result["summary"]


def test_decay_gate_does_not_delete_tier2():
    """Enabling only the tier-1 (decay) gate must NOT delete a tier-2 record."""
    m = FakeMemory(memory_id="noise3", importance=1.0, age_days=30)
    result = _run_with(
        [m], {"MEMORY_DECAY_PRUNE_APPLY": "true", "MEMORY_NOISE_PRUNE_APPLY": "false"}
    )
    assert m.deleted is False
    assert "0 tombstoned" in result["summary"]


def test_tier1_unaffected_by_noise_gate():
    """A classic tier-1 record (importance<0.15) deletes under the decay gate only."""
    m = FakeMemory(memory_id="decay1", importance=0.05, age_days=60, confidence=0.9)
    # noise gate alone should NOT touch it (it's not in the tier-2 band)
    r1 = _run_with([m], {"MEMORY_DECAY_PRUNE_APPLY": "false", "MEMORY_NOISE_PRUNE_APPLY": "true"})
    assert m.deleted is False
    assert "tier1=1" in r1["summary"]
    # decay gate deletes it
    r2 = _run_with([m], {"MEMORY_DECAY_PRUNE_APPLY": "true", "MEMORY_NOISE_PRUNE_APPLY": "false"})
    assert m.deleted is True
    assert "1 tombstoned" in r2["summary"]


# --- Non-overlap + exclusions --------------------------------------------------


def test_tiers_are_non_overlapping():
    """A record at exactly WF_MIN_THRESHOLD belongs to tier-2 only, never tier-1."""
    from reflections.memory.memory_decay_prune import WF_MIN_THRESHOLD

    m = FakeMemory(memory_id="edge", importance=WF_MIN_THRESHOLD, age_days=30)
    result = _run_with(
        [m], {"MEMORY_DECAY_PRUNE_APPLY": "false", "MEMORY_NOISE_PRUNE_APPLY": "false"}
    )
    assert "tier1=0" in result["summary"]
    assert "tier2=1" in result["summary"]


def test_reinforced_confidence_excluded_from_tier2():
    """LIVE confidence away from 0.5 baseline (reinforced/dismissed) is NOT tier-2 noise.

    `_run_with` patches `_live_confidence` to read the fixture's confidence,
    standing in for the production read of the ConfidenceField companion hash.
    """
    m = FakeMemory(memory_id="reinforced", importance=1.0, confidence=0.8, age_days=30)
    result = _run_with([m], {"MEMORY_NOISE_PRUNE_APPLY": "true"})
    assert m.deleted is False
    assert "tier2=0" in result["summary"]


def test_live_confidence_falls_back_to_baseline_on_bad_read():
    """_live_confidence returns the 0.5 baseline (never raises) when the read fails."""
    from reflections.memory.memory_decay_prune import (
        NOISE_BASELINE_CONFIDENCE,
        _live_confidence,
    )

    class _NoMeta:
        pass

    assert _live_confidence(_NoMeta()) == NOISE_BASELINE_CONFIDENCE


def test_live_confidence_uses_canonical_accessor(monkeypatch):
    """_live_confidence reads via ConfidenceField.get_confidence, not the stale attribute."""
    from popoto.fields.confidence_field import ConfidenceField

    from reflections.memory.memory_decay_prune import _live_confidence

    monkeypatch.setattr(ConfidenceField, "get_confidence", classmethod(lambda cls, m, f: 0.91))
    # The attribute mirror says 0.5, but the live accessor must win.
    fake = FakeMemory(memory_id="live", importance=1.0, confidence=0.5)
    assert _live_confidence(fake) == 0.91


def test_confidence_epsilon_tolerance():
    """confidence within epsilon of 0.5 still counts as baseline noise."""
    m = FakeMemory(memory_id="eps", importance=1.0, confidence=0.5 + 1e-9, age_days=30)
    _run_with([m], {"MEMORY_NOISE_PRUNE_APPLY": "true"})
    assert m.deleted is True


def test_none_confidence_treated_as_baseline():
    """confidence=None is treated as the 0.5 baseline (no crash, counts as noise)."""
    m = FakeMemory(memory_id="nc", importance=1.0, confidence=None, age_days=30)
    _run_with([m], {"MEMORY_NOISE_PRUNE_APPLY": "true"})
    assert m.deleted is True


def test_recent_tier2_record_exempt_14_day_boundary():
    """A tier-2 record younger than 14 days is exempt."""
    m = FakeMemory(memory_id="recent", importance=1.0, age_days=7)
    result = _run_with([m], {"MEMORY_NOISE_PRUNE_APPLY": "true"})
    assert m.deleted is False
    assert "tier2=0" in result["summary"]


def test_accessed_tier2_record_exempt():
    """A tier-2-band record that has been recalled (access_count>0) is exempt."""
    m = FakeMemory(memory_id="accessed", importance=1.0, access_count=3, age_days=30)
    _run_with([m], {"MEMORY_NOISE_PRUNE_APPLY": "true"})
    assert m.deleted is False


def test_superseded_excluded():
    m = FakeMemory(memory_id="sup", importance=1.0, age_days=30, superseded_by="other")
    result = _run_with([m], {"MEMORY_NOISE_PRUNE_APPLY": "true"})
    assert m.deleted is False
    assert "tier2=0" in result["summary"]


def test_exempt_high_importance_never_pruned():
    m = FakeMemory(memory_id="vip", importance=8.0, age_days=100)
    _run_with([m], {"MEMORY_NOISE_PRUNE_APPLY": "true", "MEMORY_DECAY_PRUNE_APPLY": "true"})
    assert m.deleted is False


# --- Cap + dedup ---------------------------------------------------------------


def test_shared_cap_across_union():
    """MAX_PRUNE_PER_RUN caps the deduped union of both tiers."""
    from reflections.memory import memory_decay_prune

    with patch.object(memory_decay_prune, "MAX_PRUNE_PER_RUN", 5):
        tier1 = [
            FakeMemory(memory_id=f"d{i}", importance=0.05, age_days=60, confidence=0.9)
            for i in range(4)
        ]
        tier2 = [FakeMemory(memory_id=f"n{i}", importance=1.0, age_days=30) for i in range(4)]
        import asyncio

        fake_cls = MagicMock()
        fake_cls.query.all.return_value = tier1 + tier2
        with (
            patch.dict(
                "os.environ",
                {"MEMORY_DECAY_PRUNE_APPLY": "true", "MEMORY_NOISE_PRUNE_APPLY": "true"},
                clear=False,
            ),
            patch("models.memory.Memory", fake_cls),
        ):
            result = asyncio.run(memory_decay_prune.run())

    deleted = sum(1 for m in tier1 + tier2 if m.deleted)
    assert deleted == 5  # capped
    assert "5 tombstoned" in result["summary"]


def test_save_failure_logged_and_run_continues():
    """A tier-2 save that raises is logged; the run still completes and tombstones the rest."""
    good = FakeMemory(memory_id="good", importance=1.0, age_days=30)
    bad = FakeMemory(memory_id="bad", importance=1.0, age_days=30)

    def _boom():
        raise RuntimeError("redis down")

    bad.save = _boom

    result = _run_with([bad, good], {"MEMORY_NOISE_PRUNE_APPLY": "true"})
    assert result["status"] == "ok"
    assert good.deleted is True  # run continued past the failure


def test_query_error_returns_error_status():
    import asyncio

    from reflections.memory import memory_decay_prune

    fake_cls = MagicMock()
    fake_cls.query.all.side_effect = RuntimeError("redis unreachable")
    with patch("models.memory.Memory", fake_cls):
        result = asyncio.run(memory_decay_prune.run())
    assert result["status"] == "error"


# --- Anti-criterion: apply mode is NOT defaulted on ---------------------------


def test_apply_modes_default_off(monkeypatch):
    """With no env vars set, both tiers run in dry-run and delete nothing."""
    monkeypatch.delenv("MEMORY_DECAY_PRUNE_APPLY", raising=False)
    monkeypatch.delenv("MEMORY_NOISE_PRUNE_APPLY", raising=False)
    m1 = FakeMemory(memory_id="a", importance=0.05, age_days=60, confidence=0.9)
    m2 = FakeMemory(memory_id="b", importance=1.0, age_days=30)

    import asyncio

    from reflections.memory import memory_decay_prune

    fake_cls = MagicMock()
    fake_cls.query.all.return_value = [m1, m2]
    with patch("models.memory.Memory", fake_cls):
        result = asyncio.run(memory_decay_prune.run())
    assert m1.deleted is False
    assert m2.deleted is False
    assert "DRY RUN" in result["summary"]


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
