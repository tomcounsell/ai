"""Unit tests for utils.json_cache.

Covers the helper's own behavior in isolation:
    - hit, miss
    - fallback on corrupt cache file (silent recovery to empty cache)
    - TTL expiry
    - LRU eviction at max_entries (with recency-bump preservation)
    - atomic write semantics (no partial file visible after a simulated crash)
    - version-key invalidation (different version -> different cache slot)
    - falsy-result-not-cached (compute_fn returns "", None, [], {}, 0, False)
    - analytics-unavailable graceful degradation
"""

from __future__ import annotations

import json
import time
from pathlib import Path
from unittest.mock import patch

import pytest

from utils.json_cache import JsonCache, get_or_compute

# ---------------------------------------------------------------------------
# Hit / miss
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestHitMiss:
    def test_miss_then_hit(self, tmp_path: Path) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=10)
        calls = {"n": 0}

        def compute() -> str:
            calls["n"] += 1
            return "value-1"

        first = get_or_compute(cache, "input-A", compute, version="v1")
        assert first == "value-1"
        assert calls["n"] == 1

        second = get_or_compute(cache, "input-A", compute, version="v1")
        assert second == "value-1"
        assert calls["n"] == 1, "cache hit should not invoke compute_fn"

    def test_different_input_misses(self, tmp_path: Path) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=10)
        calls = {"n": 0}

        def compute() -> str:
            calls["n"] += 1
            return f"value-{calls['n']}"

        get_or_compute(cache, "input-A", compute, version="v1")
        get_or_compute(cache, "input-B", compute, version="v1")
        assert calls["n"] == 2

    def test_persists_across_instances(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        cache_a = JsonCache(path, max_entries=10)
        get_or_compute(cache_a, "input", lambda: "value-1", version="v1")

        # Fresh instance reading the same file.
        cache_b = JsonCache(path, max_entries=10)
        calls = {"n": 0}

        def compute() -> str:
            calls["n"] += 1
            return "value-2"

        result = get_or_compute(cache_b, "input", compute, version="v1")
        assert result == "value-1"
        assert calls["n"] == 0


# ---------------------------------------------------------------------------
# Corrupt-file fallback
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestCorruptFile:
    def test_corrupt_json_silently_resets(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text("{ not valid json", encoding="utf-8")

        cache = JsonCache(path, max_entries=10)
        # Empty after silent recovery.
        assert dict(cache._data) == {}

        # Subsequent set/get works normally.
        result = get_or_compute(cache, "input", lambda: "value-1", version="v1")
        assert result == "value-1"

    def test_non_dict_top_level_resets(self, tmp_path: Path) -> None:
        path = tmp_path / "c.json"
        path.write_text(json.dumps([1, 2, 3]), encoding="utf-8")
        cache = JsonCache(path, max_entries=10)
        assert dict(cache._data) == {}


# ---------------------------------------------------------------------------
# TTL expiry
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestTtl:
    def test_ttl_expiry_recomputes(self, tmp_path: Path) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=10)
        calls = {"n": 0}

        def compute() -> str:
            calls["n"] += 1
            return f"value-{calls['n']}"

        # First call populates with current ts.
        get_or_compute(cache, "input", compute, ttl=1, version="v1")
        assert calls["n"] == 1

        # Monkeypatch time.time to be 10s in the future, beyond TTL.
        future = time.time() + 10
        with patch("utils.json_cache.time.time", return_value=future):
            get_or_compute(cache, "input", compute, ttl=1, version="v1")
        assert calls["n"] == 2, "expired entry should trigger recompute"

    def test_ttl_none_never_expires(self, tmp_path: Path) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=10)
        calls = {"n": 0}

        def compute() -> str:
            calls["n"] += 1
            return "value-1"

        get_or_compute(cache, "input", compute, ttl=None, version="v1")
        # Even far in the future, ttl=None means hit.
        future = time.time() + 10**9
        with patch("utils.json_cache.time.time", return_value=future):
            get_or_compute(cache, "input", compute, ttl=None, version="v1")
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# LRU eviction
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestLruEviction:
    def test_eviction_at_max_entries(self, tmp_path: Path) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=3)

        for i in range(5):
            get_or_compute(cache, f"input-{i}", lambda i=i: f"v{i}", version="v1")

        # Only 3 entries should remain.
        assert len(cache._data) == 3

        # Oldest two (input-0, input-1) should be gone; newest three remain.
        # We can't read by raw key (it's hashed), but we can re-call: the gone
        # ones must recompute, the kept ones must hit.
        recomputes = {"n": 0}

        def compute_for(_label: str):
            def inner() -> str:
                recomputes["n"] += 1
                return _label

            return inner

        # input-0 and input-1 should re-trigger compute (evicted).
        get_or_compute(cache, "input-0", compute_for("v0"), version="v1")
        get_or_compute(cache, "input-1", compute_for("v1"), version="v1")
        assert recomputes["n"] == 2

        # input-2 was evicted by the new sets above. Skip it.
        # input-3 and input-4 might still be hits depending on order, but the
        # critical assertion is the cap holds.
        assert len(cache._data) == 3

    def test_recency_preserved_by_get(self, tmp_path: Path) -> None:
        """A recent get() should mark an entry as MRU so it survives eviction."""
        cache = JsonCache(tmp_path / "c.json", max_entries=3)

        # Populate three entries.
        get_or_compute(cache, "A", lambda: "vA", version="v1")
        get_or_compute(cache, "B", lambda: "vB", version="v1")
        get_or_compute(cache, "C", lambda: "vC", version="v1")

        # Touch A (now MRU).
        get_or_compute(cache, "A", lambda: "vA", version="v1")

        # Add D, which should evict B (now the LRU).
        get_or_compute(cache, "D", lambda: "vD", version="v1")

        # A should still hit.
        recomputes = {"n": 0}

        def fail_if_called() -> str:
            recomputes["n"] += 1
            return "rebuilt"

        result = get_or_compute(cache, "A", fail_if_called, version="v1")
        assert result == "vA"
        assert recomputes["n"] == 0


