---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2714
last_comment_id: 5277602180
---

# Anchor the SDLC lease heartbeat to its supervisor's lifetime

## Problem

`tools/sdlc_lease_heartbeat.py` is launched detached by
`tools/sdlc_session_ensure.py::_maybe_launch_lease_heartbeat` and renews the
per-issue lease (`session:issuelock:{N}`) plus the companion supervised-run
signal (`session:supervisedrun:{N}`) every `ISSUE_LOCK_TTL_SECONDS // 3` (600s).
Nothing connects it to the process that *uses* the lease. Its only death signal
is `MAX_LIFETIME_SECONDS` — a 4-hour wall-clock backstop measured from its own
start.

**Current behavior:**

A supervisor that halts on a guard, stops at a gate FAIL, finishes its loop, or
is killed leaves its heartbeat running. Because the heartbeat keeps stamping
`renewed_at`, `_lock_owner_is_live` short-circuits to `True`
(`models/session_lifecycle.py:1020`) and `orphaned_lock` reads `false`. Per
`.claude/skills-global/do-sdlc/SKILL.md:91`, `orphaned_lock: false` is the *only
unconditional stop condition* — so every resume on that issue correctly stands
down against a run that no longer exists.

Two live reproductions:

1. **#2701, 2026-08-10.** Supervisor stood down at a Task 1 gate FAIL; heartbeat
   pid 76576 was still renewing 3h23m later. Recovery required adopting the dead
   run's identity with `--reuse-run-id`, then a manual `kill`.
2. **2026-08-13, six at once** (issue comment 5277602180). Six heartbeats on
   issues closed in *March* (#515, #530, #537, #540, #554, #559), all spawned
   within 16 seconds at 12:25 +07, all holding `HELD` leases with
   `orphaned_lock: False`. Still running at plan time, 2h33m in — inside the 4h
   backstop, so the backstop has neither fired nor been disproven.

**Desired outcome:**

A heartbeat's lifetime is bounded by evidence about its supervisor, not by a
fixed wall clock. A supervisor that exits releases its lease immediately; a
supervisor that is killed has its lease released within one heartbeat tick; a
heartbeat that can find no supervisor at all stops renewing well inside two
hours instead of four. In every case the #2446/#2451 guarantee (never lapse a
live run's lease mid-stage) and the #2537 guarantee (never re-acquire a lease
you lost) hold unchanged.

## Freshness Check

**Baseline commit:** `90c0e81e4` (`git rev-parse HEAD` after `git pull --rebase`
at plan time; the working checkout was one commit behind `origin/main` and was
fast-forwarded before planning)
**Issue filed at:** 2026-08-10T08:51:32Z
**Disposition:** Unchanged

**File:line references re-verified:**

- `tools/sdlc_session_ensure.py:143` — `_maybe_launch_lease_heartbeat` — **still
  holds.** Detached `subprocess.Popen(..., start_new_session=True, close_fds=True)`
  at `:180-197`; the `Popen` object is discarded, so the child pid is never
  captured, stored, or logged.
- `tools/sdlc_lease_heartbeat.py:79` — `MAX_LIFETIME_SECONDS = 4 * 60 * 60` —
  **still holds**, env-overridable via
  `SDLC_LEASE_HEARTBEAT_MAX_LIFETIME_SECONDS`.
- `tools/sdlc_session_ensure.py:29` — `orphaned_lock` — **still holds** as the
  documented refusal-table input.
- `agent/supervised_run.py` — signal renewed on the same tick — **still holds**
  (`tools/sdlc_lease_heartbeat.py:168` calls `write_supervised_run_signal`).
- `tools/sdlc_session_ensure.py:576` — the single call site of
  `_maybe_launch_lease_heartbeat`, on a fresh local mint — **still holds.**

**Cited sibling issues/PRs re-checked:**

- #2446 — closed 2026-07-30 (PR #2468). Introduced the heartbeat. Its guarantee
  is unchanged and constrains this plan.
- #2451 — same lane as #2446.
- #2537 — closed 2026-08-07 (PR #2615). Keyed same-machine liveness on
  `machine_id`; also removed the heartbeat's dependence on `orphaned_lock`.
- #2659 — closed 2026-08-10 (PR #2667), one day *after* this issue was filed.
  It added the `write_supervised_run_signal` call inside the renew loop. This
  is the only landed change in the blast radius since filing; it makes the
  problem strictly worse (both keys now stay fresh together) and does not change
  the root cause.
- #2620 — closed 2026-08-07. "orphaned_lock is unreliable for every
  locally-minted lease." Resolved by re-keying `_lock_owner_is_live` on
  `renewed_at` freshness and documenting it, not by stamping a long-lived pid.
  Its explicitly-named unexplored fix direction — "stamp an identity that is
  genuinely long-lived for the run (the supervising `claude -p` pid …)" — is
  what this plan implements, scoped to the heartbeat rather than to
  `orphaned_lock`'s semantics.

**Commits on main since issue was filed (touching referenced files):**

`git log --since=2026-08-10T08:51:32Z -- tools/sdlc_lease_heartbeat.py
tools/sdlc_session_ensure.py agent/supervised_run.py models/session_lifecycle.py
.claude/skills/sdlc/SKILL.md .claude/skills-global/do-sdlc/SKILL.md` returns
**zero commits**. Nothing in the blast radius has moved.

**Active plans in `docs/plans/` overlapping this area:** none. The four most
recent active plans (`context-recall-advisory-flag`, `sdlc-lane-recorded-slug`
#2735, `agent-session-updated-at-restamp` #2660, `session-liveness-tick-counter`
#2716) each contain **zero** matches for `lease_heartbeat`, `touch_issue_lock`,
or `issuelock`. #2716 is about UI-facing session-liveness *feedback* (a progress
counter), not lease ownership — adjacent vocabulary, disjoint code.

**Bug still reproducible:** yes, live at plan time. `ps -eo pid,ppid,etime,lstart`
shows the six 12:25:09 heartbeats plus this lane's own, all with `ppid=1`.

**Notes:** The issue comment reads `ppid=1` as evidence the supervisor is gone.
That inference is **not sound and must not be built on** — see spike-2. The
heartbeat is reparented to `launchd` within seconds of every spawn because its
literal parent is the short-lived `sdlc-tool session-ensure` CLI. `ppid=1` is
the steady state for a healthy heartbeat, not a death signal.

## Prior Art

- **#2446 / PR #2468**: "SDLC run self-recognition: owned_run_ids, lease
  heartbeat, loud marker-write." Created `tools/sdlc_lease_heartbeat.py` to stop
  local leases lapsing mid-stage. Succeeded at that; introduced the unbounded-
  in-practice lifetime this issue reports.
- **#2537 / PR #2615**: "Key issue-lock same-machine liveness on stable
  machine_id, not hostname." Fixed a rename-induced fail-open; also removed the
  heartbeat's use of `orphaned_lock`, hardening it against lease theft.
  Succeeded.
- **#2620**: "orphaned_lock is unreliable for every locally-minted lease (pid
  belongs to the ephemeral session-ensure CLI)." Closed by re-keying
  `_lock_owner_is_live` on `renewed_at` freshness. Succeeded at making the flag
  self-consistent; explicitly deferred the "stamp a long-lived identity"
  direction that this plan now takes.
- **#2659 / PR #2667**: "Renew the supervised-run signal wherever the issue lease
  is renewed." Fixed the signal expiring 30 min into every pipeline. Succeeded,
  and coupled the two keys' lifetimes — which is why a zombie heartbeat now
  keeps *both* keys fresh.
- **#2026 / PR #2076**: fork-vs-supervisor hardening, single-owner lease,
  supervised-run signal. Established the ownership model this plan operates
  inside.
- **#2305**: introduced `_lock_owner_is_live` and the pid + `create_time`
  pid-recycling guard reused verbatim by this plan.

