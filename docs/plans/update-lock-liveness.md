---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-19
tracking: https://github.com/tomcounsell/ai/issues/2169
---

# Update lock: PID-liveness + release-on-all-exit-paths

## Problem

`/update` on "Valor the Captain" failed with `Another update is already running. Skipping.`
This is the concurrency guard in `scripts/remote-update.sh` firing.

The lock is a `mkdir`-based directory at `data/update.lock` (atomic acquire on POSIX).
On a collision the guard has **only an age-based staleness backstop**
(`LOCK_AGE > 600` → reclaim, `remote-update.sh:75-90`) and **no PID liveness check**.
The lock dir stores no owner PID, so the guard cannot distinguish a *running* update
from a *dead* one.

Consequences:

1. **False positive (the durable bug).** If a prior update dies without releasing the
   lock — SIGKILL, OOM, power loss — none of which fire `trap cleanup_lock EXIT`
   (`:74`, `:91`) — the orphaned lock blocks **every** subsequent run (manual `/update`
   and the 30-min cron) for up to 600s with a green `exit 0` no-op.
2. **Poor concurrency UX.** A legitimate long run (the worker `drain` step alone holds
   the lock up to 300s — `UPDATE_WORKER_DRAIN_TIMEOUT_S`) is indistinguishable from a
   crash; a manual `/update` during that window collides and silently skips.

Observed on "Valor the Captain" at diagnosis time: a live cron-driven `remote-update.sh`
(PID 33065, PPID 1) was mid-`scripts.update.drain --timeout 300`; the lock was 126s old
and legitimately held. It released normally when the drain finished. So the reported
failure was a live-collision, but the missing-liveness gap is the real latent defect:
had that process been SIGKILLed mid-drain, the lock would have blocked all updates for
the next ~8 minutes.

## Solution

Record the holder PID in the lock dir (`data/update.lock/pid`) at acquire time, and on a
collision decide with an explicit precedence:

1. **Age backstop first** (ultimate authority — no legitimate hold approaches the TTL):
   `LOCK_AGE > STALE_LOCK_TTL` → reclaim regardless of PID. Covers PID reuse (a recycled
   but live PID) and wedged-but-alive holders.
2. Young lock + recorded PID **alive** (`kill -0`) → genuine concurrent run → skip (correct).
3. Young lock + recorded PID **dead** → crashed run → reclaim immediately (the fix — no
   600s wait).
4. Young lock + PID **unknown** (empty/non-numeric pid file: a legacy lock from older
   code, or a holder still in the microsecond window between `mkdir` and writing its pid)
   → skip conservatively; the age backstop clears it later. This deliberately avoids
   reclaiming a lock whose holder may be alive but has not yet written its pid (TOCTOU
   safety).

`STALE_LOCK_TTL` is raised from 600s to 1800s: the old 600 was the *only* guard, so it had
to be short for fast crash recovery; now that the liveness check gives *immediate* crash
recovery, the age cap's job narrows to "wedged-but-alive / PID-reuse only," so it is set
comfortably above any legitimate hold (max drain 300s + dep sync) to never reclaim a
slow-but-live legit run.

Release-on-all-exit-paths: the lock dir now contains a `pid` file, so `rmdir` (which only
removes empty dirs) no longer suffices. Switch `cleanup_lock` and the explicit
pre-bridge-kickstart release (`:418`) from `rmdir` to `rm -rf`. The `trap cleanup_lock
EXIT` covers normal + error exits; the explicit pre-kickstart release covers the SIGKILL
self-kill path (EXIT trap never fires on SIGKILL). Both paths are preserved.

### Files to modify

| File | Change |
|------|--------|
| `scripts/remote-update.sh` | Rewrite the lock acquire/collision block (`:73-91`) with pid-file + liveness + age-backstop precedence; `cleanup_lock` and pre-kickstart release `rmdir` → `rm -rf` |
| `tests/unit/test_remote_update_shell.py` | Add liveness-branch tests (dead PID reclaims, live PID skips, aged-out reclaims regardless, unknown-pid young skips); keep existing collision tests green |

## No-Gos

- **No `flock`/external lock manager** — keep the dependency-free `mkdir` primitive; only
  layer a pid file + liveness onto it.
- **No Redis/cross-machine coordination** — out of scope; each machine's lock is local.
- **No change to the drain/restart/verify tail** — the `:73-91` acquire block and the two
  release sites are the entire blast radius.

## Update System

This change **is** to the update system (`scripts/remote-update.sh`). No further update-
skill or `scripts/update/*.py` changes are required — the fix is self-contained in the
shell script that both the launchd cron (`com.valor.update`) and the bridge `/update`
handler already invoke. It propagates to every machine on the next `git pull` (the script
pulls itself before running). No migration step: the new pid-file format is written by the
new code; old-code locks (no pid file) are handled by the unknown-pid branch + age backstop.

## Agent Integration

