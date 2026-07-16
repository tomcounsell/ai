#!/usr/bin/env python3
"""Advisory cross-process lock serializing concurrent full-suite pytest runs.

Fixes issue #1967 (F1): the default pytest config runs ``-n auto`` (one xdist
worker per CPU core). When two *full-suite* runs overlap — a manual run racing
``/do-test``, ``/do-docs``, or ``scripts/refresh_test_baseline.py`` — total
workers exceed cores and every worker starves. During PR #1956 the load average
reached 79-82 on a 10-core machine; one baseline run accumulated 15 seconds of
CPU across 90 minutes of wall-clock before being killed.

The guard mirrors the ``mkdir``-atomic lock-dir pattern already used by
``scripts/remote-update.sh``: a full-suite invocation acquires a machine-global
lock dir (``default_lock_dir()`` — under ``/tmp``, keyed by a hash of the repo's
shared ``git`` common dir) before launching pytest and releases it on exit. The
lock lives outside any checkout's ``data/`` so **every worktree of one repo
contends on a single lock** — concurrent SDLC lanes in separate worktrees now
serialize instead of oversubscribing cores and cross-reaping each other's xdist
workers (issue #2064). A second concurrent full-suite run *waits* for the first
to finish rather than piling on. The lock is advisory:

* A lone run acquires instantly — single-run behavior is unchanged.
* Targeted / serial runs (``-n0``, ``-p no:xdist``, or a narrower path than the
  whole ``tests/`` tree) are not full-suite and never touch the lock, so quick
  focused runs keep their unchanged parallelism.
* A crashed owner leaves a stale lock; the next run reclaims it by checking the
  owner PID's liveness (``os.kill(pid, 0)``) and a generous age backstop.
* After an overall ``--timeout`` the waiter proceeds *unlocked* with a warning
  rather than deadlocking forever.

Shell contract (see ``scripts/pytest-clean.sh``)::

    python scripts/suite_lock.py acquire --owner-pid $$ --timeout 1800 -- "$@"
    # ... run pytest ...
    python scripts/suite_lock.py release --owner-pid $$

``acquire`` prints one status token on the last line of stdout:

* ``ACQUIRED``               — lock now held by ``--owner-pid``; caller must release.
* ``SKIPPED_NOT_FULL_SUITE`` — not a full-suite run; caller must NOT release.
* ``PROCEEDED_UNLOCKED``     — waited past ``--timeout``; caller must NOT release.

``release`` removes the lock only when its recorded owner PID matches
``--owner-pid`` (so a run that proceeded unlocked, or whose lock was stolen,
never yanks the lock out from under the real owner).
"""

from __future__ import annotations

import argparse
import hashlib
import os
import subprocess
import sys
import time
from pathlib import Path

# Value-taking pytest flags: when one appears as its own token, the following
# token is the flag's value, not a positional test path. (Flags written with
# ``=`` are self-contained and need no look-ahead.)
_VALUE_FLAGS = {
    "-k",
    "-m",
    "-n",
    "-p",
    "-c",
    "-o",
    "-r",
    "-W",
    "--dist",
    "--rootdir",
    "--maxfail",
    "--junitxml",
    "--deselect",
    "--ignore",
}

# Owner alive but the lock has out-lived any sane serialized full-suite run:
# reclaim it. With serialization in force a real run finishes in minutes, so an
# hour is a wide safety margin against a wedged-but-alive owner.
DEFAULT_STALE_AFTER = 3600.0

DEFAULT_TIMEOUT = 1800.0
DEFAULT_POLL_INTERVAL = 2.0

# Bound the ``git rev-parse --git-common-dir`` probe used to key the lock dir.
# A local, fast subprocess; provisional/tunable.
_GIT_REVPARSE_TIMEOUT = 5.0


# Machine-global base for the suite lock. Deliberately a fixed ``/tmp`` and NOT
# ``$TMPDIR``: a launchd worker daemon typically has ``TMPDIR`` unset (-> /tmp)
# while an interactive shell has ``TMPDIR=/var/folders/.../T`` (see
# ``project_launchd_plist_auth_source.md``). Using ``$TMPDIR`` would make the
# worker-driven merge gate and a manual ``pytest-clean.sh`` compute different
# lock dirs and never serialize — the exact #1967 blind spot. Only the per-repo
# hash needs to vary; the base only needs to be machine-global and stable across
# process types.
_LOCK_BASE = Path("/tmp")


