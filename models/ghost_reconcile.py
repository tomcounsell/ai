"""Reconcile-on-read for Popoto index ghost members (C3, #1817).

Background
----------
``DedupRecord`` (``Meta.ttl`` settings-backed, cursor-coupled, see
models/dedup.py) and ``AgentSession`` (``Meta.ttl=2592000``)
set a TTL on their underlying Redis *hash*. Popoto does not extend that TTL to
the secondary indexes (class set, KeyField sets, sorted sets) that reference
the hash's key. Redis SETs/ZSETs have no per-member TTL, so once the hash
expires, the index member outlives it indefinitely -- a "ghost" member whose
backing hash is gone.

What is ALREADY safe (verified empirically, see tests/unit/test_ghost_reconcile.py):
    ``Model.query.filter()`` / ``.all()`` hydrate raw index members via a
    pipelined HGETALL and silently skip any key that comes back empty
    (``popoto/models/query.py::get_many_objects``, the ``if redis_hash`` guard).
    A ghost member is therefore NEVER returned as a live record and can never
    attach stale/live data to a session that no longer exists -- the specific
    corruption scenario (an email subject-coalescing reply landing on a dead
    session) is not reachable through ``.filter()``/``.all()`` today.

What is NOT safe: the ghost member itself is never removed from the index.
Left alone it is only cleaned by the nightly ``popoto-index-cleanup``
reflection (``scripts/popoto_index_cleanup.py``, once/24h), so between hash
expiry and that sweep:
    - every query pays an extra round-trip per ghost and logs a
      ``logger.error("one or more redis keys points to missing objects")``
      line (the ghost-detection path popoto already has),
    - the index grows unboundedly for high-churn models.

Why reconcile-on-read (this module) instead of aligning member TTL to the
hash TTL: Redis SETs/ZSETs have no per-member expiry primitive, so "TTL the
index members" would require re-modeling every index as a ZSET keyed by
expiry and a periodic ZREMRANGEBYSCORE sweep -- strictly more code and risk
than reusing the SCAN-based, production-safe primitive popoto already ships
(``Model.clean_indexes()``, the same one the nightly reflection calls).
Reconcile-on-read just moves that primitive earlier: instead of waiting up to
24h for the scheduled sweep, a ghost-prone read path (dedup lookups, email
subject-coalescing) triggers it inline, rate-limited so a hot path never pays
a full SCAN on every call.

Why ``Model.clean_indexes()`` and not hand-rolled SREM/ZREM: this repo's
convention is "never touch Popoto-managed keys with raw Redis calls -- always
go through the ORM" (see CLAUDE.md). ``clean_indexes()`` IS the ORM's
sanctioned cleanup primitive; using it here (rather than reimplementing
index-member removal by hand) keeps that invariant intact even for cleanup
code.
"""

import logging
import time

logger = logging.getLogger(__name__)

# Minimum seconds between clean_indexes() sweeps for a given model. Bounds
# the cost added to a read path: a busy loop calling reconcile_ghost_members()
# many times per second still pays the SCAN at most once per interval.
_RECONCILE_MIN_INTERVAL_SECONDS = 60.0

# Module-level, in-process rate-limit cache: {model_name: last_reconciled_ts}.
# In-process (not Redis-backed) is intentional -- this is a best-effort
# frequency cap, not a correctness lock; multiple workers each reconciling at
# most once/interval is fine (clean_indexes() is idempotent and SCAN-based).
_last_reconciled: dict[str, float] = {}


def reconcile_ghost_members(
    model_class, *, min_interval: float = _RECONCILE_MIN_INTERVAL_SECONDS
) -> int:
    """Best-effort, rate-limited ghost-member reconciliation for a Popoto model.

    Call this from read paths that are prone to TTL-outlived index members
    (e.g. before/after a ``.filter()`` in dedup lookups or session
    subject-coalescing). It is safe to call on every read: the rate limit
    makes repeated calls within ``min_interval`` seconds a no-op.

    Never raises -- failures are logged and swallowed, since this is a
    hygiene pass, not part of the read's correctness contract (the read
    itself is already ghost-safe; see module docstring).

    Returns:
        Number of orphaned index entries removed. 0 if skipped due to the
        rate limit or if none were found.
    """
    name = getattr(getattr(model_class, "_meta", None), "model_name", None) or getattr(
        model_class, "__name__", "unknown"
    )
    now = time.time()
    last = _last_reconciled.get(name, 0.0)
    if now - last < min_interval:
        return 0
    _last_reconciled[name] = now

    try:
        removed = model_class.clean_indexes()
    except Exception as e:
        logger.warning(f"[ghost-reconcile] {name}: clean_indexes() failed (non-fatal): {e}")
        return 0

    if removed:
        logger.info(f"[ghost-reconcile] {name}: removed {removed} orphaned index entr(y/ies)")
    return removed or 0
