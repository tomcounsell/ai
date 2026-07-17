"""AgentSession index-drift reconciliation (detect-only).

On 2026-07-14 an eng session crashed with a msgpack decode failure. Afterward
``AgentSession.query.all()`` returned ``0`` with **no exception**, while 11
``AgentSession`` hashes still existed in Redis -- the status index / class set
had desynced from the actual hashes. Every observability surface (dashboard,
``valor_session list``, the worker itself) reported "zero sessions" while the
data was intact but unreachable through the index. Corruption masqueraded as
emptiness, silently.

This module is the reconciliation guard that makes that divergence loud. It
compares a raw bounded-SCAN count of ``AgentSession:*`` hashes against
``len(AgentSession.query.all())``. When the hash count exceeds the queryable
count, that is "drift" -- hashes exist that the index can no longer see -- and
this module logs an ``ERROR`` and reports it to Sentry unconditionally, from
inside :func:`reconcile_agent_session_index` itself, so the signal never
depends on a caller's own error handling swallowing it.

**This module is DETECT-ONLY.** It never calls ``repair_indexes()`` or
mutates Redis in any way. Repair (and the non-atomic class-set-empty window
it opens -- see issue #1720) is owned by a separate effort
(``docs/plans/session-recovery-observation-audit.md``). Calling repair from
here would risk firing that non-atomic window any time ``python -m
tools.doctor`` runs while the worker/dashboard are serving, reproducing the
exact silent-empty incident this module exists to catch.
"""

from __future__ import annotations

import logging
import os

from agent.session_archive import _SCAN_COUNT_HINT, _SCAN_MAX_ITERATIONS

logger = logging.getLogger(__name__)

# Distinct, greppable prefix for every loud log/Sentry message this module
# emits. Deliberately NOT matched by monitoring/sentry_config.py's
# drop_orphan_noise (which only filters the benign Popoto "one or more redis
# keys points to missing objects" diagnostic) -- this prefix must always
# reach Sentry.
_LOG_PREFIX = "[index-drift] AgentSession"

# Divergence tolerance for `hash_count > queryable_count`. Provisional/tunable
# via env, default 0 (any positive divergence is drift) per this repo's
# magic-numbers convention.
#
# GRAIN OF SALT: this is a footgun on a should-always-be-0 invariant. Raising
# it above 0 SUPPRESSES the exact silent-empty incident class this guard
# exists to catch -- a hash that exists but is invisible to query.all(). Only
# widen it if a specific environment is proven noisy with false positives at
# tolerance 0, and prefer fixing the root cause (Risk 1: apples-to-apples
# counting) over raising this.
AGENTSESSION_INDEX_DRIFT_TOLERANCE = int(os.getenv("AGENTSESSION_INDEX_DRIFT_TOLERANCE", "0"))


def _count_agentsession_hashes() -> tuple[int, bool]:
    """Bounded SCAN counting only `hash`-type `AgentSession:<key>` base keys.

    Excludes companion/capped-list keys of the shape
    ``AgentSession:<key>::<field>`` (these are not per-session hashes; the
    base key is the source of truth for "does this session's hash exist").
    Also excludes any non-hash key that happens to match the ``AgentSession*``
    prefix pattern, so the resulting count is apples-to-apples with
    ``len(AgentSession.query.all())``.

    Returns:
        ``(count, exhaustive)`` where ``exhaustive`` is ``True`` iff the SCAN
        reached ``cursor == 0`` within ``_SCAN_MAX_ITERATIONS`` cursor
        advances (i.e. it saw the entire matching keyspace), and ``False`` if
        it was truncated by the iteration cap.
    """
    from popoto.redis_db import POPOTO_REDIS_DB

    count = 0
    cursor = 0
    exhaustive = False
    for _ in range(_SCAN_MAX_ITERATIONS):
        cursor, keys = POPOTO_REDIS_DB.scan(
            cursor=cursor, match="AgentSession*", count=_SCAN_COUNT_HINT
        )
        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            if "::" in key_str:
                continue  # capped-list companion key, not a session hash
            if POPOTO_REDIS_DB.type(key) != b"hash":
                continue  # non-hash key matching the prefix pattern
            count += 1
        if cursor == 0:
            exhaustive = True
            break
    return count, exhaustive