def _repo_lock_key() -> str:
    """Stable per-repo identity shared across all of the repo's worktrees.

    Every worktree of one repo shares a single ``.git`` common dir
    (``git rev-parse --git-common-dir`` resolves to the same absolute path from
    every checkout), so hashing that path yields one lock key per repo — one that
    all its worktrees agree on but that differs from unrelated clones.

    Falls back to hashing ``cwd`` when the ``git`` subprocess returns non-zero OR
    empty output (git absent, corrupted repo, ``GIT_DIR`` override), so lock
    resolution never crashes and a non-repo run still gets a stable key.
    """
    common = ""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "--git-common-dir"],
            capture_output=True,
            text=True,
            timeout=_GIT_REVPARSE_TIMEOUT,
        )
        if proc.returncode == 0:
            common = proc.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        common = ""

    if common:
        # ``--git-common-dir`` may print a path relative to cwd; make it absolute
        # so every worktree hashes the identical string.
        common = os.path.abspath(common)
    else:
        common = os.path.abspath(os.getcwd())

    return hashlib.sha1(common.encode()).hexdigest()[:16]


def default_lock_dir() -> Path:
    """Resolve the machine-global suite-lock dir for the current repo.

    ``/tmp/valor-suite-lock-<sha1(git-common-dir)[:16]>/full-suite-running.lock``
    — outside any checkout's ``data/`` so all worktrees of one repo contend on a
    single lock and worktree deletion (post-merge cleanup) can't remove a live
    lock (issue #2064).
    """
    return _LOCK_BASE / f"valor-suite-lock-{_repo_lock_key()}" / "full-suite-running.lock"


def _default_lock_dir() -> Path:
    return default_lock_dir()


def is_full_suite(args: list[str]) -> bool:
    """Return True when ``args`` describe a whole-``tests/`` pytest invocation.

    A run is *not* full-suite when it narrows to a subset: a positional path
    that points below ``tests/`` (contains ``/`` or ends in ``.py`` and is not
    exactly ``tests``/``tests/``), or a serial/no-xdist mode (``-n0``,
    ``-n 0``, ``-p no:xdist``) where oversubscription cannot occur. A bare
    ``pytest`` (no path) collects the whole tree and counts as full-suite.
    """
    i = 0
    n = len(args)
    while i < n:
        tok = args[i]

        # Serial / xdist-disabled runs cannot oversubscribe cores.
        if tok in ("-n0", "-n=0"):
            return False
        if tok == "-n" and i + 1 < n and args[i + 1] in ("0", "no"):
            return False
        if tok.startswith("--numprocesses"):
            if tok in ("--numprocesses=0", "--numprocesses=no"):
                return False
        if tok == "--numprocesses" and i + 1 < n and args[i + 1] in ("0", "no"):
            return False
        if tok in ("-p", "--plugin") and i + 1 < n and args[i + 1] == "no:xdist":
            return False
        if tok in ("-pno:xdist", "-p=no:xdist", "--plugin=no:xdist"):
            return False

        # Skip a value-taking flag's separate value token so it is never
        # mistaken for a narrowing path (e.g. ``-k some/expr``).
        if tok in _VALUE_FLAGS and i + 1 < n:
            i += 2
            continue

        if tok.startswith("-"):
            i += 1
            continue

        # Positional token. Is it a narrowing test path?
        stripped = tok.rstrip("/")
        if stripped == "tests" or tok == "tests/":
            i += 1
            continue
        if "/" in tok or tok.endswith(".py") or "::" in tok:
            return False
        # Bare word positional (e.g. a stray marker) — ignore for detection.
        i += 1

    return True


def evaluate_lock_state(
    *,
    exists: bool,
    owner_pid: int | None,
    owner_alive: bool,
    age_seconds: float,
    stale_after: float,
) -> str:
    """Decide what to do about the current lock: ``take``, ``wait``, or ``steal``.

    Pure decision function (no I/O) so the policy is unit-testable:

    * no lock                       -> ``take``
    * owner PID unreadable, aged    -> ``steal`` (malformed/abandoned)
    * owner PID unreadable, fresh   -> ``wait``  (owner may still be writing it)
    * owner process gone            -> ``steal``
    * owner alive but past backstop -> ``steal``
    * owner alive and fresh         -> ``wait``
    """
    if not exists:
        return "take"
    if owner_pid is None:
        return "steal" if age_seconds >= stale_after else "wait"
    if not owner_alive:
        return "steal"
    if age_seconds >= stale_after:
        return "steal"
    return "wait"


def _pid_alive(pid: int) -> bool:
    """Return True if a process with ``pid`` exists (signal 0 probe)."""
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        # Exists but owned by another user — still alive for our purposes.
        return True
    except (OSError, ValueError):
        return False
    return True


def _read_owner_pid(lock_dir: Path) -> int | None:
    try:
        raw = (lock_dir / "owner.pid").read_text().strip()
        return int(raw)
    except (OSError, ValueError):
        return None


def _lock_age(lock_dir: Path, now: float) -> float:
    try:
        return max(0.0, now - lock_dir.stat().st_mtime)
    except OSError:
        return 0.0


def _write_owner(lock_dir: Path, owner_pid: int) -> None:
    try:
        (lock_dir / "owner.pid").write_text(f"{owner_pid}\n")
    except OSError:
        pass


