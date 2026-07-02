"""Lease-based ownership for the global worker concurrency slot.

**Problem this replaces (issue #1820).** The prior primitive was a raw,
*ownerless* ``asyncio.Semaphore``: a permit was acquired by the worker loop
and could only ever be released by that same loop's ``finally`` block. When a
session was killed **out of band** (health-check progress-kill, per-tool tier
timeout), its DB row flipped to terminal but the permit stayed held by the
parked worker loop — nothing else could release it. Permits leaked one at a
time until the worker stopped picking up new work (the #1537/#1808 leak
class).

``SlotLeaseRegistry`` fixes this by keying the slot to an *owner*
(``owner_session_id``), so any actor — the reaper, an out-of-band killer, or
the owning worker loop itself — can release the permit idempotently.

**Lease-at-bind-only (the "unbound-permit simplification").** A ``Lease`` is
recorded ONLY when ``bind()`` is called — never at ``acquire()``. There is no
separate "unbound permit" tally or token system to leak or reap. This works
because of the structure of the worker loop:

  1. ``acquire()`` decrements the wrapped semaphore (so ``permits_free()``
     stays accurate) and records nothing.
  2. Every branch where the loop fails to resolve a session (the pop returns
     ``None`` or raises, *before* a lease exists) calls ``release_unbound()``
     — a raw semaphore release with no lease bookkeeping.
  3. ``bind()`` is called synchronously immediately after a non-``None`` pop,
     with **no intervening await** between the resolving pop and the bind
     call.

  Consequently, an acquired-but-unbound permit can never leak: during the
  ``await _pop_agent_session(...)`` gap the permit is legitimately in use
  and simply absent from the lease map (so a reaper iterating only bound
  leases never observes it), and immediately after the pop resolves the
  permit is either given back (``release_unbound()``) or bound. There is no
  window in which a permit is "acquired but forgotten."

**No reclaim deadline.** ``Lease`` deliberately carries no
``deadline``/TTL field. An earlier revision of this design reclaimed any
lease older than ``acquired_at + SLOT_LEASE_TTL_S``. That is wrong: a fixed,
never-reset wall-clock deadline would strip the permit from a still-running,
*progressing* owner while its execution task keeps running, causing
semaphore over-admission (concurrently-running sessions > max) and
re-imposing exactly the wall-clock duration cap issue #1172 deliberately
removed. The reaper (``session_health.py``) reclaims on **terminal-owner
status only** — never on elapsed time. Live-but-stuck sessions are the
concern of the progress-deadline cancel scope (issue #1820 Fix #3), the
per-tool timeout loop, and the worker-dead scan — none of which need a bare
wall-clock slot reclaim.

**On-loop-only mutation.** All registry state (``_held`` and the wrapped
``asyncio.Semaphore``) is mutated exclusively on the asyncio event loop —
by the worker loop (acquire/bind/release) and by the health-check reaper,
which is a different *task* on the *same* loop, not an off-loop thread. No
lock is needed: ``asyncio.Semaphore`` is loop-affine (releasing it from a
non-loop thread is undefined behavior), and every mutating method here
contains no ``await``, so each call runs to completion atomically with
respect to the cooperative scheduler — no other coroutine can observe a
partially-updated ``_held`` dict or permit count. If a genuinely
cross-thread signal is ever needed, the caller must marshal it onto the loop
via ``loop.call_soon_threadsafe`` rather than mutating the registry directly.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass
class Lease:
    """A bound claim on one concurrency slot.

    Recorded only by ``SlotLeaseRegistry.bind()`` — never at ``acquire()``
    (see the module docstring's "lease-at-bind-only" note).

    ``acquired_at`` is retained purely as the progress-timestamp fallback for
    the progress-deadline watcher (issue #1820 Fix #3): when no other
    progress signal is present, ``acquired_at`` still gives a well-defined
    deadline baseline. It is intentionally the ONLY timestamp on this
    dataclass — there is no reclaim ``deadline`` field. The reaper never
    reads ``acquired_at`` for reclaim decisions; it keys on the owner's DB
    status being terminal (see the module docstring's "no reclaim deadline"
    note).
    """

    owner_session_id: str
    acquired_at: float


class SlotLeaseRegistry:
    """Owner-keyed wrapper around one ``asyncio.Semaphore`` for slot backpressure.

    Preserves the exact counting-semaphore backpressure contract the raw
    ``asyncio.Semaphore`` provided (the worker loop still blocks at
    ``acquire()`` when the ceiling is full) while adding an explicit,
    reclaimable owner for every held permit.

    All mutation is on-loop only — see the module docstring. No internal
    lock is used or needed.
    """

    def __init__(self, max_concurrent: int) -> None:
        """Create a registry wrapping a semaphore with ``max_concurrent`` permits."""
        self._semaphore = asyncio.Semaphore(max_concurrent)
        self._max_concurrent = max_concurrent
        # Bound leases only, keyed by owner_session_id. A permit acquired but
        # not yet bound is deliberately absent from this map — see the
        # module docstring's "lease-at-bind-only" note.
        self._held: dict[str, Lease] = {}

    async def acquire(self) -> None:
        """Await one permit from the wrapped semaphore. Records no lease.

        The caller resolves what it acquired the permit *for* afterward
        (typically an ``await _pop_agent_session(...)`` call) and either
        gives the permit back via ``release_unbound()`` (pop returned
        ``None`` / raised) or claims it via ``bind()`` (pop resolved a
        session). See the module docstring for why this ordering can never
        leak an unbound permit.
        """
        await self._semaphore.acquire()

    def release_unbound(self) -> None:
        """Give back a permit that was acquired but never bound to an owner.

        Used on every branch where the pop that was supposed to follow
        ``acquire()`` returned ``None`` or raised, *before* ``bind()`` was
        called. Raw semaphore release — there is no lease to remove because
        none was ever recorded.
        """
        self._semaphore.release()

    def bind(self, owner_session_id: str) -> None:
        """Record a lease for ``owner_session_id`` against an already-acquired permit.

        Called exactly once per session, synchronously, immediately before
        the session begins executing — with no intervening ``await`` between
        the resolving pop and this call. Overwrites any stale prior lease
        for the same owner (should not normally occur; idempotent bind is a
        defensive no-surprise choice, not a relied-upon behavior).
        """
        self._held[owner_session_id] = Lease(
            owner_session_id=owner_session_id, acquired_at=time.time()
        )

    def release(self, owner_session_id: str) -> None:
        """Release the permit held by ``owner_session_id``. Idempotent no-op if unheld.

        The lease map is the single source of truth gating the underlying
        ``semaphore.release()`` call: a double-release or a release for an
        owner with no recorded lease can never over-release the permit,
        because the second (or unknown-owner) call finds nothing to pop and
        does nothing. This matters because both the owning worker loop's
        ``finally`` block and the health-check reaper may call this (or
        ``reclaim()``) for the same owner — whichever runs first wins, the
        other silently no-ops.
        """
        lease = self._held.pop(owner_session_id, None)
        if lease is not None:
            self._semaphore.release()

    def reclaim(self, owner_session_id: str) -> None:
        """Reclaim a leaked slot for ``owner_session_id``. Idempotent; logs a WARNING.

        Semantically identical to ``release()`` (same idempotency guarantee)
        plus a WARNING log naming the owner and how long it held the slot —
        this is the operator-visible signal that the self-heal fired. Called
        by the health-check reap pass (terminal-owner leases only) and by
        ``_apply_recovery_transition`` immediately after an out-of-band kill
        flips a session's row terminal, so the slot frees on the
        health/tool-timeout cadence instead of waiting for the next reap tick.
        """
        lease = self._held.pop(owner_session_id, None)
        if lease is not None:
            self._semaphore.release()
            logger.warning(
                "[slot-lease] Reclaimed leaked slot for owner_session_id=%s (held %.1fs)",
                owner_session_id,
                time.time() - lease.acquired_at,
            )

    def leases(self) -> list[Lease]:
        """Return the currently held leases (a snapshot list, safe to iterate)."""
        return list(self._held.values())

    def permits_free(self) -> int:
        """Return the number of free permits on the wrapped semaphore.

        Reads the private ``asyncio.Semaphore._value`` attribute — the same
        accessor the (now-deleted) logging-only leaked-slot fingerprint used.
        There is no public API for this on ``asyncio.Semaphore``; it is the
        only accurate source for "how many permits are currently free."
        """
        return self._semaphore._value
