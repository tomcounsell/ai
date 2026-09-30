"""Terminate in-flight harness children on worker shutdown (issue #2141).

Before this module, the worker's SIGTERM path waited up to 60s for active
session tasks — but launchd's real kill grace is ~3-5s, so the wait never
completed and the session's `claude -p` harness subprocess was ORPHANED,
surviving into the next boot where the orphan-reap SIGKILLed it a full boot
cycle later. One boot cycle with a zombie subprocess writing into a
recovered session's worktree is an avoidable race.

``terminate_harness_children`` is called from the worker shutdown sequence
after the (now grace-bounded) active-task wait: it enumerates this process's
descendants, SIGTERMs every `claude` harness among them, waits briefly, and
SIGKILLs survivors. Loud per-PID logging makes each abandoned turn visible
in worker.log. Best-effort by design: if launchd SIGKILLs the worker before
this runs, behavior degrades to exactly the pre-#2141 status quo (next-boot
reaper). Never raises into the shutdown path.
"""

from __future__ import annotations

import logging
import os

logger = logging.getLogger(__name__)

# Env knob (documented in docs/features/config-timeout-catalog.md): how long
# the shutdown sequence waits for active session tasks before abandoning
# them and cleaning up children. Sized to launchd's observed ~3-5s SIGTERM
# grace — an honest bound, unlike the previous 60s wait that never completed.
WORKER_SHUTDOWN_GRACE_S = float(os.environ.get("WORKER_SHUTDOWN_GRACE_S", 3.0))


def _is_claude_harness(proc) -> bool:
    """True if ``proc`` looks like a `claude` harness subprocess.

    Matches on the process name OR argv[0] basename being `claude` (the
    headless runner spawns the CLI binary directly; versioned installs give
    it a bare-number basename with `claude` in argv[0]). Never raises.
    """
    try:
        if proc.name() == "claude":
            return True
        cmdline = proc.cmdline()
        if cmdline and os.path.basename(cmdline[0]) == "claude":
            return True
    except Exception:
        return False
    return False


def terminate_harness_children(term_grace_s: float = 1.5) -> int:
    """SIGTERM (then SIGKILL) all `claude` harness descendants of this process.

    Returns the number of harness processes terminated. Never raises — any
    psutil/platform failure logs a warning and returns what was done so far.
    """
    try:
        import psutil

        me = psutil.Process()
        harnesses = [c for c in me.children(recursive=True) if _is_claude_harness(c)]
    except Exception as e:
        logger.warning("[shutdown] harness-child enumeration failed: %s", e)
        return 0

    if not harnesses:
        return 0

    for proc in harnesses:
        try:
            logger.warning(
                "[shutdown] terminating in-flight harness PID %d (session turn "
                "abandoned; startup recovery will resume the session)",
                proc.pid,
            )
            proc.terminate()
        except Exception as e:
            logger.warning("[shutdown] SIGTERM failed for PID %d: %s", proc.pid, e)

    try:
        import psutil

        _, alive = psutil.wait_procs(harnesses, timeout=term_grace_s)
        for proc in alive:
            try:
                logger.warning("[shutdown] SIGKILL harness PID %d (survived SIGTERM)", proc.pid)
                proc.kill()
            except Exception as e:
                logger.warning("[shutdown] SIGKILL failed for PID %d: %s", proc.pid, e)
    except Exception as e:
        logger.warning("[shutdown] harness-child wait/kill failed: %s", e)

    return len(harnesses)
