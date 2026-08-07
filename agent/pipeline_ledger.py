"""PipelineLedger - durable, issue-keyed SDLC pipeline ledger (issue #2012).

The SDLC pipeline's stage/verdict/PR-number ledger historically lived on
``AgentSession.stage_states`` -- a JSON blob keyed by the *executor* (the
session doing the work). The executor is ephemeral: it crashes, completes,
gets killed, gets superseded, or gets taken over by a different session
(e.g. a foreign-slug takeover after the original driver goes terminal).
Every one of those lifecycle events was a potential state-loss event,
because the ledger lived on the thing most likely to disappear.

``PipelineLedger`` moves the ledger to the entity the pipeline is *about*:
the ``(target_repo, issue_number)`` pair. A driver session and a takeover
session working the same issue read and write the SAME ledger record --
the ledger never moves, because it never lived on either session. Write
authority over a given ledger is enforced separately, by the run_id issue
lock (see ``models/session_lifecycle.py::touch_issue_lock``) -- this model
is pure storage and does not itself gate writes.

No TTL: the ledger must survive indefinitely (unlike ``DedupRecord``'s
2-hour TTL). A ledger record persists even after its issue's PR merges and
every AgentSession that ever worked it is deleted -- see
``docs/features/sdlc-issue-keyed-stage-ledger.md``.
"""

from __future__ import annotations

import logging
import time

