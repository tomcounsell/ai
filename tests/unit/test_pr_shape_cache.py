"""Tests for ``scripts/pr_shape_cache`` -- the per-SHA verdict cache.

Covers hit, miss, baseline-change invalidation, LRU eviction at the
``MAX_ENTRIES`` cap, atomic-write resilience, corrupt-file recovery,
concurrent-write serialization (regression for Race 1), and lock-timeout
behavior.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import threading
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT))

from scripts import pr_shape_cache as cache_mod  # noqa: E402
from scripts.pr_shape_cache import (  # noqa: E402
    MAX_ENTRIES,
    SCHEMA_VERSION,
    _baseline_hash,
    _empty_cache,
    _evict_lru,
    get_cached_verdict,
    write_verdict,
)


@pytest.fixture
def cache_paths(tmp_path):
    cache = tmp_path / "verdict_cache.json"
    lock = tmp_path / "verdict_cache.lock"
    baseline = tmp_path / "baseline.json"
    baseline.write_text(json.dumps({"schema_version": 2, "tests": {}}))
    return cache, lock, baseline


# ---------------------------------------------------------------------------
# Basic hit / miss.
# ---------------------------------------------------------------------------


def test_miss_returns_none(cache_paths):
    cache, lock, baseline = cache_paths
    assert (
        get_cached_verdict(
            pr=1, sha="abc", baseline_path=baseline, cache_path=cache, lock_path=lock
        )
        is None
    )


def test_write_then_hit(cache_paths):
    cache, lock, baseline = cache_paths
    write_verdict(
        pr=1,
        sha="abc",
        shape="small-patch",
        verdict={"exit_code": 0},
        baseline_path=baseline,
        cache_path=cache,
        lock_path=lock,
    )
    entry = get_cached_verdict(
        pr=1, sha="abc", baseline_path=baseline, cache_path=cache, lock_path=lock
    )
    assert entry is not None
    assert entry["shape"] == "small-patch"
    assert entry["verdict"] == {"exit_code": 0}


def test_baseline_change_invalidates_cache(cache_paths):
    cache, lock, baseline = cache_paths
    write_verdict(
        pr=1,
        sha="abc",
        shape="small-patch",
        verdict={"exit_code": 0},
        baseline_path=baseline,
        cache_path=cache,
        lock_path=lock,
    )
    # Change the baseline file -> different hash -> miss.
    baseline.write_text(json.dumps({"schema_version": 2, "tests": {"changed": {}}}))
    entry = get_cached_verdict(
        pr=1, sha="abc", baseline_path=baseline, cache_path=cache, lock_path=lock
    )
    assert entry is None


def test_empty_pr_or_sha_returns_none(cache_paths):
    cache, lock, baseline = cache_paths
    assert (
        get_cached_verdict(
            pr="", sha="abc", baseline_path=baseline, cache_path=cache, lock_path=lock
        )
        is None
    )
    assert (
        get_cached_verdict(pr=1, sha="", baseline_path=baseline, cache_path=cache, lock_path=lock)
        is None
    )


# ---------------------------------------------------------------------------
# LRU eviction.
# ---------------------------------------------------------------------------


def test_evict_lru_drops_oldest():
    entries = {
        f"k{i}": {"last_used_at": f"2026-05-05T00:00:{i:02d}Z"} for i in range(MAX_ENTRIES + 5)
    }
    _evict_lru(entries)
    assert len(entries) == MAX_ENTRIES
    # The 5 oldest (k0..k4) should be gone
    for i in range(5):
        assert f"k{i}" not in entries
    # k5..k(MAX+4) should remain
    for i in range(5, MAX_ENTRIES + 5):
        assert f"k{i}" in entries


def test_evict_lru_no_op_below_cap():
    entries = {f"k{i}": {"last_used_at": f"2026-05-05T00:00:{i:02d}Z"} for i in range(10)}
    _evict_lru(entries)
    assert len(entries) == 10


# ---------------------------------------------------------------------------
# Atomic-write / corrupt-file recovery.
# ---------------------------------------------------------------------------


def test_corrupt_file_resets_to_empty(cache_paths, caplog):
    cache, lock, baseline = cache_paths
    cache.write_text("not json {{{ ")
    entry = get_cached_verdict(
        pr=1, sha="abc", baseline_path=baseline, cache_path=cache, lock_path=lock
    )
    assert entry is None
    # Subsequent write should succeed (file is reset)
    write_verdict(
        pr=2,
        sha="def",
        shape="docs-only",
        verdict={"exit_code": 0},
        baseline_path=baseline,
        cache_path=cache,
        lock_path=lock,
    )
    entry = get_cached_verdict(
        pr=2, sha="def", baseline_path=baseline, cache_path=cache, lock_path=lock
    )
    assert entry is not None


def test_unrecognized_schema_resets(cache_paths):
    cache, lock, baseline = cache_paths
    cache.write_text(json.dumps({"schema_version": 99, "entries": {}}))
    # Should silently treat as empty and accept new writes.
    write_verdict(
        pr=1,
        sha="abc",
        shape="docs-only",
        verdict={},
        baseline_path=baseline,
        cache_path=cache,
        lock_path=lock,
    )
    entry = get_cached_verdict(
        pr=1, sha="abc", baseline_path=baseline, cache_path=cache, lock_path=lock
    )
    assert entry is not None


def test_atomic_write_does_not_leak_tmp_files(cache_paths):
    cache, lock, baseline = cache_paths
    write_verdict(
        pr=1,
        sha="abc",
        shape="docs-only",
        verdict={},
        baseline_path=baseline,
        cache_path=cache,
        lock_path=lock,
    )
    # No leftover .tmp files in the cache directory.
    leftovers = [f for f in cache.parent.iterdir() if f.name.endswith(".tmp")]
    assert leftovers == []


# ---------------------------------------------------------------------------
# Concurrent-write serialization (Race 1 regression).
# ---------------------------------------------------------------------------


def test_concurrent_writers_serialize(cache_paths):
    """Two threads each call write_verdict with different keys; both must
    appear in the final cache (the fcntl.flock mitigation for Race 1).
    """
    cache, lock, baseline = cache_paths
    barrier = threading.Barrier(2)
    errors: list[Exception] = []

    def w(pr: int, sha: str) -> None:
        try:
            barrier.wait(timeout=5)
            write_verdict(
                pr=pr,
                sha=sha,
                shape="small-patch",
                verdict={"exit_code": 0},
                baseline_path=baseline,
                cache_path=cache,
                lock_path=lock,
            )
        except Exception as e:  # pragma: no cover -- diagnostic
            errors.append(e)

    t1 = threading.Thread(target=w, args=(1, "aaa"))
    t2 = threading.Thread(target=w, args=(2, "bbb"))
    t1.start()
    t2.start()
    t1.join(timeout=10)
    t2.join(timeout=10)
    assert not errors

    # Final cache must contain BOTH entries (no read-modify-write entry loss).
    data = json.loads(cache.read_text())
    assert len(data["entries"]) == 2
    e1 = get_cached_verdict(
        pr=1, sha="aaa", baseline_path=baseline, cache_path=cache, lock_path=lock
    )
    e2 = get_cached_verdict(
        pr=2, sha="bbb", baseline_path=baseline, cache_path=cache, lock_path=lock
    )
    assert e1 is not None
    assert e2 is not None


def test_lock_timeout_skips_write_without_raising(cache_paths, monkeypatch):
    """Holding the lock for >timeout causes write_verdict to log + return False."""
    cache, lock, baseline = cache_paths
    # Shrink timeout to make the test fast.
    monkeypatch.setattr(cache_mod, "LOCK_TIMEOUT_SECS", 0.5)

    # Acquire the lock manually and hold it for longer than the timeout.
    lock.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock), os.O_RDWR | os.O_CREAT, 0o644)
    fcntl.flock(fd, fcntl.LOCK_EX)
    try:
        ok = write_verdict(
            pr=1,
            sha="abc",
            shape="docs-only",
            verdict={},
            baseline_path=baseline,
            cache_path=cache,
            lock_path=lock,
        )
        assert ok is False
        # Cache must remain empty (no partial write).
        if cache.exists():
            data = json.loads(cache.read_text())
            assert data.get("entries", {}) == {}
    finally:
        fcntl.flock(fd, fcntl.LOCK_UN)
        os.close(fd)


# ---------------------------------------------------------------------------
# Baseline-hash helper.
# ---------------------------------------------------------------------------


def test_baseline_hash_missing_returns_empty(tmp_path):
    assert _baseline_hash(tmp_path / "does-not-exist.json") == ""


def test_baseline_hash_changes_on_content_change(tmp_path):
    baseline = tmp_path / "b.json"
    baseline.write_text("a")
    h1 = _baseline_hash(baseline)
    baseline.write_text("b")
    h2 = _baseline_hash(baseline)
    assert h1 != h2
    assert len(h1) == 12 and len(h2) == 12


def test_empty_cache_shape():
    c = _empty_cache()
    assert c == {"schema_version": SCHEMA_VERSION, "entries": {}}
