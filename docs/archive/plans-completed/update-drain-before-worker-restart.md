---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-18
tracking: https://github.com/tomcounsell/ai/issues/2141
last_comment_id:
---

# Update Flow Drains Sessions Before Worker Restart; No Orphaned Harness

## Problem

The ~30-min update cron (`com.valor.update` → `scripts/remote-update.sh`)
restarts the worker mid-PM-turn, discarding up to 20 minutes of in-flight
work per kill and orphaning the `claude -p` harness for the next boot's
reaper (issue #2141, observed 6× on 2026-07-17). Structural livelock: SDLC
pipelines push plan commits to main, arming the next update run to restart
the worker mid-BUILD.

Recon against the CURRENT script (worktree of main @ d2a6211c) shows the
worker-relevant-diff gate ALREADY exists (`NEED_RESTART`,
`remote-update.sh:235-242`: sha changed AND diff touches
`worker/ agent/ mcp_servers/ models/ tools/ bridge/ reflections/
pyproject.toml`) — docs/plans-only pushes already don't set it. The live
defects are:

1. **The label-not-found fallback branch restarts unconditionally.** When
   `launchctl list | grep -q "$WORKER_LABEL"` false-negatives, the else
   branch (`remote-update.sh:272+`) bootstraps/kickstarts with NO
   `NEED_RESTART` check — today's log shows `[update] Worker restarted` on
   runs where `BEFORE_SHA == AFTER_SHA` and the skip line never printed,
   proving this branch fires in production every cycle on this machine.
2. **No drain.** Even a legitimate (gated) restart fires while sessions are
   running; there is no busy-check, drain window, or defer.
3. **Worker SIGTERM orphans its harness.** `worker/__main__.py:987-996`
   waits up to 60s for active tasks, but launchd's kill grace is ~3-5s
   (observed "waiting…" → process gone in 1-5s), so the wait never
   completes and the `claude -p` child is orphaned until the next boot's
   orphan-reap SIGKILLs it.

## Freshness Check

**Baseline commit:** d2a6211c. Re-verified 2026-07-18:
- `remote-update.sh:100-109` BEFORE/AFTER sha capture around `git pull
  --ff-only`; `:235-242` NEED_RESTART diff gate; `:244-270` gated kickstart
  branch; `:272-308` ungated fallback bootstrap/kickstart branch — all hold.
- `worker/__main__.py:973-996` signal handler + 60s active wait — holds.
- Live update.log evidence: three consecutive runs with `git pull failed`
  (sha unchanged) + `[update] Worker restarted` and no skip line.

## Prior Art

- #1091 introduced the worker-relevant diff gate (works when reached).
- #1898 restart/verify race handling (RESTART_TS / VERIFY_SINCE) — the
  drain must not break the bounded beacon poll contract.
- #2136/#2137/#2149/#2145 (all merged) shrink the blast radius of the kills
  this plan prevents; they do not remove the cause.

## Solution

### Key Elements

1. **`scripts/update/drain.py` (new, unit-testable).**
   `count_running_sessions() -> int` via the AgentSession ORM (no raw
   Redis); `wait_for_idle(timeout_s, poll_s) -> bool` polls until 0 running
   or timeout. `python -m scripts.update.drain --timeout N --poll P` exits
   0 (idle → safe to restart) or 3 (still busy → DEFER). Any
   import/Redis error exits 0 with a stderr warning (fail-open: a broken
   probe must not wedge updates forever; loud line in update.log).

2. **`remote-update.sh` worker section rework:**
   - Compute `NEED_RESTART` exactly as today (gate + plan-commit exemption
     preserved).
   - **Fallback-branch fix:** if the label grep says not-loaded BUT a live
     worker process exists (`pgrep -f "python -m worker"`), treat as loaded
     (grep false-negative) and take the gated path instead of the
     unconditional bootstrap. Only a genuinely dead worker (no process) is
     bootstrapped unconditionally (that is recovery, not restart).
   - **Drain:** when `NEED_RESTART=true`, run the drain probe
     (`UPDATE_WORKER_DRAIN_TIMEOUT_S` default 300, poll
     `UPDATE_WORKER_DRAIN_POLL_S` default 10). On busy-timeout → **DEFER**:
     skip the restart, print
     `[update] Worker restart DEFERRED: N running session(s) after Xs drain — retrying next cycle`.
     Defer-not-force is chosen because the worker keeps serving on the
     previously-deployed code (same posture the bridge takes for config
     validation failures) and the next cycle retries ≤30 min later; a
     forced kill is exactly the #2141 failure. `[update] Worker restarted`
     marker semantics unchanged (only printed on an actual restart).

3. **`worker/shutdown_cleanup.py` (new): `terminate_harness_children(grace_s)`**
   — psutil enumeration of this process's descendants whose name/cmdline
   matches the `claude` harness; SIGTERM each, wait `grace_s` (default 1.5s),
   SIGKILL survivors; loud per-PID log
   `[shutdown] terminating in-flight harness PID %d (session turn abandoned)`.
   Called in `worker/__main__.py` shutdown: active-task wait bounded by
   `WORKER_SHUTDOWN_GRACE_S` (default 3s, honest about launchd's real
   grace) instead of 60s, then `terminate_harness_children()` in a
   try/except. Sessions are recovered by startup recovery exactly as today —
   the change removes the zombie-writes-to-recovered-worktree race, not the
   recovery.

## No-Gos

- No force-restart on drain timeout (defer only).
- No raw Redis in the drain probe (ORM only, enforced convention).
- No change to the `NEED_RESTART` diff-path list or the `[update] Worker
  restarted` stdout contract (#1898 beacon poll).
- No attempt to make PM turns checkpointable here (dropped in issue recon).

## Update System

This IS an update-system change. `remote-update.sh` + `scripts/update/`
propagate via the normal git pull on every machine's next cycle; the shell
edits take effect the run AFTER the pull that delivers them (the script
self-updates before executing the orchestrator — already-documented
behavior). New env knobs have safe defaults; no `.env` changes required.

## Agent Integration

No agent integration required — update-cron/worker internal. No new
`[project.scripts]` entry (module invoked as `python -m scripts.update.drain`
by the update script itself).

## Failure Path Test Strategy

- Drain probe Redis/import failure → exit 0 + stderr warning (fail-open,
  loud) — restart proceeds as today rather than wedging updates.
- Drain busy the whole window → exit 3 → shell defers, no restart lines.
- `terminate_harness_children` with no children / psutil errors → no-op,
  never raises into shutdown.
- SIGKILL-before-cleanup (launchd impatience) → behavior identical to
  today (next-boot reaper) — the cleanup is best-effort by design.

## Test Impact

- [ ] `tests/unit/test_update_drain.py` — NEW: count/wait/exit-code matrix
  with mocked AgentSession query (0 running → exit 0; N running whole
  window → exit 3; probe raises → exit 0).
- [ ] `tests/unit/test_worker_shutdown_cleanup.py` — NEW:
  `terminate_harness_children` terminates a matching fake child process,
  ignores non-matching, survives psutil exceptions.
- [ ] No existing tests affected — the touched shell branch has no test
  coverage today, and the worker shutdown sequence's 60s wait constant is
  asserted nowhere (verified by grep).

## Rabbit Holes

- Don't root-cause the `launchctl list` grep false-negative beyond the
  pgrep liveness cross-check — `launchctl print` parsing across macOS
  versions is a tar pit.
- Don't build a worker-side "update pending, finish and exit" handshake —
  the defer loop achieves the same outcome with zero new IPC.
- Don't touch the separate `.env`-copy / git-pull-failure warnings observed
  in the same log (tracked separately if they persist).

## Documentation

- [ ] `docs/features/bridge-worker-architecture.md` — add "Update restart
  semantics for in-flight sessions" subsection (drain, defer, child
  cleanup, env knobs).
- [ ] `docs/features/config-timeout-catalog.md` — raw-env note for
  `UPDATE_WORKER_DRAIN_TIMEOUT_S`, `UPDATE_WORKER_DRAIN_POLL_S`,
  `WORKER_SHUTDOWN_GRACE_S`.

## Success Criteria

- [ ] With a running session and a worker-relevant diff, the update defers
  (no restart) until sessions drain or the window expires (acceptance 1+2).
- [ ] Unchanged release / docs-only release → no restart from ANY branch,
  including the fallback (fixes the observed every-cycle restart).
- [ ] Worker SIGTERM leaves no orphaned `claude -p` when given ≥3s grace
  (acceptance 3).
- [ ] Unit tests cover drain decisions and child cleanup (acceptance 4).
- [ ] Restart semantics documented (acceptance 5).

## Verification

1. `pytest tests/unit/test_update_drain.py tests/unit/test_worker_shutdown_cleanup.py -n0`
2. `bash -n scripts/remote-update.sh` (syntax) + manual dry read of branch logic.
3. Live: next cron cycle on this machine logs either the skip line or a
   gated drain decision — never an ungated `Worker restarted`.
