"""Popoto index-drift reconciliation (detect-only), model-parameterized.

On 2026-07-14 an eng session crashed with a msgpack decode failure. Afterward
``AgentSession.query.all()`` returned ``0`` with **no exception**, while 11
``AgentSession`` hashes still existed in Redis -- the status index / class set
had desynced from the actual hashes. Every observability surface (dashboard,
``valor_session list``, the worker itself) reported "zero sessions" while the
data was intact but unreachable through the index. Corruption masqueraded as
emptiness, silently.

**Root cause of that incident is now identified** (issue #2536): the crashing
session was running a popoto below the floor declared in ``pyproject.toml``,
which cannot decode the index-pointer fields an at-or-above-floor popoto writes
into each model hash. ``rebuild_indexes()`` deletes every index BEFORE it
discovers that, so it destroyed the index and rebuilt nothing. This module is
the ALARM for that outcome; the INTERLOCK that prevents it lives in
``config/popoto_floor.py`` (see
``docs/features/popoto-version-floor-guard.md``).

This module is the reconciliation guard that makes that divergence loud. For a
registered model it compares a raw bounded-SCAN count of ``<Model>:*`` hashes
against ``len(Model.query.all())``. When the hash count exceeds the queryable
count, that is "drift" -- hashes exist that the index can no longer see -- and
this module logs an ``ERROR`` and reports it to Sentry unconditionally, from
inside :func:`reconcile_model_index` itself, so the signal never depends on a
caller's own error handling swallowing it.

**Model coverage is a registry, not a hardcode.** Drift reconciliation is
parameterized by :class:`ModelDriftSpec`; the set of covered models lives in
:data:`DRIFT_COVERED_MODELS` and is extended via :func:`register_drift_model`.
``AgentSession`` registers itself at import (see the bottom of this module);
future durable Popoto models (``Room``, ``Job``) register their own spec so
drift detection never silently narrows as models are added (Risk 3 of
``docs/plans/durability-room-job-agentrun.md``).

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
from collections.abc import Callable
from dataclasses import dataclass

from agent.session_archive import _SCAN_COUNT_HINT, _SCAN_MAX_ITERATIONS

logger = logging.getLogger(__name__)


def _log_prefix_for(model_name: str) -> str:
    """Distinct, greppable Sentry/log prefix for one model's drift messages.

    Deliberately NOT matched by ``monitoring/sentry_config.py``'s
    ``drop_orphan_noise`` (which only filters the benign Popoto "one or more
    redis keys points to missing objects" diagnostic) -- these prefixes must
    always reach Sentry.
    """
    return f"[index-drift] {model_name}"


# Back-compat constant: the AgentSession log prefix. Retained as a module-level
# symbol because monitoring/non-suppression tests import it directly.
_LOG_PREFIX = _log_prefix_for("AgentSession")

# Divergence tolerance for `hash_count > queryable_count` on AgentSession.
# Provisional/tunable via env, default 0 (any positive divergence is drift)
# per this repo's magic-numbers convention.
#
# GRAIN OF SALT: this is a footgun on a should-always-be-0 invariant. Raising
# it above 0 SUPPRESSES the exact silent-empty incident class this guard
# exists to catch -- a hash that exists but is invisible to query.all(). Only
# widen it if a specific environment is proven noisy with false positives at
# tolerance 0, and prefer fixing the root cause (Risk 1: apples-to-apples
# counting) over raising this.
AGENTSESSION_INDEX_DRIFT_TOLERANCE = int(os.getenv("AGENTSESSION_INDEX_DRIFT_TOLERANCE", "0"))


@dataclass(frozen=True)
class ModelDriftSpec:
    """Declarative description of a Popoto model's drift-reconciliation shape.

    Attributes:
        name: Human/greppable model name (also the Redis key prefix by
            default) -- used to build the loud log/Sentry prefix.
        model_loader: Zero-arg callable returning the Popoto model class.
            Lazily imported so this module has no import-time model deps.
        key_prefix: Redis base-key prefix for the model's hashes. Defaults to
            ``name`` (Popoto keys models by class name).
        counter_attr: Optional name of a *module-level* function that returns
            ``(hash_count, exhaustive)`` for this model. When set,
            reconciliation resolves it by name at call time (so tests can
            monkeypatch it); when ``None`` the generic
            :func:`_count_model_hashes` is used with ``key_prefix``.
        tolerance_env: Env var name overriding the drift tolerance for this
            model. When ``None`` the tolerance is always ``default_tolerance``.
        default_tolerance: Tolerance when the env var is unset/absent.
    """

    name: str
    model_loader: Callable[[], type]
    key_prefix: str = ""
    counter_attr: str | None = None
    tolerance_env: str | None = None
    default_tolerance: int = 0

    @property
    def prefix(self) -> str:
        return self.key_prefix or self.name

    @property
    def log_prefix(self) -> str:
        return _log_prefix_for(self.name)

    def tolerance(self) -> int:
        if self.tolerance_env is None:
            return self.default_tolerance
        return int(os.getenv(self.tolerance_env, str(self.default_tolerance)))

    def count_hashes(self) -> tuple[int, bool]:
        """Count this model's raw hashes, honoring a name-resolved override.

        When ``counter_attr`` names a module-level function, that function is
        resolved from this module's globals at call time so tests patching
        ``agent.index_drift.<counter_attr>`` are honored. Otherwise the
        generic prefix-based counter runs.
        """
        if self.counter_attr is not None:
            counter = globals().get(self.counter_attr)
            if counter is not None:
                return counter()
        return _count_model_hashes(self.prefix)


#: Registry of models covered by drift reconciliation, keyed by model name.
#: Extended via :func:`register_drift_model`; iterated by
#: :func:`reconcile_all_indexes`.
DRIFT_COVERED_MODELS: dict[str, ModelDriftSpec] = {}


def register_drift_model(spec: ModelDriftSpec) -> ModelDriftSpec:
    """Register (or replace) a model spec in the drift-coverage registry.

    Idempotent by ``spec.name``: re-registering the same name overwrites the
    prior spec (import-time re-registration is a no-op change). Returns the
    spec for convenient chaining at registration sites.
    """
    DRIFT_COVERED_MODELS[spec.name] = spec
    return spec


def covered_model_names() -> tuple[str, ...]:
    """Names of every model currently covered by drift reconciliation."""
    return tuple(DRIFT_COVERED_MODELS.keys())


def _count_model_hashes(key_prefix: str) -> tuple[int, bool]:
    """Bounded SCAN counting only `hash`-type `<key_prefix>:<key>` base keys.

    Excludes companion/capped-list keys of the shape
    ``<key_prefix>:<key>::<field>`` (these are not per-record hashes; the base
    key is the source of truth for "does this record's hash exist"). Also
    excludes any non-hash key that happens to match the ``<key_prefix>*``
    prefix pattern, so the resulting count is apples-to-apples with
    ``len(Model.query.all())``.

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
            cursor=cursor, match=f"{key_prefix}*", count=_SCAN_COUNT_HINT
        )
        for key in keys:
            key_str = key.decode() if isinstance(key, bytes) else key
            if "::" in key_str:
                continue  # capped-list companion key, not a record hash
            if POPOTO_REDIS_DB.type(key) != b"hash":
                continue  # non-hash key matching the prefix pattern
            count += 1
        if cursor == 0:
            exhaustive = True
            break
    return count, exhaustive


