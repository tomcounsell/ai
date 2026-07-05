---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1898
last_comment_id: 4882909285
revision_applied: true
---

# Update verifies the running-process release matches pulled HEAD

## Problem

On 2026-07-04 the Captain machine ran `/update`, which reported **✅ update OK @ 6b5b998a**. But the live bridge process kept reporting release `659756a4` — 52 commits behind, predating all 11 merges from the 2026-07-03/04 bug-slate. Sentry confirmed it: a production event at 2026-07-04T14:26:49Z with `server_name=Valor-the-Captain.local`, `release=659756a4463b`, `sys.argv=[.../bridge/telegram_bridge.py]`. The reason-aware interrupt copy (#1877), priming-liveness (#1878), wedge-nudge rung (#1879), and granite handshake fix (#1881) were all on disk but not in the running process, so Cuttlefish sessions kept wedging and kept sending the pre-#1877 hardcoded interrupt copy.

**Current behavior (corrected against the actual Telegram path — issue comment 4882909285):**

The path #1898 was filed against is the **Telegram `/update` (and 30-min polling cron)** path, and it is `run.py`-decoupled by construction:

1. **The `✅ OK` report attests to the pull only.** `bridge/update.py::handle_update_command` runs `bash scripts/remote-update.sh` and derives `✅ update OK @ {sha}` purely from the shell's **exit code + `git rev-parse --short HEAD`** (`bridge/update.py:134-157`). No running-process release check exists.
2. **The bridge is never restarted on this path.** `remote-update.sh` pulls, calls `run.py --cron --no-pull` (which in cron mode only *sets the worker restart flag* — `git.set_restart_requested`, `run.py:1558` — and restarts nothing), then the **shell itself** does a worker `launchctl kickstart -k` (`remote-update.sh:212-223`, gated on a worker-relevant diff). **There is no bridge kickstart anywhere in `remote-update.sh`.** The shell header comment's "write restart flag" claim is also misleading — the shell never writes the flag; `run.py` does.
3. **The deferred flag restarts the wrong process anyway.** `_check_restart_flag`/`_trigger_restart` (`agent/agent_session_queue.py:1203-1258`) run inside the *worker's* queue loop and SIGTERM their own (worker) process; the "restarting bridge" docstring predates the bridge/worker split (the bridge only *clears* stale flags at startup, `bridge/telegram_bridge.py:2985`). Nothing restarts the bridge on any update path.
4. **Restart failures are swallowed.** The shell's `kickstart -k` fallback `echo`es `ERROR` into stdout without failing the script (`remote-update.sh:221`,`:229-231`), and `handle_update_command` only scans the **first** non-marker stdout line for `warning` (`bridge/update.py:163`) — so a failed worker (or, once added, bridge) restart still reports `✅`.
5. The worker flag also has a 1h TTL with silent discard (`_RESTART_FLAG_TTL`, `agent_session_queue.py:1206`).

Net effect on Captain: the bridge started in the July-3 morning window (HEAD=659756a4), stayed healthy, and survived every subsequent successful pull — 52 commits stale while reporting `✅` each time, because nothing on the Telegram/cron path ever restarted it and nothing verified its release.

**Desired outcome:**
`/update` (a) actually restarts the bridge on bridge-relevant changes the same way it restarts the worker, and (b) verifies, *before* reporting `✅`, that both the bridge and the worker are running code at the pulled HEAD, exiting non-zero / reporting FAILED (loud, actionable) when a process still runs stale code. An update that fails to cycle the fleet onto the new code must not report OK.

## Freshness Check

**Baseline commit:** `63e43118` (re-verified at revision baseline; latest plan-doc commits only)
**Issue filed at:** 2026-07-04T15:45:52Z
**Disposition:** Minor drift (root cause reshaped by a new human comment, not by code movement)

**New comment incorporated (this revision):** issue comment `4882909285` (tomcounsell, 2026-07-04T15:51:15Z) establishes that the Telegram-triggered `/update` path never touches `run.py`'s restart/verify logic — it runs `remote-update.sh` and reports OK from `handle_update_command`. The prior revision framed the entire fix inside `run.py`, which is invoked by the shell (`remote-update.sh:109`) but only in `--cron` mode where it restarts nothing and runs *before* the shell's own worker kickstart. This revision moves the bridge restart + the OK-gating verify onto the actual Telegram/cron path (`remote-update.sh` + `handle_update_command`) and re-scopes the `run.py` verify to the synchronous `--full` path. `last_comment_id` bumped to `4882909285`.

**File:line references re-verified against current code:**
- `bridge/update.py:88-179` — `handle_update_command`: runs `bash remote-update.sh`, derives `✅ OK @ {sha}` from `result.returncode` + `git rev-parse --short HEAD`; first-line-only `warning` scan at `:163`. Confirmed.
- `scripts/remote-update.sh:78`/`:87` — `BEFORE_SHA`/`AFTER_SHA` captured around the pull. Confirmed.
- `scripts/remote-update.sh:109` — calls `run.py --cron --no-pull`. Confirmed.
- `scripts/remote-update.sh:202-233` — worker `kickstart -k` gated on a `BEFORE_SHA..AFTER_SHA` diff of `worker/ agent/ mcp_servers/ models/ tools/ bridge/ reflections/ pyproject.toml`; failures `echo ERROR` without `exit 1`. **No bridge block exists.** Confirmed.
- `scripts/valor-service.sh:552-566` — the update polling launchd job runs `scripts/remote-update.sh` every 30 min (so both entry points share the shell path). Confirmed.
- `scripts/update/run.py:1555-1558` — cron mode sets restart flag (worker), no restart. Confirmed.
- `scripts/update/git.py:262-266` — `set_restart_requested` writes `data/restart-requested` (called only from `run.py`). Confirmed.
- `scripts/update/service.py:55`/`:144` — `get_bridge_pid` (pgrep `telegram_bridge.py`) / `get_worker_pid`; `SERVICE_PREFIX = com.valor` (`:16`); bridge label `{SERVICE_PREFIX}.bridge` (`:101`,`:630`); `restart_service` at `:127`; `get_service_status`/`get_worker_status` parse `ps -o etime` only. Confirmed.
- `agent/agent_session_queue.py:1245`/`:1250` — the "restarting bridge" info line and `_trigger_restart` docstring (both actually restart the *worker*). Confirmed.
- `monitoring/bridge_watchdog.py:130` — `get_bridge_process_start_ts(pid)` (`ps -o lstart`, absolute UTC ts, None on error), fully pid-parameterized. Confirmed.
- `monitoring/sentry_config.py:61` — `release = git rev-parse HEAD` captured at process init (explains the frozen Sentry release). Confirmed.

**Cited sibling issues/PRs re-checked:** #1877/#1878/#1879/#1881 (bug-slate merges on disk but not running) — context only, all merged before the issue. #1091 (worker-restart-only-on-relevant-diff) — the design the new bridge block mirrors. #1815/#1877 (wedge resilience) — out of scope.

**Commits on main since the issue was filed (touching referenced files):** `313724f3` (plan-migration hook) — irrelevant to restart/release logic. No code drift in `remote-update.sh`, `bridge/update.py`, `run.py`, `service.py`, `agent_session_queue.py`, `bridge/telegram_bridge.py`, or `worker/__main__.py`.

**Active plans in `docs/plans/` overlapping this area:** none.

## Prior Art

- **#1767** "Worker watchdog fails to recover a U-state hung worker" (closed 2026-06-25) — hardened worker recovery, did not touch how `/update` verifies the running release.
- **#1091** worker-restart-only-on-relevant-diff — the design pattern the new bridge kickstart block mirrors (gate the restart on a `BEFORE_SHA..AFTER_SHA` diff of process-relevant paths).
- **#1815 / #1817 / #1877** (closed) — wedge survival / interrupt messaging: the *payload* that failed to reach the running bridge; none added release verification or a bridge restart to the updater.
- **PR #1832** "worker fault containment" (merged 2026-06-30) — worker fault handling, no updater-side release check.

No prior issue or PR added a bridge restart or a post-restart release-verification gate to the Telegram/cron `/update` path. Greenfield for the updater. "Why Previous Fixes Failed" is omitted — no prior attempt targeted this gap.

## Research

No relevant external findings — this is purely internal (macOS launchd, `git rev-parse`, Redis/Popoto, the repo's own update system). Proceeding with codebase context.

## Data Flow

Trace of how a code change reaches (or fails to reach) the running processes under the **Telegram `/update` / 30-min polling cron** path (the path #1898 filed against):

1. **Entry point**: Telegram `/update` → `bridge/update.py::handle_update_command` runs `bash scripts/remote-update.sh` (120s timeout). The 30-min polling launchd job runs the *same* `remote-update.sh` (`scripts/valor-service.sh:566`).
2. **Git pull** (`remote-update.sh:78-87`): `BEFORE_SHA` captured → `git pull --ff-only` → `AFTER_SHA` (e.g. `6b5b998a`).
3. **`run.py --cron --no-pull`** (`remote-update.sh:109`): migrations run; in cron mode `do_service_restart=False` so **nothing is restarted**; if `commit_count > 0`, `git.set_restart_requested()` writes the worker `data/restart-requested` flag (`run.py:1558`). run.py returns here — *before* any restart happens.
4. **Worker restart, in the SHELL** (`remote-update.sh:202-233`): if `BEFORE_SHA != AFTER_SHA` and the diff touches worker-relevant paths → `launchctl kickstart -k {prefix}.worker`. Failures `echo ERROR` without failing the script.
5. **Bridge restart**: **absent.** No bridge kickstart exists anywhere in `remote-update.sh`. → the observed frozen `659756a4` bridge.
6. **Worker deferred flag** (backup path): `_check_restart_flag()` (`agent_session_queue.py:1780`/`:2203`) between sessions restarts the *worker* only, ≤1h old, no running session — can be starved+expired.
7. **OK report** (`bridge/update.py:134-168`): `status = "✅ update OK @ {short-HEAD}"` derived from `result.returncode == 0` + `git rev-parse --short HEAD`; only the **first** stdout line is scanned for `warning`. **No running-process release comparison.** A stale bridge, a swallowed worker `kickstart` ERROR — all still report `✅`.

The **`--full`** path is different: `handle_force_update_command` → `run.py --full` restarts services **synchronously** in Step 5 (`install_service`), so a `run.py`-terminal verify is correctly placed *there*.

The fix therefore lands in **three** places, keyed to where the restart actually happens:
- **Cron/Telegram path** (steps 4-7): add the missing **bridge kickstart** in `remote-update.sh` (mirroring the worker block), surface swallowed restart failures, run the shared **release verify** as the shell's terminal step, and gate `handle_update_command`'s `✅` on that verify with per-process reload state appended.
- **`--full` path**: keep the shared verify as `run.py`'s terminal step in the `do_service_restart=True` branch (post-synchronous-restart).

## Architectural Impact

- **New durable signal (boot-SHA beacon):** the bridge and worker each record, at startup, the git SHA they were launched at, to a known file the updater can read without touching the process. Additive.
- **New restart step (bridge):** `remote-update.sh` gains a bridge `kickstart -k` block symmetric with the existing worker block, gated on a bridge-relevant `BEFORE_SHA..AFTER_SHA` diff and on the bridge plist being installed on this machine. Safe: the bridge holds no agent sessions; its catchup scan covers the downtime gap.
- **New verify step (shared):** `verify_running_release()` in `scripts/update/service.py` reads both beacons and classifies each process. It is called from three sites: `remote-update.sh` (via a thin `python -m scripts.update.verify_release` CLI, cron path), `bridge/update.py::handle_update_command` (pre-OK gate, Telegram report), and `run.py` (`--full` terminal step).
- **Interface changes:** a new `get_process_start_ts(pid)` (generalized from `bridge_watchdog.get_bridge_process_start_ts`), a new `verify_running_release()`, a new `verify_release` CLI wrapper, and a `boot_sha`/`beacon_ts` extension on process status.
- **Coupling:** low. Beacons are one-line startup writes; the verify is a read + compare. No new runtime dependency between bridge and worker.
- **Reversibility:** high. The bridge kickstart mirrors an existing, proven block; the verify is a bounded gate; beacon writes are inert if unread.

## Appetite

**Size:** Medium

**Team:** Solo dev, plan critique, code review

**Interactions:**
- PM check-ins: 0-1 (escalation policy resolved in Decisions — proactive per-process restart + post-restart verify; no open policy call remains)
- Review rounds: the Telegram-path reconciliation revision (this pass) after the human comment; prior critique rounds resolved separately

## Prerequisites

No prerequisites — this work runs against the repo's own update system, git, and launchd (present on every machine). Executable proof (acceptance criterion 3) requires a real bridge machine, captured as an `[EXTERNAL]` No-Go, not a build-time prerequisite.

## Solution

### Key Elements

- **Boot-SHA beacon**: at startup the bridge writes its launch SHA to `data/bridge_boot_sha`, and the worker writes its launch SHA to `data/worker_boot_sha` (via `git rev-parse HEAD` at process init, mirroring `monitoring/sentry_config.py:61`). Each write is `{sha}\n{iso-timestamp}` so a stale/orphaned beacon is detectable. Best-effort (swallow FS errors, never crash startup).

- **Bridge kickstart in `remote-update.sh`** (the missing restart — the core #1898 fix): after the pull, add a block symmetric with the existing worker block (`remote-update.sh:202-233`):
  - Machine-role gate: only when the bridge plist is installed on this machine (`BRIDGE_DST="$HOME/Library/LaunchAgents/${SERVICE_LABEL_PREFIX}.bridge.plist"`; `[ -f "$BRIDGE_DST" ]`). A skills-only machine has no bridge plist → the block is skipped entirely (no spurious bridge handling).
  - Change gate: `BEFORE_SHA != AFTER_SHA` AND the diff touches **bridge-relevant** paths (`bridge/ agent/ mcp_servers/ models/ tools/ config/ pyproject.toml`) → `launchctl kickstart -k "gui/$(id -u)/${SERVICE_LABEL_PREFIX}.bridge"`. Safe because the bridge holds no agent sessions; the bridge's catchup scan handles the downtime gap.
  - **Surface failures**: a worker OR bridge `kickstart` failure must set a non-zero terminal exit (or emit a distinct, scannable failure line) so `handle_update_command` reports FAILED instead of `✅`. Replaces today's swallowed `echo ERROR`.

- **Release-verify (shared)**: `scripts/update/service.py::verify_running_release(project_dir, head_sha) -> ReleaseCheck` reads both beacons and classifies each in-role process `matches | stale | unknown`:
  - `matches` = `boot_sha == get_short_sha(HEAD)`.
  - `stale` (**positive staleness only**) = `beacon_ts > process_start_ts AND boot_sha != get_short_sha(HEAD)`, where `process_start_ts` is the process's *absolute* launch time from the shared `get_process_start_ts(pid)` helper (`ps -o lstart`; generalized from `get_bridge_process_start_ts`). The beacon must belong to the *current* process image.
  - `unknown` = beacon missing / empty / malformed, `process_start_ts is None`, or `beacon_ts <= process_start_ts` (orphaned / predates the current image). A swallowed best-effort beacon write can only ever downgrade to `unknown → warn` — it can never invert into a false FAILED/force-restart of a healthy process.

- **Thin CLI wrapper for the shell**: `python -m scripts.update.verify_release` calls `verify_running_release()`, prints the per-process summary line (naming any stale process + its lagging short-SHA, e.g. `bridge running 659756a4 but HEAD is 6b5b998a`), and exits non-zero on any in-role `stale`. `remote-update.sh` invokes it as its terminal step (after both kickstarts) — so it runs on **every** cron cycle, including no-op cycles where no commits were pulled, re-classifying a starved/never-restarted process.

- **`handle_update_command` gates OK on release** (the exact "reports OK but stale" surface): after `remote-update.sh` returns, before printing `✅`, call `verify_running_release()` (direct import) and:
  - Report FAILED / mismatch (naming the stale process + lagging SHA) instead of `✅` when any in-role process is `stale`, OR when the shell exit code is non-zero.
  - Append **per-process reload state** to the report: `(bridge restarted, worker restarted)` / `(bridge STALE 659756a4 ≠ 6b5b998a, worker restarted)` so the human sees reload state per process (the comment's explicit ask).
  - Scan **all** stdout lines (not only the first) for `ERROR`/`warning`, so a swallowed restart ERROR no longer slips past.

- **`run.py` verify (re-scoped to `--full`)**: keep the terminal `verify_running_release()` call **inside the `if config.do_service_restart:` (full-mode) branch**, after the synchronous `install_service` restart in Step 5. On any in-role `stale` → append error + `result.success = False` (non-zero exit). `unknown → warn`. This gate is NOT on the cron path (where `run.py` restarts nothing and runs before the shell's kickstarts) — the cron-path verify lives in `remote-update.sh` + `handle_update_command`.

- **Worker deferred flag unchanged**: this plan does NOT rip out `data/restart-requested` or add bridge consumption of it (that would introduce the first-reader-wins race #1898 closes). The shell's proactive worker kickstart and the deferred flag remain independent; the flag stays worker-owned. Only the misleading shell header comment (`remote-update.sh:2` "write restart flag") and the `agent_session_queue.py` docstring/log line are corrected.

### Flow

**Cron/Telegram:** `handle_update_command` → `remote-update.sh` → pull to new HEAD → migrations (`run.py --cron`, sets worker flag, restarts nothing) → **worker kickstart** (existing, relevant-diff gated) → **bridge kickstart** (NEW, relevant-diff + plist gated, safe/no-sessions) → any kickstart failure sets non-zero exit → **`verify_release` terminal step** reads both beacons vs HEAD → non-zero on positive staleness → shell returns → `handle_update_command` re-verifies, and prints `✅ OK @ {sha} (bridge restarted, worker restarted)` only when both processes are `matches`; otherwise FAILED naming the stale process + lagging SHA. A no-op cron cycle still runs `verify_release`, re-catching a process that never converged.

**`--full`:** `run.py --full` → synchronous `install_service` restart (Step 5) → terminal `verify_running_release()` → `result.success = False` + non-zero exit on any in-role `stale`.

### Technical Approach

- **Beacon write**: add a best-effort writer (e.g. `monitoring/boot_beacon.py` or a function beside the flag helpers) writing `{sha}\n{iso-timestamp}` to `data/{bridge,worker}_boot_sha`. Call it once at bridge startup (near `bridge/telegram_bridge.py:2985`, where the stale flag is already cleared) and once at worker startup (`worker/__main__.py`). Use `git rev-parse HEAD` with the subprocess pattern of `monitoring/sentry_config.py:61` / `crash_tracker.py:59`. Swallow FS errors.

- **Process-start primitive (`process_start_ts` source)**: `get_service_status`/`get_worker_status` (`service.py:71`/`:166`) parse only `ps -o etime` — an *elapsed duration*, not comparable to a beacon's absolute ISO timestamp — so they cannot feed the positive-staleness gate. The only absolute-launch-time (`lstart`) primitive is `get_bridge_process_start_ts(pid)` (`bridge_watchdog.py:130`), already fully pid-parameterized (UTC unix ts, None on error). **Generalize it to `get_process_start_ts(pid) -> float | None`** in `scripts/update/service.py` (re-imported by `bridge_watchdog`, leaving no duplicate lstart parser), called with `get_bridge_pid()` (`:55`) and `get_worker_pid()` (`:144`). `None` → `process_start_ts` unknown → classify `unknown` (fail-safe).

- **`verify_running_release()`** in `service.py`: returns per-process `{running, boot_sha, beacon_ts, process_start_ts, classification}` with the classification rules above; reuses `git.get_short_sha()` for HEAD and `get_process_start_ts(pid)` for `process_start_ts`. Gated per-process on machine role via the passed-in `machine_check` (bridge: `machine_check["bridge_projects"]`; worker: `machine_check["projects"]` — the same gates Step 5 uses at `run.py:1041`/`:1058`) so a machine lacking a role skips that process (no beacon read, no false "release could not be confirmed").

- **`verify_release` CLI** (`python -m scripts.update.verify_release`): reads HEAD, builds `machine_check`, calls `verify_running_release()`, prints the operator-facing summary line, exits `1` on any in-role `stale`, `0` otherwise (`unknown` prints a warning, exit 0). This is what `remote-update.sh` calls as its terminal step.

- **`remote-update.sh` bridge block + failure surfacing + verify**: insert the bridge kickstart block after the worker block (`:233`); compute `NEED_BRIDGE_RESTART` from a `BEFORE_SHA..AFTER_SHA` diff of the bridge-relevant path set; gate on `[ -f "$BRIDGE_DST" ]`. On any kickstart failure (worker or bridge) set a terminal failure (e.g. `RESTART_FAILED=1`) that makes the script `exit 1`. As the final step, run `"$PYTHON" -m scripts.update.verify_release` and propagate its exit code. Fix the header comment at `:2`.

- **`handle_update_command` verify + reload state** (`bridge/update.py:134-168`): after the `subprocess.run` returns, `from scripts.update.service import verify_running_release`; build the per-process reload-state string and gate `✅` on `result.returncode == 0 AND` no in-role `stale`. Scan all stdout lines for `ERROR`/`warning`. On stale → `❌ update FAILED @ {sha}: bridge running {short} but HEAD is {short}` (+ still spawn the fix session as today).

- **`run.py` `--full` wiring**: call `verify_running_release()` at the end of the `if config.do_service_restart:` block (Step 5), using the same `machine_check`. Any in-role `stale` → `result.warnings`/error + `result.success = False`. Do NOT add a cron-branch verify.

- **Out-of-band alerting** (scoped to the one channel-dead case): a **bridge** hard-fail / bridge-down-after-restart cannot deliver its own Telegram alarm, so write a filesystem sentinel `data/update-release-failed` (SHA lag + timestamp) that `monitoring/bridge_watchdog.py` reads on its 60s cycle. A **worker** hard-fail keeps its non-zero exit + Sentry (the updater process and Telegram channel are both still alive) — no sentinel. Sentry capture via `monitoring/sentry_config.py` fires on any hard-fail as the durable off-machine record.

- **Docstring/log-line corrections** (`agent_session_queue.py`, string-only, MUST NOT change the SIGTERM target): rewrite `_trigger_restart`'s docstring (`:1250`) and the sibling `_check_restart_flag` info line (`:1245`, "…— restarting bridge") to state they SIGTERM the **worker** PID and launchd respawns the **worker**. These misleading on-disk artifacts plausibly seeded the operator's false trust that `/update` cycled everything.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Beacon writes are wrapped best-effort (like `_green_heartbeat_write` at `worker/__main__.py:260`). Test that a write failure (unwritable `data/`) logs a warning and does NOT crash startup.
- [ ] `verify_running_release()` must not raise on a missing beacon — test the missing-file path returns a well-formed `unknown` result, not an exception.
- [ ] `handle_update_command` must not crash if `verify_running_release()` import/call raises — test it degrades to reporting the shell result (never a bridge crash).

### Empty/Invalid Input Handling
- [ ] `verify_running_release()` classification: missing (→ unknown), empty (→ unknown), malformed/no-timestamp (→ unknown), SHA == HEAD (→ matches), SHA != HEAD with `beacon_ts > process_start_ts` (→ stale), SHA != HEAD with `beacon_ts <= process_start_ts`/orphaned (→ unknown), `process_start_ts is None` (→ unknown).
- [ ] **`get_process_start_ts` generalization**: assert the shared helper computes an absolute start timestamp for a **worker** PID (not just a bridge PID), and classification uses that absolute ts.
- [ ] **Machine-role gate**: a machine with `machine_check["bridge_projects"]` falsy skips bridge verification (no "bridge release could not be confirmed" warning); a machine with no worker role skips worker verification.
- [ ] **Swallowed-write inversion guard**: a beacon-write failure leaving a missing/orphaned beacon classifies `unknown → warn` and MUST NOT flip `✅` to FAILED nor trigger a restart. Assert no restart/no FAILED in this path.

### Bridge Restart + Cron Path
- [ ] **`remote-update.sh` bridge block**: with `[ -f "$BRIDGE_DST" ]` and a bridge-relevant `BEFORE_SHA..AFTER_SHA` diff, `launchctl kickstart -k {prefix}.bridge` is invoked (mock/assert the command); with an irrelevant diff, it is NOT; with no bridge plist, the block is skipped.
- [ ] **Restart-failure surfacing**: a failed worker OR bridge `kickstart` makes the script exit non-zero (no longer swallowed), and `handle_update_command` reports FAILED. (Shell-level test or a Python test that mocks the subprocess returncode.)
- [ ] **No-op cron verify**: a cron cycle with no new commits and a positively-stale beacon → the terminal `verify_release` step still runs and exits non-zero. Assert verify is invoked and fails even though no restart happened.
- [ ] **`handle_update_command` gates OK**: a stale bridge beacon → the report is FAILED naming `bridge running {short} but HEAD is {short}`, NOT `✅`; a matched fleet → `✅ … (bridge restarted, worker restarted)`. Assert the per-process reload-state string.
- [ ] **All-lines warning scan**: a `warning`/`ERROR` on a non-first stdout line is detected (the fix session is spawned / report reflects it), where today only the first line is scanned.

### `--full` Path
- [ ] `run.py --full` with an in-role `stale` beacon → `result.success = False` + non-zero exit + a clear error naming both short-SHAs. `unknown` → warn only.

### Out-of-band Alerting
- [ ] Bridge hard-fail → Sentry capture invoked AND `data/update-release-failed` sentinel written; `bridge_watchdog` reads it on its cycle. Worker hard-fail → non-zero exit + Sentry, **no** sentinel.

## Test Impact

- [ ] `tests/unit/test_update_release_verify.py` (create) — verify classification (matches/stale/unknown incl. positive-staleness + orphaned), the `get_process_start_ts` worker-PID path, the machine-role gate, the swallowed-write inversion guard, and the `--full` `result.success=False` path.
- [ ] `tests/unit/` bridge-update tests (e.g. `test_bridge_update.py` if present, else add to the new file / a `test_handle_update_command.py`) — UPDATE/ADD: `handle_update_command` gates `✅` on `verify_running_release`, appends per-process reload state, and scans all stdout lines for warnings. No such assertion exists today.
- [ ] `remote-update.sh` coverage — ADD a shell/subprocess test asserting the bridge kickstart block fires on a bridge-relevant diff + bridge plist, is skipped otherwise, that a kickstart failure exits non-zero, and that the terminal `verify_release` runs on a no-op cron cycle. If no shell-test harness exists, cover the equivalent logic via the `verify_release` CLI unit test + a documented manual/on-bridge step.
- [ ] `monitoring/bridge_watchdog.py` tests that pin `get_bridge_process_start_ts` by name — UPDATE: renamed/moved to shared `get_process_start_ts`; update import/reference. lstart parsing / None-on-error unchanged. ADD: watchdog reads `data/update-release-failed`.
- [ ] Worker/bridge startup tests asserting startup side effects — UPDATE: assert the boot-SHA beacon is written at startup.
- [ ] `agent/agent_session_queue.py` restart-flag tests — UPDATE ONLY the `_trigger_restart`/`_check_restart_flag` docstring/log-string assertions if any test pins them; SIGTERM target and flag mechanics unchanged. This plan does NOT add bridge consumption of `data/restart-requested`, so no new shared-flag test.

No existing test asserts release verification, a bridge restart on the cron path, or OK-report gating today, so nothing needs DELETE/REPLACE; changes are additive to startup, `remote-update.sh`, `handle_update_command`, and the updater's terminal steps.

## Rabbit Holes

- **Do not rebuild the restart mechanism.** launchd KeepAlive + `kickstart -k` works; the gap is the *missing bridge kickstart* + verification. The bridge block mirrors the proven worker block — resist redesigning the restart lifecycle.
- **Do not add bridge consumption of `data/restart-requested`.** Reusing the worker's single consumable flag is a first-reader-wins race that reproduces #1898. The bridge reaches new code via the shell's proactive kickstart, not a shared flag.
- **Do not read the running process's in-memory code SHA.** A file beacon written at startup is the durable, testable signal.
- **Do not solve wedged-session detection here.** That is the resilience workstream (#1815/#1877). This plan restarts the bridge (safe, no sessions) and *reports* a stale worker loudly; it does not force-kill a busy worker.
- **Do not couple the beacon to Sentry.** Sentry release is external and only visible after an event fires; the updater needs a local, synchronous signal.

## Risks

### Risk 1: Cron-mode false failure while the worker is legitimately mid-session
**Impact:** A stale-worker report right after a cron update could fire while a session is legitimately draining (the worker's own deferral).
**Mitigation:** Classification is *positive staleness only* — a worker classifies `stale` solely when a beacon belonging to the current image lags HEAD. The existing shell worker kickstart is `#1091`-relevant-diff-gated and pre-dates this plan; the verify reports a genuinely-stale worker loudly (non-zero exit + Sentry) rather than force-killing it. A worker mid-session that has *already* restarted onto new code reads `matches`.

### Risk 2: Bridge kickstart interrupts work
**Impact:** A `kickstart -k` on the bridge could interrupt in-flight I/O.
**Mitigation:** The bridge holds **no** agent sessions — the worker is the sole session executor. The bridge's Telethon `catch_up=True` backfills any messages missed during the brief restart. The kickstart is relevant-diff-gated so no-op cron cycles never restart it.

### Risk 3: Orphaned beacon file
**Impact:** A beacon left by a previous process image could read as "current" and mask a stale process, or invert into a false failure.
**Mitigation:** The verifier cross-checks `beacon_ts` against the process's absolute start time from `get_process_start_ts(pid)` (`ps -o lstart`). `get_service_status` reads only `ps -o etime` (elapsed, not comparable) — deliberately not the source. A beacon `<= process_start_ts` (or `process_start_ts is None`) → `unknown → warn`, never `stale`, never `match`. Only `beacon_ts > process_start_ts AND boot_sha != HEAD` escalates.

### Risk 4: Best-effort beacon-write failure inverts into a false FAILED/restart
**Impact:** Beacon writes swallow FS errors; if a missing/orphaned beacon were treated as authoritative "stale", a swallowed write on a healthy process would flip `✅` to FAILED or trigger a restart.
**Mitigation:** Missing/empty/malformed/predates-process beacons all classify `unknown → warn`, which never fails the run nor restarts on staleness grounds. Escalation requires *positive* confirmation the live process is on old code.

## Race Conditions

### Race 1: Verify reads the beacon before the restarted process has rewritten it
**Location:** `remote-update.sh` verify step (and `run.py --full` verify) vs. bridge/worker startup beacon write.
**Trigger:** verify runs right after `kickstart -k`/`install_service`; the process may be bootstrapped but not yet at its startup beacon write.
**Data prerequisite:** the restarted process must have written its `*_boot_sha` beacon before the verifier reads it.
**State prerequisite:** the beacon's timestamp must post-date the restart moment to be trusted.
**Mitigation:** the verify polls the beacon (bounded — reuse the 20s/30s startup poll windows in Step 5 / mirror the worker-heartbeat freshness check at `run.py:1381-1400`) for a beacon whose timestamp post-dates the restart. A beacon that never freshens within the window → `stale`/fail (the process failed to come up on new code — exactly what to catch). The `handle_update_command` re-verify uses the same bounded poll, so it does not race the just-restarted bridge's own startup write.

### Race 2: Restart flag set-then-consumed vs. verify
**Location:** `git.set_restart_requested` (`run.py:1558`) vs. worker `_check_restart_flag` vs. verify step.
**Trigger:** verify runs while the worker is between flag-set and its next idle check.
**Data/State prerequisite:** classification must not race on the flag being deleted mid-read.
**Mitigation:** classification is read-only over the *beacons*, not the flag — a matched beacon reads success regardless of flag state; a stale beacon reads stale. No write contention is introduced.

## No-Gos (Out of Scope)

- `[EXTERNAL]` Running `/update` on the Captain (or any bridge machine) to capture the executable proof for acceptance criterion 3 — the dev machine has no bridge role, so the release-verification output (bridge kickstart + OK-gating report) must be captured on a real bridge machine by the operator. The build produces the code + local/full-mode tests + the `verify_release` CLI; the on-bridge proof run is human-gated.
- `[SEPARATE-SLUG]` A dedicated idle-gated bridge self-restart flag (`data/bridge-restart-requested` with a bridge-side consumer mirroring `_check_restart_flag`) that would let the bridge converge on new code at its *own* next idle boundary rather than via the shell's `kickstart -k`. The DESIRED outcome (bridge actually restarts + release is verified before OK) is fully delivered by the shell kickstart + verify in *this* plan. A bridge self-restart code path is a distinct capability for its own issue. Explicitly NOT reusing the worker's `data/restart-requested` flag (first-reader-wins race).
- `[SEPARATE-SLUG]` Fixing the underlying session-wedge that starves the worker's deferred restart (resilience workstream #1815/#1877) — this plan bounds the *consequence* (a stale worker is surfaced loudly), not the wedge.

Everything else relevant — the boot beacons, the `remote-update.sh` bridge kickstart + failure surfacing, the shared `verify_running_release()` + `verify_release` CLI, `handle_update_command`'s OK-gating + per-process reload state, the `run.py --full` verify, the docstring/log corrections, and the tests — is in scope.

## Update System

This bug **is** in the update system, so the change is intrinsically to `/update`:
- `scripts/remote-update.sh` — **primary fix**: add the bridge `kickstart -k` block (bridge-relevant diff + `[ -f "$BRIDGE_DST" ]` gated), stop swallowing worker/bridge restart failures (non-zero exit), run `python -m scripts.update.verify_release` as the terminal step, fix the misleading header comment.
- `bridge/update.py` — `handle_update_command` gates `✅` on `verify_running_release()`, appends per-process reload state, scans all stdout lines for warnings. `handle_force_update_command` already restarts via `run.py --full` (covered by the run.py verify).
- `scripts/update/service.py` — new `verify_running_release()` (positive-staleness/unknown classification), the generalized `get_process_start_ts(pid)` (moved from `bridge_watchdog`), and `boot_sha`/`beacon_ts` on status.
- `scripts/update/verify_release.py` (new) — thin `python -m` CLI wrapper for the shell (prints the summary line, exit 1 on stale).
- `scripts/update/run.py` — `verify_running_release()` as the terminal step of the `--full` (`do_service_restart=True`) branch; the `--full` failure sentinel + Sentry on hard-fail.
- `monitoring/bridge_watchdog.py` — `get_bridge_process_start_ts` generalized to shared `get_process_start_ts(pid)` and re-imported (no duplicate lstart parser); reads `data/update-release-failed` on its 60s cycle (bridge-channel-dead case only).
- `bridge/telegram_bridge.py` + `worker/__main__.py` — write boot-SHA beacons at startup.
- `agent/agent_session_queue.py` — string-only corrections (no behavior change, MUST NOT change the SIGTERM target): `_trigger_restart` docstring (`:1250`) and the `_check_restart_flag` "restarting bridge" log line (`:1245`) → state they restart the WORKER.
- No new deps. No `migrations.py` change (beacon files + the failure sentinel are inert, self-healing on next startup / next failed update). Propagates to all machines via the normal `/update` git pull; the first post-merge full `/update` restarts the fleet and begins writing beacons, and the first cron cycle thereafter runs the bridge kickstart + verify.

## Agent Integration

No agent integration required — entirely internal to the update system and process startup. No new MCP tool, no `.mcp.json` change. The agent already invokes `/update` via the existing Telegram `handle_update_command` → `remote-update.sh` path; that path gains the bridge restart + verification without a new entry point. (`python -m scripts.update.verify_release` is a shell-internal helper, not an agent-facing CLI.)

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` (or the update-system doc) to describe: the boot-SHA beacon, the new `remote-update.sh` bridge kickstart (symmetric with the worker block, safe/no-sessions), the pre-OK release verification in `handle_update_command` with per-process reload state, the `--full` verify, and the bridge-down `data/update-release-failed` sentinel.
- [ ] Add/refresh an entry in `docs/features/README.md` index for the release-verification behavior.

### Inline Documentation
- [ ] Docstrings on `verify_running_release()`, `get_process_start_ts()`, and the beacon writer explaining positive-staleness vs. unknown and the `beacon_ts > process_start_ts` cross-check.
- [ ] Comment in `remote-update.sh` at the bridge block explaining why the bridge kickstart is safe (no sessions) and why it is relevant-diff gated; comment at the verify step explaining the per-cron-cycle re-check.
- [ ] Comment in `handle_update_command` explaining why `✅` is gated on `verify_running_release` and why all stdout lines are scanned.
- [ ] **`_trigger_restart` docstring correction** (`agent/agent_session_queue.py:1250`) — state it SIGTERMs the **worker** PID; launchd respawns the worker, not the bridge. Documentation-only; do NOT change the SIGTERM target.
- [ ] **Sibling log-line correction** (`agent/agent_session_queue.py:1245`) — "restarting bridge" → "restarting worker". String-only.

## Success Criteria

- [ ] Root cause documented (this plan's Problem/Data Flow): the Telegram/cron `/update` path never restarts the bridge and reports `✅` from the shell exit code + `rev-parse` with no release verification; the worker deferred restart can be starved+expired. Includes the `_trigger_restart` docstring + log-line corrections. (Acceptance criterion 1.)
- [ ] Bridge and worker write a boot-SHA beacon (SHA + ISO timestamp) at startup.
- [ ] `remote-update.sh` restarts the **bridge** via `kickstart -k` on bridge-relevant changes (mirroring the worker block, machine-role gated), and a failed worker/bridge kickstart makes the script exit non-zero (no longer swallowed).
- [ ] `handle_update_command` verifies bridge AND worker running release == pulled HEAD **before** printing `✅`, reports FAILED naming the stale process + lagging short-SHA otherwise, and appends per-process reload state (`(bridge restarted, worker restarted)`). (Acceptance criterion 2 — the exact #1898 surface.)
- [ ] The terminal `verify_release` step runs on **every** cron cycle (including no-op cycles) and exits non-zero on positive staleness; only positive staleness (`beacon_ts > process_start_ts AND boot_sha != HEAD`) escalates; `unknown → warn`.
- [ ] `run.py --full` verifies release after its synchronous restart and sets `result.success = False` + non-zero exit on positive staleness.
- [ ] **Operator-facing (off-bridge):** a release mismatch names the stale process and its lagging short-SHA (e.g. `bridge running 659756a4 but HEAD is 6b5b998a`) in the `handle_update_command` Telegram report and the `verify_release` CLI output — asserted in unit tests, independent of the on-bridge proof.
- [ ] Out-of-band failure signal (Sentry capture + `data/update-release-failed` sentinel, watchdog read) fires on a bridge hard-fail so a bridge-down-after-restart cannot silence its own alarm.
- [ ] Executable proof captured on a bridge machine: a `/update` run showing the bridge kickstart + release-verification (SHA match, or a deliberate mismatch producing FAILED). (Acceptance criterion 3 — operator-gated per No-Gos.)
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).
- [ ] grep confirms `remote-update.sh` restarts the bridge label + calls `verify_release`, `handle_update_command` calls `verify_running_release`, and both startup paths call the beacon writer.

## Team Orchestration

The lead agent orchestrates; it does not build directly.

### Team Members

- **Builder (beacon + verify + Telegram-path wiring)**
  - Name: `update-verify-builder`
  - Role: Add boot-SHA beacon writes (bridge + worker); `verify_running_release()` + `get_process_start_ts()` + the `verify_release` CLI in `scripts/update/`; the `remote-update.sh` bridge kickstart block + failure surfacing + terminal verify; `handle_update_command` OK-gating + per-process reload state + all-lines warning scan; the `run.py --full` verify; the out-of-band sentinel + Sentry + watchdog read.
  - Agent Type: builder
  - Domain: process-lifecycle / shell + async
  - Resume: true

- **Builder (root-cause docstring/log correction)**
  - Name: `docstring-correction-builder`
  - Role: Rewrite the `_trigger_restart` docstring (`:1250`) and the `_check_restart_flag` log line (`:1245`) to state they restart the WORKER. Documentation-only; MUST NOT change the SIGTERM target. Can fold into `update-verify-builder`.
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `release-verify-tester`
  - Role: Unit tests for classification, the worker-PID `get_process_start_ts` path, the machine-role gate, the swallowed-write inversion guard, the bridge kickstart block, restart-failure surfacing, the no-op cron verify, `handle_update_command` OK-gating + reload-state string, all-lines warning scan, and startup beacon writes.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `release-verify-validator`
  - Role: Verify acceptance criteria and the Verification table.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `release-verify-docs`
  - Role: Update self-healing / update-system docs + index; docstrings + comments.
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

### 2. verify_running_release() + get_process_start_ts + verify_release CLI
- **Task ID**: build-verify-core
- **Depends On**: build-beacon
- **Validates**: tests/unit/test_update_release_verify.py
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: false
- Move `get_bridge_process_start_ts(pid)` (`bridge_watchdog.py:130`) to a shared `get_process_start_ts(pid) -> float | None` in `scripts/update/service.py` (re-imported by `bridge_watchdog`; no duplicate lstart parser). Call with `get_bridge_pid()`/`get_worker_pid()`. `None` → `unknown`.
- Add `verify_running_release(project_dir, head_sha, machine_check)` returning per-process `{running, boot_sha, beacon_ts, process_start_ts, classification ∈ {matches, stale, unknown}}`; `stale` requires positive staleness (`beacon_ts > process_start_ts AND boot_sha != get_short_sha(HEAD)`); everything ambiguous → `unknown`. Gate each process on machine role.
- Add `scripts/update/verify_release.py` (`python -m scripts.update.verify_release`): reads HEAD + machine role, calls the verifier, prints the operator-facing summary line naming any stale process + lagging short-SHA, exits 1 on any in-role `stale`.

### 3. remote-update.sh bridge kickstart + failure surfacing + terminal verify
- **Task ID**: build-shell-restart
- **Depends On**: build-verify-core
- **Validates**: remote-update.sh shell/subprocess test, verify_release CLI test
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a bridge `kickstart -k` block after the worker block (`:233`): `BRIDGE_DST` plist gate + `BEFORE_SHA..AFTER_SHA` bridge-relevant diff (`bridge/ agent/ mcp_servers/ models/ tools/ config/ pyproject.toml`) → `launchctl kickstart -k {prefix}.bridge`. Safe (no sessions); catchup covers downtime.
- Make a worker OR bridge kickstart failure set a non-zero terminal exit (no more swallowed `echo ERROR`).
- Run `"$PYTHON" -m scripts.update.verify_release` as the terminal step and propagate its exit code. Fix the misleading header comment (`:2`).

### 4. handle_update_command OK-gating + reload state
- **Task ID**: build-report-gate
- **Depends On**: build-verify-core
- **Validates**: bridge-update unit tests
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: false
- After `remote-update.sh` returns, call `verify_running_release()`; gate `✅` on `returncode == 0 AND` no in-role `stale`; on stale report `❌ update FAILED @ {sha}: {process} running {short} but HEAD is {short}`; append per-process reload state (`(bridge restarted, worker restarted)`). Scan ALL stdout lines for `ERROR`/`warning`. Degrade gracefully if the verify import/call raises.

### 5. run.py --full verify + out-of-band alert
- **Task ID**: build-full-verify
- **Depends On**: build-verify-core
- **Validates**: tests/unit/test_update_release_verify.py
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: false
- Call `verify_running_release()` at the end of the `if config.do_service_restart:` block; any in-role `stale` → error + `result.success = False`. Do NOT add a cron-branch verify.
- On a bridge hard-fail / bridge-down-after-restart, write `data/update-release-failed` (SHA lag + ts) and make `monitoring/bridge_watchdog.py` read it on its 60s cycle. Sentry capture on any hard-fail.

### 6. Root-cause docstring + log-line correction
- **Task ID**: build-docstring-fix
- **Depends On**: none
- **Validates**: agent/agent_session_queue restart-flag tests (string assertions only)
- **Assigned To**: docstring-correction-builder
- **Agent Type**: builder
- **Parallel**: true
- Rewrite the `_trigger_restart` docstring (`:1250`) and the `_check_restart_flag` "restarting bridge" log line (`:1245`) to state they restart the WORKER. Do NOT touch the SIGTERM target or flag mechanics.

### 7. Tests
- **Task ID**: build-tests
- **Depends On**: build-shell-restart, build-report-gate, build-full-verify, build-docstring-fix
- **Assigned To**: release-verify-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- Cover verify classification (matches/stale/unknown incl. orphaned), the worker-PID `get_process_start_ts`, the machine-role gate, the swallowed-write inversion guard, the bridge kickstart block (fires on relevant diff + plist; skipped otherwise), restart-failure surfacing (non-zero exit), the no-op cron verify, `handle_update_command` OK-gating + reload-state string + all-lines warning scan, the `run.py --full` non-zero exit, out-of-band alert firing, and startup beacon writes.

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: release-verify-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Update the self-healing / update-system feature doc + index; add docstrings and the bridge-kickstart / verify / OK-gating comments.

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests, document-feature
- **Assigned To**: release-verify-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification-table checks; confirm acceptance criteria 1 and 2 met and criterion 3 is staged for the operator-gated on-bridge run.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit -x -q -k "release_verify or boot_sha or handle_update or restart_flag"` | exit code 0 |
| Lint clean | `python -m ruff check scripts/update bridge worker agent monitoring` | exit code 0 |
| Format clean | `python -m ruff format --check scripts/update bridge worker agent monitoring` | exit code 0 |
| verifier defined | `grep -c "def verify_running_release" scripts/update/service.py` | output > 0 |
| verify CLI exists | `test -f scripts/update/verify_release.py && echo ok` | `ok` |
| shell calls verify | `grep -c "verify_release" scripts/remote-update.sh` | output > 0 |
| shell restarts bridge | `grep -n "kickstart -k.*\.bridge\|BRIDGE_DST" scripts/remote-update.sh` | exit code 0 |
| report gates OK | `grep -c "verify_running_release" bridge/update.py` | output > 0 |
| bridge writes beacon | `grep -rn "bridge_boot_sha" bridge/telegram_bridge.py` | exit code 0 |
| worker writes beacon | `grep -rn "worker_boot_sha" worker/__main__.py` | exit code 0 |
| full-mode failure wired | `grep -n "result.success = False" scripts/update/run.py` | exit code 0 |
| out-of-band sentinel | `grep -rn "update-release-failed" scripts/update/run.py monitoring/bridge_watchdog.py` | exit code 0 |
| docstring + log-line corrected | `grep -n "worker PID\|worker loop\|restarting worker" agent/agent_session_queue.py` | exit code 0 |
| shared start-ts primitive | `grep -c "def get_process_start_ts" scripts/update/service.py` | output > 0 |
| bridge NOT consuming shared flag | `grep -c "_check_restart_flag\|_trigger_restart" bridge/telegram_bridge.py` | output 0 (bridge only `clear_restart_flag`s at startup) |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Decisions (resolved in critique revision, 2026-07-05)

Prior Open Questions resolved by the earlier critique rounds; carried forward where still applicable.

1. **Positive-staleness escalation:** a process escalates only on *positive* staleness (`beacon_ts > process_start_ts AND boot_sha != get_short_sha(HEAD)`); `unknown` (missing/orphaned beacon) → warn, never escalate.
2. **Pending window / cadence:** the verify runs on every 30-min cron cycle (matching the polling interval, `scripts/valor-service.sh:566`), shorter than the worker flag's 1h TTL, so a starved worker is surfaced before the flag silently expires.
3. **`process_start_ts` primitive:** the positive-staleness gate cannot reuse `get_service_status`/`get_worker_status` (`ps -o etime`, elapsed); it uses the generalized `get_process_start_ts(pid)` (`ps -o lstart`, absolute), moved from `bridge_watchdog`.
4. **Machine-role gate:** verification is gated per-process on machine role (`machine_check["bridge_projects"]` / `["projects"]`); a non-bridge machine skips bridge verification.
5. **Sentinel scope:** `data/update-release-failed` + `bridge_watchdog` read are scoped to the bridge-down-after-restart case (the only case where the Telegram channel is provably dead); a worker hard-fail keeps non-zero exit + Sentry, no sentinel.

---

## Decisions (resolved in Telegram-path reconciliation revision, 2026-07-05)

The human comment `4882909285` established that the Telegram-triggered `/update` path never touches `run.py`'s restart/verify logic, so the prior `run.py`-only fix missed the exact path #1898 was filed against. Resolutions:

6. **The fix moves onto the actual Telegram/cron path (remote-update.sh + handle_update_command), IN ADDITION TO the run.py verify — not instead of.** `remote-update.sh` invokes `run.py --cron`, but in cron mode `run.py` restarts nothing and runs *before* the shell's own worker kickstart, and the OK/FAIL report is produced by `handle_update_command`, not `run.py`. So: the bridge restart is added to `remote-update.sh` (mirroring the worker block); the OK-gating verify + per-process reload state is added to `handle_update_command`; the terminal `verify_release` CLI runs on every cron cycle; and the `run.py` verify is **re-scoped to the `--full` (synchronous-restart) branch only**, which is the path `handle_force_update_command` uses.

7. **The missing bridge restart is the core defect** (comment claims 2, 3): nothing on the Telegram/cron path ever restarts the bridge. Fix: a bridge `kickstart -k` block in `remote-update.sh`, symmetric with the worker block, bridge-relevant-diff + `[ -f "$BRIDGE_DST" ]` gated, safe because the bridge holds no sessions and its catchup scan covers the gap. This is the human's suggested fix shape verbatim.

8. **Swallowed restart failures + first-line-only warning scan** (comment claim 4): `remote-update.sh`'s `kickstart` failures now set a non-zero terminal exit; `handle_update_command` scans all stdout lines and gates `✅` on both the exit code and the release verify — a failed restart can no longer report `✅`.

9. **Per-process reload state in the report** (comment's explicit ask): `handle_update_command` appends `(bridge restarted, worker restarted)` / names any stale process + lagging short-SHA, so the human sees reload state per process. This is the human-visible artifact the original "misleading update OK" bug lacked.

10. **The prior "unconditional cron verify in run.py" (old Blocker 2) is superseded, not dropped:** its purpose — re-classifying a starved/never-restarted process on a no-op cron cycle — is now served by the `verify_release` terminal step running on *every* `remote-update.sh` invocation. The run.py verify keeps only the `--full` synchronous-restart gate.

11. **Preserved from prior revisions (still apply):** boot-SHA beacons, positive-staleness vs. unknown classification, the shared `get_process_start_ts` helper, per-process machine-role gating, and the bridge-scoped out-of-band sentinel. The worker's `data/restart-requested` flag stays worker-owned and unchanged (no bridge consumption — first-reader-wins race).
