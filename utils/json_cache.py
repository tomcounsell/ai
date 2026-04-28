"""Persistent JSON-on-disk cache for deterministic LLM call sites.

A small helper for caching expensive deterministic computations (typically
LLM calls) to a single JSON file on disk, with LRU eviction and optional TTL.

Contract:
    - Cache values must be JSON-serializable. For dataclasses, store
      ``dataclasses.asdict(...)`` and rehydrate at the call site.
    - Single-writer-per-file invariant: each ``JsonCache`` instance must be
      written to by exactly one process. Multi-writer scenarios will silently
      lose writes (atomic ``os.replace`` guarantees no corruption, but
      last-write-wins). See ``docs/features/json-cache-layer.md`` for the
      upgrade path (``fcntl.flock``).
    - Falsy results from ``compute_fn`` are not cached. Empty string, None,
      empty dict/list, False, and 0 bypass storage so transient API flakes
      that returned empty content do not get permanently cached.

The helper is intentionally tiny:
    - ``JsonCache`` wraps an ``OrderedDict`` and a single JSON file.
    - ``get_or_compute`` hashes the input with a version prefix, looks up
      the cache, calls ``compute_fn`` on miss, stores the result, and emits
      ``cache.hit`` / ``cache.miss`` analytics keyed by namespace.
    - All cache failures (corrupt JSON, disk full, IO errors, analytics
      unavailable) are caught silently. Cache failure means cache miss —
      callers fall through to their existing behavior unchanged.

Atomic snapshot: writes go to ``<path>.tmp`` then ``os.replace(tmp, final)``.
POSIX guarantees the rename is atomic — readers see either the old or new
file, never a partial.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import time
from collections import OrderedDict
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")


class JsonCache:
    """Persistent JSON-backed LRU cache, single writer per file.

    See module docstring for the contract and invariants.
    """

    def __init__(self, path: Path, max_entries: int = 2000) -> None:
        self.path = Path(path)
        self.max_entries = max_entries
        # Each entry is {"value": <json>, "ts": <epoch float>}
        self._data: OrderedDict[str, dict[str, Any]] = OrderedDict()
        self._load()

    # ---- internal: load/save ----

    def _load(self) -> None:
        """Load the cache from disk. Silently empty on any error."""
        try:
            if not self.path.exists():
                return
            raw = self.path.read_text(encoding="utf-8")
            obj = json.loads(raw)
            if not isinstance(obj, dict):
                return
            # Preserve order from JSON (Python dicts preserve insertion order
            # since 3.7, and json.loads honors source order).
            for k, v in obj.items():
                if isinstance(v, dict) and "value" in v and "ts" in v:
                    self._data[k] = v
        except Exception as e:
            logger.warning("[json_cache] _load failed for %s: %s", self.path, e)
            self._data = OrderedDict()

    def _save(self) -> None:
        """Snapshot the cache to disk atomically. Silent on failure."""
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(self.path.suffix + ".tmp")
            tmp.write_text(json.dumps(self._data), encoding="utf-8")
            os.replace(tmp, self.path)
        except Exception as e:
            logger.warning("[json_cache] _save failed for %s: %s", self.path, e)

    # ---- public API ----

    def get(self, key: str, ttl: int | None = None) -> Any:
        """Return the cached value or None on miss/expiry.

        On hit, mark the entry as recently used (LRU bookkeeping).
        ``ttl`` is in seconds; ``None`` means no TTL.
        """
        entry = self._data.get(key)
        if entry is None:
            return None
        if ttl is not None:
            ts = entry.get("ts", 0)
            if (time.time() - ts) > ttl:
                # Expired — evict and treat as miss.
                try:
                    del self._data[key]
                except KeyError:
                    pass
                return None
        # LRU: mark recency
        self._data.move_to_end(key)
        return entry.get("value")

    def set(self, key: str, value: Any) -> None:
        """Store value under key, persisting the snapshot. LRU-evict overflow."""
        self._data[key] = {"value": value, "ts": time.time()}
        self._data.move_to_end(key)
        # Evict oldest entries until under cap.
        while len(self._data) > self.max_entries:
            self._data.popitem(last=False)
        self._save()


def _emit_metric(name: str, dimensions: dict[str, Any]) -> None:
    """Emit a cache.hit/cache.miss analytics event. Silent on any failure."""
    try:
        from analytics.collector import record_metric

        record_metric(name, 1.0, dimensions)
    except Exception as e:
        logger.debug("[json_cache] analytics emission failed: %s", e)


def get_or_compute(
    cache: JsonCache,
    key_input: str,
    compute_fn: Callable[[], T],
    *,
    ttl: int | None = None,
    version: str = "v1",
) -> T:
    """Look up ``key_input`` in ``cache``; on miss, call ``compute_fn`` and store.

    Key is ``sha256(f"{version}:{key_input}").hexdigest()`` so bumping
    ``version`` invalidates all old keys (they LRU-evict naturally as new
    entries land).

    Falsy results from ``compute_fn`` are NOT cached — they pass through but
    bypass ``cache.set``. This avoids permanently caching transient empty
    responses.

    Hit/miss is reported via ``analytics.collector.record_metric`` keyed by
    the cache file's stem (e.g., ``intent_classifier``). Analytics failures
    never affect cache behavior.

    Returns the cached or freshly-computed value.
    """
    namespace = cache.path.stem
    digest = hashlib.sha256(f"{version}:{key_input}".encode()).hexdigest()

    cached = cache.get(digest, ttl=ttl)
    if cached is not None:
        _emit_metric("cache.hit", {"namespace": namespace})
        return cached  # type: ignore[return-value]

    _emit_metric("cache.miss", {"namespace": namespace})
    result = compute_fn()
    # Falsy results bypass storage — no permanent caching of transient flakes.
    if not result:
        return result
    try:
        cache.set(digest, result)
    except Exception as e:
        logger.warning("[json_cache] cache.set failed for %s: %s", namespace, e)
    return result
