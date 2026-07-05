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
- **Cron/Telegram path** (steps 4-7): add the missing **bridge kickstart** in `remote-update.sh` (mirroring the worker block) as the shell's **final act** (it self-kills the shell + `handle_update_command` by process group), surface swallowed restart failures, run the shared **worker release verify** as the shell's terminal step before the bridge kickstart, and gate the `✅` on that verify with per-process reload state appended — reported inline by `handle_update_command` when no bridge restart occurred, or by the **fresh bridge's boot flush** of a staged `data/update-pending-report` when the bridge restarted.
- **`--full` path**: keep the shared verify as `run.py`'s terminal step in the `do_service_restart=True` branch (post-synchronous-restart).

## Architectural Impact

- **New durable signal (boot-SHA beacon):** the bridge and worker each record, at startup, the git SHA they were launched at, to a known file the updater can read without touching the process. Additive.
- **New restart step (bridge):** `remote-update.sh` gains a bridge `kickstart -k` block symmetric with the existing worker block, gated on a bridge-relevant `BEFORE_SHA..AFTER_SHA` diff and on the bridge plist being installed on this machine. Safe: the bridge holds no agent sessions; its catchup scan covers the downtime gap.
- **New verify step (shared):** `verify_running_release()` in `scripts/update/service.py` reads both beacons and classifies each process. It is called from four sites: `remote-update.sh` (via a thin `python -m scripts.update.verify_release` CLI, cron path, worker-scoped when a bridge restart is queued), `bridge/update.py::handle_update_command` (pre-OK gate, inline Telegram report on the no-bridge-restart path), the **fresh-bridge boot flush** (`bridge/telegram_bridge.py` startup, reporting a staged `data/update-pending-report` after a bridge restart), and `run.py` (`--full` terminal step).
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

- **Boot-SHA beacon**: at startup the bridge writes its launch SHA to `data/bridge_boot_sha`, and the worker writes its launch SHA to `data/worker_boot_sha` (via `scripts/update/git.py::get_short_sha(project_dir)` — the SAME short-SHA helper the classifier compares against, so writer and classifier share one representation by construction and `matches` is reachable on every successful update). Each write is `{sha}\n{iso-timestamp}` so a stale/orphaned beacon is detectable. Best-effort (swallow FS errors, never crash startup).