from popoto import Field, IntField, KeyField, Model

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Retry budgets (issue #2395)
# ---------------------------------------------------------------------------
# These constants live in this module (the lower layer) rather than in
# tools/sdlc_stage_query.py, which imports them UPWARD -- the existing
# dependency edge is tools/ -> agent/ (sdlc_stage_query.py already imports
# PipelineLedger), so defining them here and importing up preserves that
# layering instead of inverting it into a circular import.
#
# Writer budget: bounds the get_or_create() retry loop that guards the
# "genuine miss vs. racing concurrent create" window (see get_or_create's
# docstring for why this is NOT the #1720 class-set-index window). Mirrors
# the class-set retry loop in tools/class_set_retry.py in shape but is a
# distinct budget for a distinct race -- provisional/tunable.
_CREATE_RACE_RETRY_ATTEMPTS = 5
_CREATE_RACE_RETRY_BACKOFF_S = 0.20  # seconds between attempts; provisional/tunable

# Reader budget: PipelineLedger.get() never creates on a miss, so it cannot
# amortize a retry sleep the way get_or_create() can (a never-written issue
# would pay the sleep on every single router poll, forever). One attempt,
# no sleep -- provisional/tunable, but must stay small by design (see
# get()'s docstring and Risk 2 in docs/plans/sdlc_ledger_durability.md).
_READER_RETRY_ATTEMPTS = 1

# Create-lock TTL (issue #2397). A short-lived SETNX lock serializes the
# create step of get_or_create() so two concurrent callers cannot both fall
# through to create() and clobber each other (the residual TOCTOU window that
# #2395's re-load-before-create narrowed but did not close). The TTL is a
# self-heal fuse: if the lock holder crashes mid-create, the lock evaporates
# and a later caller creates the record instead of blocking forever. It must
# comfortably exceed a single create() round-trip; provisional/tunable.
_CREATE_LOCK_TTL_S = 5


def _create_lock_key(ledger_key: str) -> str:
    """Redis key for the get_or_create() create-serialization lock.

    This is a DEDICATED, NON-Popoto-managed key -- it is NOT the ledger's
    Popoto hash key and holds no model data, so SETNX/DELETE on it via the
    underlying Redis client does not touch Popoto-managed data (mirrors the
    ``worker:pop_lock:*`` lock in ``agent/session_pickup.py``). The ``sdlc:``
    prefix keeps it clearly out of Popoto's ``<ModelName>:`` keyspace.
    """
    return f"sdlc:ledger_create_lock:{ledger_key}"


def _acquire_create_lock(ledger_key: str) -> bool:
    """SETNX-acquire the create lock for ``ledger_key``. True if acquired.

    Uses Popoto's underlying Redis client (no new dependency) against a
    dedicated non-Popoto key (see :func:`_create_lock_key`). Fails OPEN: on
    any Redis error it returns ``True`` so a broker hiccup degrades to the
    pre-#2397 behavior (proceed to create) rather than blocking the pipeline.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        acquired = POPOTO_REDIS_DB.set(
            _create_lock_key(ledger_key), "1", nx=True, ex=_CREATE_LOCK_TTL_S
        )
        return bool(acquired)
    except Exception as exc:  # pragma: no cover -- defensive, broker-dependent
        logger.warning(
            "PipelineLedger create-lock acquire failed for %r (failing open): %s",
            ledger_key,
            exc,
        )
        return True


def _release_create_lock(ledger_key: str) -> None:
    """Release (DELETE) the create lock for ``ledger_key``. Best-effort.

    Operates on the dedicated non-Popoto key only; a failure is non-fatal
    because the ``_CREATE_LOCK_TTL_S`` fuse guarantees eventual release.
    """
    try:
        from popoto.redis_db import POPOTO_REDIS_DB

        POPOTO_REDIS_DB.delete(_create_lock_key(ledger_key))
    except Exception as exc:  # pragma: no cover -- defensive, broker-dependent
        logger.warning(
            "PipelineLedger create-lock release failed for %r (non-fatal): %s",
            ledger_key,
            exc,
        )


def _build_key(target_repo: str, issue_number: int) -> str:
    """Assemble the composite ``{target_repo}:{issue_number}`` ledger key.

    Callers must supply an already-resolved, non-``None`` ``target_repo``
    (resolved once at lease-acquire time and pinned on the issue lock
    payload -- see ``tools/sdlc_session_ensure.py::_acquire_run_lock_and_bind``).
    This module does not resolve or validate ``target_repo`` itself; a
    ``None`` or empty ``target_repo`` reaching here would mint a phantom
    ``None:{issue}`` key, which is exactly the failure mode Risk 5 of the
    plan guards against at the call sites (writers hard-fail, readers take
    the defined empty-ledger outcome) rather than here.
    """
    return f"{target_repo}:{issue_number}"


class PipelineLedger(Model):
    """Durable SDLC pipeline ledger, keyed by ``(target_repo, issue_number)``.

    Holds exactly what ``AgentSession.stage_states`` held before this model
    existed: the ``ALL_STAGES`` stage-status dict, the two cycle counters
    (``_patch_cycle_count``, ``_critique_cycle_count``), ``_verdicts``, and
    ``_sdlc_dispatches`` -- all serialized together as a single JSON blob in
    ``stage_states_json``, mirroring the wire format ``AgentSession.stage_states``
    already used. ``pr_number`` is a separate typed field (not embedded in
    the JSON blob) because ``AgentSession.pr_number`` is itself a field-backed
    attribute with a single writer (``sdlc-tool meta-set --key pr_number``),
    not a key inside the stage_states blob -- this model mirrors that shape.

    Fields:
        ledger_key: Composite string key ``"{target_repo}:{issue_number}"``.
            Built via :func:`_build_key`; never assembled with a ``None``
            component (see that function's docstring).
        target_repo: The GitHub ``owner/name`` slug this record belongs to,
            stored redundantly (also embedded in ``ledger_key``) so
            inspection/debugging/migration tooling can filter without
            parsing the composite key.
        issue_number: The GitHub issue number, stored redundantly for the
            same reason.
        stage_states_json: JSON-serialized dict holding the stage-status
            map plus all underscore-prefixed metadata keys. Defaults to
            ``"{}"`` for a freshly created, empty-but-valid ledger.
        pr_number: The PR number resolved for this issue's work, or
            ``None``. Field-backed, single-writer, mirrors
            ``AgentSession.pr_number``.

    No TTL (see module docstring) -- this record must outlive every
    AgentSession lifecycle event, indefinitely.
    """

    ledger_key = KeyField()
    target_repo = Field(null=True)
    issue_number = IntField(null=True)
    stage_states_json = Field(default="{}")
    pr_number = IntField(null=True)

    @classmethod
    def get_or_create(cls, target_repo: str, issue_number: int) -> PipelineLedger:
        """Return the ledger for ``(target_repo, issue_number)``, creating it if absent.

        An absent ledger is empty-but-valid, not an error: this is what lets
        ``PipelineStateMachine.for_issue()`` construct a fresh state machine
        for an issue that has never been written to before (predecessor
        backfill on first write, matching the pre-ledger session-keyed
        behavior of ``PipelineStateMachine.__init__`` on a session with no
        prior ``stage_states``).

        Existence check (issue #2395): this uses a direct-key ``cls.load(ledger_key=key)``
        -- an ``HGETALL`` on the specific record -- instead of
        ``cls.query.filter(ledger_key=key)``, which reads popoto's class-set
        index. That index is exactly what popoto's ``rebuild_indexes()``
        transiently empties (the #1720 hazard), and a false-miss there used
        to fall through to ``cls.create(...)``, which unconditionally
        overwrites ``stage_states_json`` back to ``"{}"`` -- wiping a live,
        populated ledger. ``load()`` resolves to a direct key GET
        (``query.get(db_key=...)``) and is already index-independent --
        ``agent/pipeline_state.py``'s ``_refresh_ledger`` relies on exactly
        this property today.

        The bounded retry below (``_CREATE_RACE_RETRY_ATTEMPTS`` x
        ``_CREATE_RACE_RETRY_BACKOFF_S``) does **not** exist to wait out the
        #1720 index window -- ``load()`` is immune to that window by
        construction. It exists to bridge a *different*, narrower window:
        genuine miss vs. a concurrent caller's ``create()`` landing between
        this call's ``load()`` and its own ``create()``. Do not "simplify"
        this retry away as redundant with the index-independent load -- it
        guards a distinct TOCTOU race, not #1720.

        On a hit at any attempt, the record is returned immediately -- no
        retry budget is spent past a hit. On a cap-exhausted miss, the create
        step is serialized by a short-lived SETNX lock on a dedicated
        non-Popoto key (issue #2397): exactly one caller wins the lock and
        performs the ``load()``-then-``create()`` sequence, while any
        concurrent loser waits and re-``load()``s the record the winner
        writes instead of racing its own ``create()``. This fully *closes*
        the residual create-race that #2395's re-load-before-create only
        *narrowed* -- two callers can no longer both observe ``None`` and
        both call ``create()``, so the second can no longer clobber the
        first's freshly-written ``stage_states_json``. The lock fails OPEN
        (see :func:`_acquire_create_lock`): a broker error degrades to the
        pre-#2397 behavior rather than blocking the pipeline, and a
        ``_CREATE_LOCK_TTL_S`` fuse self-heals a crashed lock holder.

        Args:
            target_repo: Already-resolved ``owner/name`` GitHub slug. Callers
                are responsible for never passing ``None``/empty here (see
                :func:`_build_key`'s docstring for why that responsibility is
                pushed to the caller rather than enforced in this model).
            issue_number: The GitHub issue number.

        Returns:
            The existing or newly created ``PipelineLedger`` record.
        """
        key = _build_key(target_repo, issue_number)

        for attempt in range(_CREATE_RACE_RETRY_ATTEMPTS):
            existing = cls.load(ledger_key=key)
            if existing is not None:
                return existing
            if attempt < _CREATE_RACE_RETRY_ATTEMPTS - 1:
                time.sleep(_CREATE_RACE_RETRY_BACKOFF_S)

        # Serialize the create step with a SETNX lock on a dedicated
        # non-Popoto key (issue #2397). This closes the residual create-race
        # that the re-load-before-create only narrowed: without it, two
        # callers could both observe None on the final load and both call
        # create(), the second clobbering the first's stage_states_json.
        lock_acquired = _acquire_create_lock(key)
        try:
            if not lock_acquired:
                # Another caller holds the create lock and is creating the
                # record right now. Wait for it to appear rather than racing
                # our own create() against it. Bounded by the same budget as
                # the miss-vs-concurrent-create retry above.
                for attempt in range(_CREATE_RACE_RETRY_ATTEMPTS):
                    existing = cls.load(ledger_key=key)
                    if existing is not None:
                        return existing
                    if attempt < _CREATE_RACE_RETRY_ATTEMPTS - 1:
                        time.sleep(_CREATE_RACE_RETRY_BACKOFF_S)
                # The lock holder never produced a record within the budget
                # (it crashed, or its TTL fuse fired). Fall through and create
                # it ourselves -- strictly no worse than the pre-#2397
                # behavior, and expected to be exceedingly rare.

            # We hold the create lock (or the holder vanished): re-load once
            # under the lock, then create only if still absent. Under the
            # lock this load-then-create sequence is atomic against other
            # get_or_create callers, so the observability point below is the
            # only place a narrowly-averted clobber could ever surface.
            existing = cls.load(ledger_key=key)
            if existing is not None:
                if existing.stage_states_json not in ("{}", "", None):
                    logger.warning(
                        "PipelineLedger.get_or_create: averted a create()-clobber on "
                        "an already-populated ledger_key=%r (residual create-race "
                        "window, issue #2395/#2397) -- stage_states_json was %r",
                        key,
                        existing.stage_states_json,
                    )
                return existing

            return cls.create(
                ledger_key=key,
                target_repo=target_repo,
                issue_number=issue_number,
                stage_states_json="{}",
            )
        finally:
            if lock_acquired:
                _release_create_lock(key)

    @classmethod
    def get(cls, target_repo: str, issue_number: int) -> PipelineLedger | None:
        """Non-mutating read: return the ledger for ``(target_repo, issue_number)``, or ``None``.

        Unlike :meth:`get_or_create`, this method never creates a record --
        callers that only want to observe current state (e.g. the router's
        ``stage-query`` poll) must use this instead of ``get_or_create``, so
        read-only polling never has the side effect of littering empty
        ledgers or racing a concurrent writer's create.

        Uses a single-attempt budget (``_READER_RETRY_ATTEMPTS = 1``, no
        sleep) rather than the writer's 5x200ms budget. The writer can
        amortize its retry sleep because a miss is followed by a create --
        the record exists on every subsequent call. This method never
        creates, so a never-written issue would miss on *every* call; if it
        inherited the writer's budget, the router's hottest path (constant
        polling) would pay up to 1000ms of retry latency per poll, forever,
        with no amortization. One attempt, return ``None`` on absence, done.

        Args:
            target_repo: Already-resolved ``owner/name`` GitHub slug.
            issue_number: The GitHub issue number.

        Returns:
            The existing ``PipelineLedger`` record, or ``None`` if absent.
        """
        key = _build_key(target_repo, issue_number)
        for _attempt in range(_READER_RETRY_ATTEMPTS):
            existing = cls.load(ledger_key=key)
            if existing is not None:
                return existing
        return None