No agent integration required — this is a bridge/cron-internal shell change. The existing
`_handle_update_command` in `bridge/telegram_bridge.py` invokes `scripts/remote-update.sh`
unchanged; no new CLI entry point in `pyproject.toml` and no new bridge import.

## Failure Path Test Strategy

- **Dead holder, young lock** → script does NOT skip, reclaims, and proceeds to pull
  (simulated with a reaped subprocess PID that is guaranteed dead).
- **Live holder, young lock** → script skips (simulated with a live `sleep` PID).
- **Aged-out lock with a live PID** → age backstop reclaims regardless of liveness
  (dir mtime backdated past the TTL).
- **Unknown pid (empty file), young lock** → skips conservatively (existing bare-dir
  collision tests already exercise the no-pid-file shape; keep them green).
- **Cleanup with a non-empty lock dir** → `rm -rf` removes the dir + pid file on exit
  (existing `test_lockfile_cleaned_up_on_exit` integration test asserts removal).
- **Pre-kickstart release with pid file present** → `lock=released` before the bridge
  kickstart (existing `test_bridge_kickstart_on_relevant_diff_with_plist` asserts this;
  it must stay green with `rm -rf`).

## Test Impact

- [ ] `tests/unit/test_remote_update_shell.py::test_lock_collision_without_marker_prints_generic_skip` — KEEP (bare dir = unknown pid, young → generic skip; behavior unchanged)
- [ ] `tests/unit/test_remote_update_shell.py::test_lock_collision_with_fresh_marker_prints_distinct_notice` — KEEP (bare dir, young → skip → distinct marker notice; unchanged)
- [ ] `tests/unit/test_remote_update_shell.py::test_bridge_kickstart_on_relevant_diff_with_plist` — KEEP: asserts `lock=released` pre-kickstart; must stay green after `rmdir`→`rm -rf` (a pid file is now present, so `rmdir` alone would fail)
- [ ] `tests/unit/test_remote_update_shell.py::test_lock_released_before_self_kill_second_run_not_skipped` — KEEP: unchanged
- [ ] `tests/integration/test_remote_update.py::test_lockfile_prevents_concurrent_runs` — KEEP: bare-dir collision (unknown pid, young) still skips
- [ ] `tests/integration/test_remote_update.py::test_lockfile_cleaned_up_on_exit` — KEEP: `rm -rf` still removes the dir
- [ ] ADD `test_lock_collision_dead_holder_pid_reclaims` — NEW: dead recorded PID → no skip, reclaims, proceeds to pull
- [ ] ADD `test_lock_collision_live_holder_pid_skips` — NEW: live recorded PID → skips
- [ ] ADD `test_lock_collision_aged_out_reclaims_regardless_of_liveness` — NEW: backdated lock + live PID → age backstop reclaims

## Rabbit Holes

- **TOCTOU on reclaim** — the `rm -rf; mkdir` reclaim is non-atomic (matches the pre-
  existing reclaim). On a lost race the second `mkdir` fails and the branch falls to
  `skip_locked` — never a double-run. Do not chase a fully-atomic reclaim; the update is a
  low-frequency cron/manual event.
- **PID reuse** — a dead holder's PID recycled to an unrelated live process would make
  `kill -0` succeed and skip; the age backstop (item 1) is the safety net and fires
  regardless of liveness once the TTL passes.
- **Do not lower the drain timeout to fit the TTL** — the TTL is set above the drain, not
  the other way around.

## Success Criteria

- [ ] A crashed prior run (dead recorded PID) no longer blocks updates: the next
  `remote-update.sh` invocation reclaims the lock immediately and proceeds to pull.
- [ ] A genuinely running update (live recorded PID, young lock) still causes a concurrent
  invocation to skip with `exit 0`.
- [ ] A lock older than `STALE_LOCK_TTL` is reclaimed regardless of the recorded PID's
  liveness (age backstop against PID reuse / wedged holders).
- [ ] The lock is released on all exit paths: normal exit and error (EXIT trap, `rm -rf`),
  and the explicit pre-bridge-kickstart release (`rm -rf`) still leaves `lock=released`
  before the kickstart.
- [ ] All existing `remote-update.sh` shell + integration lock tests stay green; the three
  new liveness-branch unit tests pass.
- [ ] `docs/features/remote-update.md` describes the new stale-lock handling.

## Documentation

- [ ] Update `docs/features/remote-update.md`: rewrite the lockfile Decision-table row
  (`mkdir`-based lockfile / `trap EXIT` cleanup) to describe the pid-file + liveness guard
  and the `rm -rf` release semantics.
- [ ] Add a "Stale lock handling" subsection to `docs/features/remote-update.md` documenting
  the four collision cases (live PID → skip, dead PID → reclaim, unknown PID young → skip,
  aged-out → reclaim) and the `STALE_LOCK_TTL` age backstop and its relationship to the
  300s worker-drain window.
