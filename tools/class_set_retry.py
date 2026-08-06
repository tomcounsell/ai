"""Bounded, observable retry for popoto class-set reads (issues #1720, #2550).

popoto's ``rebuild_indexes()`` deletes the class set (``$Class:AgentSession``)
and re-adds its members in ``batch_size=1000`` pipeline batches. A concurrent
``AgentSession.query.filter(...)`` reads ``smembers($Class:AgentSession)`` and
filters in memory, so during the delete-to-re-add window it observes an empty or
partial class set and returns nothing for a session that is very much alive.

Two properties this module exists to guarantee:

**The bound is a wall-clock budget, not an attempt count.** The retired cap was
5 attempts x 200ms, sized against a spike measured at 150 sessions. The window
grows with row count, so that cap stopped describing production the moment hosts
reached thousands of rows, and a fresh constant measured at today's row count
would go stale exactly the same way. A budget in
:class:`config.settings.TimeoutSettings` can be raised per host without a code
change.

**Exhaustion is never silent.** A cap-exhausted read returns the same ``None`` as
a genuine miss, so the caller reports "session not found" for a live session and
nothing anywhere says a retry loop gave up. :func:`log_class_set_exhaustion` is
how that stops being invisible; call it on every path where the budget runs out.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator

from config.settings import settings

logger = logging.getLogger(__name__)


def class_set_retry_attempts(
    budget_s: float | None = None, backoff_s: float | None = None
) -> Iterator[int]:
    """Yield 1-based attempt numbers, sleeping between them, until the budget is spent.

    The first attempt always yields, even with a zero budget: the retry is an
    addition to the read, never a precondition for it. Sleeps happen between
    attempts only, so a hit on the first read costs nothing.

    Usage::

        for attempt in class_set_retry_attempts():
            rows = list(Model.query.filter(...))
            if rows:
                return rows[0]
        log_class_set_exhaustion(logger, "Model.query.filter", key, attempt)
        return None
    """
    if budget_s is None:
        budget_s = settings.timeouts.class_set_retry_budget_s
    if backoff_s is None:
        backoff_s = settings.timeouts.class_set_retry_backoff_s

    deadline = time.monotonic() + budget_s
    attempt = 0
    while True:
        attempt += 1
        yield attempt
        # Only sleep if another attempt would still land inside the budget.
        if time.monotonic() + backoff_s >= deadline:
            return
        time.sleep(backoff_s)


def log_class_set_exhaustion(
    log: logging.Logger,
    reader: str,
    key: str,
    attempts: int,
    *,
    resolved_by_fallback: bool = False,
) -> None:
    """Record that a class-set read spent its whole budget and still saw nothing.

    ``resolved_by_fallback`` is the discriminator the issue asks for. A caller
    that can reach the row by a direct key lookup (``AgentSession.get_by_id``,
    which does not touch the class set) and *does* find it has proven the class
    set was mid-rebuild rather than the row being absent. That is the one case
    where "index was stale" is a fact rather than a guess, and it is logged as
    such. Without that proof the message says plainly that the two causes are
    indistinguishable, rather than asserting either.
    """
    budget_s = settings.timeouts.class_set_retry_budget_s
    if resolved_by_fallback:
        log.warning(
            "%s: class-set read for %r came back empty on all %d attempt(s) over a "
            "%.1fs budget, but a direct key lookup found the row — the class set was "
            "mid-rebuild. Raise TIMEOUTS__CLASS_SET_RETRY_BUDGET_S on this host; the "
            "rebuild window grows with row count.",
            reader,
            key,
            attempts,
            budget_s,
        )
        return
    log.warning(
        "%s: class-set read for %r came back empty on all %d attempt(s) over a %.1fs "
        "budget. This is reported to the caller as 'not found', which is "
        "indistinguishable from genuine absence — if the row does exist, the class set "
        "was still rebuilding and TIMEOUTS__CLASS_SET_RETRY_BUDGET_S is too small for "
        "this host's keyspace.",
        reader,
        key,
        attempts,
        budget_s,
    )
