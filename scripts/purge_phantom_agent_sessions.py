#!/usr/bin/env python3
"""Purge phantom AgentSession hashes from Redis (issue #2207).

A phantom is an ``AgentSession:None:{uuid}:None:None:None:None`` hash holding
only Popoto ``$IndexF`` index-bookkeeping fields — every keyed field is None.
Because ``project_key`` is a required (non-null) KeyField, no legitimate
session can ever serialize to this key shape, so the SCAN pattern plus the
strict uuid regex below cannot match real data.

Deletion goes through the Popoto ORM (``instance.delete(pipeline=...)``), not
raw Redis. The one subtlety: ``AgentSession.load(db_key=...)`` on a phantom
regenerates the AutoKeyField uuid, so a naive load-then-delete targets a
freshly invented key and silently no-ops. We instead pin ``_redis_key`` on a
blank template instance so the full ORM delete flow (hash + class set +
field on_delete hooks + composite-index cleanup) targets the scanned key.

Popoto's ``delete()`` issues a handful of synchronous Redis round-trips per
call (field hooks that bypass the pipeline), so throughput is I/O-bound at
roughly 100 keys/s per thread — hence the thread pool.

Usage:
    python scripts/purge_phantom_agent_sessions.py --dry-run
    python scripts/purge_phantom_agent_sessions.py
    python scripts/purge_phantom_agent_sessions.py --max-seconds 900 --repair

Exit codes:
    0 — keyspace clean (no phantoms remain)
    3 — time budget expired with phantoms remaining (resumable: re-run)
"""

from __future__ import annotations

import argparse
import logging
import queue
import re
import sys
import threading
import time

logging.basicConfig(level=logging.INFO, format="%(message)s")
logger = logging.getLogger("purge-phantoms")

PHANTOM_PATTERN = "AgentSession:None:*:None:None:None:None"
PHANTOM_RE = re.compile(r"^AgentSession:None:[0-9a-f]{32}:None:None:None:None$")

# Provisional tuning knobs (grain of salt — adjust if throughput or memory
# pressure on a given machine demands it).
DEFAULT_THREADS = 8
DEFAULT_BATCH = 200
SCAN_COUNT = 5000
PROGRESS_EVERY = 25_000


def _decode(key: bytes | str) -> str:
    return key.decode() if isinstance(key, bytes) else str(key)


def _worker(key_queue: queue.Queue, deleted: list, lock: threading.Lock, batch_size: int) -> None:
    import redis.exceptions
    from popoto.redis_db import POPOTO_REDIS_DB

    from models.agent_session import AgentSession

    template = AgentSession()  # blank — all keyed fields None, same as a phantom
    pipe = POPOTO_REDIS_DB.pipeline()
    pending = 0
    while True:
        key = key_queue.get()
        if key is None:
            break
        # Persistence stalls (bgsave/AOF fsync) can exceed the client socket
        # timeout; a raised exception here would kill the thread and wedge the
        # queue. Drop the batch instead — the loop-until-dry outer pass
        # re-finds any keys whose deletes were lost with it.
        try:
            template._redis_key = key  # pin ORM delete to the scanned key (see module docstring)
            template.delete(pipeline=pipe)
            pending += 1
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            logger.warning(f"[purge-phantoms] transient Redis error (batch dropped): {e}")
            pipe = POPOTO_REDIS_DB.pipeline()
            pending = 0
            time.sleep(1)
            continue
        if pending >= batch_size:
            try:
                pipe.execute()
                with lock:
                    deleted[0] += batch_size
                    if deleted[0] % PROGRESS_EVERY < batch_size:
                        logger.info(f"[purge-phantoms] deleted {deleted[0]:,} so far")
            except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
                logger.warning(f"[purge-phantoms] transient Redis error (batch dropped): {e}")
                time.sleep(1)
            pipe = POPOTO_REDIS_DB.pipeline()
            pending = 0
    if pending:
        try:
            pipe.execute()
            with lock:
                deleted[0] += pending
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            logger.warning(f"[purge-phantoms] transient Redis error (final batch dropped): {e}")