def _report_loud(message: str, *, hash_count: int, queryable_count: int, truncated: bool) -> None:
    """Emit the loud ERROR log + Sentry capture shared by every drift path."""
    logger.error("%s %s", _LOG_PREFIX, message)
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            f"{_LOG_PREFIX} {message}",
            level="error",
        )
    except Exception:
        # Sentry capture must never crash the caller -- the ERROR log above
        # is already the loud signal of record even if Sentry is unreachable.
        logger.warning("%s Sentry capture_message failed", _LOG_PREFIX, exc_info=True)


def reconcile_agent_session_index() -> tuple[int, int, bool, bool]:
    """Compare raw AgentSession hash count to the queryable (indexed) count.

    "Drift" means a `AgentSession:<key>` hash exists in Redis that
    `AgentSession.query.all()` cannot see -- the index/class-set has desynced
    from the underlying data. This is loud (ERROR log + Sentry capture at
    `error` level, both counts included) because it silently masquerades as
    "zero sessions" on every downstream observability surface (dashboard,
    `valor_session list`, the worker's own queue) -- see the module docstring
    for the 2026-07-14 incident this guard exists to catch.

    This function is DETECT-ONLY: it never calls `repair_indexes()` and never
    mutates Redis.

    Returns:
        A 4-tuple ``(hash_count, queryable_count, drifted, truncated)``:
          - ``hash_count``: raw bounded-SCAN count of `AgentSession:<key>`
            hashes (companion `::field` keys excluded).
          - ``queryable_count``: ``len(AgentSession.query.all())``, or ``0``
            if that call itself raised (see below).
          - ``drifted``: ``True`` iff ``hash_count > queryable_count +
            AGENTSESSION_INDEX_DRIFT_TOLERANCE`` -- the primary incident
            class this guard exists to catch. Only ever computed when the
            SCAN was exhaustive; always ``False`` when ``truncated`` is
            ``True`` (a partial undercount must never be reported as "no
            drift").
          - ``truncated``: ``True`` iff the bounded SCAN hit
            `_SCAN_MAX_ITERATIONS` without reaching `cursor == 0` -- the hash
            count is a partial undercount and drift is deliberately NOT
            computed from it.

        The inverse anomaly (`hash_count < queryable_count`, stale index
        members already partially handled by `clean_indexes()`) is logged
        distinctly but does not set `drifted=True` for this tuple's contract
        (it is a different, already-mitigated incident class).

        If `AgentSession.query.all()` itself raises (a genuinely corrupt
        hash), this function catches that internally, logs the loud ERROR +
        Sentry capture itself, and returns `(hash_count, 0, True, False)` (or
        `truncated=True` if the SCAN was also truncated) -- surfacing never
        depends on any outer caller's try/except.
    """
    hash_count, exhaustive = _count_agentsession_hashes()
    truncated = not exhaustive

    if truncated:
        logger.warning(
            "%s scan incomplete: hit iteration cap (%d) before exhausting the "
            "AgentSession keyspace; hash_count=%d is a PARTIAL undercount, drift "
            "not computed",
            _LOG_PREFIX,
            _SCAN_MAX_ITERATIONS,
            hash_count,
        )

    try:
        from models.agent_session import AgentSession

        queryable_count = len(AgentSession.query.all())
    except Exception as e:
        _report_loud(
            f"AgentSession.query.all() raised ({e!r}) -- treating as drifted; "
            f"hash_count={hash_count}",
            hash_count=hash_count,
            queryable_count=0,
            truncated=truncated,
        )
        return hash_count, 0, True, truncated

    if truncated:
        # Partial hash_count must never be compared -- skip drift determination.
        return hash_count, queryable_count, False, True

    drifted = hash_count > queryable_count + AGENTSESSION_INDEX_DRIFT_TOLERANCE
    if drifted:
        _report_loud(
            f"drift detected: hash_count={hash_count} > queryable_count="
            f"{queryable_count} (tolerance={AGENTSESSION_INDEX_DRIFT_TOLERANCE}) -- "
            f"hashes exist that the index cannot see",
            hash_count=hash_count,
            queryable_count=queryable_count,
            truncated=False,
        )
    elif hash_count < queryable_count:
        # Distinct, already-partially-handled anomaly (stale index members
        # pointing at deleted hashes -- see clean_indexes()/#1459). Logged
        # distinctly so it is never confused with the primary drift class.
        logger.warning(
            "%s stale-index anomaly (not primary drift): hash_count=%d < "
            "queryable_count=%d -- index has members with no backing hash "
            "(see clean_indexes/#1459)",
            _LOG_PREFIX,
            hash_count,
            queryable_count,
        )

    return hash_count, queryable_count, drifted, False