## Research

**Queries used:**

- `psutil detect parent process death orphan daemon pid create_time reuse guard`

**Key findings:**

- The canonical guard for "is my recorded process still the same process" is to
  record `(pid, create_time)` at startup and, on every poll, re-open
  `psutil.Process(pid)` and compare `create_time` for **exact** equality against
  the stored value. Both `NoSuchProcess` and a `create_time` mismatch mean the
  original process is gone; an inequality comparison is wrong because a recycled
  pid can belong to a process *older* than yours.
  (https://github.com/giampaolo/psutil/issues/356) — This is exactly the shape
  `models/session_lifecycle.py::_lock_owner_is_live` already uses (with a `1e-3`
  tolerance for float round-trip through JSON), so this plan reuses that helper's
  pattern rather than inventing one.
- Polling-based orphan reapers (`orphand`) treat the pid-recycling window as a
  real, reproducible risk and mitigate it with the same `create_time` check.
  (https://github.com/mnunberg/orphand) — Informs the decision to require a
  *positive* death observation before releasing a lease.
- `PPID == 1` polling is a cheap orphan signal but is explicitly called out as
  unreliable under subreapers and modern init systems.
  (https://github.com/tgree/pyreap) — Corroborates spike-2's rejection of the
  issue comment's `ppid=1` reasoning; on macOS with `start_new_session=True` the
  heartbeat is reparented to `launchd` immediately regardless of supervisor
  health.
- `prctl(PR_SET_PDEATHSIG)` is the race-free alternative but is Linux-only and
  fires on the parent *thread*'s exit. Not portable to this macOS-first repo,
  and the relevant ancestor is not the literal parent anyway. Rejected.

Sources: [psutil #356](https://github.com/giampaolo/psutil/issues/356),
[orphand](https://github.com/mnunberg/orphand),
[pyreap](https://github.com/tgree/pyreap)

## Spike Results

### spike-1: Is the supervising `claude` process pid discoverable from a `sdlc-tool` Bash call?

- **Assumption**: "`session-ensure` can identify the long-lived supervisor
  process at mint time, without heuristics."
- **Method**: prototype (live process/env probe from a Claude Code Bash call in
  this very session)
- **Finding**: **Yes, deterministically.** Claude Code exports `CLAUDE_PID` into
  every Bash tool call's environment. Measured here: `CLAUDE_PID=32886`, and the
  ancestry walk independently resolved
  `zsh(72484) → claude --permission-mode bypassPermissions (32886) → -zsh(1439) → login → iTermServer → iTerm2`.
  `CLAUDE_PID` matched the `claude` ancestor exactly. Also present:
  `CLAUDECODE=1`, `CLAUDE_CODE_SESSION_ID`, `CLAUDE_CODE_CHILD_SESSION=1`. The
  probe was run from a **subagent**, and `CLAUDE_PID` still pointed at the
  top-level `claude` process — which is the correct supervisor identity for
  `/do-sdlc`, since the supervisor loop and every stage subagent live inside
  that one process.
- **Confidence**: high (direct measurement, Claude Code 2.1.222)
- **Impact on plan**: `CLAUDE_PID` becomes the primary supervisor-identity
  source. Because it is an undocumented harness variable, the design keeps an
  ancestry-walk fallback and a final "unresolvable" branch (see Risk 2).

### spike-2: Does `ppid=1` indicate a dead supervisor?

- **Assumption**: "`ppid=1` on the heartbeat proves the supervisor died" (claimed
  in the issue's 2026-08-13 comment).
- **Method**: code-read + live process observation
- **Finding**: **False — the assumption is unsound.** The heartbeat's literal
  parent is the `sdlc-tool session-ensure` CLI, which exits within seconds of
  spawning it; `start_new_session=True` guarantees immediate detachment. Every
  healthy heartbeat is reparented to pid 1 almost at once. Confirmed live: this
  lane's own heartbeat (pid 68294, spawned 14:56:42, 2 minutes old, supervisor
  demonstrably alive) already showed `ppid=1`.
- **Confidence**: high
- **Impact on plan**: the design must watch an *explicitly recorded* supervisor
  pid, never `os.getppid()` and never "ppid became 1". This is called out in
  Rabbit Holes so a builder does not re-derive the comment's shortcut.

### spike-3: Is there an existing release path the supervisor could call?

- **Assumption**: "Something already releases the lease when a run ends."
- **Method**: code-read (`grep` for `release_issue_lock` / `finalize_session`
  call sites)
- **Finding**: `models/session_lifecycle.py:1326::release_issue_lock` exists and
  is a correct compare-and-delete (Lua `_RELEASE_IF_VALUE_MATCHES_LUA`,
  ownership-checked, returns `False` on any mismatch or error). Non-test callers:
  `finalize_session` (`:574`, terminal session transitions only),
  `tools/sdlc_session_ensure.py:495/517/532` (bind-failure rollback),
  `tools/sdlc_stage_marker.py:815` (synthetic cold-ISSUE lease), and
  `tools/_sdlc_run_identity.py:204` (terminal-pipeline guard). **None of these
  fire when a `/do-sdlc` supervisor exits** — the `sdlc-local-{N}` session is not
  transitioned to a terminal status by the skill. `scripts/sdlc-tool`'s
  `ALLOWED_SUBCOMMANDS` (line 19) has **no** release subcommand.
- **Confidence**: high
- **Impact on plan**: L1 requires a new `sdlc-tool session-release` subcommand
  plus a skill-body exit step. The underlying primitive already exists and is
  safe, so the new tool is a thin, ownership-checked wrapper.

### spike-4: Is the heartbeat's log file usable for diagnosis today?

- **Assumption**: "`logs/sdlc_lease_heartbeat.log` records what the heartbeats
  did."
- **Method**: code-read + `ls`
- **Finding**: **No.** The file is 0 bytes, last touched 2026-08-04, across every
  heartbeat spawned since. `main()` only calls `logging.basicConfig` under
  `--verbose` (`tools/sdlc_lease_heartbeat.py:205-206`), and every log call in
  the module is `logger.debug`. The six zombies produced zero diagnostic output.
- **Confidence**: high
- **Impact on plan**: adds an observability task (Task 6). Without it, the new
  exit paths would be equally invisible and untestable in production.

### spike-5: Where does a supervisor's activity become observable to a detached process?

- **Assumption**: "The supervisor emits a periodic, cheap, run-scoped signal that
  a detached process could read."
- **Method**: code-read
- **Finding**: Yes. All four state-mutating SDLC CLIs — `tools/sdlc_verdict.py:103`,
  `tools/sdlc_dispatch.py:55`, `tools/sdlc_stage_marker.py:128`,
  `tools/sdlc_meta_set.py:72` — import `heal_missing_run_id` /
  `maybe_heal_after_write` from `tools/_sdlc_run_identity.py`. That is a single
  shared seam through which every supervisor-or-stage write for a run passes.
  `/do-sdlc` additionally re-ensures identity at every stage seam (Step 3d.6).
- **Confidence**: high
- **Impact on plan**: makes L3 (intent-staleness) implementable with four
  one-line call sites and no new plumbing in the skills.

## Data Flow

1. **Entry point**: local `/do-sdlc` supervisor (a `claude` process, pid
   `$CLAUDE_PID`) shells out to `sdlc-tool session-ensure --issue-number N`.
2. **`tools/sdlc_session_ensure.py`**: mints `run_id`, acquires
   `session:issuelock:{N}` via `touch_issue_lock` (payload stamps the *CLI's*
   pid — dead seconds later), writes `session:supervisedrun:{N}`, and **(new)**
   resolves `(supervisor_pid, supervisor_create_time)` and writes
   `session:runintent:{N}`.
3. **`_maybe_launch_lease_heartbeat`**: detached `Popen` of
   `python -m tools.sdlc_lease_heartbeat`, **(new)** carrying
   `--supervisor-pid` / `--supervisor-create-time`.
4. **Heartbeat loop** (every 600s today; **(new)** supervisor check every 60s):
   peek lease → if self-owned, extend TTL + refresh supervised-run signal.
   **(new)** before each renew, evaluate supervisor liveness; on positive death
   release the lease + clear the signal + exit; on unresolvable supervisor,
   consult `session:runintent:{N}` and exit (without releasing) when stale.
5. **Supervisor activity**: every `stage-marker` / `verdict` / `dispatch` /
   `meta-set` write **(new)** refreshes `session:runintent:{N}` TTL.
6. **Supervisor exit** (merged / blocked / HALT / cap reached): **(new)**
   `sdlc-tool session-release --issue-number N --run-id X` →
   `release_issue_lock` + `clear_supervised_run_signal`.
7. **Output**: on the next peek the heartbeat sees `owner_run_id is None` and
   exits 0 through its existing, unmodified path. A resuming supervisor's
   `session-ensure` finds a free lock and mints a fresh `run_id` with no
   `--reuse-run-id` adoption.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2468 (#2446/#2451) | Created the detached heartbeat so a local lease stops lapsing mid-stage | Solved lease *under*-renewal. Chose a fixed 4h wall-clock self-exit as the only death signal because no long-lived run identity was available to key on — and none was sought. |
| PR #2615 (#2537) | Same-machine liveness keyed on `machine_id`; removed the heartbeat's `orphaned_lock` dependence | Correct and necessary, but it removed the *only* liveness input the heartbeat consulted, leaving pure self-timing. |
| #2620 | Re-keyed `_lock_owner_is_live` on `renewed_at` freshness | Made the flag internally consistent, but "fresh renewal" is produced by the heartbeat itself. A zombie heartbeat now *manufactures* the proof-of-life that suppresses `orphaned_lock`. The issue explicitly named "stamp the supervising `claude -p` pid" as the unexplored alternative. |
| PR #2667 (#2659) | Renewed the supervised-run signal on the same tick as the lease | Correct for the under-renewal failure it targeted, and it strictly amplifies this one: a zombie now keeps both keys fresh in lockstep. |

**Root cause pattern:** every prior fix hardened the heartbeat against dying too
early, and each did so by making its renewal *more* self-sufficient. Nothing ever
gave it an external, falsifiable liveness input, so the system has no way to
distinguish "lease is fresh" from "run is alive" — and the two have drifted into
being the same measurement.

## Architectural Impact

- **New dependencies**: none. `psutil` is already a dependency and is already
  used for exactly this check in `models/session_lifecycle.py` and
  `agent/session_health.py`.
- **Interface changes**:
  - `run_heartbeat()` gains keyword-only `supervisor_pid`,
    `supervisor_create_time`, `supervisor_check_interval`, and
    `intent_staleness` parameters, all defaulting to the current behavior.
  - New CLI `tools/sdlc_session_release.py` + one entry in
    `scripts/sdlc-tool::ALLOWED_SUBCOMMANDS`.
  - New helpers `touch_run_intent()` / `read_run_intent()` in
    `agent/supervised_run.py`.
- **Coupling**: increases coupling from the heartbeat to the harness environment
  (`CLAUDE_PID`) by one clearly-isolated resolver function. Decreases coupling
  between "lease freshness" and "run liveness", which is the point.
- **Data ownership**: `session:runintent:{N}` is a new key owned by the
  state-mutating SDLC write path. The heartbeat is a strict *reader* of it — it
  must never write it, or it recreates the self-proving loop this plan removes.
- **Reversibility**: high. Every new signal is additive and each layer degrades
  to today's behavior when its input is unresolvable. Reverting is deleting the
  new module plus four call sites.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirming the `/do-sdlc`-only release scoping)
- Review rounds: 2+ (core SDLC concurrency control; #2446 regression risk)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `psutil` importable | `python -c "import psutil; psutil.Process().create_time()"` | Supervisor-liveness check |
| Redis reachable via Popoto | `.venv/bin/python -c "from popoto.redis_db import POPOTO_REDIS_DB as r; r.ping()"` | Lease + intent keys |
| `sdlc-tool` on PATH | `sdlc-tool --help` | Dispatcher for the new subcommand |

## Solution

### Key Elements

- **L1 — Explicit release at supervisor exit** (`sdlc-tool session-release`): a
  new ownership-checked subcommand that frees the lease and clears the
  supervised-run signal, invoked by `/do-sdlc` on **every** exit path. Covers the
  deliberate stand-down case, which no process-liveness check can ever see
  (a stood-down supervisor's `claude` process is still alive).
- **L2 — Supervisor-process liveness watch**: `session-ensure` records the
  supervisor's `(pid, create_time)` at mint and hands it to the heartbeat, which
  polls it and, on a *positively established* death, releases the lease and
  exits. Covers the killed/crashed supervisor.
- **L3 — Intent-staleness backstop**: when the supervisor is unresolvable
  (no `CLAUDE_PID`, no ancestry match — the shape the six March zombies have),
  the heartbeat instead requires the run to have shown *any* SDLC write activity
  within a bounded window, and stops renewing when it has not. Replaces the 4h
  wall clock as the effective bound on that path.
- **L4 — Observability**: the heartbeat logs its decisions at INFO to
  `logs/sdlc_lease_heartbeat.log`, which is currently permanently empty.

The three liveness layers are **mutually exclusive by evidence quality**, not
stacked: L2 is consulted when the supervisor is resolvable, L3 only when it is
not. This is deliberate — see Risk 1.

### Flow

**Supervisor mints run** → `session-ensure` resolves `CLAUDE_PID` + create_time,
writes intent key, spawns heartbeat with `--supervisor-pid` →
**Heartbeat ticking** → every 60s: supervisor alive? → yes → every 600s: peek +
renew lease + signal → **Supervisor exits (any path)** →
`sdlc-tool session-release` frees lease → **Heartbeat's next peek** →
`owner_run_id is None` → exit 0 (existing code path, untouched) →
**Resuming supervisor** → `session-ensure` finds a free lock → fresh `run_id`,
no `--reuse-run-id`.

Crash branch: **Supervisor killed** → next 60s supervisor check → psutil
`NoSuchProcess` (or `create_time` mismatch) on two consecutive checks →
`release_issue_lock` + `clear_supervised_run_signal` → exit 0.

Unresolvable branch: **No supervisor identity recorded** → intent key absent or
foreign → stop renewing, exit 0 **without** releasing → lease lapses on its own
1800s TTL.

### Technical Approach

**L1 — `tools/sdlc_session_release.py` + skill wiring**

- New module exposing `release_run(issue_number, run_id) -> dict` and a `main()`
  emitting typed JSON: `{"released": bool, "reason": str, "issue_number": int,
  "run_id": str}`. `reason` ∈ `{"released", "not_owner", "no_lease",
  "missing_args", "error"}`.
- Implementation is a thin wrapper: `release_issue_lock(issue_number, run_id)`
  (already a compare-and-delete, `models/session_lifecycle.py:1326`) then
  `clear_supervised_run_signal(issue_number, run_id)`
  (`agent/supervised_run.py:273`, also compare-and-delete). Both are already
  ownership-checked, so a wrong `run_id` is a safe no-op — the tool never needs
  its own ownership logic and **must not** add a raw `DEL` path.
- Register `session-release` in `scripts/sdlc-tool::ALLOWED_SUBCOMMANDS` (line
  19) and in its `usage()` block. The dispatcher maps kebab → `tools.sdlc_session_release`
  automatically.
- Wire into `.claude/skills-global/do-sdlc/SKILL.md` as a new **Step 5: Release
  the run lease** placed after Step 4 (Final Report), stated to run on *every*
  exit path enumerated in Step 3e (merged / blocked / cap reached) **and** on the
  Step 3d.4 HALT. Concrete invocation goes in the repo addendum
  `docs/sdlc/do-sdlc.md`, per the skill-context convention.
- **Scoping decision (load-bearing):** do **not** wire release into
  `.claude/skills/sdlc/SKILL.md`. `/sdlc` is a single-stage router whose run must
  survive across invocations; releasing there would re-mint a `run_id` at every
  stage boundary. `/do-sdlc` is the only supervisor whose exit means the run is
  over.

**L2 — supervisor identity + watch**

- New `tools/sdlc_supervisor_identity.py` (or a private section of
  `sdlc_session_ensure.py`) with
  `resolve_supervisor_identity() -> tuple[int | None, float | None]`:
  1. `CLAUDE_PID` env, if it parses as an int **and** `psutil.Process(pid)`
     resolves → return `(pid, proc.create_time())`.
  2. Fallback: walk `psutil.Process().parents()` and take the first ancestor
     whose executable basename is `claude` (or a `node` process whose cmdline
     contains `claude`).
  3. Otherwise `(None, None)`.
  Every branch is wrapped best-effort; failure returns `(None, None)`, never
  raises, never fails the ensure.
- `_maybe_launch_lease_heartbeat` appends `--supervisor-pid` /
  `--supervisor-create-time` only when both resolve.
- `run_heartbeat` gains `_supervisor_is_dead()`, mirroring
  `_lock_owner_is_live`'s proven shape: `_psutil_process_for_pid(pid) is None`
  → dead; else dead iff `abs(proc.create_time() - recorded) > 1e-3`; **any
  exception → not dead** (fail toward holding the lease, #2446).
- Require `SDLC_SUPERVISOR_DEATH_CONFIRMATIONS` (default `2`) consecutive
  positive-death observations before acting, so a single psutil flake cannot
  drop a live run's lease.
- Decouple cadences: the loop sleeps `SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS`
  (default `60`, provisional/tunable) and only performs the peek+renew when at
  least `interval` seconds have elapsed since the last renew. This tightens the
  crash-detection bound from 600s to ~120s without changing renew load on Redis.
- On confirmed death: `release_issue_lock` + `clear_supervised_run_signal`, log
  at INFO, `return 0`.

**L3 — run-intent beacon (unresolvable-supervisor path only)**

- `agent/supervised_run.py` gains
  `touch_run_intent(issue_number, run_id, ttl=None)` and
  `read_run_intent(issue_number) -> str | None`, over a new raw-Redis key
  `session:runintent:{N}` holding the bare `run_id`, `ex=SDLC_RUN_INTENT_TTL_SECONDS`
  (default `5400` = 90 min, PROVISIONAL/TUNABLE per the magic-number convention).
  Same raw-Redis idiom as the sibling keys in that module — these are **not**
  Popoto-managed keys, so the raw-Redis guard does not apply and no Popoto
  migration is required.
- Written by: `tools/sdlc_session_ensure.py` at mint/rebind, and by the four
  state-mutating CLIs (`sdlc_stage_marker`, `sdlc_verdict`, `sdlc_dispatch`,
  `sdlc_meta_set`) at the `heal_missing_run_id` seam identified in spike-5.
- **Never** written by `tools/sdlc_lease_heartbeat.py`. This is an anti-criterion
  in Verification.
- Heartbeat consults it **only when `supervisor_pid is None`**. Absent or
  foreign `run_id` → stop renewing and `return 0` **without releasing**: absence
  of a beacon is not positive proof of death, so the 1800s lease TTL — not a
  delete — is the correct disposition.
- `MAX_LIFETIME_SECONDS` stays at 4h as an unchanged absolute ceiling. With L2 or
  L3 active it should never bind; leaving it in place costs nothing and keeps the
  #2446 backstop intact if both new layers somehow no-op.

**L4 — observability**

- `main()` calls `logging.basicConfig(level=logging.INFO)` unconditionally
  (`DEBUG` under `--verbose`).
- Log one INFO line at startup (issue, run_id, supervisor pid/create_time or
  "unresolved", intervals) and one INFO line at every exit naming the reason:
  `supervisor_dead` / `intent_stale` / `lease_lost` / `foreign_owner` /
  `max_lifetime`.

## Failure Path Test Strategy

### Exception Handling Coverage

- `tools/sdlc_lease_heartbeat.py:169` — existing broad `except Exception` around
  the tick. Already covered by
  `test_tick_exception_is_swallowed_and_loop_continues`. New tests must assert
  that an exception raised **inside `_supervisor_is_dead`** is swallowed *and*
  that the tick still renews (fail toward holding).
- `tools/sdlc_session_ensure.py:203` — existing broad `except` around the spawn.
  New test: `resolve_supervisor_identity` raising must not prevent the heartbeat
  from being spawned (it just spawns without the supervisor flags).
- New `resolve_supervisor_identity` and `touch_run_intent` are best-effort by
  contract; each gets a test asserting the observable fallback (`(None, None)`,
  and "no exception propagates to the caller") rather than `pass`-and-hope.

### Empty/Invalid Input Handling

- `session-release` with missing/empty `--run-id`, `--issue-number`, or a
  whitespace-only `run_id`: must emit `{"released": false, "reason":
  "missing_args"}` and exit non-zero-free (best-effort tools exit 0), never call
  `release_issue_lock(None, ...)`.
- `--supervisor-pid 0`, negative, or non-numeric: treated as unresolved, not as
  "dead". Test each.
- `--supervisor-create-time` present without `--supervisor-pid` (and vice
  versa): treated as unresolved.
- `read_run_intent` on an absent key returns `None`; on a corrupt/binary value
  returns `None` rather than raising.

### Error State Rendering

- `session-release` output is machine-readable JSON consumed by the `/do-sdlc`
  supervisor's Step 5. Test that a `not_owner` result renders a distinct,
  non-empty `reason` the supervisor can print, rather than an empty object.
- The heartbeat's exit reason must appear in `logs/sdlc_lease_heartbeat.log` —
  test by capturing the logger, asserting a non-empty INFO record naming the
  exit reason.

## Test Impact

- [ ] `tests/unit/test_sdlc_lease_heartbeat.py::TestPeekFirstRenewOnly` (6 tests) — UPDATE: all six must keep passing unchanged; add explicit assertions that with no supervisor args the behavior is byte-for-byte today's (default-off proof). No signature change is allowed to break them.
- [ ] `tests/unit/test_sdlc_lease_heartbeat.py::test_renews_when_self_owned_payload_pid_is_dead` — UPDATE: this test encodes the #2446 guarantee that a dead *payload* pid must not stop renewal. Re-assert it explicitly against the new code path so the new supervisor check is never confused with the payload pid.
- [ ] `tests/unit/test_sdlc_lease_heartbeat.py::TestSupervisedRunSignalRenewal` (4 tests) — UPDATE: add a case asserting the signal is **cleared** (not merely left to expire) on the `supervisor_dead` exit.
- [ ] `tests/unit/test_sdlc_session_ensure.py::test_session_with_heartbeat_never_listed` — UPDATE: verify the new spawn argv (with `--supervisor-pid`) does not break whatever cmdline matching this test performs.
- [ ] `tests/unit/test_sdlc_session_ensure.py::test_stale_local_pipeline_no_heartbeat_still_listed` — UPDATE: same argv-shape check.
- [ ] `tests/unit/test_sdlc_session_ensure.py::test_heartbeat_restores_a_signal_that_lapsed_under_a_live_lease` — UPDATE: must still pass; the restore path is unchanged when the supervisor is alive.
- [ ] `tests/unit/test_sdlc_lease_heartbeat.py::TestSupervisorLiveness` — CREATE: dead supervisor releases + exits; live supervisor renews; unresolved supervisor never consults psutil; `create_time` mismatch counts as dead; single flake does not trip the 2-confirmation gate; psutil exception → keeps renewing.
- [ ] `tests/unit/test_sdlc_lease_heartbeat.py::TestIntentStaleness` — CREATE: fresh intent renews; stale/absent intent stops renewing **without** releasing; intent path is skipped entirely when a supervisor pid is present.
- [ ] `tests/unit/test_sdlc_session_release.py` — CREATE: releases when owner; no-op `not_owner` on a foreign run_id; `no_lease` on an absent lease; clears the supervised-run signal; missing args; Redis error is swallowed into `{"released": false, "reason": "error"}`.
- [ ] `tests/unit/test_supervised_run.py` (if present; else add to the heartbeat suite) — CREATE: `touch_run_intent` / `read_run_intent` round-trip, TTL applied, absent-key `None`, corrupt-value `None`.

## Rabbit Holes

- **Reading `ppid == 1` as a death signal.** The issue's own comment does this
  and it is wrong (spike-2): every healthy heartbeat is reparented to `launchd`
  within seconds because its literal parent is the ephemeral `session-ensure`
  CLI. Building on it would lapse every live lease on tick one — the exact
  #2446 failure.
- **Reworking `orphaned_lock` semantics.** Tempting, because the flag's
  "renewal freshness" definition is what makes a zombie invisible, and the issue
  asks whether it should distinguish "lease fresh, run unproven" from "run
  confirmed live". But `_lock_owner_is_live` is consumed by `sdlc_next_skill`,
  `sdlc_session_ensure`, and `_sdlc_utils` (four sites, per #2620), and changing
  it changes routing for *every* run including worker-driven ones. This plan
  fixes the producer (zombie heartbeats stop existing) rather than the consumer.
  Leave the flag alone.
- **Stamping the supervisor pid into the issue-lock payload** so all consumers
  benefit. Architecturally the better long-term shape and explicitly the
  direction #2620 named. It also drags `touch_issue_lock`,
  `_lock_owner_is_live`, and the renewal branch's "never re-stamp pid" invariant
  into scope. Out of scope here; the heartbeat carries the identity in argv
  instead.
- **`prctl(PR_SET_PDEATHSIG)` or process-group kills.** Linux-only / kills
  siblings. This repo is macOS-first and the relevant ancestor is not the literal
  parent.
- **Killing the six existing zombies as part of the build.** They are pre-fix
  artifacts on March-closed issues; their 4h backstop resolves them. Reaping
  them is an operator action, not code (see No-Gos), and a pattern-kill is
  forbidden by `.claude/hooks/validators/validate_no_broad_process_kill.py`.
- **Chasing what spawned six heartbeats on March-closed issues.** A real and
  separate defect (a spawn-side guard is missing), but it is a different root
  cause from "the heartbeat outlives its supervisor" and would double this
  plan's size. See No-Gos.

## Risks

### Risk 1: The intent-staleness backstop lapses a live lease mid-stage (#2446 regression)
**Impact:** A BUILD stage running longer than the intent TTL with no SDLC writes
would let the lease lapse mid-run — precisely the failure the heartbeat exists to
prevent, and the worst possible regression here.
**Mitigation:** L3 is consulted **only when the supervisor pid is unresolvable**.
On every normal local run `CLAUDE_PID` resolves (spike-1), so L2 is authoritative
and L3 never runs. Additionally L3 only *stops renewing* — it never deletes — so
the lease still survives its full 1800s TTL, and `owned_run_ids` self-recognition
(#2446) plus run-identity self-heal (#2144) already cover re-binding after a
lapse. The 90-minute default is generously above the observed longest gap between
SDLC writes within a stage. Verification includes a test asserting L3 is skipped
whenever a supervisor pid is present.

### Risk 2: `CLAUDE_PID` is an undocumented harness variable and may disappear
**Impact:** If a Claude Code upgrade drops it, L2 silently stops resolving and
every run falls back to L3's 90-minute bound.
**Mitigation:** Three-tier resolution (env → ancestry walk → unresolved), so
losing the env var degrades to the ancestry walk, not to nothing. The startup
INFO log names which source resolved the supervisor, making a silent regression
visible in `logs/sdlc_lease_heartbeat.log`. Worst case is a 90-minute bound —
still materially better than today's 4 hours. Verification asserts the fallback
path is exercised by a test, not just present.

### Risk 3: Releasing at `/do-sdlc` exit breaks cross-invocation continuity
**Impact:** A human who runs `/do-sdlc`, sees it HALT, and immediately re-runs it
gets a new `run_id` instead of continuing the old one.
**Mitigation:** This is the explicitly desired outcome (issue AC 3: "a new run on
the same issue can acquire the lease without `--reuse-run-id` adoption of a dead
run's identity"). The stage ledger is issue-keyed, not run-keyed, so no pipeline
state is lost. The release is scoped to `/do-sdlc` only — `/sdlc`'s per-stage
router, and the worker path, are untouched.

### Risk 4: A supervisor `claude` process that is alive but idle keeps the lease
**Impact:** L2 cannot detect a stand-down; if L1's skill-body step is skipped
(the model does not follow it), the lease is held until the 4h ceiling.
**Mitigation:** L1 lives on the `/do-sdlc` exit path that already has a mandatory
Step 4 (Final Report) on every exit, making it a natural, hard-to-miss
attachment point. Verification greps the skill body for the release invocation.
Residual exposure is the pre-existing 4h ceiling, i.e. no worse than today.

### Risk 5: pid recycling makes a dead supervisor look alive
**Impact:** A recycled pid could suppress the death signal, leaving the lease
held to the ceiling.
**Mitigation:** `create_time` exact-match within `1e-3` — the same guard
`_lock_owner_is_live` already uses and the pattern the external research
identifies as canonical. A recycled pid produces a *mismatch*, which counts as
dead, so the guard fails toward detection here rather than away from it.

## Race Conditions

### Race 1: Supervisor releases the lease while the heartbeat is mid-renew
**Location:** `tools/sdlc_lease_heartbeat.py:139-158` vs
`models/session_lifecycle.py:1326::release_issue_lock`
**Trigger:** Step 5's release lands between the heartbeat's peek (owner matches)
and its `touch_issue_lock` extend. The extend then re-`SET`s the key — the
heartbeat re-mints a lease the supervisor just freed.
**Data prerequisite:** the lock key must be absent at extend time for the harm to
occur.
**State prerequisite:** the run must be over.
**Mitigation:** window is bounded by two adjacent Redis round-trips (single-digit
ms). If it fires, the re-minted lease is owned by the *retiring* `run_id`, and
the very next tick (≤60s) sees `supervisor_dead` or `intent_stale` and exits;
worst case the lease lapses on its own TTL. A build task must additionally
confirm `touch_issue_lock`'s renewal branch is a plain `SET` and record whether
`XX`-guarding it is a safe tightening — do **not** change it speculatively, since
`SET NX EX` semantics there are load-bearing for #2537.

### Race 2: Two heartbeats for the same issue after a re-mint
**Location:** `tools/sdlc_session_ensure.py:576`
**Trigger:** an old heartbeat is still ticking when a successor mints a new
`run_id` and spawns its own.
**Data prerequisite:** both processes hold distinct `run_id`s.
**State prerequisite:** the lock is owned by the successor.
**Mitigation:** already handled and unchanged — the old heartbeat's peek returns
a foreign `owner_run_id` and it exits 0 without mutating (#2537). The new
release path is likewise CAD-guarded on `run_id`, so an old heartbeat can never
release a successor's lease.

### Race 3: Intent key written by a stage fork after the supervisor died
**Location:** the four `heal_missing_run_id` call sites
**Trigger:** a stage subagent's write lands after the supervisor process is gone,
refreshing the intent TTL for a dead run.
**Data prerequisite:** the fork still carries the dead run's `run_id`.
**State prerequisite:** supervisor unresolvable (only then is L3 consulted).
**Mitigation:** in `/do-sdlc` the stage forks live *inside* the supervisor
process, so this cannot outlive it. In the degenerate unresolvable case the
window is one intent TTL and the disposition is only "keep renewing", never
"release". Acceptable.

## No-Gos (Out of Scope)

- [EXTERNAL] Reaping the six pre-existing zombie heartbeats (pids 7406, 7438,
  7560, 7740, 7743, 10637). These predate the fix, sit on March-closed issues,
  and expire via the existing 4h backstop. A pattern kill is blocked by
  `.claude/hooks/validators/validate_no_broad_process_kill.py` and would take out
  other agents' processes; a human must `kill` the six pids individually if they
  want them gone sooner.
- [SEPARATE-SLUG #2714] Nothing — this plan closes #2714 itself.

Investigating *why* six heartbeats were spawned on issues closed five months ago
(the missing spawn-side terminal/closed-issue guard, and the off-pin `python@3.14`
interpreter they ran under) is a genuinely different root cause. It is **not**
deferred by this plan: the fix here bounds any such heartbeat's life to ≤2 hours
regardless of how it was spawned, so the observed symptom is covered. A
build-time task files it as a fresh investigation issue via
`/do-investigation-issue` so the spawn-side gap is tracked on its own slug rather
than promised here.

## Update System

- **No new dependencies, config files, or migrations.** `psutil` is already
  installed; the two new Redis keys are plain TTL'd strings that need no
  migration and self-populate on first use.
- **One propagation requirement:** the edit to
  `.claude/skills-global/do-sdlc/SKILL.md` is a *global* skill and reaches other
  machines only through `/update`'s hardlink sync
  (`scripts/update/hardlinks.py` → `~/.claude/skills/`). No `RENAMED_REMOVALS`
  entry is needed (no file is added, moved, or removed — only edited).
- **Existing installations:** a machine running the old code keeps today's
  behavior until `/update`. No mixed-version hazard: the new
  `--supervisor-pid` flags are only ever passed by the same-checkout
  `session_ensure` that ships them, and `session-release` is invoked only by the
  updated skill body.
- Run `/update` after merge per the standing post-merge rule.

## Agent Integration

- **New CLI entry point:** `session-release` must be appended to
  `ALLOWED_SUBCOMMANDS` in `scripts/sdlc-tool` (line 19) **and** described in its
  `usage()` heredoc. Without the allowlist entry the dispatcher exits 2 with
  "unknown subcommand" and the `/do-sdlc` Step 5 becomes a silent no-op. There is
  no `pyproject.toml [project.scripts]` change — `sdlc-tool` is the single
  entrypoint and maps kebab-case subcommands to `tools.sdlc_<snake_case>`
  automatically.
- **No bridge import.** `bridge/telegram_bridge.py` does not call any of this;
  the heartbeat and release path are invoked from `tools/` and from skill bodies
  via Bash.
- **Integration test:** a test that shells out to `sdlc-tool session-release
  --help` (or asserts `session-release` is present in `ALLOWED_SUBCOMMANDS`)
  proving the agent can actually reach the new subcommand through the dispatcher,
  not just import the module.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/sdlc-local-supervision.md` — the paragraph at line 21
      describes the heartbeat as self-terminating "after a bounded max lifetime";
      replace with the supervisor-anchored lifetime model (L1/L2/L3) and the
      release-on-exit contract.
- [ ] Update `docs/features/sdlc-issue-ownership-lock.md` — the renewer table
      (line ~316) and the file inventory (line ~384) must describe the new exit
      conditions and the `session:runintent:{N}` key; add `session-release` as an
      explicit release path alongside `finalize_session`.
- [ ] Update `docs/features/sdlc-run-self-recognition.md` — note that the
      heartbeat now has external liveness inputs, so self-recognition is a
      narrower fallback than before.
- [ ] Update `docs/sdlc/do-sdlc.md` (repo addendum) — add the concrete
      `sdlc-tool session-release --issue-number {n} --run-id {run_id}` invocation
      for the new Step 5.
- [ ] Update `docs/tools-reference.md` — add the `session-release` subcommand.
- [ ] No new `docs/features/README.md` index row is needed (no new feature doc
      is created; all four targets already have index entries). Verify this
      during `/do-docs` and add a row only if a new doc is introduced.

### Inline Documentation
- [ ] Rewrite the `tools/sdlc_lease_heartbeat.py` module docstring: it currently
      asserts "Run-liveness is this heartbeat's own existence; its bounded
      max-lifetime is the death backstop" (lines 32-33), which this change makes
      false. Per the no-legacy rule, describe only the new status quo.
- [ ] Docstrings for `resolve_supervisor_identity`, `_supervisor_is_dead`,
      `touch_run_intent`, `read_run_intent`, `release_run`.
- [ ] A comment at the L3 branch stating explicitly that it is consulted **only**
      when the supervisor is unresolvable, and why (Risk 1).
- [ ] `GRAIN OF SALT` comments marking `SDLC_RUN_INTENT_TTL_SECONDS`,
      `SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS`, and
      `SDLC_SUPERVISOR_DEATH_CONFIRMATIONS` as provisional/tunable with env
      overrides, per the repo's magic-number convention.

## Success Criteria

- [ ] A `/do-sdlc` supervisor that exits on any path (merged, blocked, HALT, cap
      reached) leaves no held lease: `touch_issue_lock(N, None, peek=True)`
      reports `owner_run_id is None`.
- [ ] A killed supervisor's heartbeat releases the lease and exits within
      `2 × SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS` (~120s default), verified by a
      unit test with injected clocks.
- [ ] A heartbeat with no resolvable supervisor stops renewing within
      `SDLC_RUN_INTENT_TTL_SECONDS`, so the lease is free within ≤2h total
      (90 min + 30 min TTL) instead of 4h.
- [ ] After a supervisor stops, `sdlc-tool session-ensure --issue-number N` mints
      a fresh `run_id` without `--reuse-run-id`.
- [ ] #2446/#2451 preserved: a live supervisor's lease is renewed indefinitely; a
      dead *payload* pid still never stops renewal
      (`test_renews_when_self_owned_payload_pid_is_dead` passes unchanged).
- [ ] #2537 preserved: the heartbeat still peeks before every renew and exits on
      a foreign owner; it never mints on a free key.
- [ ] A test covers the stood-down-supervisor case asserting the lease stops
      being renewed.
- [ ] `logs/sdlc_lease_heartbeat.log` is non-empty after a heartbeat runs and
      names the exit reason.
- [ ] `sdlc-tool session-release` is reachable through the dispatcher.
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)
- [ ] `grep` confirms `scripts/sdlc-tool` references `session-release`
- [ ] No xfail/xpass markers relate to this bug (searched: none found in
      `tests/unit/test_sdlc_lease_heartbeat.py` or
      `tests/unit/test_sdlc_session_ensure.py`)

## Team Orchestration

### Team Members

- **Builder (supervisor-liveness)**
  - Name: `hb-liveness-builder`
  - Role: supervisor identity resolution + heartbeat liveness watch + cadence split
  - Agent Type: builder
  - Resume: true

- **Builder (release-path)**
  - Name: `release-builder`
  - Role: `sdlc-tool session-release` subcommand, dispatcher registration, skill wiring
  - Agent Type: builder
  - Resume: true

- **Builder (intent-beacon)**
  - Name: `intent-builder`
  - Role: `session:runintent:{N}` helpers + the four write-path call sites
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `hb-test-engineer`
  - Role: new test classes + the Test Impact updates
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `hb-validator`
  - Role: verifies #2446 and #2537 guarantees survive; runs the Verification table
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `hb-documentarian`
  - Role: the Documentation checklist
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Supervisor identity resolver
- **Task ID**: build-supervisor-identity
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_lease_heartbeat.py (create `TestSupervisorLiveness`)
- **Informed By**: spike-1 (confirmed: `CLAUDE_PID` is exported into every Bash tool call and matched the `claude` ancestor exactly), spike-2 (confirmed: `ppid=1` is NOT a death signal)
- **Assigned To**: hb-liveness-builder
- **Agent Type**: builder
- **Domain**: async/process-liveness
- **Parallel**: true
- Add `resolve_supervisor_identity()` returning `(pid, create_time)` with the three-tier resolution: `CLAUDE_PID` env → `psutil` ancestry walk for a `claude` ancestor → `(None, None)`.
- Never use `os.getppid()` and never treat `ppid == 1` as a signal.
- Best-effort throughout: any exception returns `(None, None)`.
- Pass `--supervisor-pid` / `--supervisor-create-time` from `_maybe_launch_lease_heartbeat` only when both resolve.

### 2. Heartbeat supervisor watch + cadence split
- **Task ID**: build-heartbeat-watch
- **Depends On**: build-supervisor-identity
- **Validates**: tests/unit/test_sdlc_lease_heartbeat.py
- **Informed By**: Research (canonical `(pid, create_time)` exact-match guard), Risk 5
- **Assigned To**: hb-liveness-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_supervisor_is_dead()` mirroring `_lock_owner_is_live`'s psutil shape (`_psutil_process_for_pid` + `create_time` within `1e-3`); any exception → not dead.
- Gate action behind `SDLC_SUPERVISOR_DEATH_CONFIRMATIONS` (default 2) consecutive observations.
- Split the loop: sleep `SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS` (default 60), renew only when `interval` has elapsed.
- On confirmed death: `release_issue_lock` + `clear_supervised_run_signal`, log INFO, `return 0`.
- Keep every existing peek-first branch byte-for-byte reachable; new params default to today's behavior.

### 3. `sdlc-tool session-release`
- **Task ID**: build-session-release
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_session_release.py (create)
- **Informed By**: spike-3 (confirmed: `release_issue_lock` is an ownership-checked CAD; no release subcommand exists)
- **Assigned To**: release-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/sdlc_session_release.py` with `release_run()` + `main()` emitting typed JSON `{released, reason, issue_number, run_id}`.
- Wrap `release_issue_lock` then `clear_supervised_run_signal`. No raw Redis `DEL`.
- Register `session-release` in `scripts/sdlc-tool::ALLOWED_SUBCOMMANDS` and its `usage()` block.

### 4. Run-intent beacon
- **Task ID**: build-intent-beacon
- **Depends On**: none
- **Validates**: tests/unit/test_sdlc_lease_heartbeat.py (create `TestIntentStaleness`)
- **Informed By**: spike-5 (confirmed: all four state-mutating CLIs import from `tools/_sdlc_run_identity.py`), Risk 1
- **Assigned To**: intent-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: true
- Add `touch_run_intent` / `read_run_intent` to `agent/supervised_run.py` over `session:runintent:{N}` with `SDLC_RUN_INTENT_TTL_SECONDS` (default 5400, marked provisional/tunable).
- Call `touch_run_intent` from `sdlc_session_ensure` at mint/rebind and from the four state-mutating CLIs at the `heal_missing_run_id` seam.
- Wire the heartbeat's consult **only** under `supervisor_pid is None`; stale → stop renewing, `return 0`, **do not release**.
- The heartbeat must never write the intent key.

### 5. `/do-sdlc` release-on-exit wiring
- **Task ID**: build-skill-wiring
- **Depends On**: build-session-release
- **Assigned To**: release-builder
- **Agent Type**: builder
- **Parallel**: false
- Add "Step 5: Release the run lease" to `.claude/skills-global/do-sdlc/SKILL.md`, stated to run on every Step 3e exit path and on the Step 3d.4 HALT.
- Put the concrete invocation in `docs/sdlc/do-sdlc.md` per the skill-context convention; keep the global body generic.
- Do **not** touch `.claude/skills/sdlc/SKILL.md` — the single-stage router's run must survive across invocations.

### 6. Heartbeat observability
- **Task ID**: build-heartbeat-logging
- **Depends On**: build-heartbeat-watch, build-intent-beacon
- **Informed By**: spike-4 (confirmed: `logs/sdlc_lease_heartbeat.log` has been 0 bytes since 2026-08-04)
- **Assigned To**: hb-liveness-builder
- **Agent Type**: builder
- **Parallel**: false
- `logging.basicConfig(level=INFO)` unconditionally in `main()`; DEBUG under `--verbose`.
- One INFO startup line (issue, run_id, supervisor source + pid, intervals) and one INFO exit line naming the reason (`supervisor_dead` / `intent_stale` / `lease_lost` / `foreign_owner` / `max_lifetime`).
- Rewrite the module docstring to describe only the new status quo (delete the "max-lifetime is the death backstop" claim).

### 7. Tests
- **Task ID**: build-tests
- **Depends On**: build-heartbeat-watch, build-session-release, build-intent-beacon, build-heartbeat-logging
- **Validates**: tests/unit/test_sdlc_lease_heartbeat.py, tests/unit/test_sdlc_session_release.py (create), tests/unit/test_sdlc_session_ensure.py
- **Assigned To**: hb-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Implement every item in the Test Impact and Failure Path Test Strategy sections.
- Use injected `_sleep` / `_monotonic` clocks (already supported) so no test sleeps in real time.
- Never spawn a real detached heartbeat under pytest (the `PYTEST_CURRENT_TEST` guard at `sdlc_session_ensure.py:164` must stay intact — add a test asserting it).

### 8. File the spawn-side investigation issue
- **Task ID**: file-spawn-investigation
- **Depends On**: none
- **Assigned To**: release-builder
- **Agent Type**: builder
- **Parallel**: true
- Use `/do-investigation-issue` to file the "six heartbeats spawned at 12:25:09 on issues closed in March, under an off-pin `python@3.14`" observation as its own issue, citing #2714 comment 5277602180.
- Do not attempt the spawn-side fix in this plan.

### 9. Guarantee validation
- **Task ID**: validate-guarantees
- **Depends On**: build-tests
- **Assigned To**: hb-validator
- **Agent Type**: validator
- **Parallel**: false
- Prove #2446/#2451 survives: a live supervisor renews indefinitely; a dead payload pid never stops renewal.
- Prove #2537 survives: peek-before-renew intact, foreign owner exits, never mints on a free key.
- Prove L3 is unreachable whenever a supervisor pid is present.
- Run every row of the Verification table.

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-guarantees
- **Assigned To**: hb-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Execute the Documentation checklist.

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hb-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the Verification table and confirm every Success Criterion.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Heartbeat tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_lease_heartbeat.py -q` | exit code 0 |
| Release tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_session_release.py -q` | exit code 0 |
| Session-ensure tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_session_ensure.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Subcommand registered | `grep -c "session-release" scripts/sdlc-tool` | output > 1 |
| Subcommand reachable | `sdlc-tool session-release --help` | exit code 0 |
| Peek-first preserved (#2537) | `grep -c "peek=True" tools/sdlc_lease_heartbeat.py` | output > 0 |
| Heartbeat still ignores orphaned_lock (#2620) | `grep -c "orphaned_lock" tools/sdlc_lease_heartbeat.py` | match count == 0 |
| Heartbeat never writes the intent key | `grep -c "touch_run_intent" tools/sdlc_lease_heartbeat.py` | match count == 0 |
| No ppid-based inference (spike-2 anti-criterion) | `grep -cE "getppid\|ppid" tools/sdlc_lease_heartbeat.py tools/sdlc_session_ensure.py` | match count == 0 |
| No raw Redis DEL in the release path | `grep -cE "\.delete\(\|DEL " tools/sdlc_session_release.py` | match count == 0 |
| Release wired into /do-sdlc exit | `grep -c "session-release" .claude/skills-global/do-sdlc/SKILL.md docs/sdlc/do-sdlc.md` | output > 1 |
| /sdlc router NOT wired (scoping anti-criterion) | `grep -c "session-release" .claude/skills/sdlc/SKILL.md` | match count == 0 |
| Pytest spawn guard intact | `grep -c "PYTEST_CURRENT_TEST" tools/sdlc_session_ensure.py` | output > 0 |
| Stale docstring claim removed | `grep -c "max-lifetime is the death backstop" tools/sdlc_lease_heartbeat.py` | match count == 0 |
| Docs updated | `grep -c "session-release" docs/features/sdlc-issue-ownership-lock.md` | output > 0 |

## Critique Results

**Verdict:** NEEDS REVISION (1 blocker, 4 concerns) — FULL war room (force-FULL: plan edits `.claude/skills-global/`).

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | Race 1's mitigation fails on its own primary trigger. The premise that `touch_issue_lock`'s renewal branch is "a plain SET" is wrong — `models/session_lifecycle.py:1222` is already `_R.set(key, value, nx=True, ex=ttl)`, and NX gives no protection when the key is absent, so the heartbeat re-mints the lease the supervisor just released. The stated escape ("next tick sees supervisor_dead or intent_stale") cannot fire on the L1 stand-down path: the supervisor process is alive, so L2 says alive and L3 is never consulted. The re-minted lease then renews to the 4h ceiling — the exact #2714 bug, on the plan's new happy path. | pending | Do NOT add `xx=True` to the `_R.set(...)` at `models/session_lifecycle.py:1222` — that statement is also `ensure_session`'s acquire path and #2537's SET-NX contest is load-bearing. Add keyword-only `renew_only: bool = False` to `touch_issue_lock`; when True and the pre-SET `_R.get(key)` is `None`, return `IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)` instead of minting. Heartbeat passes `renew_only=True` and treats `owner_run_id is None` as its existing lease-lost exit. Test: delete the lock key between peek and extend, assert the key stays absent. |
| CONCERN | Risk & Robustness | Two Verification anti-criterion greps can never fail. In an ERE, an escaped pipe is a *literal* pipe, so the spike-2 row matches only the literal string, not `os.getppid()` (measured: escaped form returns 0 on a file containing `os.getppid()`; unescaped alternation returns 1). Same defect in the raw-Redis-DEL row. Three rows also run `grep -c` over two files, which prints `path:count` per file, so "output > 1" is not evaluable, and `grep -c` exits 1 on zero matches, aborting any `set -e` harness. | pending | Use unescaped ERE alternation in both anti-criterion rows (keep the `\(` escape, drop only the pipe escape). Express every "match count == 0" row as `! grep -qE '<pattern>' <files>` so a violation fails by exit code. Split the multi-file rows one-per-file, or use `grep -ho '<pattern>' f1 f2 \| wc -l` for a single integer. |
| CONCERN | History & Consistency | L1 reverses a documented decision and reinstates a pattern already recorded as failed here. `tools/sdlc_session_ensure.py:150` states "Wiring stays in the tool layer -- NO `/do-sdlc` skill-body edit required"; the plan makes a skill-body prose step the primary release mechanism without refuting it. `docs/sdlc/do-plan-critique.md` records #1654's identical failure (a barrier that "lived only in prose aimed at an LLM"). Risk 4 concedes the exposure but mitigates only with a grep, which proves the text exists, not that it ran. | pending | Give L1 a tool-layer leg: `tools/_sdlc_run_identity.py:204` already calls `release_issue_lock` on its terminal-pipeline guard — route that site through the new `release_run()` so it also calls `clear_supervised_run_signal`, releasing on MERGE-stage completion with zero skill cooperation. Reserve skill-body Step 5 for exits the tool layer cannot see (3d.4 HALT, 3e blocked/cap). Add a test asserting `release_run` fires on the terminal transition. |
| CONCERN | History & Consistency | No-Gos asserts a bound the plan disproves, and defers Task 8 on it. It claims the fix "bounds any such heartbeat's life to ≤2 hours regardless of how it was spawned"; Risk 4 says the resolvable-supervisor path is "held until the 4h ceiling". The ≤2h figure holds only on the L3 unresolvable path, and a re-spawned heartbeat is more likely to resolve a live `CLAUDE_PID` than not. | pending | Restate No-Gos as the honest split: ≤2h only with no resolvable supervisor; otherwise bounded by `MAX_LIFETIME_SECONDS`. To make ≤2h unconditional instead, close Open Question 3 by setting `MAX_LIFETIME_SECONDS` (`tools/sdlc_lease_heartbeat.py:79`) to `2 * 60 * 60` — but only alongside the Task 2 cadence split, since the ceiling then binds live long runs. Task 8's deferral must not rest on the unqualified claim. |
| CONCERN | Scope & Value | L3 is a third backstop for a path spike-1 says never occurs, and it is the largest cost in a Medium plan: a new Redis key, two helpers, five write call sites, a new tunable, a new test class, and a new Race 3 — to move a never-observed path from 4h to 2h, while `MAX_LIFETIME_SECONDS` is retained as a backstop for the same path and Open Question 3 asks whether lowering it would suffice. The plan builds the expensive option without deciding against the one-line one. | pending | Resolve Open Question 3 before build. If dropping L3: delete Task 4 and default `SDLC_LEASE_HEARTBEAT_MAX_LIFETIME_SECONDS` to `5400` at `tools/sdlc_lease_heartbeat.py:79`, keeping the GRAIN OF SALT comment. If keeping L3: add the missing check for its load-bearing invariant (Task 4 states the heartbeat consults intent only when `supervisor_pid is None`, but no Verification row tests it) — in `TestIntentStaleness`, patch `read_run_intent` with a `Mock` and assert `call_count == 0` when `run_heartbeat` is called with a non-None `supervisor_pid`. |

**Structural checks:** required sections PASS (Documentation / Update System / Agent Integration / Test Impact all present and substantive); task numbering PASS (1-11 contiguous); dependencies PASS (all `Depends On` IDs resolve, no cycles); file paths PASS (20 of 24 exist; the 4 absent are the modules this plan creates); prerequisites PASS (psutil, Redis, `sdlc-tool` all verified live); cross-references PASS except the two gaps captured above. Plan file:line claims re-verified against `f614124110`: `_maybe_launch_lease_heartbeat` at `:143`, `MAX_LIFETIME_SECONDS` at `:79`, the single call site at `:576`, and `release_issue_lock` at `:1326` all hold.

---

## Open Questions

1. **Is the `/do-sdlc`-only release scoping right?** L1 deliberately does not
   touch `/sdlc`, because that router dispatches one stage and returns while the
   run must continue. That means a bridge-PM-driven pipeline never gets an
   explicit release — it relies on `finalize_session` at terminal transition
   instead. Confirm that is the intended asymmetry, or name where the worker path
   should release too.
2. **Is 90 minutes the right intent-staleness default?** It only binds on the
   unresolvable-supervisor path, so the #2446 risk is contained — but it also
   sets how long a mystery heartbeat (like the six from March) can hold an issue.
   Shorter is safer for zombies and riskier for the degenerate path. 90 min gives
   a ≤2h worst case against today's 4h.
3. **Should the 4h `MAX_LIFETIME_SECONDS` ceiling be lowered now that two earlier
   layers exist?** The plan leaves it at 4h as an inert final backstop. Lowering
   it to, say, 2h would make the guarantee uniform but adds a second thing that
   can lapse a live lease if both new layers no-op.
