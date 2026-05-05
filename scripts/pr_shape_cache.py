#!/usr/bin/env python3
"""Per-SHA verdict cache for shape-aware merge gates.

Stores ``compute_gate_verdict()`` output keyed on
``{pr_number}:{commit_sha}:{baseline_hash[:12]}`` so an unchanged tree +
unchanged baseline can skip the full pytest re-run.

Schema (``data/pr_shape_verdict_cache.json``)::

    {
      "schema_version": 1,
      "entries": {
        "<key>": {
          "pr": 1283,
          "sha": "abc123...",
          "baseline_hash": "9f8e7d6c5b4a",
          "shape": "small-patch",
          "verdict": { ...JSON from compute_gate_verdict... },
          "classified_at": "2026-05-05T...",
          "last_used_at":  "2026-05-05T..."
        },
        ...
      }
    }

Eviction: when ``len(entries) > MAX_ENTRIES`` (100), drop the entry with
the oldest ``last_used_at`` field. Single-pass, no background process.

Concurrency: every read-modify-write is wrapped in an ``fcntl.flock(LOCK_EX)``
on a sidecar lock file (``data/pr_shape_verdict_cache.lock``) to bound the
RMW race window. The lock is advisory but every cache writer goes through
this module. See plan §Race 1 for the failure-mode analysis.

CLI usage (called from ``.claude/commands/do-merge.md``)::

    python -m scripts.pr_shape_cache get  --pr N --sha S [--baseline path]
    python -m scripts.pr_shape_cache write --pr N --sha S --baseline path
                                           --shape X --verdict-file f.json

See ``docs/features/pr-shape-aware-merge-gates.md``.
"""

from __future__ import annotations

import argparse
import errno
import fcntl
import hashlib
import json
import logging
import os
import sys
import tempfile
import time
from datetime import UTC, datetime
from pathlib import Path

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants -- defended values, see plan §Cache.
# ---------------------------------------------------------------------------

SCHEMA_VERSION: int = 1
MAX_ENTRIES: int = 100
LOCK_TIMEOUT_SECS: float = 10.0

DEFAULT_CACHE_PATH = Path("data/pr_shape_verdict_cache.json")
DEFAULT_LOCK_PATH = Path("data/pr_shape_verdict_cache.lock")
DEFAULT_BASELINE_PATH = Path("data/main_test_baseline.json")


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _now_iso() -> str:
    return datetime.now(UTC).isoformat()


def _baseline_hash(baseline_path: Path = DEFAULT_BASELINE_PATH) -> str:
    """Return the first 12 chars of the sha256 of the baseline file contents.

    Returns an empty string when the baseline is missing -- the cache key
    will then deterministically miss for every entry written against a
    real baseline, which is the desired behavior.
    """
    if not baseline_path.exists():
        return ""
    try:
        return hashlib.sha256(baseline_path.read_bytes()).hexdigest()[:12]
    except OSError as exc:
        logger.warning("[pr_shape_cache] baseline read failed: %s", exc)
        return ""


def _key(pr: int | str, sha: str, baseline_hash: str) -> str:
    return f"{pr}:{sha}:{baseline_hash}"


def _empty_cache() -> dict:
    return {"schema_version": SCHEMA_VERSION, "entries": {}}


def _load_cache(cache_path: Path) -> dict:
    """Load cache file. Corrupt/missing files reset to empty + log warning."""
    if not cache_path.exists():
        return _empty_cache()
    try:
        raw = cache_path.read_text()
    except OSError as exc:
        logger.warning("[pr_shape_cache] read failed; treating as empty: %s", exc)
        return _empty_cache()
    if not raw.strip():
        return _empty_cache()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        logger.warning("[pr_shape_cache] corrupt cache file (%s); resetting", exc)
        return _empty_cache()
    if not isinstance(data, dict) or data.get("schema_version") != SCHEMA_VERSION:
        logger.warning("[pr_shape_cache] unrecognized schema; resetting")
        return _empty_cache()
    if not isinstance(data.get("entries"), dict):
        data["entries"] = {}
    return data


def _atomic_write(cache_path: Path, data: dict) -> None:
    """Write ``data`` to ``cache_path`` atomically via tmpfile + rename."""
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_path = tempfile.mkstemp(
        prefix=cache_path.name + ".",
        suffix=".tmp",
        dir=str(cache_path.parent),
    )
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(data, f, indent=2, sort_keys=True)
            f.write("\n")
        os.replace(tmp_path, cache_path)
    except Exception:
        # Clean up the tmp file on any error so the directory doesn't fill up.
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        raise


class _LockTimeoutError(Exception):
    pass


# Backwards-compatible alias for tests that imported the old name.
_LockTimeout = _LockTimeoutError


def _acquire_lock(lock_path: Path, timeout: float = LOCK_TIMEOUT_SECS) -> int:
    """Acquire an exclusive ``fcntl.flock`` on ``lock_path`` with timeout.

    Returns the open file descriptor (caller must close to release).
    Raises :class:`_LockTimeout` on timeout.
    """
    lock_path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(str(lock_path), os.O_RDWR | os.O_CREAT, 0o644)
    deadline = time.monotonic() + timeout
    while True:
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return fd
        except OSError as e:
            if e.errno not in (errno.EAGAIN, errno.EACCES):
                os.close(fd)
                raise
            if time.monotonic() >= deadline:
                os.close(fd)
                raise _LockTimeoutError
            time.sleep(0.05)


