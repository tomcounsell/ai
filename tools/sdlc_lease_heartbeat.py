"""Detached lease-heartbeat renewer for the pure-local ``/do-sdlc`` supervisor.

Issue #2446/#2451: a worker-run pipeline keeps its per-issue lease
(``session:issuelock:{N}``) alive via the worker's in-process 60s tick
(``agent/session_executor.py::_tick_issue_lock_renewal``). The pure-local
``/do-sdlc`` supervisor -- a per-turn ``claude -p`` subprocess that is BLOCKED
inside a synchronous stage call and makes no mid-stage sdlc-tool writes -- has
no equivalent renewer, so its lease can lapse mid-run and force a re-mint. This
module is that missing renewer, launched detached by
``tools/sdlc_session_ensure._acquire_run_lock_and_bind`` on a fresh local mint.

**Peek-first, renew-only (the load-bearing safety property).**
A DEFAULT ``touch_issue_lock`` call passing a ``run_id`` against an *absent*
key does ``SET NX EX`` and makes the caller the owner. A naive renew loop
would therefore let a zombie heartbeat *re-acquire* a lapsed lease under its
stale id and block a legitimate successor with ``ISSUE_LOCKED`` -- the exact
lease-theft Risk 2 forbids. Two independent guards close that:

**The extend is ``renew_only=True``** (issue #2714), which skips both of
``touch_issue_lock``'s minting branches and writes through a compare-and-set
Lua script. This is the structural half: minting is impossible for this
caller, not merely avoided by convention, so even a release landing between
the peek and the extend cannot be undone by the extend.

**Every tick PEEKS first**
(``touch_issue_lock(issue, run_id, peek=True)`` -- a peek never mutates,
whatever run_id it carries) and:

- ``peek.owner_run_id is None`` (lease absent/lapsed) -> EXIT 0 immediately.
- ``peek.owner_run_id != run_id`` (a successor owns it) -> EXIT 0 immediately.
- ``peek.owner_run_id == run_id`` -> ONLY THEN call the mutating
  ``touch_issue_lock(issue, run_id, ..., renew_only=True)`` to extend the TTL.

This is deliberately an OWNERSHIP check, never a pid-liveness one (issue
#2537 review): the payload's ``pid`` is stamped by the short-lived
``sdlc-tool session-ensure`` CLI at acquire time and is dead before this
detached heartbeat's first tick, so a pid-keyed ``orphaned_lock`` signal
would read every locally-minted lease as orphaned and must not gate the
renew. The shape mirrors ``touch_issue_lock``'s own "no run_id supplied:
never mutates" special case at the caller layer: the heartbeat can only ever
EXTEND a lease it already owns, never mint on a free key nor steal one from a
successor.

**``renewer_pid`` is the exception, and it is the whole point of issue
#2648.** That claim about the payload's ``pid`` holds for ``pid`` alone. This
heartbeat is one of exactly two DURABLE renewers -- processes that live as
long as the run does -- so its renewals also stamp ``renewer_pid`` +
``renewer_create_time``, which name a process that is ALIVE for as long as the
lease deserves to be. That is what makes the lease's pid evidence checkable at
all, and it is why ``_lock_owner_is_live`` can treat a fresh renewal stamp as
corroborating rather than conclusive: a run that renewed once and then died no
longer reads live for the full freshness window. The distinction is load-
bearing in both directions -- gating the renew on ``pid`` would be the #2620
regression, while never stamping ``renewer_pid`` would leave #2648 unfixed.

**Run liveness is the SUPERVISOR's liveness (issue #2714).** The heartbeat's
own existence proves nothing about the run: a supervisor killed mid-stage
leaves this loop renewing a lease for a pipeline nobody is driving, and every
subsequent attempt on that issue reads ``ISSUE_LOCKED``. So the supervising
``claude`` process's identity -- ``(pid, create_time)``, resolved by
``tools/sdlc_supervisor_identity.py`` at mint time and handed over on the
argv -- is polled every ``SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS``. On
``SDLC_SUPERVISOR_DEATH_CONFIRMATIONS`` CONSECUTIVE positive-death
observations the lease is released, the supervised-run signal cleared, and
the loop exits. The evidence bar is deliberately asymmetric: a pid psutil
cannot find, or one whose ``create_time`` no longer matches within ``1e-3``
(pid recycling), counts as dead -- but ANY exception counts as NOT dead, so
an unverifiable probe fails toward HOLDING the lease (#2446).

The parent pid is never consulted. ``start_new_session=True`` detaches this
process at spawn and its spawner (the ephemeral ``session-ensure`` CLI) exits
seconds later, so a HEALTHY heartbeat is reparented to pid 1 almost at once;
reading that as death would drop a live run's lease on tick one.

**Two bounds, chosen by evidence quality.** When a supervisor identity was
recorded, the loop's ceiling is the unchanged 4h ``MAX_LIFETIME_SECONDS`` --
the supervisor watch is the real death detector and a long BUILD stage must
not lapse its own lease. When no identity could be resolved, the ceiling
drops to the 90-minute ``UNSUPERVISED_MAX_LIFETIME_SECONDS`` and the loop
stops renewing WITHOUT releasing: a failure to resolve a supervisor is not
positive proof the run is dead, so the lease's own 1800s TTL is the correct
disposition. The two are mutually exclusive, never stacked.

**The supervised-run signal renews with the lease (issue #2659).** A renewed
tick also refreshes ``session:supervisedrun:{N}``
(``agent/supervised_run.py``), the companion key a stage fork reads to inherit
the supervisor's ``run_id``. It carries the lease TTL but used to be written
only at acquire, so it expired 1800s into every pipeline while this loop kept
the lease alive indefinitely; from that point every stage fork the supervisor
dispatched read a bare ``ISSUE_LOCKED`` from its own supervisor's lock and
stood down. The two keys now share this loop's lifetime, so the signal can
never outlive the lease and the lease can never outlive the signal by more
than one renewal interval.

**Cadence is split.** The loop wakes every
``SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS`` to poll the supervisor but only
peeks and renews once ``--interval`` seconds of sleep have accumulated. Crash
detection tightens to ~2 minutes without changing Redis renew load.

**Observability.** ``main()`` configures INFO logging unconditionally and
every decision is an INFO record in ``logs/sdlc_lease_heartbeat.log``: one
startup line naming the supervisor source, pid and both cadences, and one
exit line naming the reason (``supervisor_dead``,
``unsupervised_max_lifetime``, ``lease_lost``, ``foreign_owner``,
``max_lifetime``).

Best-effort throughout: every tick is wrapped in try/except; a Redis hiccup is
swallowed and retried next tick.

Usage::

    python -m tools.sdlc_lease_heartbeat --issue-number 2446 --run-id <hex> \
        --session-id sdlc-local-2446 \
        --supervisor-pid 32886 --supervisor-create-time 1755069402.5

Exit codes:
    0 -- always (lost ownership, supervisor died, ceiling reached, or clean
    shutdown).
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
import time

logger = logging.getLogger(__name__)


def _env_int(name: str, default: int) -> int:
    """A positive int from the environment, falling back to ``default``.

    A missing, non-numeric, zero, or negative value yields ``default`` rather
    than raising or selecting a degenerate zero-length bound -- a typo in an
    override must never be the thing that lapses a live run's lease.
    """
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


# Upper bound on how long a SUPERVISED heartbeat runs before self-exiting
# (issue #2446/#2451). With a resolvable supervisor the process watch is the
# real death detector, so this is only a backstop -- and it is deliberately
# NOT lowered (issue #2714): a live supervisor's BUILD stage can exceed two
# hours, and lapsing its lease mid-stage would reintroduce #2446 to buy
# tidiness.
# GRAIN OF SALT: 4h (14400s) is PROVISIONAL/TUNABLE -- generously above the
# longest observed end-to-end local run, low enough to bound a zombie. Override
# via the SDLC_LEASE_HEARTBEAT_MAX_LIFETIME_SECONDS env var.
MAX_LIFETIME_SECONDS = _env_int("SDLC_LEASE_HEARTBEAT_MAX_LIFETIME_SECONDS", 4 * 60 * 60)

# Upper bound when NO supervisor identity could be resolved (issue #2714 L3).
# Applies only on that path: unresolvable means the heartbeat has no death
# detector at all, so a tighter ceiling is the whole safety story. It stops
# renewing but never releases, so the worst case is this bound plus the lock's
# own 1800s TTL.
# GRAIN OF SALT: 90 minutes (5400s) is PROVISIONAL/TUNABLE -- above a typical
# end-to-end local pipeline, far below the 4h supervised ceiling. Override via
# the SDLC_HEARTBEAT_UNSUPERVISED_MAX_SECONDS env var.
UNSUPERVISED_MAX_LIFETIME_SECONDS = _env_int("SDLC_HEARTBEAT_UNSUPERVISED_MAX_SECONDS", 90 * 60)

# How often the loop wakes to poll the supervising process. Decoupled from the
# renew cadence so crash detection is ~2 minutes (two confirmations) instead of
# one full renew interval, at no extra Redis cost.
# GRAIN OF SALT: 60s is PROVISIONAL/TUNABLE -- matches the worker's own
# in-process renewal tick. Override via SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS.
SUPERVISOR_CHECK_INTERVAL_SECONDS = _env_int("SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS", 60)

# Consecutive positive-death observations required before the lease is
# released. One psutil flake must never drop a live run's lease; a genuinely
# dead supervisor stays dead, so the only cost of >1 is detection latency.
# GRAIN OF SALT: 2 is PROVISIONAL/TUNABLE. Override via
# SDLC_SUPERVISOR_DEATH_CONFIRMATIONS.
SUPERVISOR_DEATH_CONFIRMATIONS = _env_int("SDLC_SUPERVISOR_DEATH_CONFIRMATIONS", 2)

# Exit reasons, each emitted verbatim on the single INFO exit line.
EXIT_SUPERVISOR_DEAD = "supervisor_dead"
EXIT_UNSUPERVISED_MAX_LIFETIME = "unsupervised_max_lifetime"
EXIT_LEASE_LOST = "lease_lost"
EXIT_FOREIGN_OWNER = "foreign_owner"
EXIT_MAX_LIFETIME = "max_lifetime"


def _default_interval() -> int:
    """Renew cadence: a third of the lease TTL, so ~3 renews per TTL window.

    Read lazily (not at import) so a test/env override of ISSUE_LOCK_TTL_SECONDS
    is honored. Floored at 1s to guarantee forward progress.
    """
    from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS

    return max(1, ISSUE_LOCK_TTL_SECONDS // 3)


def _resolved_supervisor(pid, create_time) -> tuple[int | None, float | None]:
    """Normalize a recorded supervisor identity, or ``(None, None)``.

    Both halves are required: a pid without a ``create_time`` cannot be
    distinguished from a recycled pid, so a partial identity is no identity.
    A zero, negative, or non-numeric value is likewise UNRESOLVED -- never
    "dead". Nothing about a malformed argv is evidence that a supervisor died.
    """
    if pid is None or create_time is None:
        return (None, None)
    try:
        pid_int = int(str(pid).strip())
        create_time_float = float(str(create_time).strip())
    except (TypeError, ValueError):
        return (None, None)
    if pid_int <= 0:
        return (None, None)
    return (pid_int, create_time_float)


def _supervisor_is_dead(pid: int, create_time: float) -> bool:
    """True only on POSITIVE evidence that the supervising process is gone.

    Mirrors ``models/session_lifecycle.py::_lock_owner_is_live``'s proven
    shape:

    - psutil cannot find the pid -> dead.
    - psutil finds it but its ``create_time`` differs by more than ``1e-3``
      -> the OS recycled the pid onto an unrelated process -> dead (Risk 5;
      the guard fails toward detection here, which is the safe direction).
    - Any exception -> NOT dead. An unverifiable probe must fail toward
      HOLDING the lease (#2446): a lapsed lease under a live supervisor is
      the worse failure, and the caller's confirmation gate plus the lifetime
      ceiling both remain as backstops.
    """
    try:
        from agent.session_health import _psutil_process_for_pid

        proc = _psutil_process_for_pid(pid)
        if proc is None:
            return True
        return abs(float(proc.create_time()) - float(create_time)) > 1e-3
    except Exception as e:  # noqa: BLE001 - unverifiable means "not dead", never "dead"
        logger.debug(
            "sdlc_lease_heartbeat: supervisor liveness probe for pid=%s failed "
            "(%s: %s) -- treating as alive",
            pid,
            type(e).__name__,
            e,
        )
        return False


def _log_exit(reason: str, issue_number: int, run_id: str, detail: str = "") -> int:
    """Emit the single INFO exit line naming ``reason``. Always returns 0."""
    logger.info(
        "sdlc_lease_heartbeat: exiting issue #%s run_id=%s reason=%s%s",
        issue_number,
        run_id,
        reason,
        f" ({detail})" if detail else "",
    )
    return 0


def _clear_renewer_identity_at_exit(issue_number: int, run_id: str, session_id: str = "") -> None:
    """Drop this heartbeat's recorded renewer identity from the lease payload.

    Called at the two deadline exits that deliberately do NOT release (issue
    #2648, spike-5 shape (a)). Those exits already reason in prose that
    failing to resolve a supervisor is not positive proof the run is dead --
    this applies that same judgement to the payload. A retiring renewer that
    left its pid behind would otherwise be read as a DEAD run once the grace
    window lapsed, and the orphan reaper has no idle-time gate on the
    payload-present path, so a live lane would be reaped mid-stage. With the
    group cleared the payload falls back to the untouched renewal-freshness
    short-circuit and the verdict is exactly what it is today.

    Bounded to TWO attempts. The write is a compare-and-set against the bytes
    just read, so a benign same-run_id CLI renewal landing inside the window
    makes attempt one a no-op -- and unlike the tick loop, which notices a
    lost CAS via EXIT_LEASE_LOST, nothing downstream would notice this one.
    A genuine successor carries a different run_id and fails both attempts,
    which is the guarantee the CAS exists for. Best-effort throughout: every
    failure is swallowed and the exit proceeds, with the lease TTL as backstop.
    """
    try:
        from models.session_lifecycle import touch_issue_lock

        for _attempt in range(2):
            result = touch_issue_lock(
                issue_number,
                run_id,
                session_id=session_id,
                drop_renewer_identity=True,
            )
            if result.acquired:
                return
    except Exception as e:  # noqa: BLE001 - best-effort; the lease TTL is the backstop
        logger.info(
            "sdlc_lease_heartbeat: renewer-identity clear failed for issue #%s (%s: %s) "
            "-- lease TTL is the backstop",
            issue_number,
            type(e).__name__,
            e,
        )
        return
    logger.info(
        "sdlc_lease_heartbeat: renewer-identity clear for issue #%s did not land in 2 "
        "attempts -- the lease is no longer ours, or a writer keeps racing it",
        issue_number,
    )


def run_heartbeat(
    issue_number: int,
    run_id: str,
    session_id: str = "",
    interval: int | None = None,
    max_lifetime: int | None = None,
    _sleep=time.sleep,
    _monotonic=time.monotonic,
    *,
    supervisor_pid=None,
    supervisor_create_time=None,
    supervisor_check_interval: int | None = None,
    supervisor_source: str | None = None,
) -> int:
    """Peek-first renew-only heartbeat loop. Returns an exit code (always 0).

    Args:
        issue_number: The issue whose lease this heartbeat renews.
        run_id: The run identity that must own the lease for a renew to fire.
        session_id: Stored in the lock payload for display only.
        interval: Seconds between renews (default: ``ISSUE_LOCK_TTL_SECONDS//3``).
        max_lifetime: Hard upper bound on total run time before self-exit.
            ``None`` (the default) selects it from whether a supervisor
            resolved -- see below. An explicit value is always honored as-is.
        supervisor_pid / supervisor_create_time: the supervising ``claude``
            process's identity, recorded at mint time. Both are required;
            either missing, or a non-numeric/non-positive pid, means
            UNRESOLVED (never "dead").
        supervisor_check_interval: Seconds between supervisor liveness polls
            (default: :data:`SUPERVISOR_CHECK_INTERVAL_SECONDS`).
        supervisor_source: Which tier resolved the identity, for the startup
            log line only.
        _sleep / _monotonic: Injectable clocks for deterministic tests.

    The ``max_lifetime`` sentinel is load-bearing. Resolving the bound
    unconditionally from ``supervisor_pid`` would silently override a caller's
    explicit value on the unsupervised path, which is exactly the path every
    small-bound caller takes.
    """
    if not issue_number or not run_id:
        # Nothing to renew without both identifiers -- exit cleanly, never mint.
        return 0

    if interval is None:
        interval = _default_interval()
    interval = max(1, interval)

    sup_pid, sup_create_time = _resolved_supervisor(supervisor_pid, supervisor_create_time)
    source = supervisor_source or "unresolved"
    if sup_pid is None:
        source = "unresolved"

    # Bound selection, ONLY when the caller left max_lifetime unset (the same
    # explicit-vs-default sentinel `interval` uses just above).
    unsupervised_bound = False
    if max_lifetime is None:
        if sup_pid is None:
            max_lifetime = UNSUPERVISED_MAX_LIFETIME_SECONDS
            unsupervised_bound = True
        else:
            max_lifetime = MAX_LIFETIME_SECONDS

    check_interval = max(1, int(supervisor_check_interval or SUPERVISOR_CHECK_INTERVAL_SECONDS))
    # Wake often enough to poll the supervisor, but never more often than the
    # renew cadence itself when that is the tighter of the two.
    sleep_seconds = max(1, min(check_interval, interval))
    confirmations_required = max(1, SUPERVISOR_DEATH_CONFIRMATIONS)

    from agent.supervised_run import write_supervised_run_signal
    from models.session_lifecycle import ISSUE_LOCK_TTL_SECONDS, touch_issue_lock

    logger.info(
        "sdlc_lease_heartbeat: starting issue #%s run_id=%s session_id=%s "
        "supervisor=%s pid=%s create_time=%s renew_interval=%ss check_interval=%ss "
        "lifetime_bound=%ss",
        issue_number,
        run_id,
        session_id or "-",
        source,
        sup_pid if sup_pid is not None else "-",
        sup_create_time if sup_create_time is not None else "-",
        interval,
        check_interval,
        max_lifetime,
    )

    deadline = _monotonic() + max_lifetime
    consecutive_deaths = 0
    # Seconds of accumulated sleep since the last renew. Seeded at `interval`
    # so tick one always renews. Accumulated from the sleep amount rather than
    # read off the clock so an injected/frozen test clock cannot starve the
    # renew cadence.
    since_renew = interval

    while True:
        if _monotonic() >= deadline:
            # Both deadline exits below leave the lease alone on purpose, so
            # the payload outlives this process -- including the renewer
            # identity it stamped on every tick. Retract that identity first
            # (issue #2648): a renewer retiring on its own clock says nothing
            # about whether the RUN is alive, and leaving a dead pid behind
            # would let the liveness predicate read it as proof of death.
            _clear_renewer_identity_at_exit(issue_number, run_id, session_id)
            if unsupervised_bound:
                # Risk 1: this shortened bound applies ONLY when the supervisor
                # was unresolvable. Deliberately NO release -- failing to
                # resolve a supervisor is not positive proof the run is dead,
                # so the lease's own 1800s TTL is the correct disposition. A
                # live-but-unresolvable run therefore loses at most one TTL
                # window, and #2446's owned_run_ids self-recognition re-binds it.
                return _log_exit(
                    EXIT_UNSUPERVISED_MAX_LIFETIME,
                    issue_number,
                    run_id,
                    "no supervisor identity; lease left to expire on its own TTL",
                )
            return _log_exit(EXIT_MAX_LIFETIME, issue_number, run_id)

        if sup_pid is not None:
            if _supervisor_is_dead(sup_pid, sup_create_time):
                consecutive_deaths += 1
            else:
                consecutive_deaths = 0
            if consecutive_deaths >= confirmations_required:
                # Positively dead: the run has no driver, so holding its lease
                # only blocks a successor. Release both keys under our own
                # identity (both are compare-and-delete, so a successor that
                # already took over is never harmed).
                try:
                    from agent.supervised_run import clear_supervised_run_signal
                    from models.session_lifecycle import release_issue_lock

                    release_issue_lock(issue_number, run_id)
                    clear_supervised_run_signal(issue_number, run_id)
                except Exception as e:  # noqa: BLE001 - best-effort; TTL is the backstop
                    logger.info(
                        "sdlc_lease_heartbeat: release after supervisor death failed "
                        "for issue #%s (%s: %s) -- lease TTL is the backstop",
                        issue_number,
                        type(e).__name__,
                        e,
                    )
                return _log_exit(
                    EXIT_SUPERVISOR_DEAD,
                    issue_number,
                    run_id,
                    f"pid={sup_pid} gone on {consecutive_deaths} consecutive "
                    "checks; lease released",
                )

        if since_renew >= interval:
            since_renew = 0
            try:
                # PEEK FIRST, carrying the guarded run_id (issue #2537 review):
                # peek=True never mutates regardless of run_id, and passing the
                # run_id makes this an OWNERSHIP check, not a pid-liveness
                # inference. The payload's pid is stamped by the short-lived
                # `sdlc-tool session-ensure` CLI and is dead by the time this
                # detached heartbeat first ticks, so any pid-keyed signal
                # (`orphaned_lock`) reads every locally-minted lease as orphaned
                # on tick one and would lapse it mid-stage -- the exact
                # #2446/#2451 failure this heartbeat exists to prevent.
                peek = touch_issue_lock(issue_number, run_id, session_id=session_id, peek=True)
                owner = peek.owner_run_id
                if owner is None:
                    # Lease absent/lapsed -> stop. Never re-acquire (that would
                    # be lease-theft, Risk 2).
                    return _log_exit(EXIT_LEASE_LOST, issue_number, run_id)
                if owner != run_id:
                    return _log_exit(
                        EXIT_FOREIGN_OWNER, issue_number, run_id, f"current owner={owner}"
                    )
                # Self-owned: extend the TTL under our own identity only.
                # `renew_only=True` (issue #2714) makes that structural rather
                # than conventional: the extend skips both of `touch_issue_lock`'s
                # minting branches and writes through a compare-and-set, so a
                # release landing between the peek above and this call cannot be
                # undone by it. Without it the extend re-minted the lease the
                # supervisor had just given up and renewed it to the max-lifetime
                # ceiling with nothing behind it.
                # `stamp_renewer_identity` (issue #2648) records THIS
                # process's pid on the payload. It is the one pid on that
                # payload worth checking: the acquire-time `pid` belongs to
                # the ephemeral `session-ensure` CLI and is dead by tick one,
                # while this loop lives as long as the run does. Recording it
                # is what lets the liveness predicate stop treating a renewal
                # stamp as conclusive proof that the stamp's writer is still
                # alive. The module token is passed EXPLICITLY because this
                # file runs as `python -m tools.sdlc_lease_heartbeat`, where
                # `__name__` is "__main__" and any introspective allowlist
                # check would silently never match.
                extended = touch_issue_lock(
                    issue_number,
                    run_id,
                    session_id=session_id,
                    ttl=ISSUE_LOCK_TTL_SECONDS,
                    renew_only=True,
                    stamp_renewer_identity=True,
                    renewer_module="tools.sdlc_lease_heartbeat",
                )
                if not extended.acquired:
                    # The CAS lost (a release landed inside the peek/extend
                    # window) or `renew_only`'s fail-closed handler fired. Either
                    # way we no longer hold the lease, so stop here rather than
                    # falling through: the signal write below would otherwise
                    # republish `session:supervisedrun:{N}` with a fresh TTL for
                    # a lease we do not hold, which is the exact shape
                    # `renew_only` fails closed to prevent. Exiting instead of
                    # retrying is correct -- an absent or foreign lease is
                    # terminal for this heartbeat, never re-acquirable (Risk 2).
                    return _log_exit(
                        EXIT_LEASE_LOST,
                        issue_number,
                        run_id,
                        "renewal lost the lease between peek and extend",
                    )
                # Refresh the companion supervised-run signal on the same tick
                # (issue #2659). The signal carries the lock's TTL but was written
                # only at acquire, so before this it expired 1800s into every run
                # while the lock was renewed forever -- and from that moment on
                # every stage fork got a bare ISSUE_LOCKED from its own
                # supervisor's lock and correctly stood down. Renewing both
                # together is what makes the module docstring's "refreshed on
                # every acquire/renew" true. working_dir is deliberately omitted:
                # the worktree file carrier has no TTL and never needs refreshing.
                write_supervised_run_signal(issue_number, run_id, session_id=session_id)
            except Exception as e:  # noqa: BLE001 - best-effort; never crash the loop
                logger.debug(
                    "sdlc_lease_heartbeat: tick failed for issue #%s (%s: %s) -- retrying",
                    issue_number,
                    type(e).__name__,
                    e,
                )

        _sleep(sleep_seconds)
        since_renew += sleep_seconds


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Detached peek-first renew-only lease heartbeat for local /do-sdlc",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    parser.add_argument("--issue-number", type=int, required=True)
    parser.add_argument("--run-id", dest="run_id", required=True)
    parser.add_argument("--session-id", dest="session_id", default="")
    parser.add_argument(
        "--interval",
        type=int,
        default=None,
        help="Seconds between renews (default: ISSUE_LOCK_TTL_SECONDS//3)",
    )
    parser.add_argument(
        "--max-lifetime",
        dest="max_lifetime",
        type=int,
        default=None,
        help=(
            "Hard upper bound on total run time before self-exit. Unset selects "
            "MAX_LIFETIME_SECONDS when a supervisor is recorded, else "
            "UNSUPERVISED_MAX_LIFETIME_SECONDS."
        ),
    )
    # Deliberately parsed as strings, not `type=int` / `type=float`: a
    # malformed value must degrade to UNRESOLVED, not abort the heartbeat with
    # argparse's exit code 2 and leave the lease with no renewer at all.
    parser.add_argument("--supervisor-pid", dest="supervisor_pid", default=None)
    parser.add_argument("--supervisor-create-time", dest="supervisor_create_time", default=None)
    parser.add_argument("--supervisor-source", dest="supervisor_source", default=None)
    parser.add_argument("--verbose", action="store_true", help="Enable debug logging")

    args = parser.parse_args()
    # INFO unconditionally: this log file is the only diagnostic a detached
    # heartbeat can leave behind, and configuring it only under --verbose is
    # why it stayed 0 bytes across every zombie heartbeat ever spawned.
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(message)s",
    )

    exit_code = run_heartbeat(
        issue_number=args.issue_number,
        run_id=args.run_id,
        session_id=args.session_id or "",
        interval=args.interval,
        max_lifetime=args.max_lifetime,
        supervisor_pid=args.supervisor_pid,
        supervisor_create_time=args.supervisor_create_time,
        supervisor_source=args.supervisor_source,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