def _count_agentsession_hashes() -> tuple[int, bool]:
    """AgentSession-specific hash counter (back-compat name / patch point).

    Delegates to :func:`_count_model_hashes` with the ``AgentSession`` prefix.
    Retained as a distinct module-level symbol because existing tests
    monkeypatch ``agent.index_drift._count_agentsession_hashes`` directly.
    """
    return _count_model_hashes("AgentSession")


def _report_loud(
    message: str,
    *,
    hash_count: int,
    queryable_count: int,
    truncated: bool,
    log_prefix: str = _LOG_PREFIX,
) -> None:
    """Emit the loud ERROR log + Sentry capture shared by every drift path."""
    logger.error("%s %s", log_prefix, message)
    try:
        import sentry_sdk

        sentry_sdk.capture_message(
            f"{log_prefix} {message}",
            level="error",
        )
    except Exception:
        # Sentry capture must never crash the caller -- the ERROR log above
        # is already the loud signal of record even if Sentry is unreachable.
        logger.warning("%s Sentry capture_message failed", log_prefix, exc_info=True)


def reconcile_model_index(spec: ModelDriftSpec) -> tuple[int, int, bool, bool]:
    """Compare a model's raw hash count to its queryable (indexed) count.

    "Drift" means a `<prefix>:<key>` hash exists in Redis that
    `Model.query.all()` cannot see -- the index/class-set has desynced from
    the underlying data. This is loud (ERROR log + Sentry capture at `error`
    level, both counts included) because it silently masquerades as "zero
    records" on every downstream observability surface -- see the module
    docstring for the 2026-07-14 incident this guard exists to catch.

    This function is DETECT-ONLY: it never calls `repair_indexes()` and never
    mutates Redis.

    Returns:
        A 4-tuple ``(hash_count, queryable_count, drifted, truncated)``:
          - ``hash_count``: raw bounded-SCAN count of `<prefix>:<key>` hashes
            (companion `::field` keys excluded).
          - ``queryable_count``: ``len(Model.query.all())``, or ``0`` if that
            call itself raised (see below).
          - ``drifted``: ``True`` iff ``hash_count > queryable_count +
            tolerance`` -- the primary incident class this guard exists to
            catch. Only ever computed when the SCAN was exhaustive; always
            ``False`` when ``truncated`` is ``True`` (a partial undercount
            must never be reported as "no drift").
          - ``truncated``: ``True`` iff the bounded SCAN hit
            `_SCAN_MAX_ITERATIONS` without reaching `cursor == 0` -- the hash
            count is a partial undercount and drift is deliberately NOT
            computed from it.

        The inverse anomaly (`hash_count < queryable_count`, stale index
        members already partially handled by `clean_indexes()`) is logged
        distinctly but does not set `drifted=True` for this tuple's contract
        (it is a different, already-mitigated incident class).

        If `Model.query.all()` itself raises (a genuinely corrupt hash), this
        function catches that internally, logs the loud ERROR + Sentry capture
        itself, and returns `(hash_count, 0, True, False)` (or
        `truncated=True` if the SCAN was also truncated) -- surfacing never
        depends on any outer caller's try/except.
    """
    log_prefix = spec.log_prefix
    tolerance = spec.tolerance()

    hash_count, exhaustive = spec.count_hashes()
    truncated = not exhaustive

    if truncated:
        logger.warning(
            "%s scan incomplete: hit iteration cap (%d) before exhausting the "
            "%s keyspace; hash_count=%d is a PARTIAL undercount, drift not computed",
            log_prefix,
            _SCAN_MAX_ITERATIONS,
            spec.name,
            hash_count,
        )

    try:
        model = spec.model_loader()
        queryable_count = len(model.query.all())
    except Exception as e:
        _report_loud(
            f"{spec.name}.query.all() raised ({e!r}) -- treating as drifted; "
            f"hash_count={hash_count}",
            hash_count=hash_count,
            queryable_count=0,
            truncated=truncated,
            log_prefix=log_prefix,
        )
        return hash_count, 0, True, truncated

    if truncated:
        # Partial hash_count must never be compared -- skip drift determination.
        return hash_count, queryable_count, False, True

    drifted = hash_count > queryable_count + tolerance
    if drifted:
        _report_loud(
            f"drift detected: hash_count={hash_count} > queryable_count="
            f"{queryable_count} (tolerance={tolerance}) -- "
            f"hashes exist that the index cannot see",
            hash_count=hash_count,
            queryable_count=queryable_count,
            truncated=False,
            log_prefix=log_prefix,
        )
    elif hash_count < queryable_count:
        # Distinct, already-partially-handled anomaly (stale index members
        # pointing at deleted hashes -- see clean_indexes()/#1459). Logged
        # distinctly so it is never confused with the primary drift class.
        logger.warning(
            "%s stale-index anomaly (not primary drift): hash_count=%d < "
            "queryable_count=%d -- index has members with no backing hash "
            "(see clean_indexes/#1459)",
            log_prefix,
            hash_count,
            queryable_count,
        )

    return hash_count, queryable_count, drifted, False