def purge(
    dry_run: bool = False,
    max_seconds: float = 0,
    threads: int = DEFAULT_THREADS,
    batch_size: int = DEFAULT_BATCH,
) -> tuple[int, bool]:
    """Scan for phantoms and delete them via the ORM.

    Returns (count_processed, completed_full_scan).
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    deadline = time.monotonic() + max_seconds if max_seconds > 0 else None

    key_queue: queue.Queue = queue.Queue(maxsize=batch_size * threads * 4)
    deleted = [0]
    lock = threading.Lock()
    workers = []
    if not dry_run:
        for _ in range(threads):
            t = threading.Thread(
                target=_worker, args=(key_queue, deleted, lock, batch_size), daemon=True
            )
            t.start()
            workers.append(t)

    import redis.exceptions

    matched = 0
    cursor = 0
    completed = True
    while True:
        try:
            cursor, page = POPOTO_REDIS_DB.scan(
                cursor=cursor, match=PHANTOM_PATTERN, count=SCAN_COUNT
            )
        except (redis.exceptions.ConnectionError, redis.exceptions.TimeoutError) as e:
            logger.warning(f"[purge-phantoms] transient Redis error in scan (retrying): {e}")
            time.sleep(1)
            continue
        for raw in page:
            key = _decode(raw)
            if not PHANTOM_RE.match(key):
                logger.warning(f"[purge-phantoms] skipping non-phantom-shaped match: {key}")
                continue
            matched += 1
            if not dry_run:
                key_queue.put(key)
        if cursor == 0:
            break
        if deadline is not None and time.monotonic() > deadline:
            completed = False
            logger.info("[purge-phantoms] time budget expired — stopping scan (resumable)")
            break

    for _ in workers:
        key_queue.put(None)
    for t in workers:
        t.join()

    if dry_run:
        logger.info(f"[purge-phantoms] dry-run: {matched:,} phantom keys found")
        return matched, completed

    logger.info(
        f"[purge-phantoms] deleted {deleted[0]:,} phantom keys "
        f"({'full scan' if completed else 'partial — re-run to continue'})"
    )
    return deleted[0], completed


def repair_indexes() -> None:
    """Clear stale $IndexF members and rebuild AgentSession indexes."""
    from models.agent_session import AgentSession

    logger.info("[purge-phantoms] running AgentSession.repair_indexes() ...")
    stale, rebuilt = AgentSession.repair_indexes()
    logger.info(
        f"[purge-phantoms] repair_indexes: {stale} stale pointers cleared, "
        f"{rebuilt} sessions reindexed"
    )


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--dry-run", action="store_true", help="count phantoms, delete nothing")
    parser.add_argument(
        "--max-seconds", type=float, default=0, help="time budget; 0 = run to completion"
    )
    parser.add_argument("--threads", type=int, default=DEFAULT_THREADS)
    parser.add_argument("--batch", type=int, default=DEFAULT_BATCH)
    parser.add_argument(
        "--repair",
        action="store_true",
        help="run AgentSession.repair_indexes() after a clean full scan",
    )
    args = parser.parse_args()

    t0 = time.monotonic()
    if args.dry_run:
        count, completed = purge(dry_run=True, max_seconds=args.max_seconds)
        logger.info(f"[purge-phantoms] elapsed {time.monotonic() - t0:.0f}s")
        return 0 if (completed and count == 0) else 3

    # Loop until dry: a single SCAN pass can miss keys created mid-scan (e.g.
    # by a concurrent rebuild_indexes() re-inflating phantoms — the #2207
    # producer), so repeat full passes until one deletes nothing.
    total = 0
    while True:
        remaining = args.max_seconds - (time.monotonic() - t0) if args.max_seconds > 0 else 0
        if args.max_seconds > 0 and remaining <= 0:
            logger.info(f"[purge-phantoms] elapsed {time.monotonic() - t0:.0f}s, deleted {total:,}")
            return 3
        count, completed = purge(max_seconds=remaining, threads=args.threads, batch_size=args.batch)
        total += count
        if not completed:
            logger.info(f"[purge-phantoms] elapsed {time.monotonic() - t0:.0f}s, deleted {total:,}")
            return 3
        if count == 0:
            break

    logger.info(
        f"[purge-phantoms] keyspace dry — {total:,} deleted in {time.monotonic() - t0:.0f}s"
    )
    if total and args.repair:
        repair_indexes()
    return 0


if __name__ == "__main__":
    sys.exit(main())
