# Plan: Update Lock Guard PID Liveness Check

tracking: https://github.com/tomcounsell/ai/issues/2169

**Issue:** https://github.com/tomcounsell/ai/issues/2169
**Slug:** update-lock-pid-liveness

## Problem

The `/update` concurrency guard in `scripts/remote-update.sh` uses a `mkdir`-based
lock directory at `data/update.lock`. On a collision it has **only an age-based
staleness backstop** (`LOCK_AGE > 600` → reclaim) and **no PID liveness check**.
The lock dir records no owner PID, so the guard cannot tell a *running* update
from a *dead* one.

If a prior update run dies without releasing the lock — SIGKILL, OOM, power loss,
or the bridge-kickstart self-kill on a path that skips the explicit pre-kickstart
release — the `trap cleanup_lock EXIT` handler never fires and the orphaned lock
blocks **every** subsequent run (manual `/update` and the 30-min cron) for up to
600 seconds with a green `exit 0` "Skipping" no-op.

## Root Cause

The lock dir carries no owner identity. Collision handling can only reason about
the lock's mtime, so a crashed run and a legitimately-running one are
indistinguishable until the 600s TTL elapses.

## Fix

Record the holder PID in the lock dir and check liveness on collision, in this
precedence order:

- **Age backstop first** (ultimate authority; no legitimate hold approaches the
  TTL): lock older than TTL → reclaim regardless of PID. This covers PID reuse
  and wedged-but-alive holders.
- Young lock + recorded PID **alive** (`kill -0` succeeds) → genuine concurrent
  run → skip (correct).
- Young lock + recorded PID **dead** → crashed run → reclaim immediately (the fix).
- Young lock + PID **unknown** (legacy lock or a run mid-claim that hasn't written
  its pid yet) → skip conservatively; the age backstop clears it later.

Implementation notes:
- After the `mkdir "$LOCK_DIR"` that claims the lock, write `$$` to
  `$LOCK_DIR/pid`.
- The reclaim-then-reclaim path (age backstop and dead-PID) must re-`mkdir` and
  re-write the pid file so the reclaiming run becomes the recorded owner.
- Switch the lock-release calls from `rmdir` to `rm -rf` since the dir now
  contains a `pid` file (`rmdir` only removes empty dirs). Affects
  `cleanup_lock()` (the EXIT trap) and the pre-kickstart release.

## Success Criteria

- A young `data/update.lock` whose recorded PID is dead is reclaimed immediately;
  the update run proceeds instead of green-skipping.
- A young lock whose recorded PID is alive still causes a skip (genuine concurrent
  run is not stomped).
- A lock older than the 600s TTL is reclaimed regardless of PID liveness.
- A young lock with no/unknown pid file is skipped conservatively.
- Lock release works on the now-non-empty lock dir (`rm -rf`, not `rmdir`), so the
  release-before-kickstart path and the EXIT trap both clear the lock.
- All existing `tests/unit/test_remote_update_shell.py` cases still pass, plus new
  PID-liveness cases.

## No-Gos

- Do not replace the `mkdir` atomic-claim mechanism with `flock` or a lockfile —
  `mkdir` atomicity across POSIX is the intentional design and other machines
  rely on it.
- Do not lower or remove the 600s age backstop; it remains the ultimate authority
  against PID reuse and wedged-but-alive holders.
- Do not add a PID check that skips when the pid file is missing on an *old* lock
  — age wins first, unconditionally.

## Update System

This change **is** an update-system change: it modifies `scripts/remote-update.sh`,
the top-level update entrypoint. No new dependencies, config files, or migration
steps — the lock dir format gains a `pid` file that older code simply ignores
(older `rmdir` release would fail on the non-empty dir, but the new code ships as
one atomic script replacement via `git pull` at the top of the same run, so no
mixed-version window exists). No changes to `.claude/skills/update/` or
`scripts/update/*.py` are required.

## Agent Integration

No agent integration required — this is a bridge/update-infrastructure shell
change with no new CLI entry point and no bridge import surface. The agent already
invokes `/update` (which calls `remote-update.sh`) via the existing update skill.

## Failure Path Test Strategy

Extend the existing real-script harness in
`tests/unit/test_remote_update_shell.py` (runs the actual `remote-update.sh` in a
sandboxed fake project). New cases seed a pre-existing `data/update.lock` dir with
a controlled mtime and pid file, then assert the collision branch's decision:

- Young lock + dead PID → reclaimed, run proceeds (the fix).
- Young lock + live PID → skipped (genuine concurrent run).
- Young lock + missing pid file → skipped conservatively.
- Old lock (mtime > 600s) + live PID → reclaimed anyway (age backstop wins).
- Lock is released via `rm -rf` (non-empty dir with pid file) — existing
  release-before-kickstart test still passes.

## Test Impact
- [ ] `tests/unit/test_remote_update_shell.py` — UPDATE: add PID-liveness
  collision cases; existing `test_lock_collision_*` and
  `test_lock_released_before_self_kill_second_run_not_skipped` cases must still
  pass (release path now `rm -rf`).

## Rabbit Holes

- Making the lock robust against `stat -f` (BSD) vs `stat -c` (GNU) portability —
  the script already uses `stat -f %m` (BSD/macOS), matching the target fleet.
  Keep that; do not add GNU fallbacks not needed here.
- Overthinking PID-reuse races: the age backstop is the deliberate coarse guard
  for that; a young lock whose pid was reused by an unrelated live process is an
  accepted (astronomically rare, self-healing at TTL) edge.

## Documentation
- [ ] Add a note to `docs/features/bridge-self-healing.md` documenting the update
  lock's PID-liveness reclaim behavior (age backstop first, then live/dead/unknown
  PID handling) so the self-healing reference reflects the new guard.
- [ ] Update the in-script comment block above the lock guard in
  `scripts/remote-update.sh` to describe the pid-file + liveness decision table
  (the script comments are the authoritative inline reference for this mechanism).