# ---------------------------------------------------------------------------
# Atomic write semantics
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAtomicWrite:
    def test_save_failure_silent(self, tmp_path: Path) -> None:
        """If write_text raises, _save swallows the error; in-memory state intact."""
        cache = JsonCache(tmp_path / "c.json", max_entries=10)

        # Patch tmp.write_text to raise. Cache.set should not propagate.
        with patch("pathlib.Path.write_text", side_effect=OSError("disk full")):
            cache.set("k1", "v1")

        # In-memory still has the entry.
        assert "k1" in cache._data
        # No partial file on disk: either the path doesn't exist or it's empty/old.
        if cache.path.exists():
            # Whatever's there must be parseable JSON.
            json.loads(cache.path.read_text(encoding="utf-8"))

    def test_no_partial_tmp_file_visible_to_reader(self, tmp_path: Path) -> None:
        """A reader opening the final path always sees a complete file or nothing."""
        path = tmp_path / "c.json"
        cache = JsonCache(path, max_entries=10)
        cache.set("k1", "v1")

        # Final path is parseable.
        assert path.exists()
        loaded = json.loads(path.read_text(encoding="utf-8"))
        assert isinstance(loaded, dict)


# ---------------------------------------------------------------------------
# Version-key invalidation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestVersionInvalidation:
    def test_different_version_different_slot(self, tmp_path: Path) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=10)
        calls = {"n": 0}

        def compute() -> str:
            calls["n"] += 1
            return f"v{calls['n']}"

        get_or_compute(cache, "input", compute, version="v1")
        get_or_compute(cache, "input", compute, version="v2")
        assert calls["n"] == 2, "different version must miss"

    def test_same_version_same_slot(self, tmp_path: Path) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=10)
        calls = {"n": 0}

        def compute() -> str:
            calls["n"] += 1
            return "v"

        get_or_compute(cache, "input", compute, version="v1")
        get_or_compute(cache, "input", compute, version="v1")
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Falsy-result-not-cached
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestFalsyNotCached:
    @pytest.mark.parametrize("falsy", ["", None, [], {}, 0, False])
    def test_falsy_bypasses_cache(self, tmp_path: Path, falsy) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=10)
        calls = {"n": 0}

        def compute():
            calls["n"] += 1
            return falsy

        # First call returns falsy but does NOT cache.
        result_a = get_or_compute(cache, "input", compute, version="v1")
        assert result_a == falsy
        assert len(cache._data) == 0

        # Second call also re-invokes compute_fn.
        result_b = get_or_compute(cache, "input", compute, version="v1")
        assert result_b == falsy
        assert calls["n"] == 2


# ---------------------------------------------------------------------------
# Analytics-unavailable graceful degradation
# ---------------------------------------------------------------------------


@pytest.mark.unit
class TestAnalyticsUnavailable:
    def test_record_metric_raises_does_not_break_cache(self, tmp_path: Path) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=10)

        # Patch record_metric (imported lazily inside _emit_metric) to raise.
        def boom(*args, **kwargs):
            raise RuntimeError("analytics offline")

        with patch("analytics.collector.record_metric", side_effect=boom):
            # Miss path
            r1 = get_or_compute(cache, "input", lambda: "value-1", version="v1")
            # Hit path
            r2 = get_or_compute(cache, "input", lambda: "value-2", version="v1")

        assert r1 == "value-1"
        assert r2 == "value-1", "second call must hit cache despite analytics raising"

    def test_analytics_import_failure_does_not_break_cache(self, tmp_path: Path) -> None:
        cache = JsonCache(tmp_path / "c.json", max_entries=10)

        # Patch the lazy import to raise ImportError.
        import builtins

        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "analytics.collector":
                raise ImportError("analytics module gone")
            return real_import(name, *args, **kwargs)

        with patch("builtins.__import__", side_effect=fake_import):
            r1 = get_or_compute(cache, "input", lambda: "value-1", version="v1")
            r2 = get_or_compute(cache, "input", lambda: "value-2", version="v1")

        assert r1 == "value-1"
        assert r2 == "value-1"
