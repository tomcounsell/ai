"""Best-effort ``(pid, create_time)`` process-identity fence.

The durability plan (#2494) consolidates the former ``claude_pid`` / ``pm_pid``
/ ``harness_pid`` trio into ONE fenced execution record on ``AgentSession``.
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
"""

from collections.abc import Callable

# ``create_time()`` equality tolerance (seconds). Mirrors the existing guards in
# ``agent/reap_killlist.py`` and ``agent/session_health.py``'s staged-SIGKILL
# drain. Provisional / tunable (grain of salt): psutil's create_time is stable
# to well under a millisecond across reads of the same live process.
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
    live_ct = ctf(pid)
    if live_ct is None:
        return False
    if recorded_create_time is None:
        return False
    try:
        return abs(live_ct - float(recorded_create_time)) <= CREATE_TIME_TOLERANCE_S
    except (TypeError, ValueError):
        return False