def _release_lock(fd: int) -> None:
    try:
        fcntl.flock(fd, fcntl.LOCK_UN)
    finally:
        try:
            os.close(fd)
        except OSError:
            pass


def _evict_lru(entries: dict) -> None:
    """If ``entries`` exceeds ``MAX_ENTRIES``, drop oldest by ``last_used_at``."""
    if len(entries) <= MAX_ENTRIES:
        return
    # Sort by last_used_at ascending (oldest first); break ties by key.
    items = sorted(
        entries.items(),
        key=lambda kv: (kv[1].get("last_used_at", ""), kv[0]),
    )
    keep = items[-MAX_ENTRIES:]
    entries.clear()
    entries.update(dict(keep))


# ---------------------------------------------------------------------------
# Public API.
# ---------------------------------------------------------------------------


def get_cached_verdict(
    pr: int | str,
    sha: str,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    cache_path: Path = DEFAULT_CACHE_PATH,
    lock_path: Path = DEFAULT_LOCK_PATH,
) -> dict | None:
    """Return the cached entry for ``{pr, sha, baseline_hash}`` or ``None``.

    Updates ``last_used_at`` on hit (best-effort -- a write failure is
    logged and ignored; the read still succeeds).
    """
    if not pr or not sha:
        return None
    bhash = _baseline_hash(baseline_path)
    key = _key(pr, sha, bhash)

    try:
        fd = _acquire_lock(lock_path)
    except _LockTimeoutError:
        logger.warning("[pr_shape_cache] lock timeout on get; treating as miss")
        return None

    try:
        cache = _load_cache(cache_path)
        entry = cache["entries"].get(key)
        if entry is None:
            return None
        # Touch last_used_at -- best effort.
        entry["last_used_at"] = _now_iso()
        cache["entries"][key] = entry
        try:
            _atomic_write(cache_path, cache)
        except OSError as exc:
            logger.warning("[pr_shape_cache] LRU touch write failed: %s", exc)
        return entry
    finally:
        _release_lock(fd)


def write_verdict(
    pr: int | str,
    sha: str,
    shape: str,
    verdict: dict,
    baseline_path: Path = DEFAULT_BASELINE_PATH,
    cache_path: Path = DEFAULT_CACHE_PATH,
    lock_path: Path = DEFAULT_LOCK_PATH,
) -> bool:
    """Write a verdict entry. Returns True on success, False on lock timeout."""
    if not pr or not sha:
        logger.warning("[pr_shape_cache] write rejected: empty pr or sha")
        return False
    bhash = _baseline_hash(baseline_path)
    key = _key(pr, sha, bhash)
    now = _now_iso()
    entry = {
        "pr": pr,
        "sha": sha,
        "baseline_hash": bhash,
        "shape": shape,
        "verdict": verdict,
        "classified_at": now,
        "last_used_at": now,
    }

    try:
        fd = _acquire_lock(lock_path)
    except _LockTimeoutError:
        logger.warning("[pr_shape_cache] lock timeout on write; skipping (cache miss next time)")
        return False

    try:
        # Re-read the cache fresh INSIDE the lock so concurrent writes
        # don't lose entries (Race 1).
        cache = _load_cache(cache_path)
        cache["entries"][key] = entry
        _evict_lru(cache["entries"])
        _atomic_write(cache_path, cache)
        return True
    finally:
        _release_lock(fd)


# ---------------------------------------------------------------------------
# CLI.
# ---------------------------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Per-SHA verdict cache for merge gates.")
    sub = parser.add_subparsers(dest="cmd", required=True)

    g = sub.add_parser("get", help="Look up a cached verdict")
    g.add_argument("--pr", required=True)
    g.add_argument("--sha", required=True)
    g.add_argument("--baseline", default=str(DEFAULT_BASELINE_PATH))
    g.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    g.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))

    w = sub.add_parser("write", help="Write a verdict to the cache")
    w.add_argument("--pr", required=True)
    w.add_argument("--sha", required=True)
    w.add_argument("--shape", required=True)
    w.add_argument(
        "--verdict-file",
        required=True,
        help="Path to a JSON file containing the verdict (or '-' for stdin)",
    )
    w.add_argument("--baseline", default=str(DEFAULT_BASELINE_PATH))
    w.add_argument("--cache-path", default=str(DEFAULT_CACHE_PATH))
    w.add_argument("--lock-path", default=str(DEFAULT_LOCK_PATH))

    args = parser.parse_args(argv)

    if args.cmd == "get":
        entry = get_cached_verdict(
            pr=args.pr,
            sha=args.sha,
            baseline_path=Path(args.baseline),
            cache_path=Path(args.cache_path),
            lock_path=Path(args.lock_path),
        )
        if entry is None:
            return 1  # cache miss -- non-zero so shell `|| true` patterns work
        print(json.dumps(entry, sort_keys=True))
        return 0

    if args.cmd == "write":
        if args.verdict_file == "-":
            verdict = json.load(sys.stdin)
        else:
            verdict = json.loads(Path(args.verdict_file).read_text())
        ok = write_verdict(
            pr=args.pr,
            sha=args.sha,
            shape=args.shape,
            verdict=verdict,
            baseline_path=Path(args.baseline),
            cache_path=Path(args.cache_path),
            lock_path=Path(args.lock_path),
        )
        return 0 if ok else 1

    return 2


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