- **Bridge kickstart in `remote-update.sh`** (the missing restart — the core #1898 fix): after the pull, add a block symmetric with the existing worker block (`remote-update.sh:202-233`):
  - Machine-role gate: only when the bridge plist is installed on this machine (`BRIDGE_DST="$HOME/Library/LaunchAgents/${SERVICE_LABEL_PREFIX}.bridge.plist"`; `[ -f "$BRIDGE_DST" ]`). A skills-only machine has no bridge plist → the block is skipped entirely (no spurious bridge handling).
  - Change gate: `BEFORE_SHA != AFTER_SHA` AND the diff touches **bridge-relevant** paths (`bridge/ agent/ mcp_servers/ models/ tools/ config/ pyproject.toml`) → `launchctl kickstart -k "gui/$(id -u)/${SERVICE_LABEL_PREFIX}.bridge"`. Safe because the bridge holds no agent sessions; the bridge's catchup scan handles the downtime gap.
  - **Surface failures**: a worker OR bridge `kickstart` failure must set a non-zero terminal exit (or emit a distinct, scannable failure line) so `handle_update_command` reports FAILED instead of `✅`. Replaces today's swallowed `echo ERROR`.

- **Release-verify (shared)**: `scripts/update/service.py::verify_running_release(project_dir, head_sha) -> ReleaseCheck` reads both beacons and classifies each in-role process `matches | stale | unknown`:
  - `matches` = `boot_sha == get_short_sha(HEAD)`.
  - `stale` (**positive staleness only**) = `beacon_ts > process_start_ts AND boot_sha != get_short_sha(HEAD)`, where `process_start_ts` is the process's *absolute* launch time from the shared `get_process_start_ts(pid)` helper (`ps -o lstart`; generalized from `get_bridge_process_start_ts`). The beacon must belong to the *current* process image.
  - `unknown` = beacon missing / empty / malformed, `process_start_ts is None`, or `beacon_ts <= process_start_ts` (orphaned / predates the current image). A swallowed best-effort beacon write can only ever downgrade to `unknown → warn` — it can never invert into a false FAILED/force-restart of a healthy process.

- **Thin CLI wrapper for the shell**: `python -m scripts.update.verify_release` calls `verify_running_release()`, prints the per-process summary line (naming any stale process + its lagging short-SHA, e.g. `bridge running 659756a4 but HEAD is 6b5b998a`), and exits non-zero on any in-role `stale`. `remote-update.sh` invokes it as its terminal step (after both kickstarts) — so it runs on **every** cron cycle, including no-op cycles where no commits were pulled, re-classifying a starved/never-restarted process.

- **Report path splits on whether the bridge is restarted this cycle** (the survivable-channel fix for the self-kill gap): a bridge `kickstart -k` SIGKILLs the whole bridge launchd job — including `handle_update_command` and the `remote-update.sh` bash child it spawned (they share the job's process group). So the process that ran `/update` cannot survive its own bridge restart to verify and reply. The report is therefore keyed to the restart shape:
  - **Worker-only / no-op update (no bridge restart this cycle):** `handle_update_command` survives. After `remote-update.sh` returns, before printing `✅`, it calls `verify_running_release()` (direct import), gates `✅` on `returncode == 0 AND` no in-role `stale`, and reports FAILED naming the stale process + lagging SHA otherwise. This path also re-catches a *pre-existing* stale bridge (a bridge left behind by an earlier missed restart) even though the current update didn't touch bridge code.
  - **Bridge-relevant update (bridge restart triggered this cycle):** `handle_update_command` will be killed by the bridge kickstart, so it **cannot** be the reporter. Before the bridge kickstart fires, the originating chat context (chat id + reply-to message id) plus the pulled HEAD short-SHA and the worker reload state are staged to `data/update-pending-report`. The **fresh bridge**, on startup — after writing its own boot-SHA beacon so its release is knowable — reads the pending report, calls `verify_running_release()` against its own fresh beacon + the worker beacon, composes the OK/FAILED message (this is where "verify running release before printing OK" actually holds — verify and report both execute in the survivor that just booted at HEAD), flushes it to the staged chat, and deletes the file. A pending report left undrained past a TTL, or a fresh bridge that comes up still stale, escalates via the out-of-band sentinel below.
  - Both paths append **per-process reload state** to the report: `(bridge restarted, worker restarted)` / `(bridge STALE 659756a4 ≠ 6b5b998a, worker restarted)` so the human sees reload state per process (the comment's explicit ask).
  - Both paths scan **all** stdout lines (not only the first) for `ERROR`/`warning`, so a swallowed restart ERROR no longer slips past.

- **`run.py` verify (re-scoped to `--full`)**: keep the terminal `verify_running_release()` call **inside the `if config.do_service_restart:` (full-mode) branch**, after the synchronous `install_service` restart in Step 5. On any in-role `stale` → append error + `result.success = False` (non-zero exit). `unknown → warn`. This gate is NOT on the cron path (where `run.py` restarts nothing and runs before the shell's kickstarts) — the cron-path verify lives in `remote-update.sh` + `handle_update_command`.

- **Worker deferred flag unchanged**: this plan does NOT rip out `data/restart-requested` or add bridge consumption of it (that would introduce the first-reader-wins race #1898 closes). The shell's proactive worker kickstart and the deferred flag remain independent; the flag stays worker-owned. Only the misleading shell header comment (`remote-update.sh:2` "write restart flag") and the `agent_session_queue.py` docstring/log line are corrected.

### Flow

**Cron/Telegram, worker-only or no-op update (no bridge restart):** `handle_update_command` → `remote-update.sh` → pull to new HEAD → migrations (`run.py --cron`, sets worker flag, restarts nothing) → **worker kickstart** (existing, relevant-diff gated) → any kickstart failure sets non-zero exit → **`verify_release` terminal step** reads both beacons vs HEAD → non-zero on positive staleness → shell returns → `handle_update_command` re-verifies, and prints `✅ OK @ {sha} (bridge current, worker restarted)` only when both processes are `matches`; otherwise FAILED naming the stale process + lagging SHA. `handle_update_command` survives (no bridge kickstart), so it is the reporter. A no-op cron cycle still runs `verify_release`, re-catching a process that never converged — including a bridge left stale by an earlier missed restart.

**Cron/Telegram, bridge-relevant update (bridge restart triggered — the self-kill case):** … → **worker kickstart** → **worker `verify_release`** (worker only; the bridge is deliberately about to restart, so bridge staleness is not escalated here) → **stage `data/update-pending-report`** (originating chat id + reply-to, HEAD short-SHA, worker reload state — only when a Telegram chat context is present; the pure 30-min cron cycle has none, so nothing is staged) → **bridge kickstart LAST** (NEW, relevant-diff + plist gated, safe/no-sessions). The kickstart SIGKILLs the bridge job — `handle_update_command` and the `remote-update.sh` bash child die here by process-group semantics; that is expected and the report is already durably staged. The **fresh bridge** boots at HEAD, writes its boot-SHA beacon, reads `data/update-pending-report`, runs `verify_running_release()` (its own fresh beacon + the worker beacon), and flushes `✅ OK @ {sha} (bridge restarted, worker restarted)` (or FAILED naming the stale process + lagging SHA) to the staged chat, then deletes the file. If the fresh bridge fails to come up on HEAD, the boot flush never runs → the out-of-band sentinel + watchdog (below) make it audible.

**`--full`:** `run.py --full` → synchronous `install_service` restart (Step 5) → terminal `verify_running_release()` → `result.success = False` + non-zero exit on any in-role `stale`.

### Technical Approach

- **Beacon write**: add a best-effort writer (e.g. `monitoring/boot_beacon.py` or a function beside the flag helpers) writing `{sha}\n{iso-timestamp}` to `data/{bridge,worker}_boot_sha`. Call it once at bridge startup (near `bridge/telegram_bridge.py:2985`, where the stale flag is already cleared) and once at worker startup (`worker/__main__.py`). Derive the SHA via `scripts/update/git.py::get_short_sha(project_dir)` — the same short-SHA helper the classifier uses — never a full 40-char `rev-parse HEAD`, which can never equal its short form and would make `matches` unreachable. Swallow FS errors.

- **Process-start primitive (`process_start_ts` source)**: `get_service_status`/`get_worker_status` (`service.py:71`/`:166`) parse only `ps -o etime` — an *elapsed duration*, not comparable to a beacon's absolute ISO timestamp — so they cannot feed the positive-staleness gate. The only absolute-launch-time (`lstart`) primitive is `get_bridge_process_start_ts(pid)` (`bridge_watchdog.py:130`), already fully pid-parameterized (UTC unix ts, None on error). **Generalize it to `get_process_start_ts(pid) -> float | None`** in `scripts/update/service.py` (re-imported by `bridge_watchdog`, leaving no duplicate lstart parser), called with `get_bridge_pid()` (`:55`) and `get_worker_pid()` (`:144`). `None` → `process_start_ts` unknown → classify `unknown` (fail-safe).

- **`verify_running_release()`** in `service.py`: returns per-process `{running, boot_sha, beacon_ts, process_start_ts, classification}` with the classification rules above; reuses `git.get_short_sha()` for HEAD and `get_process_start_ts(pid)` for `process_start_ts`. Gated per-process on machine role via the passed-in `machine_check` (bridge: `machine_check["bridge_projects"]`; worker: `machine_check["projects"]` — the same gates Step 5 uses at `run.py:1041`/`:1058`) so a machine lacking a role skips that process (no beacon read, no false "release could not be confirmed").

- **`verify_release` CLI** (`python -m scripts.update.verify_release`): reads HEAD, builds `machine_check`, calls `verify_running_release()`, prints the operator-facing summary line, exits `1` on any in-role `stale`, `0` otherwise (`unknown` prints a warning, exit 0). This is what `remote-update.sh` calls as its terminal step.

- **`remote-update.sh` ordering + failure surfacing + verify + bridge kickstart LAST**: the bridge `kickstart -k` SIGKILLs the bridge launchd job, which by process-group semantics also kills `remote-update.sh` itself (spawned by `handle_update_command` inside the bridge) — so **nothing in the shell can run after the bridge kickstart**. Sequence accordingly:
  1. Worker block first (existing `:202-233`): `kickstart -k` the worker on a worker-relevant diff.
  2. Terminal **worker** verify: run `"$PYTHON" -m scripts.update.verify_release` (worker-scoped when a bridge restart is queued — pass a flag so a deliberately-about-to-restart bridge is not escalated as stale here). Capture its exit as `VERIFY_FAILED`.
  3. Compute `NEED_BRIDGE_RESTART` from a `BEFORE_SHA..AFTER_SHA` diff of the bridge-relevant path set, gated on `[ -f "$BRIDGE_DST" ]`. When true **and** a Telegram chat context is present in the env (`UPDATE_REPORT_CHAT_ID`/`UPDATE_REPORT_REPLY_TO`, exported by `handle_update_command`), stage `data/update-pending-report` (chat id + reply-to, `AFTER_SHA` short, worker reload state, timestamp) so the fresh bridge can flush the reply.
  4. **Bridge kickstart LAST**: `launchctl kickstart -k {prefix}.bridge`. This ends the shell.
  - When `NEED_BRIDGE_RESTART` is false, there is no self-kill: run the terminal verify over **both** processes and `exit` normally so `handle_update_command` reports inline. On any kickstart failure (worker or bridge) set `RESTART_FAILED=1`; the terminal exit ORs `RESTART_FAILED || VERIFY_FAILED` so a kickstart failure is never masked by a passing verify. Fix the misleading header comment (`:2`).

- **`handle_update_command` verify + reload state** (`bridge/update.py:134-168`): export the originating chat id + reply-to message id into the `remote-update.sh` env (`UPDATE_REPORT_CHAT_ID`/`UPDATE_REPORT_REPLY_TO`) so a bridge-relevant run can stage the pending report before self-restarting. Then:
  - **If `subprocess.run` returns** (no bridge restart this cycle): `from scripts.update.service import verify_running_release`; build the per-process reload-state string and gate `✅` on `result.returncode == 0 AND` no in-role `stale`. Scan all stdout lines for `ERROR`/`warning`. On stale → `❌ update FAILED @ {sha}: {process} running {short} but HEAD is {short}` (+ still spawn the fix session as today). Degrade gracefully if the verify import/call raises.
  - **If the bridge was restarted** this coroutine is SIGKILLed and never returns — the fresh bridge's boot flush is the reporter (see the fresh-bridge boot flush below), so there is no inline `handle_update_command` report on this path.
- **Fresh-bridge boot flush** (`bridge/telegram_bridge.py` startup, after the boot-SHA beacon write): if `data/update-pending-report` exists, call `verify_running_release()` (fresh bridge beacon + worker beacon), compose `✅ OK @ {sha} (bridge restarted, worker restarted)` or the FAILED variant, send it to the staged chat/reply-to via the normal Telegram send path, then delete the pending report. If the fresh bridge's own beacon lags HEAD, also write the `data/update-release-failed` sentinel and leave the pending report for the watchdog. Best-effort — a flush failure must never crash bridge startup.

- **`run.py` `--full` wiring**: call `verify_running_release()` at the end of the `if config.do_service_restart:` block (Step 5), using the same `machine_check`. Any in-role `stale` → `result.warnings`/error + `result.success = False`. Do NOT add a cron-branch verify.

- **Out-of-band alerting** (the backstop when the survivable-channel reporter itself is dead — **deliberately IN scope, not a tangent**): the bridge-relevant path hands the report to the *fresh* bridge's boot flush (above). That covers a fresh bridge that comes up healthy — even a healthy-but-stale one can still send a FAILED over Telegram, because it booted and its beacon shows the lagging SHA. The gap the sentinel closes is the fresh bridge that **never comes up** (crash-loop, or launchd fails to relaunch): the pending report is never drained, and there is no live channel to report on. Without an off-machine signal, this reproduces #1898's exact symptom (a stale/down bridge with no loud, actionable report). Mechanism, two prongs both read by `monitoring/bridge_watchdog.py` on its 60s cycle: (1) a fresh bridge that boots but self-detects its beacon lags HEAD writes `data/update-release-failed` (SHA lag + timestamp) and does not clear the pending report; (2) a `data/update-pending-report` left undrained past a short TTL is itself the watchdog's signal that the fresh bridge never reported (never-came-up case) — the watchdog surfaces it and escalates. A **worker** hard-fail keeps its non-zero exit + Sentry (the updater process and Telegram channel are both still alive) — no sentinel needed. Sentry capture via `monitoring/sentry_config.py` fires on any hard-fail as the durable off-machine record. (A broader bridge-down/alert-escalation subsystem beyond this sentinel + undrained-report read remains out of scope — see No-Gos.)

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
- [ ] **Kickstart failure NOT masked by passing verify (swallowed-failure regression guard)**: `RESTART_FAILED=1` together with a verify that exits `0` (`matches`/`unknown`) → the script STILL exits non-zero because the terminal exit ORs the two sources. Assert the exit is non-zero and `handle_update_command` reports FAILED — a green verify must never override a kickstart failure (the #1898 root-cause path).
- [ ] **No-op cron verify**: a cron cycle with no new commits and a positively-stale beacon → the terminal `verify_release` step still runs and exits non-zero. Assert verify is invoked and fails even though no restart happened.
- [ ] **`handle_update_command` gates OK (no-bridge-restart path)**: a stale bridge beacon on a worker-only/no-op update (process survives) → the inline report is FAILED naming `bridge running {short} but HEAD is {short}`, NOT `✅`; a matched fleet → `✅ … (bridge current, worker restarted)`. Assert the per-process reload-state string.
- [ ] **Bridge-relevant update stages + flushes via the survivor**: a bridge-relevant diff → `remote-update.sh` stages `data/update-pending-report` (chat id + reply-to + HEAD short-SHA) before the bridge kickstart; the fresh-bridge boot flush reads it, verifies against its own fresh beacon + worker beacon, sends `✅ … (bridge restarted, worker restarted)` (or FAILED) to the staged chat, and deletes the file. Assert the pending-report is written pre-kickstart and drained on boot (no inline `handle_update_command` report on this path).
- [ ] **All-lines warning scan**: a `warning`/`ERROR` on a non-first stdout line is detected (the fix session is spawned / report reflects it), where today only the first line is scanned.

### `--full` Path
- [ ] `run.py --full` with an in-role `stale` beacon → `result.success = False` + non-zero exit + a clear error naming both short-SHAs. `unknown` → warn only.

### Out-of-band Alerting
- [ ] Fresh bridge boots stale → self-writes `data/update-release-failed` (SHA lag) AND leaves the pending report; `bridge_watchdog` reads the sentinel on its cycle. Fresh bridge never comes up → the undrained `data/update-pending-report` past its TTL is surfaced by the watchdog. Worker hard-fail → non-zero exit + Sentry, **no** sentinel. Sentry capture fires on any hard-fail.

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
**Mitigation (concrete, on the cron/shell path):** the `verify_release` CLI takes a **`--since <epoch>` argument** — the restart moment (`RESTART_TS`) that `remote-update.sh` captures just before the kickstarts — and runs a **bounded `10 × 2s` poll** (mirroring the worker-heartbeat freshness poll at `run.py:1387-1400`, `for _ in range(N): sleep(2)`) waiting for a beacon whose `beacon_ts > --since` before it classifies. This is the explicit implementation the shell path needs: without it, running verify as an immediate terminal step right after `kickstart -k` reads a beacon predating the fresh `process_start_ts`, classifies `unknown → warn`, and masks the stale/FAILED. A beacon that never freshens past `--since` within the window → `stale`/fail (the process failed to come up on new code — exactly what to catch). On a no-op cron cycle (nothing restarted) `--since` is `0`/omitted so no poll wait is incurred. The shell's terminal verify is worker-scoped when a bridge restart is queued, so it does not race the bridge (which the fresh bridge verifies against its own just-written beacon on boot — no cross-process race). The `handle_update_command` inline re-verify (no-bridge-restart path only) uses the same bounded poll seeded from its subprocess start moment.

### Race 2: Restart flag set-then-consumed vs. verify
**Location:** `git.set_restart_requested` (`run.py:1558`) vs. worker `_check_restart_flag` vs. verify step.
**Trigger:** verify runs while the worker is between flag-set and its next idle check.
**Data/State prerequisite:** classification must not race on the flag being deleted mid-read.
**Mitigation:** classification is read-only over the *beacons*, not the flag — a matched beacon reads success regardless of flag state; a stale beacon reads stale. No write contention is introduced.

## No-Gos (Out of Scope)

- `[EXTERNAL]` Running `/update` on the Captain (or any bridge machine) to capture the executable proof for acceptance criterion 3 — the dev machine has no bridge role, so the release-verification output (bridge kickstart + OK-gating report) must be captured on a real bridge machine by the operator. The build produces the code + local/full-mode tests + the `verify_release` CLI; the on-bridge proof run is human-gated.
- `[SEPARATE-SLUG]` A dedicated idle-gated bridge self-restart flag (`data/bridge-restart-requested` with a bridge-side consumer mirroring `_check_restart_flag`) that would let the bridge converge on new code at its *own* next idle boundary rather than via the shell's `kickstart -k`. The DESIRED outcome (bridge actually restarts + release is verified before OK) is fully delivered by the shell kickstart + verify in *this* plan. A bridge self-restart code path is a distinct capability for its own issue. Explicitly NOT reusing the worker's `data/restart-requested` flag (first-reader-wins race).
- `[SEPARATE-SLUG]` Fixing the underlying session-wedge that starves the worker's deferred restart (resilience workstream #1815/#1877) — this plan bounds the *consequence* (a stale worker is surfaced loudly), not the wedge.
- `[SEPARATE-SLUG]` A broader bridge-down alerting/escalation subsystem — a retry ladder, PagerDuty/second-channel escalation, sentinel-age SLOs, or auto-remediation on top of `data/update-release-failed`. This plan ships only the single sentinel write + the one `bridge_watchdog` 60s read needed to make a bridge-down-after-restart audible (kept in scope because the bridge cannot report its own FAILED — see the Out-of-band alerting bullet). Any richer alerting policy is a distinct capability for its own issue.

Everything else relevant — the boot beacons, the `remote-update.sh` bridge kickstart + failure surfacing, the shared `verify_running_release()` + `verify_release` CLI, `handle_update_command`'s OK-gating + per-process reload state, the `run.py --full` verify, the docstring/log corrections, and the tests — is in scope.

## Update System

This bug **is** in the update system, so the change is intrinsically to `/update`:
- `scripts/remote-update.sh` — **primary fix**: add the bridge `kickstart -k` block (bridge-relevant diff + `[ -f "$BRIDGE_DST" ]` gated), stop swallowing worker/bridge restart failures (non-zero exit), run `python -m scripts.update.verify_release` as the terminal step, fix the misleading header comment.
- `bridge/update.py` — `handle_update_command` gates `✅` on `verify_running_release()`, appends per-process reload state, scans all stdout lines for warnings. `handle_force_update_command` already restarts via `run.py --full` (covered by the run.py verify).
- `scripts/update/service.py` — new `verify_running_release()` (positive-staleness/unknown classification), the generalized `get_process_start_ts(pid)` (moved from `bridge_watchdog`), and `boot_sha`/`beacon_ts` on status.
- `scripts/update/verify_release.py` (new) — thin `python -m` CLI wrapper for the shell (prints the summary line, exit 1 on stale).
- `scripts/update/run.py` — `verify_running_release()` as the terminal step of the `--full` (`do_service_restart=True`) branch; the `--full` failure sentinel + Sentry on hard-fail.
- `monitoring/bridge_watchdog.py` — `get_bridge_process_start_ts` generalized to shared `get_process_start_ts(pid)` and re-imported (no duplicate lstart parser); reads `data/update-release-failed` and treats an undrained `data/update-pending-report` past a short TTL as a bridge-never-came-up signal on its 60s cycle (the fresh-bridge-reporter-dead cases).
- `bridge/telegram_bridge.py` + `worker/__main__.py` — write boot-SHA beacons at startup.
- `agent/agent_session_queue.py` — string-only corrections (no behavior change, MUST NOT change the SIGTERM target): `_trigger_restart` docstring (`:1250`) and the `_check_restart_flag` "restarting bridge" log line (`:1245`) → state they restart the WORKER.
- No new deps. No `migrations.py` change (beacon files + the failure sentinel are inert, self-healing on next startup / next failed update). Propagates to all machines via the normal `/update` git pull; the first post-merge full `/update` restarts the fleet and begins writing beacons, and the first cron cycle thereafter runs the bridge kickstart + verify.

## Agent Integration

No agent integration required — entirely internal to the update system and process startup. No new MCP tool, no `.mcp.json` change. The agent already invokes `/update` via the existing Telegram `handle_update_command` → `remote-update.sh` path; that path gains the bridge restart + verification without a new entry point. (`python -m scripts.update.verify_release` is a shell-internal helper, not an agent-facing CLI.)

## Documentation

### Feature Documentation
- [ ] Update `docs/features/bridge-self-healing.md` (or the update-system doc) to describe: the boot-SHA beacon, the new `remote-update.sh` bridge-kickstart-LAST block (symmetric with the worker block, safe/no-sessions, self-kills the shell so nothing runs after it), the survivable-channel report path (`data/update-pending-report` staged pre-kickstart, flushed by the fresh bridge's boot verify on a bridge-relevant update; `handle_update_command` inline on the worker-only/no-op path), the `--full` verify, and the bridge-down `data/update-release-failed` sentinel + undrained-report watchdog read.
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
- [ ] The `✅` is gated on bridge AND worker running release == pulled HEAD **before** it is printed, reporting FAILED naming the stale process + lagging short-SHA otherwise, with per-process reload state appended (`(bridge restarted, worker restarted)`). The reporter is `handle_update_command` inline when no bridge restart occurred, or the **fresh bridge's boot flush** of a staged `data/update-pending-report` when the bridge restarted (since the bridge `kickstart -k` kills `handle_update_command` and its shell child by process group, the doomed process cannot be the reporter for a bridge-relevant update). (Acceptance criterion 2 — the exact #1898 surface.)
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
  - Role: Add boot-SHA beacon writes (bridge + worker); `verify_running_release()` + `get_process_start_ts()` + the `verify_release` CLI in `scripts/update/`; the `remote-update.sh` bridge-kickstart-LAST block + pending-report staging + failure surfacing + terminal worker verify; `handle_update_command` OK-gating + chat-context export + per-process reload state + all-lines warning scan; the fresh-bridge boot flush of `data/update-pending-report`; the `run.py --full` verify; the out-of-band sentinel + undrained-report watchdog read + Sentry.
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
- Add `scripts/update/verify_release.py` (`python -m scripts.update.verify_release [--since <epoch>]`): reads HEAD + machine role; when `--since` is non-zero, runs a bounded `10 × 2s` poll (mirroring `run.py:1387-1400`) waiting for a beacon with `beacon_ts > --since` before classifying; then calls the verifier, prints the operator-facing summary line naming any stale process + lagging short-SHA, exits 1 on any in-role `stale`.

### 3. remote-update.sh bridge kickstart + failure surfacing + terminal verify
- **Task ID**: build-shell-restart
- **Depends On**: build-verify-core
- **Validates**: remote-update.sh shell/subprocess test, verify_release CLI test
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: false
- Worker block first (existing `:202-233`): `kickstart -k` the worker on a worker-relevant diff. Capture `RESTART_TS=$(date +%s)` just before it.
- Terminal **worker** verify: run `"$PYTHON" -m scripts.update.verify_release --since "$RESTART_TS"` (pass `0`/omit when nothing restarted), capturing its exit as `VERIFY_FAILED`. When a bridge restart is queued this step is worker-scoped (a flag tells it not to escalate the deliberately-about-to-restart bridge as stale); with no bridge restart it verifies both processes.
- Compute `NEED_BRIDGE_RESTART` from a `BEFORE_SHA..AFTER_SHA` bridge-relevant diff (`bridge/ agent/ mcp_servers/ models/ tools/ config/ pyproject.toml`) gated on `[ -f "$BRIDGE_DST" ]`. When true AND `UPDATE_REPORT_CHAT_ID`/`UPDATE_REPORT_REPLY_TO` are in the env, stage `data/update-pending-report` (chat id + reply-to, `AFTER_SHA` short, worker reload state, ts) so the fresh bridge can flush the reply.
- **Bridge kickstart LAST**: `launchctl kickstart -k {prefix}.bridge`. This SIGKILLs the bridge job — and, by process-group semantics, `remote-update.sh` itself — so it MUST be the final statement; nothing after it runs. Safe (no sessions); catchup covers downtime. The fresh bridge boot flush reports.
- When `NEED_BRIDGE_RESTART` is false there is no self-kill: `exit` with `RESTART_FAILED || VERIFY_FAILED` so `handle_update_command` reports inline. A worker OR bridge kickstart failure sets `RESTART_FAILED=1` (no more swallowed `echo ERROR`); **the terminal exit ORs both sources so a kickstart failure is never masked by a passing verify**. Fix the misleading header comment (`:2`).

### 4. handle_update_command OK-gating + reload state
- **Task ID**: build-report-gate
- **Depends On**: build-verify-core
- **Validates**: bridge-update unit tests
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: false
- Export `UPDATE_REPORT_CHAT_ID`/`UPDATE_REPORT_REPLY_TO` into the `remote-update.sh` env so a bridge-relevant run can stage the pending report before self-restarting.
- **If `subprocess.run` returns** (no bridge restart this cycle): call `verify_running_release()`; gate `✅` on `returncode == 0 AND` no in-role `stale`; on stale report `❌ update FAILED @ {sha}: {process} running {short} but HEAD is {short}`; append per-process reload state (`(bridge current, worker restarted)`). Scan ALL stdout lines for `ERROR`/`warning`. Degrade gracefully if the verify import/call raises. (This path also re-catches a pre-existing stale bridge.)
- **If the bridge was restarted** this coroutine is SIGKILLed and never returns — no inline report on this path.
- **Fresh-bridge boot flush** (`bridge/telegram_bridge.py` startup, after the beacon write): if `data/update-pending-report` exists, call `verify_running_release()` (fresh beacon + worker beacon), compose `✅ OK @ {sha} (bridge restarted, worker restarted)` / FAILED, send to the staged chat/reply-to, delete the file. If the fresh beacon lags HEAD, also write `data/update-release-failed` and leave the pending report for the watchdog. Best-effort — never crash startup.

### 5. run.py --full verify + out-of-band alert
- **Task ID**: build-full-verify
- **Depends On**: build-verify-core
- **Validates**: tests/unit/test_update_release_verify.py
- **Assigned To**: update-verify-builder
- **Agent Type**: builder
- **Parallel**: false
- Call `verify_running_release()` at the end of the `if config.do_service_restart:` block; any in-role `stale` → error + `result.success = False`. Do NOT add a cron-branch verify.
- On a bridge hard-fail / bridge-down-after-restart, write `data/update-release-failed` (SHA lag + ts) and make `monitoring/bridge_watchdog.py` read it on its 60s cycle. The watchdog also treats a `data/update-pending-report` left undrained past a short TTL as a bridge-never-came-up signal (the fresh bridge never booted to flush it). Sentry capture on any hard-fail.
- **Fresh-bridge boot flush**: at bridge startup, after the boot-SHA beacon write, drain `data/update-pending-report` — verify against the fresh beacon + worker beacon, send the OK/FAILED reply to the staged chat, delete the file; write the sentinel if the fresh beacon lags HEAD. Best-effort; never crash startup. (Folds into `update-verify-builder`.)

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
| Blocker | SHA-form mismatch | Beacon writer specified `git rev-parse HEAD` (full 40-char) while the classifier compares `boot_sha == get_short_sha(HEAD)`; a full SHA can never equal its short form, so `matches` is unreachable and every healthy post-restart process misclassifies `stale` — inverting the feature into a false-FAILED on every successful update. | Solution "Boot-SHA beacon" (line ~118) + Technical Approach "Beacon write" (line ~149) prose **now actually edited** (a prior revision recorded this row but left the prose saying `rev-parse HEAD`): both mandate `scripts/update/git.py::get_short_sha(project_dir)`, the `sentry_config.py:61` framing is dropped, and a SHA-form round-trip test is added. | Writer and classifier share one representation by construction; the round-trip test writes a beacon at HEAD and asserts `matches` (fails hard if a 40-char SHA ever leaks in). Grep confirms `rev-parse HEAD`/`sentry_config.py:61` no longer appear in the beacon-writer spec. |
| Blocker | Bridge self-kill vs OK-report | The plan asserted (Out-of-band bullet) that the bridge's own `kickstart -k` SIGKILLs `handle_update_command` + its `remote-update.sh` child mid-update, yet Flow + Step 4 had that SAME process survive to run the terminal verify and print OK — mutually exclusive. On a bridge-relevant update the reporter dies, so acceptance criterion 2 (verify running release before OK) was unreachable on the path it targets. | Approach (b): the bridge kickstart is the shell's **final act**; `remote-update.sh` stages `data/update-pending-report` (chat context from `handle_update_command`'s env) before it; the **fresh bridge** boots at HEAD, writes its beacon, verifies, and flushes the OK/FAILED reply. Flow, Step 3/4, the Out-of-band bullet, Architectural Impact, Success Criterion 2, Race 1, and new Decisions 12-13 are all reconciled. | Approach (a) rejected: it prints OK before the fresh bridge's release is knowable (violates "verify bridge AND worker before OK") and the pure cron path has no reporter to sequence a reply through. The sentinel is reframed as the backstop for a fresh bridge that never comes up. |
| Concern | Verify races an in-flight restart | Race 1's "poll the beacon" mitigation had no concrete implementation on the cron/shell path; running verify immediately after `kickstart -k` reads a beacon predating the fresh `process_start_ts` → `unknown → warn`, masking the stale/FAILED. | `verify_release` CLI gains a `--since <epoch>` arg with a bounded `10 × 2s` poll (mirroring `run.py:1387-1400`); Race 1, the CLI spec, Steps 2-3, and the `handle_update_command` re-verify all specify it. | `remote-update.sh` passes `RESTART_TS`; `--since 0`/omit skips the poll on no-op cron cycles; a beacon that never freshens past `--since` → `stale`/fail. |
| Concern | Swallowed-failure regression risk | "propagate verify's exit code" was under-specified; literal propagation lets an `unknown`/`matches` verify (exit 0) override a kickstart `RESTART_FAILED=1`, reproducing #1898. | `remote-update.sh` terminal exit now ORs the two sources (`RESTART_FAILED \|\| VERIFY_FAILED`); Technical Approach, Step 3, and a dedicated regression-guard test lock it in. | `handle_update_command`'s `returncode == 0` half of its gate consequently already captures a swallowed kickstart failure. |
| Concern | Sentinel/watchdog/Sentry scope creep | The `data/update-release-failed` sentinel + watchdog + Sentry alerting was flagged as answering a different question than #1898 and was not fenced. | Decided IN scope with an explicit load-bearing rationale (the bridge's self-`kickstart` kills the very `handle_update_command` process that would report FAILED, so for the bridge case the off-machine signal is the only way #1898's symptom surfaces); a broader alerting/escalation subsystem is fenced `[SEPARATE-SLUG]`. | Made explicit in the Out-of-band alerting Solution bullet and a new No-Go drawing the boundary. |

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

---

## Decisions (resolved in the surgical revision, 2026-07-05)

12. **The bridge cannot report its own restart — the report is handed to a survivable channel (approach (b)).** `launchctl kickstart -k {prefix}.bridge` SIGKILLs the bridge launchd job; by process-group semantics that also kills `handle_update_command` and the `remote-update.sh` bash child it spawned. So on a **bridge-relevant** update the process that ran `/update` cannot survive to verify the post-restart bridge release and reply — the previous Flow/Step-4 text (doomed process runs the terminal verify + prints OK) contradicted the line-163 sentinel rationale. Resolution: (i) the bridge kickstart is the shell's **final act** (nothing after it runs); (ii) before it fires, `remote-update.sh` stages `data/update-pending-report` (chat id + reply-to + HEAD short-SHA + worker reload state) using the chat context `handle_update_command` exports into its env; (iii) the **fresh bridge** boots at HEAD, writes its beacon (so its own release is knowable), reads the pending report, runs `verify_running_release()` over its fresh beacon + the worker beacon, and flushes the OK/FAILED reply — this is where "verify running release before printing OK" (acceptance criterion 2) actually holds, in the survivor. Approach (a) (kickstart last, reply first) was rejected because it prints OK *before* the fresh bridge's release is knowable, violating the plan's own "verify bridge AND worker before OK" criterion, and because the pure 30-min cron path has no reporter to sequence a reply through at all.

13. **Sentinel rationale, reconciled.** The sentinel is no longer "the only channel" — a healthy fresh bridge (even one that boots stale) can send FAILED over Telegram via the boot flush. `data/update-release-failed` + the undrained-`data/update-pending-report`-past-TTL watchdog read cover only the residual case where the fresh bridge **never comes up** (crash-loop / launchd fails to relaunch) and thus cannot flush anything. On the no-bridge-restart path (worker-only / no-op), `handle_update_command` survives and reports inline — and still re-catches a pre-existing stale bridge.
