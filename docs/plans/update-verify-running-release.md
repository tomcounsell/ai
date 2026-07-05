---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1898
last_comment_id:
revision_applied: true
---

# Update verifies the running-process release matches pulled HEAD

## Problem

On 2026-07-04 the Captain machine ran `/update`, which reported **✅ update OK @ 6b5b998a**. But the live bridge process kept reporting release `659756a4` — 52 commits behind, predating all 11 merges from the 2026-07-03/04 bug-slate. Sentry confirmed it: a production event at 2026-07-04T14:26:49Z with `server_name=Valor-the-Captain.local`, `release=659756a4463b`, `sys.argv=[.../bridge/telegram_bridge.py]`. The reason-aware interrupt copy (#1877), priming-liveness (#1878), wedge-nudge rung (#1879), and granite handshake fix (#1881) were all on disk but not in the running process, so Cuttlefish sessions kept wedging and kept sending the pre-#1877 hardcoded interrupt copy.

**Current behavior:**
Cron `/update` (the Telegram-triggered path and the 30-min polling cron) pulls code, runs migrations, sets a deferred restart flag, and reports success — all decoupled from whether the bridge or worker ever cycled onto the new code. The bridge has no cron-mode restart path at all; the worker's deferred restart can be starved by a wedged session and then silently expires after 1h. The update's "OK" makes no claim about the release the live processes are actually running.

**Desired outcome:**
`/update` verifies, after its restart step, that both the bridge and the worker are running code at the pulled HEAD, and it exits non-zero (loud, actionable) when a process still runs stale code. An update that fails to cycle the fleet onto the new code must not report OK.

## Freshness Check

**Baseline commit:** `63e43118`
**Issue filed at:** 2026-07-04T15:45:52Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `scripts/update/run.py:88-100` — `UpdateConfig.cron()` sets `do_service_restart=False` — still holds.
- `scripts/update/run.py:1555-1558` — cron mode sets restart flag instead of restarting — still holds.
- `scripts/update/run.py:1339` — bridge success gate checks only `service_status.running` — still holds.
- `scripts/update/service.py:71-105` — `get_service_status()` returns `running`/`pid`/`uptime`, no release — still holds.
- `agent/agent_session_queue.py:1209-1258` — `_check_restart_flag` / `_trigger_restart` (1h TTL, no-running-sessions gate) — still holds.
- `agent/agent_session_queue.py:1780`, `:2203` — only the worker loop consumes the flag — still holds.
- `bridge/telegram_bridge.py:2985` — bridge only *clears* a stale flag on startup, never triggers restart — still holds.
- `monitoring/sentry_config.py:61` — `release=git rev-parse HEAD` captured at process init — still holds (explains the frozen Sentry release).

**Cited sibling issues/PRs re-checked:**
- #1877, #1878, #1879, #1881 — the bug-slate merges whose code was on disk but not running; all merged before the issue. Context only; not blockers.

**Commits on main since issue was filed (touching referenced files):**
- `313724f3` "Fix Tier 0: plan-migration invariant (#1900, PR 1/2)" — irrelevant to restart/release logic (plan-migration hook).

**Active plans in `docs/plans/` overlapping this area:** none. (`consolidate_delivery_paths.md` and `granite_lossless_checkpoint_resume.md` touch delivery/PTY, not the update/restart path.)

**Notes:** No drift. Re-verified at the revision baseline `3d527474` (2026-07-05): all commits between `63e43118` and HEAD are plan-doc commits only — none touch `scripts/update/`, `agent/agent_session_queue.py`, `bridge/telegram_bridge.py`, or `worker/__main__.py`. All line references still current. Confirmed at revision: `_RESTART_FLAG = data/restart-requested` and `_RESTART_FLAG_TTL = 1h` (`agent_session_queue.py:1203/1206`); the polling cron runs every 30 min (`scripts/update/run.py:1538`, `scripts/remote-update.sh:155`).

## Prior Art

- **#1767** "Worker watchdog fails to recover a U-state hung worker" (closed 2026-06-25) — hardened worker recovery, but did not touch how `/update` verifies the running release. Relevant only as prior evidence that wedged/hung processes are a recurring failure mode that starves the deferred-restart path.
- **#1815 / #1817 / #1877** (closed) — resilience and lifecycle work on wedge survival and interrupt messaging. These are the *payload* that failed to reach the running bridge; none of them added release verification to the updater.
- **PR #1832** "worker fault containment" (merged 2026-06-30) — added worker fault handling but no updater-side release check.

No prior issue or PR added a post-restart release-verification gate to `/update`. This is greenfield for the updater. The "Why Previous Fixes Failed" section is omitted — no prior attempt targeted this specific gap.

## Research

No relevant external findings — this is purely internal (macOS launchd, git rev-parse, Redis/Popoto, the repo's own update system). Proceeding with codebase context.

## Data Flow

Trace of how a code change reaches (or fails to reach) the running processes under cron `/update`:

1. **Entry point**: Telegram `/update` (or the 30-min polling cron) → `scripts/remote-update.sh` → `python -m scripts.update.run --cron`.
2. **Git pull** (`run.py` Step 3): `origin/main` pulled to disk; HEAD becomes the new SHA (e.g. `6b5b998a`).
3. **Migrations** (Step 3.6): run against the pulled code.
4. **Service management** (Step 5): `do_service_restart` is **False** in cron mode → the `if config.do_service_restart:` block is skipped entirely. Bridge and worker are NOT restarted.
5. **Restart flag** (`run.py:1555-1558`): `git.set_restart_requested()` writes `data/restart-requested` with a timestamp + commit count.
6. **Flag consumption** (worker only): `agent/agent_session_queue.py:1780`/`:2203` call `_check_restart_flag()` between sessions. Returns True only if flag < 1h old AND no session has `status="running"`. On True → `_trigger_restart()` sends SIGTERM → launchd KeepAlive respawns the **worker** on new code.
   - **Failure branch A (bridge):** the bridge never checks the flag. It keeps running old code until a full `/update` (`install_service`), a crash+KeepAlive respawn, or a watchdog restart. → the observed `659756a4` bridge.
   - **Failure branch B (worker):** if a session is wedged in `status="running"` for >1h, `_check_restart_flag()` returns False every cycle and then deletes the flag as stale. The worker also stays on old code, and nothing reports it.
7. **Success summary** (`run.py:1867-1906`): status derived from `result.success` (git + migrations + best-effort service checks). Reports "update successful" / "updated to {sha}". **No comparison of running-process release vs. HEAD.**

The fix inserts a new terminal step between (6) and (7): read each in-role process's **boot SHA** and compare to HEAD. This step runs on **every** service-managed run — full mode *and* every cron cycle, including the frequent no-op polling cycles where `commit_count == 0` — so a beacon left stale by a starved deferred restart is re-classified and escalated on a subsequent cron run rather than only at the single flag-write moment.

## Architectural Impact

- **New durable signal (boot-SHA beacon):** the bridge and worker each record, at startup, the git SHA they were launched at, to a known location the updater can read without touching the process. This is additive.
- **Interface changes:** `service.get_service_status()` / `get_worker_status()` (or a new helper) gains a `boot_sha` field; a new `verify_running_release()` function in `scripts/update/service.py`. `run.py` gains a verification step.
- **Coupling:** low. The beacon is a one-line write at startup and a file read at verify time. No new runtime dependency between bridge and worker.
- **Data ownership:** each process owns its own beacon file; the updater is a read-only consumer + the process that resets HEAD.
- **Reversibility:** high. The verification step is a bounded gate; disabling it is a one-line revert. The beacon writes are inert if unread.

## Appetite

**Size:** Medium

**Team:** Solo dev, plan critique, code review

**Interactions:**
- PM check-ins: 0-1 (the cron-mode escalation policy is resolved in the Decisions section — per-process branching; no open policy call remains)
- Review rounds: 1 (one critique round completed; this is the post-critique revision)

## Prerequisites

No prerequisites — this work has no external dependencies. It runs against the repo's own update system, git, and launchd (already present on every machine). Executable proof (acceptance criterion 3) requires running on a bridge machine, which is captured as an `[EXTERNAL]` No-Go step, not a build-time prerequisite.

## Solution

### Key Elements

- **Boot-SHA beacon**: at startup the bridge writes its launch SHA to `data/bridge_boot_sha`, and the worker writes its launch SHA to `data/worker_boot_sha` (via `git rev-parse HEAD` at process init, mirroring `monitoring/sentry_config.py:61`). Each write includes the SHA and a timestamp so a stale/orphaned beacon is detectable.
- **Release-verification step** in the updater: a new `verify_running_release()` reads both beacons and compares them to the pulled HEAD. It runs as the last step of a service-restart run.
- **Mode-aware verdict** (per-process, positive-staleness gated):
  - **Full mode** (`do_service_restart=True`): the restart already happened synchronously in Step 5. Verify bridge AND worker boot SHA == HEAD. On mismatch → `result.success = False`, loud error, exit non-zero.
  - **Cron mode** (`do_service_restart=False`): the restart is deferred. A single stale reading immediately after flag-set is expected (the process drains its session first), so verification distinguishes "a fresh restart is pending" (warn) from "the restart has been starved" (escalate). **Only positive staleness escalates** — a process is deemed genuinely stale only when `beacon_ts > process_start_ts` (where `process_start_ts` is the process's absolute launch time from `ps -o lstart`, read via the shared `get_process_start_ts(pid)` helper generalized from `get_bridge_process_start_ts` — see Technical Approach; the beacon must belong to the *current* process image) **AND** `boot_sha != get_short_sha(HEAD)`. A missing beacon, or a beacon that predates the process start, classifies as **UNKNOWN → warn**, never escalate — so a swallowed best-effort beacon write can never invert into a false force-restart of a healthy process.
  - **Per-process escalation branch** (resolves the internal contradiction the critique flagged): the escalation target is never uniform across bridge and worker.
    - **Bridge, positively stale + starved** (flag/pending-window exceeded): force-restart the bridge via the same synchronous path full mode uses (`service.install_service` / service restart) and re-verify once; still stale → hard-fail. This is **safe** — the bridge holds no agent sessions, so force-restarting it interrupts nothing.
    - **Worker, positively stale + starved**: **never force-killed.** Hard-fail loud + out-of-band alert; leave the worker to its own `_check_restart_flag` session-running defer. Force-killing a busy (not necessarily wedged) worker session is indistinguishable from a wedge on the beacon alone, which would cross the #1815/#1877 wedge No-Go this plan scopes out.
- **Bridge cron-mode path to new code**: the bridge reaches new code in cron mode via the **verification-driven force-restart above** (safe, no sessions), NOT by consuming the worker's shared `data/restart-requested` flag. Reusing that single consumable flag is a first-reader-wins race — whichever of bridge/worker checks first unlinks it and the other never restarts, reproducing the exact #1898 bug. A dedicated idle-gated bridge self-restart flag (`data/bridge-restart-requested`) that would let the bridge converge on its *own* next idle boundary — before the next cron verify — is a distinct, orthogonal optimization deferred to its own slug (see No-Gos). The worker keeps exclusive ownership of `data/restart-requested`, unchanged.

### Flow

Cron `/update` runs → git pull to new HEAD → migrations → set restart flag (worker only) → (worker cycles when idle) → **verify step reads `data/bridge_boot_sha` + `data/worker_boot_sha`** → both positively == HEAD? → report OK. A process is positively stale (fresh beacon belongs to current image AND `boot_sha != HEAD`) AND the pending window is exceeded → escalate **per-process**: bridge → force-restart (safe, no sessions) + re-verify, else hard-fail; worker → hard-fail + out-of-band alert, never force-kill. Beacon missing / predates process → UNKNOWN → warn, exit "bridge/worker release could not be confirmed" without force-restarting.

### Technical Approach

- **Beacon write**: add a small helper (e.g. `monitoring/boot_beacon.py` or a function in `agent/agent_session_queue.py` alongside the flag helpers) that writes `{sha}\n{iso-timestamp}` to `data/{bridge,worker}_boot_sha`. Call it once at bridge startup (near `bridge/telegram_bridge.py:2985`, where the stale flag is already cleared) and once at worker startup (`worker/__main__.py`). Use `git rev-parse HEAD` with the same subprocess pattern as `monitoring/sentry_config.py:61` and `monitoring/crash_tracker.py:59`. Writes are best-effort (swallow FS errors, never crash startup).
- **Process-start primitive (the `process_start_ts` source — corrected)**: `get_service_status()` (`scripts/update/service.py:71`) and `get_worker_status()` (`:166`) parse only `ps -o etime` — an *elapsed duration* (e.g. `01-04:22:10`), never an absolute start time. An elapsed duration cannot be compared against the beacon's absolute ISO timestamp, so the positive-staleness gate (`beacon_ts > process_start_ts`) **cannot** reuse `get_service_status`. The only `lstart`-based (absolute-launch-time) primitive in the repo is `get_bridge_process_start_ts(pid)` (`monitoring/bridge_watchdog.py:130`), which is bridge-*named* but already fully pid-parameterized and returns a UTC unix timestamp (None on any error). **Generalize it into a process-agnostic `get_process_start_ts(pid) -> float | None`** (move it to `scripts/update/service.py`, or a shared util both `bridge_watchdog` and `service` import — leaving no duplicate lstart parser), and call it with `get_bridge_pid()` (`service.py:55`) and `get_worker_pid()` (`service.py:144`). A `None` return (unparseable/missing) makes `process_start_ts` unknown → the process classifies `unknown` (fail-safe, never `stale`).
- **Verify helper**: `scripts/update/service.py::verify_running_release(project_dir, head_sha) -> ReleaseCheck` returning per-process `{running, boot_sha, beacon_ts, process_start_ts, classification}` where `classification ∈ {matches, stale, unknown}`. `matches` = `boot_sha == get_short_sha(HEAD)`; `stale` (positive staleness) = `beacon_ts > process_start_ts AND boot_sha != HEAD`; `unknown` = beacon missing, empty, malformed, `process_start_ts` is None, or `beacon_ts <= process_start_ts` (orphaned/predates the current image). Reads the beacon files; reuse `git.get_short_sha()` for HEAD and the generalized `get_process_start_ts(pid)` above for `process_start_ts`.
- **run.py wiring** — verify is called **UNCONDITIONALLY after the `if/elif config.do_service_restart` block** (after `run.py:1558`, before Step 5.5 at `:1560`), **NOT** nested inside the `elif result.git_result and result.git_result.commit_count > 0` cron branch. Nesting it in that branch would run verify exactly once, at flag-write time (beacon age relative to a just-set flag → always `pending`), and it would never re-run on a later **no-op cron cycle** (`commit_count == 0`) to re-classify a starved beacon — making the cron `starved` escalation (the core new capability for #1898) practically unreachable. Placing it after the block lets every service-managed cron run (including the frequent commit-count-0 polling cycles) re-evaluate a still-stale beacon and escalate. Mode is read from `config.do_service_restart`, not from which branch executed:
  - **Machine-role gate (per-process)**: verify the **bridge** only when `machine_check.get("bridge_projects")` is truthy, and the **worker** only when `machine_check.get("projects")` is truthy (the same gates Step 5 uses at `run.py:1041`/`:1058`). A machine lacking a role **skips that process entirely** — no beacon read, no classification — so a non-bridge machine never emits a permanent "bridge release could not be confirmed" warning.
  - **Full mode** (`config.do_service_restart` True): on any in-role process classified `stale` append an error and set `result.success = False`. `unknown` → warn (the restart may have raced the beacon write — the freshness poll in Race 1 covers the legitimate case).
  - **Cron mode** (`config.do_service_restart` False): classify `pending` (positively stale but flag younger than the 30-min pending window — the deferred restart hasn't had its chance yet) → warning, or `starved` (positively stale AND flag older than the pending window, **or the flag is already absent/expired while the beacon still lags** — the no-op-cron case) → escalate **per-process** (see Escalation).
- **Escalation implementation** (cron starved, per-process — the two branches never share a policy):
  - **Bridge stale+starved**: `service.install_service` / bridge restart (the synchronous path full mode uses) + re-verify once; still stale → hard-fail. Safe because the bridge holds no sessions. The re-verify **must reuse the bounded beacon-freshness poll from Race 1** (poll for a bridge beacon whose `beacon_ts` post-dates the restart moment), not a bare single read — otherwise the cron re-verify races the just-restarted bridge's own startup beacon write and can false-fail a bridge that is in fact coming up on new code.
  - **Worker stale+starved**: do NOT force-kill. Set `result.success = False`, emit the out-of-band alert, and let `_check_restart_flag`'s own `status="running"` defer stand — a busy worker is not force-interrupted.
  - `unknown` (either process): warn only; never restart, never fail on staleness the verifier cannot positively confirm.
- **Out-of-band alerting** (scoped to the one case the Telegram channel can't cover itself): the filesystem sentinel + watchdog read exists specifically because a **bridge that ends down after a forced restart** cannot deliver its own Telegram alarm — `run.py:1331-1345` can legitimately end with "Bridge not running after restart", which disables the very channel meant to report it. So: on a **bridge** hard-fail / bridge-down-after-restart, write a filesystem sentinel `data/update-release-failed` (SHA lag + timestamp) that `monitoring/bridge_watchdog.py` reads on its 60s health cycle. A **worker** hard-fail keeps its non-zero exit + Sentry capture but does **not** need the sentinel/watchdog path (the updater process and the Telegram channel are both still alive to report it) — keeping the sentinel scoped to the bridge-channel-dead case avoids over-building. Sentry capture via `monitoring/sentry_config.py` fires on any hard-fail (bridge or worker) as the durable off-machine record.
- **Summary surfacing**: extend the cron summary (`run.py:1867-1906`) so a release mismatch/pending explicitly names the stale process AND its lagging short-SHA (e.g. "bridge running 659756a4 but HEAD is 6b5b998a") in the Telegram status line, not only in the log file. This string is an operator-facing acceptance artifact (see Success Criteria), asserted off-bridge in tests.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Beacon writes are wrapped in best-effort try/except (like `_green_heartbeat_write` at `worker/__main__.py:260`). Add a test asserting a write failure (unwritable `data/`) logs a warning and does NOT crash startup.
- [ ] `verify_running_release()` must not raise on a missing beacon file — test the missing-file path returns a well-formed "unknown/stale" result, not an exception.

### Empty/Invalid Input Handling
- [ ] Test `verify_running_release()` classification with: missing beacon (→ unknown), empty beacon (→ unknown), malformed beacon / no timestamp (→ unknown), beacon SHA == HEAD (→ matches), beacon SHA != HEAD with `beacon_ts > process_start_ts` (→ stale), beacon SHA != HEAD with `beacon_ts <= process_start_ts` / orphaned (→ unknown).
- [ ] Test the cron classifier: positively-stale beacon + fresh flag (< 30 min) → pending/warn; positively-stale beacon + flag older than the 30-min pending window → starved/escalate; positively-stale beacon + already-expired flag → starved/escalate.
- [ ] **No-op cron re-verify (blocker-2 regression guard)**: a cron run with `commit_count == 0` (no new commits pulled) and a positively-stale beacon whose flag is already absent/expired → the unconditional verify step still runs and fires the `starved` per-process escalation. Assert verify is invoked and escalation triggers even though the `elif ... commit_count > 0` cron branch never executed. This is the test that fails if verify is (wrongly) nested inside the commit-count branch.
- [ ] **`get_process_start_ts` generalization**: assert the shared helper computes an absolute start timestamp for a worker PID (not just a bridge PID), and that a positively-stale classification uses that absolute `process_start_ts` (a beacon `> process_start_ts` with a lagging SHA classifies `stale`; a beacon `<= process_start_ts` classifies `unknown`).
- [ ] **Machine-role gate (concern b)**: a machine with no bridge role (`machine_check["bridge_projects"]` falsy) skips bridge verification entirely — no "bridge release could not be confirmed" warning is emitted; a machine with no worker role skips worker verification.
- [ ] **Swallowed-write inversion guard**: a beacon-write failure (unwritable `data/`) leaving a missing/orphaned beacon must classify UNKNOWN → warn, and MUST NOT trigger a force-restart of the (healthy) process. Assert no restart is invoked in this path.

### Per-Process Escalation
- [ ] Bridge positively-stale+starved → bridge force-restart invoked (mock `install_service`) + one re-verify; worker restart path NOT invoked.
- [ ] Worker positively-stale+starved → `result.success=False` + out-of-band alert, and the worker is NEVER force-killed (assert no SIGTERM / restart_worker call).
- [ ] Out-of-band alert fires on hard-fail: Sentry capture invoked AND `data/update-release-failed` sentinel written, independent of whether the Telegram line was delivered.

### Error State Rendering
- [ ] Full-mode `stale` surfaces a non-zero exit and a clear error string naming both short-SHAs. Test the exit code and message.
- [ ] Cron-mode escalation surfaces the mismatch in the Telegram summary line naming the stale process and its lagging SHA (not only the attached log). Test the summary builder includes the release warning string. (Operator-facing acceptance check — runs off-bridge.)

## Test Impact

- [ ] `tests/unit/` (update-system tests, e.g. `test_update_service.py` / `test_update_run.py` if present) — UPDATE: add coverage for `verify_running_release()` positive-staleness/unknown classification, the per-process escalation branches, the mode-aware verdict, the unconditional-cron-verify no-op cycle (blocker 2), the generalized `get_process_start_ts` worker-PID path (blocker 1), and the machine-role gate (concern b); assert full-mode `stale` sets `result.success=False`.
- [ ] `monitoring/bridge_watchdog.py` tests, if any pin `get_bridge_process_start_ts` by name — UPDATE: the function is renamed/moved to shared `get_process_start_ts`; update the import/reference. Behavior (lstart parsing, None on error) is unchanged.
- [ ] Worker/bridge startup tests that assert startup side effects — UPDATE: add assertion that the boot-SHA beacon is written at startup.
- [ ] `agent/agent_session_queue.py` restart-flag tests — UPDATE ONLY the `_trigger_restart` docstring assertion if any test pins the string; the SIGTERM target and flag mechanics are unchanged. This plan does NOT add bridge consumption of `data/restart-requested` (that would introduce the first-reader-wins race #1898 closes), so no new shared-flag test is added here.

If no dedicated update-system unit test file exists, this is greenfield for that module — create `tests/unit/test_update_release_verify.py`. No existing test asserts release verification today, so nothing needs DELETE/REPLACE; changes are additive to startup and the updater's terminal step.

## Rabbit Holes

- **Do not rebuild the restart mechanism.** The launchd KeepAlive + SIGTERM graceful-restart model works; the gap is verification and the missing bridge path. Resist redesigning the whole restart lifecycle.
- **Do not try to read the running process's in-memory code SHA.** A file/Redis beacon written at startup is the durable, testable signal. Inspecting a live process's loaded modules is fragile and unnecessary.
- **Do not solve wedged-session detection here.** That is the resilience workstream (#1815/#1877). This plan bounds the *consequence* (a starved restart must escalate, not silently expire), not the wedge itself.
- **Do not couple the beacon to Sentry.** Sentry release is external and only visible after an event fires; the updater needs a local, synchronous signal.

## Risks

### Risk 1: Cron-mode false failures during legitimate session draining
**Impact:** A hard "release == HEAD" check right after a cron update would fail every time a session is legitimately mid-flight (the intended deferral), turning normal updates red.
**Mitigation:** The cron path classifies `pending` (fresh flag, process draining) as a warning, not a failure; only a `starved` state (flag older than the pending window, or beacon still stale) escalates. The staleness window is the Open Question to confirm.

### Risk 2: Forced restart interrupts an in-flight session
**Impact:** A force-restart could kill a running agent session mid-turn.
**Mitigation:** The escalation force-restart **only ever targets the bridge**, which holds no agent sessions — so it interrupts nothing. The worker (the only process that runs sessions) is **never force-killed**: a positively-stale+starved worker hard-fails loud and defers to `_check_restart_flag`'s own `status="running"` gate, which already waits for the session to drain. This removes the internal contradiction the critique flagged (the prior "interrupts in-flight session" mitigation named a scenario that cannot occur for the bridge and must not occur for the worker). A stale worker running known-broken code is surfaced loudly for the operator rather than silently force-restarted.

### Risk 3: Beacon staleness / orphaned beacon file
**Impact:** A beacon left by a previous process image could read as "current" and mask a stale process, or (worse) invert into a false escalation.
**Mitigation:** The beacon stores a timestamp; the verifier cross-checks `beacon_ts` against the process's absolute start time from the shared `get_process_start_ts(pid)` helper (generalized from `get_bridge_process_start_ts`, `ps -o lstart`). Note `get_service_status` reads only `ps -o etime` — an elapsed duration that is *not* comparable to an absolute beacon timestamp — so it is not the source here. A beacon whose timestamp is `<= process_start_ts` (or when `process_start_ts` is None) is classified **unknown → warn**, never `stale` and never a `match`. Only `beacon_ts > process_start_ts AND boot_sha != HEAD` (positive staleness) can escalate.

### Risk 4: Best-effort beacon write failure inverts into a false force-restart
**Impact:** Beacon writes swallow FS errors (never crash startup). If the verdict treated a missing/orphaned beacon as authoritative "stale", a swallowed write on a healthy up-to-date process would trigger a false force-restart.
**Mitigation:** Missing / empty / malformed / predates-process beacons all classify **unknown → warn**, which never escalates and never fails the run on staleness grounds. Escalation requires *positive* confirmation the live process is on old code (a fresh beacon belonging to the current image whose SHA lags HEAD). A swallowed write can only ever downgrade to a warning, never invert to a force-restart.

## Race Conditions

### Race 1: Verify reads the beacon before the restarted process has rewritten it
**Location:** `scripts/update/run.py` verify step vs. `bridge`/`worker` startup beacon write.
**Trigger:** In full mode, `verify_running_release()` runs right after `install_service`; the process may have been bootstrapped but not yet reached its startup beacon write.
**Data prerequisite:** the restarted process must have written its `*_boot_sha` beacon before the verifier reads it.
**State prerequisite:** the beacon's timestamp must be newer than the restart moment for the reading to be trusted.
**Mitigation:** the verify step polls the beacon (bounded, e.g. reuse the existing 20s/30s startup poll windows in Step 5) for a beacon whose timestamp post-dates the restart, mirroring the worker-heartbeat freshness check at `run.py:1381-1400`. A beacon that never freshens within the window → mismatch/escalate (the process failed to come up on new code — exactly what we want to catch).

### Race 2: Restart flag set-then-consumed vs. verify in cron mode
**Location:** `git.set_restart_requested` (run.py:1558) vs. worker `_check_restart_flag` (agent_session_queue.py:1780/2203) vs. verify step.
**Trigger:** verify runs while the worker is between the flag-set and its next idle check.
**Data prerequisite:** the pending/starved classification must not race on the flag file being deleted by `_check_restart_flag` mid-read.
**State prerequisite:** classification reads flag age + beacon age atomically enough to be stable.
**Mitigation:** classification is read-only and tolerant — a missing flag with a fresh beacon == HEAD reads as success; a missing flag with a stale beacon reads as starved (the flag expired without cycling). No write contention is introduced by the verifier.

## No-Gos (Out of Scope)

- `[EXTERNAL]` Running `/update` on the Captain (or any bridge machine) to capture the executable proof for acceptance criterion 3 — the agent's dev machine has no Telegram bridge role, so the release-verification output must be captured on a real bridge machine by the operator. The build produces the verification step and a local/full-mode test; the on-bridge proof run is the human-gated step.
- `[SEPARATE-SLUG]` A dedicated idle-gated bridge self-restart flag (`data/bridge-restart-requested` with a bridge-side consumer mirroring `_check_restart_flag` semantics) that would let the bridge converge on new code at its *own* next idle boundary, before the next cron verify. This is an orthogonal optimization: the DESIRED outcome ("verify both == HEAD and exit non-zero / escalate") is fully delivered by verification plus the verifier-driven bridge force-restart in *this* plan. A new self-restart code path in the bridge is a distinct capability that should ship on its own issue after verification lands. Explicitly NOT reusing the worker's `data/restart-requested` flag (first-reader-wins race).
- `[SEPARATE-SLUG]` Fixing the underlying session-wedge that starves the restart (resilience workstream) — not filed under a new issue here because this plan only bounds the *consequence*; if a dedicated wedge issue is desired it should be filed separately. (No `[SEPARATE-SLUG #NNN]` tag claimed since no issue is being asserted — this item is explicitly out of scope with the wedge tracked by the existing resilience issues #1815/#1877.)

Everything else relevant — the beacon, the verify step, the per-process mode-aware verdict, the verifier-driven bridge force-restart, the `_trigger_restart` docstring correction, and the tests — is in scope for this plan.

## Update System

This bug **is** in the update system, so the change is intrinsically to `/update`:
- `scripts/update/run.py` — new per-process verification step wired into the service-restart path; cron summary names the stale process + lagging SHA; out-of-band failure sentinel + Sentry capture on hard-fail.
- `scripts/update/service.py` — new `verify_running_release()` (positive-staleness/unknown classification) + `boot_sha`/`beacon_ts` on status; home of the generalized `get_process_start_ts(pid)` (moved from `bridge_watchdog`) reused for both bridge and worker.
- `monitoring/bridge_watchdog.py` — `get_bridge_process_start_ts` is generalized to a shared `get_process_start_ts(pid)` and re-imported here (leaving no duplicate lstart parser); also reads the `data/update-release-failed` sentinel on its 60s cycle so a **bridge-down-after-restart** failure is surfaced even when the Telegram channel is dead (sentinel scoped to that bridge-channel-dead case only).
- `bridge/telegram_bridge.py` + `worker/__main__.py` — write boot-SHA beacons at startup.
- `agent/agent_session_queue.py` — string-only corrections (no behavior change, MUST NOT change the SIGTERM target): (1) the `_trigger_restart` docstring, and (2) the sibling misleading log line at `:1245` (`"...— restarting bridge"` inside `_check_restart_flag`, which runs in the worker loop and actually restarts the worker) — both must state it SIGTERMs the WORKER PID and launchd respawns the worker, not the bridge.
- No new deps to propagate. No `migrations.py` change (beacon files and the failure sentinel are inert, self-healing, written on next startup / next failed update — no data migration needed). The change propagates to all machines via the normal `/update` git pull; the first post-merge full `/update` restarts the fleet and begins writing beacons.

## Agent Integration

No agent integration required — this is entirely internal to the update system and process startup. No new MCP tool, no `.mcp.json` change, no new bridge-imported code surface. The agent already invokes `/update` via the existing `remote-update.sh` / cron path; that path gains the verification step without a new entry point.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` (or the update-system doc) to describe the boot-SHA beacon and the post-restart release-verification gate, including the cron-mode pending-vs-starved policy, the per-process escalation branch (bridge force-restart safe; worker never force-killed), and the out-of-band failure sentinel.
- [ ] Add/refresh an entry in `docs/features/README.md` index for the release-verification behavior.

### Inline Documentation
- [ ] Docstrings on `verify_running_release()` and the beacon-writer explaining the positive-staleness vs. unknown classification and the beacon-freshness (`beacon_ts > process_start_ts`) cross-check.
- [ ] Comment at the `run.py` verify step explaining why cron mode does not hard-fail on a fresh pending restart, and why only the bridge is force-restarted.
- [ ] **`_trigger_restart` docstring correction** (`agent/agent_session_queue.py:1250`): rewrite the current "graceful bridge restart" / "Launchd KeepAlive restarts the process" wording to state it SIGTERMs the **worker** PID from inside the worker loop, and launchd's KeepAlive respawns the **worker** (not the bridge). This misleading on-disk artifact plausibly seeded the operator's false trust that `/update` cycled everything. Documentation-only — do NOT change the SIGTERM target.
- [ ] **Sibling log-line correction** (`agent/agent_session_queue.py:1245`): the `_check_restart_flag` info line `"Restart flag found (...), no running sessions — restarting bridge"` runs in the worker loop and restarts the **worker**, not the bridge. Change "restarting bridge" → "restarting worker". String-only; same misleading-artifact class as the docstring.

## Success Criteria

- [ ] Root cause documented (this plan's Recon/Data Flow): cron `/update` never restarts the bridge and its worker restart can be starved+expired, with no release verification. Includes the `_trigger_restart` docstring correction (the misleading "restarts the bridge" artifact). (Acceptance criterion 1.)
- [ ] Bridge and worker write a boot-SHA beacon (SHA + ISO timestamp) at startup.
- [ ] `/update` verifies bridge AND worker running release == pulled HEAD after the restart step and exits non-zero on positively-stale mismatch in full mode; in cron mode escalates **per-process** — bridge force-restart (safe, no sessions), worker hard-fail without force-kill — rather than silently passing. Only positive staleness (`beacon_ts > process_start_ts AND boot_sha != HEAD`) escalates; unknown → warn. (Acceptance criterion 2.)
- [ ] The bridge has a cron-mode path to reach new code via the verifier-driven force-restart (the dedicated idle-gated self-restart flag is deferred to its own slug per No-Gos).
- [ ] **Operator-facing (off-bridge):** the cron summary string (`run.py:1867-1906`) explicitly names the stale process and its lagging short-SHA (e.g. "bridge running 659756a4 but HEAD is 6b5b998a") — asserted in a unit test, independent of the bridge-machine proof. This is the human-visible artifact that the original "misleading update OK" bug lacked.
- [ ] Out-of-band failure signal (Sentry capture + `data/update-release-failed` sentinel) fires on hard-fail so a bridge-down-after-restart cannot silence its own alarm.
- [ ] Executable proof captured on a bridge machine: `/update` run output showing the release-verification step (SHA match, or a deliberate mismatch producing non-zero exit). (Acceptance criterion 3 — operator-gated per No-Gos.)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] grep confirms `run.py` calls `verify_running_release` and both startup paths call the beacon writer.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (beacon + verify + escalation)**
  - Name: `update-verify-builder`
  - Role: Add boot-SHA beacon writes (bridge + worker) and `verify_running_release()` (positive-staleness/unknown classification); wire the per-process mode-aware verify step into `run.py`, including the verifier-driven bridge force-restart, the worker hard-fail-without-force-kill branch, and the out-of-band alert (Sentry + `data/update-release-failed` sentinel + `bridge_watchdog` read).
  - Agent Type: builder
  - Domain: async/process-lifecycle
  - Resume: true

- **Builder (root-cause docstring correction)**
  - Name: `docstring-correction-builder`
  - Role: Rewrite the misleading `_trigger_restart` docstring (`agent/agent_session_queue.py:1250`) to state it SIGTERMs the WORKER PID (launchd respawns the worker, not the bridge). Documentation-only; MUST NOT change the SIGTERM target. Small task, can fold into `update-verify-builder` if preferred.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `release-verify-tester`
  - Role: Unit tests for `verify_running_release()` classification, beacon freshness, full-mode hard-fail, cron pending-vs-starved, and startup beacon writes.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `release-verify-validator`
  - Role: Verify acceptance criteria and Verification-table checks.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `release-verify-docs`
  - Role: Update self-healing / update-system docs and index.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Boot-SHA beacon writers
- **Task ID**: build-beacon
- **Depends On**: none
- **Validates**: tests/unit/test_update_release_verify.py (create), startup-side-effect tests
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a best-effort beacon writer (SHA + ISO timestamp) writing `data/bridge_boot_sha` and `data/worker_boot_sha`.
- Call it at bridge startup (near `bridge/telegram_bridge.py:2985`) and worker startup (`worker/__main__.py`), swallowing FS errors.

### 2. verify_running_release() + run.py per-process wiring
- **Task ID**: build-verify
- **Depends On**: build-beacon
- **Validates**: tests/unit/test_update_release_verify.py
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: false
- **Generalize the process-start primitive**: move `get_bridge_process_start_ts(pid)` (`monitoring/bridge_watchdog.py:130`) to a shared, process-agnostic `get_process_start_ts(pid) -> float | None` in `scripts/update/service.py` (re-imported by `bridge_watchdog`; leave no duplicate lstart parser). `get_service_status`/`get_worker_status` parse only `etime` (elapsed) — NOT usable for the absolute `beacon_ts > process_start_ts` gate. Call the generalized helper with `get_bridge_pid()` and `get_worker_pid()`.
- Add `verify_running_release()` to `scripts/update/service.py` returning per-process `{running, boot_sha, beacon_ts, process_start_ts, classification}` with `classification ∈ {matches, stale, unknown}`; `stale` requires positive staleness (`beacon_ts > process_start_ts AND boot_sha != get_short_sha(HEAD)`); everything ambiguous — including `process_start_ts is None` — → `unknown`.
- Wire the verify step into `run.py` **UNCONDITIONALLY after the `if/elif config.do_service_restart` block** (after `:1558`, before Step 5.5 at `:1560`) — NOT inside the `elif ... commit_count > 0` cron branch, so it re-runs on no-op cron cycles to re-classify starved beacons. Read mode from `config.do_service_restart`. Gate each process on machine role (`machine_check["bridge_projects"]` for bridge, `machine_check["projects"]` for worker) — skip a process this machine doesn't run. Full mode → hard-fail on `stale`; cron mode → pending (warn, flag < 30-min window) vs. starved (escalate). Escalation branches **per-process**: bridge stale+starved → force-restart via `install_service` + one re-verify (using the Race-1 bounded beacon-freshness poll), else hard-fail; worker stale+starved → `result.success=False` WITHOUT force-kill; `unknown` → warn.
- Emit the out-of-band alert on hard-fail: Sentry capture on any hard-fail; write `data/update-release-failed` on the **bridge-down-after-restart** case and make `monitoring/bridge_watchdog.py` read that sentinel on its 60s cycle (sentinel scoped to the bridge-channel-dead case).
- Cron summary (`run.py:1867-1906`) names the stale process + lagging short-SHA.

### 3. Root-cause docstring correction + watchdog sentinel
- **Task ID**: build-docstring-fix
- **Depends On**: none
- **Validates**: agent/agent_session_queue restart-flag tests (docstring assertion only)
- **Assigned To**: docstring-correction-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite the `_trigger_restart` docstring (`agent/agent_session_queue.py:1250`) to state it SIGTERMs the WORKER PID; launchd respawns the worker, not the bridge. Do NOT touch the SIGTERM target or flag mechanics. (This is the on-disk artifact that seeded the operator's false trust.) Can be folded into build-verify if the builder prefers a single PR.

### 4. Tests
- **Task ID**: build-tests
- **Depends On**: build-verify, build-docstring-fix
- **Assigned To**: release-verify-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Cover verify classification (matches/stale/unknown), beacon freshness, the swallowed-write inversion guard (unknown never force-restarts), per-process escalation (bridge force-restart invoked; worker never force-killed), out-of-band alert firing, full-mode non-zero exit, cron pending-vs-starved, startup beacon writes, and the operator-facing summary string.
- **Blocker-2 regression guard**: assert the verify step runs and fires `starved` escalation on a `commit_count == 0` no-op cron cycle with a positively-stale beacon (proves verify is not nested inside the commit-count cron branch).
- **Blocker-1 primitive**: assert `get_process_start_ts(pid)` returns an absolute start timestamp for a worker PID and that classification uses it (beacon `> process_start_ts` + lagging SHA → `stale`; `<= process_start_ts` or None → `unknown`).
- **Concern-b gate**: assert a non-bridge-role machine skips bridge verification (no false "bridge release could not be confirmed" warning).

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-verify, build-docstring-fix, build-tests
- **Assigned To**: release-verify-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update the self-healing / update-system feature doc + index; add docstrings and the cron-mode + per-process-escalation comments.

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: release-verify-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification-table checks; confirm acceptance criteria 1 and 2 met and criterion 3 is staged for the operator-gated on-bridge run.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit -x -q -k "release_verify or boot_sha or restart_flag"` | exit code 0 |
| Lint clean | `python -m ruff check scripts/update bridge worker agent` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/update bridge worker agent` | exit code 0 |
| run.py calls verifier | `grep -c "verify_running_release" scripts/update/run.py` | output > 0 |
| verifier defined | `grep -c "def verify_running_release" scripts/update/service.py` | output > 0 |
| bridge writes beacon | `grep -rn "bridge_boot_sha" bridge/telegram_bridge.py` | exit code 0 |
| worker writes beacon | `grep -rn "worker_boot_sha" worker/__main__.py` | exit code 0 |
| full-mode failure wired | `grep -n "result.success = False" scripts/update/run.py` | exit code 0 |
| out-of-band sentinel | `grep -rn "update-release-failed" scripts/update/run.py monitoring/bridge_watchdog.py` | exit code 0 |
| docstring + log-line corrected | `grep -n "worker PID\|worker loop\|restarting worker" agent/agent_session_queue.py` | exit code 0 |
| shared start-ts primitive | `grep -c "def get_process_start_ts" scripts/update/service.py` | output > 0 |
| verify not nested in commit-count branch | `grep -n "verify_running_release" scripts/update/run.py` | line is after the `elif ... commit_count > 0` block (unconditional) |
| bridge NOT consuming shared flag | `grep -c "_check_restart_flag\|_trigger_restart" bridge/telegram_bridge.py` | output 0 (bridge only `clear_restart_flag`s at startup; never a restart-triggering consumer of `data/restart-requested`) |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Decisions (resolved in critique revision, 2026-07-05)

All three prior Open Questions are resolved by the critique revision; none remains open.

1. **Cron-mode escalation policy (was OQ1 — resolved via Blocker 2 fix):** escalation is **per-process, not a single blanket policy.** A process escalates only on *positive* staleness (`beacon_ts > process_start_ts AND boot_sha != get_short_sha(HEAD)`). Bridge stale+starved → force-restart (safe, no sessions) + one re-verify, then hard-fail if still stale. Worker stale+starved → hard-fail loud + out-of-band alert, **never force-killed** (defers to `_check_restart_flag`'s own session-running gate). `unknown` (missing/orphaned beacon) → warn, never escalate.

2. **Pending window duration (was OQ2 — resolved):** **30 minutes**, matching the polling-cron interval (`scripts/update/run.py:1538`, `scripts/remote-update.sh:155`), so a subsequent cron run catches a starved restart — and shorter than the restart flag's own 1h TTL (`_RESTART_FLAG_TTL`), so `/update` escalates *before* the flag silently expires without cycling. A positively-stale process whose flag is younger than 30 min classifies `pending` (warn); older than 30 min (or flag already expired) classifies `starved` (escalate).

3. **Bridge restart path preference (was OQ3 — resolved via Simplifier concern):** this plan uses the **verification-driven force-restart** as the bridge's cron-mode path (keeps the bridge dumb, no new self-restart code path, no shared-flag race). The dedicated idle-gated bridge self-restart flag (`data/bridge-restart-requested`) that would let the bridge converge on its own next idle boundary is a distinct orthogonal capability **deferred to its own slug** (see No-Gos) — the DESIRED outcome is fully delivered by verification + the force-restart without it.

---

## Decisions (resolved in re-critique revision 2, 2026-07-05)

Re-critique returned NEEDS REVISION with two new wiring-precision blockers and four non-blocking concerns. All are resolved below.

4. **`process_start_ts` primitive (new Blocker 1 — resolved):** the positive-staleness gate cannot reuse `get_service_status`/`get_worker_status`, which parse only `ps -o etime` (an *elapsed duration*, not comparable to the beacon's absolute ISO timestamp). The only `lstart`-based absolute-launch-time primitive is `get_bridge_process_start_ts(pid)` (`monitoring/bridge_watchdog.py:130`). Resolution: **generalize it into a shared `get_process_start_ts(pid) -> float | None`** in `scripts/update/service.py` (re-imported by `bridge_watchdog`, no duplicate parser), called with `get_bridge_pid()` and `get_worker_pid()`; `None` → `unknown` (fail-safe). Cited correctly in Technical Approach, Key Elements, Risk 3, Update System, and task build-verify; a test asserts the worker-PID path.

5. **Unconditional cron verify (new Blocker 2 — resolved):** verify must NOT be nested inside the `elif result.git_result and result.git_result.commit_count > 0` cron branch (`run.py:1555`) — nested, it would run once at flag-write time (always `pending`) and never re-run on a later no-op cron cycle, making the cron `starved` escalation practically unreachable. Resolution: call `verify_running_release()` **unconditionally after the `if/elif config.do_service_restart` block** (after `:1558`, before Step 5.5), reading mode from `config.do_service_restart`. A test asserts escalation fires on a `commit_count == 0` cron run with a positively-stale beacon. Reflected in Technical Approach, Data Flow, task build-verify, Failure Path Test Strategy, and the Verification table.

**Non-blocking concerns folded in:**
- (a) **Cron-bridge re-verify race** — the cron bridge force-restart's re-verify reuses the Race-1 bounded beacon-freshness poll (not a bare read), so it doesn't race the just-restarted bridge's own startup beacon write. (Escalation implementation, Risk-1-adjacent.)
- (b) **Missing `has_bridge` gate** — verification is gated per-process on machine role (`machine_check["bridge_projects"]` / `machine_check["projects"]`); a non-bridge machine skips bridge verification, preventing permanent "bridge release could not be confirmed" warnings. (Technical Approach run.py wiring + test.)
- (c) **Over-scoped sentinel/watchdog** — the `data/update-release-failed` sentinel + `bridge_watchdog` read are scoped to the **bridge-down-after-restart** case only (the sole case where the Telegram channel is provably dead). Worker hard-fail keeps non-zero exit + Sentry, no sentinel. (Escalation implementation, Update System, task build-verify.)
- (d) **Sibling misleading log line** — `agent/agent_session_queue.py:1245` (`"...— restarting bridge"` in `_check_restart_flag`, which runs in the worker loop) is corrected to "restarting worker" alongside the `_trigger_restart` docstring; string-only, no behavior change. (Documentation, Update System.)