def reconcile_agent_session_index() -> tuple[int, int, bool, bool]:
    """Reconcile the ``AgentSession`` index (back-compat entry point).

    Thin wrapper over :func:`reconcile_model_index` for the registered
    ``AgentSession`` spec. Retained because callers (worker startup guard,
    ``tools/doctor.py``) and their tests import this exact name.
    """
    return reconcile_model_index(DRIFT_COVERED_MODELS["AgentSession"])


def reconcile_all_indexes() -> dict[str, tuple[int, int, bool, bool]]:
    """Reconcile every registered model's index; return per-model results.

    Iterates :data:`DRIFT_COVERED_MODELS` and runs
    :func:`reconcile_model_index` for each, returning a name -> 4-tuple map.
    A per-model failure never aborts the sweep: reconciliation is already
    internally loud + non-raising, but any unexpected escape is caught here so
    one model's corruption cannot blind the others.
    """
    results: dict[str, tuple[int, int, bool, bool]] = {}
    for name, spec in DRIFT_COVERED_MODELS.items():
        try:
            results[name] = reconcile_model_index(spec)
        except Exception:  # noqa: BLE001 -- detect-only; never let one model abort the sweep
            logger.error(
                "%s reconcile_model_index raised unexpectedly", spec.log_prefix, exc_info=True
            )
    return results


# ── Registry population ──────────────────────────────────────────────
#
# AgentSession is covered today. Room and Job register their own specs in
# Milestones 2/3 of docs/plans/durability-room-job-agentrun.md (they do not
# exist yet). Each new durable Popoto model MUST add a register_drift_model
# call so drift detection never silently narrows (Risk 3).


def _load_agent_session() -> type:
    from models.agent_session import AgentSession

    return AgentSession


register_drift_model(
    ModelDriftSpec(
        name="AgentSession",
        model_loader=_load_agent_session,
        counter_attr="_count_agentsession_hashes",
        tolerance_env="AGENTSESSION_INDEX_DRIFT_TOLERANCE",
        default_tolerance=0,
    )
)