def acquire(
    lock_dir: Path,
    owner_pid: int,
    *,
    timeout: float = DEFAULT_TIMEOUT,
    poll_interval: float = DEFAULT_POLL_INTERVAL,
    stale_after: float = DEFAULT_STALE_AFTER,
    now_fn=time.monotonic,
    sleep_fn=time.sleep,
    alive_fn=_pid_alive,
    log=lambda msg: print(msg, file=sys.stderr),
) -> str:
    """Acquire the advisory lock for ``owner_pid``. Returns a status token.

    Blocks (polling every ``poll_interval`` seconds) until the lock is free or
    reclaimable, up to ``timeout`` seconds of *waiting*. Returns:

    * ``ACQUIRED``           — the lock dir now exists and records ``owner_pid``.
    * ``PROCEEDED_UNLOCKED`` — waited past ``timeout``; caller runs without the lock.
    """
    lock_dir.parent.mkdir(parents=True, exist_ok=True)
    deadline = now_fn() + timeout
    waited = False

    while True:
        try:
            lock_dir.mkdir()  # atomic on POSIX — the actual mutual exclusion
            _write_owner(lock_dir, owner_pid)
            if waited:
                log("[suite-lock] acquired after waiting for another full-suite run")
            return "ACQUIRED"
        except FileExistsError:
            pass

        owner = _read_owner_pid(lock_dir)
        alive = alive_fn(owner) if owner is not None else False
        age = _lock_age(lock_dir, time.time())
        decision = evaluate_lock_state(
            exists=True,
            owner_pid=owner,
            owner_alive=alive,
            age_seconds=age,
            stale_after=stale_after,
        )

        if decision == "steal":
            log(
                f"[suite-lock] reclaiming stale lock "
                f"(owner_pid={owner}, alive={alive}, age={age:.0f}s)"
            )
            _force_remove(lock_dir)
            continue  # retry mkdir immediately

        # decision == "wait"
        if now_fn() >= deadline:
            log(
                f"[suite-lock] waited {timeout:.0f}s for another full-suite run "
                f"(owner_pid={owner}); proceeding UNLOCKED"
            )
            return "PROCEEDED_UNLOCKED"

        if not waited:
            log(
                f"[suite-lock] another full-suite run is active "
                f"(owner_pid={owner}); waiting up to {timeout:.0f}s"
            )
            waited = True
        sleep_fn(poll_interval)


def _force_remove(lock_dir: Path) -> None:
    try:
        (lock_dir / "owner.pid").unlink()
    except OSError:
        pass
    try:
        lock_dir.rmdir()
    except OSError:
        # Someone else may have won the race and repopulated it; leave it.
        pass


def release(lock_dir: Path, owner_pid: int) -> bool:
    """Release the lock only if its recorded owner matches ``owner_pid``.

    Returns True if this call removed the lock, False otherwise (not held, or
    held by a different owner — e.g. after a steal or a PROCEEDED_UNLOCKED run).
    """
    if not lock_dir.exists():
        return False
    owner = _read_owner_pid(lock_dir)
    if owner is not None and owner != owner_pid:
        return False
    _force_remove(lock_dir)
    return True


def _split_double_dash(argv: list[str]) -> tuple[list[str], list[str]]:
    """Split ``argv`` at the first ``--`` into (lock-flags, pytest-args)."""
    if "--" in argv:
        idx = argv.index("--")
        return argv[:idx], argv[idx + 1 :]
    return argv, []


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv:
        print("usage: suite_lock.py {acquire,release,is-full-suite} ...", file=sys.stderr)
        return 2
    command = argv[0]
    rest = argv[1:]
    lock_flags, pytest_args = _split_double_dash(rest)

    parser = argparse.ArgumentParser(prog="suite_lock.py")
    parser.add_argument("--owner-pid", type=int, default=os.getpid())
    parser.add_argument("--timeout", type=float, default=DEFAULT_TIMEOUT)
    parser.add_argument("--poll-interval", type=float, default=DEFAULT_POLL_INTERVAL)
    parser.add_argument("--stale-after", type=float, default=DEFAULT_STALE_AFTER)
    parser.add_argument("--lock-dir", type=Path, default=None)
    args = parser.parse_args(lock_flags)

    lock_dir = args.lock_dir or _default_lock_dir()

    if command == "is-full-suite":
        return 0 if is_full_suite(pytest_args) else 1

    if command == "acquire":
        if not is_full_suite(pytest_args):
            print("SKIPPED_NOT_FULL_SUITE")
            return 0
        status = acquire(
            lock_dir,
            args.owner_pid,
            timeout=args.timeout,
            poll_interval=args.poll_interval,
            stale_after=args.stale_after,
        )
        print(status)
        return 0

    if command == "release":
        release(lock_dir, args.owner_pid)
        return 0

    print(f"unknown command: {command}", file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
