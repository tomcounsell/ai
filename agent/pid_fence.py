"""Best-effort ``(pid, create_time)`` process-identity fence.

The durability plan (#2494) consolidates the former per-turn pid trio (the
old claude/pm/harness process-id fields) into ONE fenced execution record on
``AgentSession``.
The fence is ``(exec_pid, pid_create_time)``: a recorded pid together with the
psutil ``create_time()`` captured for it at spawn.

**This is DETECTION, not a guarantee.** There is an irreducible TOCTOU window
between reading a live process' ``create_time`` and acting on its pid — the OS
can recycle the pid in between. Linux's race-free answer is ``pidfd``
(``pidfd_open`` / ``pidfd_send_signal``); **macOS has no pidfd**, and this
system runs on darwin, so ``psutil.Process.create_time()`` is the ceiling
available here. A future reader must NOT mistake ``(pid, create_time)`` for a
safety guarantee. See https://lwn.net/Articles/784997/ ("Rethinking race-free
process signaling"). For processes the runner spawns itself, the retained child
handle (``_TurnHandle`` in ``agent/session_runner/runner.py``) is the primary
mechanism and this fence is the backstop.

Legacy-row rule (canonical — #2518)
-----------------------------------
Rows written before the fence existed carry a pid with no ``create_time``, and
a live process' ``create_time`` can also be unreadable (``AccessDenied``, psutil
missing). PR #2516 left three different behaviors for that one condition, so
this is now the single rule every consumer inherits:

    **An unreadable or absent ``create_time`` on EITHER side means "unknown".
    Unknown never authorizes a kill — but it may authorize the gentler action
    already in place at that site.**

Concretely: ``fence_is_live`` returns ``False`` for unknown, which callers must
read as "cannot claim ownership", NOT as "this process is dead". A site that
would SIGTERM/SIGKILL on a positive verdict must therefore refuse to signal on
unknown. A site whose action is gentler than a kill — popping a stale handle,
sweeping a row to terminal, falling back to a plain ``_pid_is_alive`` liveness
probe — may proceed on unknown, because none of those signal a process that
might not be ours. This is upstream psutil practice: treat an unfetchable
``create_time`` as "unknown → fall back", never as "assume valid".

Two log-only reads of a fenced pid exist in ``agent/session_health.py`` and are
deliberately unguarded because they drive no decision; they carry the marker
``# fence-census: log-only, not a decision consumer``, which
``tools/check_fence_census.py`` honors as the sole exemption mechanism.
"""

from collections.abc import Callable

# ``create_time()`` equality tolerance (seconds). Mirrors the existing guards in
# ``agent/reap_killlist.py`` and ``agent/session_health.py``'s staged-SIGKILL
# drain.
#
# Deliberately TIGHTER than psutil's own documented precision: psutil documents
# ``create_time()`` as accurate to 0.01s on Linux, and disables its pid-reuse
# check entirely on FreeBSD/OpenBSD/SunOS/AIX because ctime moves with the
# system clock (psutil #2396). This system is darwin-only, where the start time
# is monotonic boot-relative with clock-change adjustment and stable to well
# under a millisecond across reads of the same live process — so 1e-3 holds
# here and would not on Linux.
#
# The error direction is what makes a tight value safe: too tight yields a
# FALSE NEGATIVE ("not ours" for our own process), and per the legacy-row rule
# above, a negative verdict withholds a kill. It degrades ownership claims; it
# never authorizes signalling an unrelated process. Pinned by
# ``tests/unit/test_pid_fence.py``.
CREATE_TIME_TOLERANCE_S = 1e-3


def proc_create_time(pid: int | None) -> float | None:
    """Live ``create_time`` for ``pid`` via psutil, or ``None`` if gone/unreadable.

    Returns ``None`` on ``NoSuchProcess``, ``AccessDenied``, a missing psutil,
    or any other error — i.e. "cannot positively read this pid's identity".
    """
    if pid is None:
        return None
    try:
        import psutil  # noqa: PLC0415

        return psutil.Process(int(pid)).create_time()
    except Exception:  # noqa: BLE001 — psutil missing, NoSuchProcess, AccessDenied, bad pid
        return None


def create_times_match(recorded: float | None, observed: float | None) -> bool:
    """True iff two ``create_time`` readings identify the SAME process.

    The tolerance compare, factored out so every consumer shares one definition
    of "same process" (#2518). Two callers need it with different inputs:

    - :func:`fence_is_live` compares a RECORDED time against one it reads live
      from the pid right now.
    - ``AgentSession.find_live_session_by_pid`` compares a recorded time against
      one its CALLER already observed for the process it is holding, so it must
      not re-read psutil.

    ``None`` on either side is "unknown" and returns ``False`` per the
    legacy-row rule — callers fall back rather than assuming valid. Never
    raises.
    """
    if recorded is None or observed is None:
        return False
    try:
        return abs(float(observed) - float(recorded)) <= CREATE_TIME_TOLERANCE_S
    except (TypeError, ValueError):
        return False


def fence_is_live(
    pid: int | None,
    recorded_create_time: float | None,
    *,
    create_time_fn: Callable[[int | None], float | None] | None = None,
) -> bool:
    """True iff ``pid`` is alive AND still the SAME process we recorded.

    The fence re-reads the live process' ``create_time`` and compares it to
    ``recorded_create_time`` within :data:`CREATE_TIME_TOLERANCE_S`:

    - dead pid (``create_time`` unreadable) → ``False``
    - recycled pid (alive, different ``create_time``) → ``False`` ("not ours")
    - missing fence (``recorded_create_time is None``) → ``False``: without a
      recorded fence we cannot claim ownership, so callers must fall back to a
      plain liveness check rather than trusting this.
    - matching pid + ``create_time`` → ``True``

    ``create_time_fn`` is a seam for tests to drive outcomes without real
    processes. Never raises.

    Callers must remember the residual TOCTOU window documented in this module:
    a ``True`` verdict can go stale before the signal lands.
    """
    if pid is None:
        return False
    ctf = create_time_fn or proc_create_time
    return create_times_match(recorded_create_time, ctf(pid))
