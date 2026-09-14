---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-08-13
tracking: https://github.com/tomcounsell/ai/issues/2714
last_comment_id: 5278530522
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
- **Impact on plan (round 1)**: made the intent-staleness beacon implementable
  with four one-line call sites. **Superseded in round 2** — the beacon was
  dropped on the Scope critique, so this seam is no longer used. The spike stands
  as a correct finding about the codebase; it simply no longer has a consumer
  here, and is retained because it is the natural place a future reviewer would
  reach for if the ceiling proves too blunt.

## Data Flow

1. **Entry point**: local `/do-sdlc` supervisor (a `claude` process, pid
   `$CLAUDE_PID`) shells out to `sdlc-tool session-ensure --issue-number N`.
2. **`tools/sdlc_session_ensure.py`**: mints `run_id`, acquires
   `session:issuelock:{N}` via `touch_issue_lock` (payload stamps the *CLI's*
   pid — dead seconds later), writes `session:supervisedrun:{N}`, and **(new)**
   resolves `(supervisor_pid, supervisor_create_time)`.
3. **`_maybe_launch_lease_heartbeat`**: detached `Popen` of
   `python -m tools.sdlc_lease_heartbeat`, **(new)** carrying
   `--supervisor-pid` / `--supervisor-create-time`.
4. **Heartbeat loop** (every 600s today; **(new)** supervisor check every 60s):
   peek lease → if self-owned, extend TTL **(new: `renew_only=True`, which can
   never mint)** + refresh supervised-run signal. **(new)** before each renew,
   evaluate supervisor liveness; on positive death release the lease + clear the
   signal + exit. **(new)** when the supervisor was never resolvable, the
   lifetime ceiling is 90 min rather than 4h.
5. **Run ends, tool-layer leg**: a successful `MERGE`/`completed` marker write in
   `tools/sdlc_stage_marker.py` **(new)** calls `release_run()` →
   `release_issue_lock` + `clear_supervised_run_signal`. No skill cooperation.
6. **Run ends, skill-body leg** (HALT / blocked / cap reached — invisible to the
   tool layer): **(new)** `sdlc-tool session-release --issue-number N --run-id X`.
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
    `supervisor_create_time`, and `supervisor_check_interval` parameters, all
    defaulting to the current behavior.
  - `touch_issue_lock()` gains keyword-only `renew_only: bool = False`. This is
    the only change to a widely-called shared primitive in this plan; the default
    preserves all ~dozen existing callers unchanged, and only the heartbeat's
    extend call passes `True`.
  - New CLI `tools/sdlc_session_release.py` + one entry in
    `scripts/sdlc-tool::ALLOWED_SUBCOMMANDS`.
- **Coupling**: increases coupling from the heartbeat to the harness environment
  (`CLAUDE_PID`) by one clearly-isolated resolver function. Decreases coupling
  between "lease freshness" and "run liveness", which is the point.
- **Data ownership**: unchanged. No new Redis key is introduced (the round-1
  `session:runintent:{N}` beacon was dropped — see the round-2 scope decision).
  `tools/sdlc_stage_marker.py` gains a release responsibility on the MERGE
  transition it already owns.
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

- **L0 — Renewal must never mint** (`touch_issue_lock(..., renew_only=True)`):
  the heartbeat's extend call currently `SET NX`s its way back into ownership of
  a lock that was just released. Until this is fixed, L1 is self-defeating. This
  is the round-1 critique BLOCKER and is a prerequisite for everything below.
- **L1 — Explicit release when the run ends**, on two legs:
  - **Tool-layer leg (primary, no model cooperation required):** a successful,
    non-idempotent `MERGE`/`completed` marker write in
    `tools/sdlc_stage_marker.py` releases the lease. This fires on the happy path
    for *every* pipeline — local `/do-sdlc`, `/sdlc` router, and worker-driven
    alike — because marking MERGE completed is the unavoidable tool-layer event
    that means "the run is over".
  - **Skill-body leg (exits the tool layer cannot see):** a new
    ownership-checked `sdlc-tool session-release` subcommand, invoked by
    `/do-sdlc` on the Step 3d.4 HALT and the Step 3e blocked / cap-reached exits.
    Covers the deliberate stand-down, which no process-liveness check can ever
    see (a stood-down supervisor's `claude` process is still alive).
- **L2 — Supervisor-process liveness watch**: `session-ensure` records the
  supervisor's `(pid, create_time)` at mint and hands it to the heartbeat, which
  polls it and, on a *positively established* death, releases the lease and
  exits. Covers the killed/crashed supervisor.
- **L3 — Unsupervised lifetime ceiling**: when the supervisor is unresolvable
  (no `CLAUDE_PID`, no ancestry match), the heartbeat is bound by
  `SDLC_HEARTBEAT_UNSUPERVISED_MAX_SECONDS` (default `5400`) instead of the 4h
  `MAX_LIFETIME_SECONDS`, and exits **without releasing**. One constant and one
  branch — no new Redis key, no new write call sites.
- **L4 — Observability**: the heartbeat logs its decisions at INFO to
  `logs/sdlc_lease_heartbeat.log`, which is currently permanently empty.

L2 and L3 are **mutually exclusive by evidence quality**, not stacked: L2 is
consulted when the supervisor is resolvable, L3's shortened ceiling applies only
when it is not. This is deliberate — see Risk 1.

**Round-2 scope decision (closes Open Questions 2 and 3).** The round-1 plan
proposed L3 as a run-intent beacon: a new `session:runintent:{N}` Redis key, two
helpers, five write call sites, a new tunable, a new test class, and a new race
— all to bound a path spike-1 says never occurs on a healthy local run, while
`MAX_LIFETIME_SECONDS` was retained as a backstop for that same path. The
Scope critic was right that the plan built the expensive option without deciding
against the cheap one. Decision: **drop the beacon.** A shortened ceiling on the
unresolvable path buys the identical ≤2h bound (90 min + the lock's own 1800s
TTL) for one constant.

The global 4h `MAX_LIFETIME_SECONDS` is deliberately **not** lowered. Lowering it
uniformly would make a live supervisor's long BUILD stage lapse its own lease at
2h — reintroducing #2446 for the sake of tidiness. Gating the shorter ceiling on
"supervisor unresolvable" gets the bound where the risk is and leaves the
resolvable path's behavior unchanged.

### Flow

**Supervisor mints run** → `session-ensure` resolves `CLAUDE_PID` + create_time,
spawns heartbeat with `--supervisor-pid` → **Heartbeat ticking** → every 60s:
supervisor alive? → yes → every 600s: peek + `renew_only` extend + signal →
**MERGE marked completed** → stage-marker calls `release_run()` → lease freed →
**Heartbeat's next peek** → `owner_run_id is None` → exit 0 (existing code path,
untouched) → **Resuming supervisor** → `session-ensure` finds a free lock →
fresh `run_id`, no `--reuse-run-id`.

Stand-down branch: **Supervisor HALTs / blocks / hits its cap** → Step 5
`sdlc-tool session-release` frees lease → same heartbeat exit as above. Because
the extend is `renew_only`, the heartbeat cannot re-mint the freed lease in the
window before it notices.

Crash branch: **Supervisor killed** → next 60s supervisor check → psutil
`NoSuchProcess` (or `create_time` mismatch) on two consecutive checks →
`release_issue_lock` + `clear_supervised_run_signal` → exit 0.

Unresolvable branch: **No supervisor identity recorded** → 90-minute ceiling
instead of 4h → stop renewing, exit 0 **without** releasing → lease lapses on its
own 1800s TTL, ≤2h total.

### Technical Approach

**L0 — `touch_issue_lock(..., renew_only=True)` (BLOCKER fix, do this first)**

The round-1 plan asserted the renewal branch was "a plain `SET`" and asked a
build task to confirm it. It was wrong in a way that breaks the plan's own happy
path, and the real shape is worse than the critique's summary. Verified at
`models/session_lifecycle.py`:

- `:1222` — `acquired = _R.set(key, value, nx=True, ex=ttl)`. This runs **before**
  any ownership comparison. On an absent key it *succeeds*, and the function
  returns `acquired=True`. The docstring says so outright: "No existing key:
  `SET NX EX` claims it carrying `run_id`." A heartbeat calling this a
  millisecond after L1 released the lease does not renew — it **re-mints**.
- `:1230` — the follow-up `raw is None` branch (SET-NX lost, key then expired)
  *also* returns `acquired=True`. A second, independent path that reports
  ownership of a key nobody holds.

So the heartbeat re-acquires a released lease and, because its supervisor
`claude` process is still alive on the stand-down path, L2 reports "alive"
forever and L3 is never consulted. The lease then renews to the 4h ceiling —
#2714's exact bug, reached through this plan's new happy path. Fixing this is a
precondition for L1 being worth anything.

- Add keyword-only `renew_only: bool = False` to `touch_issue_lock`. Default
  `False` preserves every existing caller byte-for-byte.
- When `renew_only=True`, **skip the `SET NX` entirely** and skip the `raw is
  None` "treat as acquired" branch. Both are minting paths and a renewer must
  have neither. An absent key returns
  `IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)`,
  which the heartbeat already handles as its lease-lost exit.
- Make the surviving renewal write atomic. The existing renewal branch
  (`:1253-1296`) is a read-then-`_R.set(key, ..., ex=ttl)` with a real TOCTOU
  window: a release landing between the `GET` and the `SET` is undone by the
  `SET`, which recreates the key. Replace the write under `renew_only` with a
  compare-and-set Lua script mirroring the existing
  `_RELEASE_IF_VALUE_MATCHES_LUA`:

  ```lua
  if redis.call('GET', KEYS[1]) == ARGV[1] then
    redis.call('SET', KEYS[1], ARGV[2], 'EX', ARGV[3])
    return 1
  end
  return 0
  ```

  `ARGV[1]` is the exact raw value this call read, `ARGV[2]` the self-healed
  payload. A `GET` on an absent key returns `false`, never equal to `ARGV[1]`, so
  the script is structurally incapable of minting. Returns `0` → report
  `acquired=False, owner_run_id=None`.
- Do **not** add `xx=True` to the `_R.set(...)` at `:1222`. That statement is
  also `ensure_session`'s acquire path and #2537's SET-NX contest is load-bearing
  there.
- Heartbeat passes `renew_only=True` on its extend call. Its peek call is
  unchanged (`peek=True` already never mutates).

**L1 — release path: tool-layer leg + `tools/sdlc_session_release.py` + skill wiring**

*Tool-layer leg (primary).* The round-1 plan made a skill-body prose step the
only release mechanism. The History critic was right to refuse that:
`tools/sdlc_session_ensure.py:150` already records the opposite decision
("Wiring stays in the tool layer -- NO `/do-sdlc` skill-body edit required"), and
`docs/sdlc/do-plan-critique.md` records #1654 failing for exactly this reason (a
barrier that "lived only in prose aimed at an LLM"). A grep proving the sentence
exists is not evidence it ran.

- In `tools/sdlc_stage_marker.py::_write_marker_impl`, on a **successful,
  non-idempotent** `--stage MERGE --status completed` write, call
  `release_run(issue_number, run_id)` best-effort (wrapped, never fails the
  marker write). The already-completed idempotent branch (`:613`) must **not**
  release — it does not own the transition.
- This leg is path-agnostic: it fires for `/do-sdlc`, for the `/sdlc` router, and
  for worker-driven pipelines, none of which need to cooperate. It is also
  ordering-consistent with what already exists — after MERGE is completed,
  `_pipeline_is_terminal` makes `reestablish_run_id` decline to re-mint
  (`tools/_sdlc_run_identity.py:200-207`), so nothing downstream expects to still
  hold the lease.
- Route the existing terminal-guard release at
  `tools/_sdlc_run_identity.py:204` through `release_run()` instead of bare
  `release_issue_lock`, so that site also clears the supervised-run signal.
  (This site is narrow — it only fires when something tries to re-establish a
  run identity on a terminal pipeline — so it is a cleanup, not the primary leg.)

*The subcommand.*

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
  the run lease** placed after Step 4 (Final Report), scoped to the exits the
  tool layer cannot observe: the Step 3d.4 HALT and the Step 3e **blocked** and
  **cap-reached** exits. The Step 3e *merged* exit is already covered by the
  stage-marker leg, and the skill step is idempotent there anyway (a released
  lease yields `no_lease`). Concrete invocation goes in the repo addendum
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

**L3 — unsupervised lifetime ceiling (unresolvable-supervisor path only)**

- New module constant `UNSUPERVISED_MAX_LIFETIME_SECONDS = 90 * 60`, env
  override `SDLC_HEARTBEAT_UNSUPERVISED_MAX_SECONDS`, marked GRAIN OF SALT /
  provisional per the magic-number convention.
- The heartbeat's existing max-lifetime comparison selects its bound once at
  startup: `UNSUPERVISED_MAX_LIFETIME_SECONDS` when `supervisor_pid is None`,
  else `MAX_LIFETIME_SECONDS`. One expression; the existing lifetime check and
  its exit path are otherwise untouched.
- On that exit: log INFO with reason `unsupervised_max_lifetime` and `return 0`
  **without releasing**. Failure to resolve a supervisor is not
  positive proof the run is dead, so the 1800s lease TTL — not a delete — is the
  correct disposition. Worst case on this path is 90 min + 30 min = **2h**.
- `MAX_LIFETIME_SECONDS` stays at 4h, unchanged, as the bound on the resolvable
  path. See the round-2 scope decision above for why it is not lowered
  uniformly.
- **No new Redis key.** `session:runintent:{N}`, `touch_run_intent`,
  `read_run_intent`, and the five write call sites from round 1 are all deleted
  from this plan.

**L4 — observability**

- `main()` calls `logging.basicConfig(level=logging.INFO)` unconditionally
  (`DEBUG` under `--verbose`).
- Log one INFO line at startup (issue, run_id, supervisor pid/create_time or
  "unresolved", intervals) and one INFO line at every exit naming the reason:
  `supervisor_dead` / `unsupervised_max_lifetime` / `lease_lost` /
  `foreign_owner` / `max_lifetime`.

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
- New `resolve_supervisor_identity` is best-effort by contract; it gets a test
  asserting the observable fallback (`(None, None)`, and "no exception propagates
  to the caller") rather than `pass`-and-hope.
- `release_run` raising inside `tools/sdlc_stage_marker.py` must **not** fail the
  MERGE marker write. Test with `release_run` patched to raise, asserting the
  marker write still returns success.

### Empty/Invalid Input Handling

- `session-release` with missing/empty `--run-id`, `--issue-number`, or a
  whitespace-only `run_id`: must emit `{"released": false, "reason":
  "missing_args"}` and exit non-zero-free (best-effort tools exit 0), never call
  `release_issue_lock(None, ...)`.
- `--supervisor-pid 0`, negative, or non-numeric: treated as unresolved, not as
  "dead". Test each.
- `--supervisor-create-time` present without `--supervisor-pid` (and vice
  versa): treated as unresolved.
- `SDLC_HEARTBEAT_UNSUPERVISED_MAX_SECONDS` set to a non-numeric or negative
  value falls back to the `90 * 60` default rather than raising or selecting a
  zero-length lifetime that exits on tick one.

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
- [ ] `tests/unit/test_sdlc_lease_heartbeat.py::TestUnsupervisedCeiling` — CREATE: with `supervisor_pid=None` the selected bound is 5400s and the exit logs `unsupervised_max_lifetime` **without** releasing the lease; with a non-None `supervisor_pid` the selected bound is the unchanged 4h `MAX_LIFETIME_SECONDS`. The second case is the round-1 plan's load-bearing invariant that it asserted but never tested.
- [ ] `tests/unit/test_sdlc_session_release.py` — CREATE: releases when owner; no-op `not_owner` on a foreign run_id; `no_lease` on an absent lease; clears the supervised-run signal; missing args; Redis error is swallowed into `{"released": false, "reason": "error"}`.
- [ ] **Issue-lock suite (`tests/unit/` — locate the existing `touch_issue_lock` tests)** — CREATE `TestRenewOnly`: `renew_only=True` on an absent key returns `acquired=False, owner_run_id=None` and **leaves the key absent** (the BLOCKER regression test); `renew_only=True` on a self-owned key still renews and still self-heals `target_repo` / `machine_id` / `renewed_at`; `renew_only=True` on a foreign key reports the foreign owner and does not write; `renew_only=False` (default) behavior is unchanged for every existing caller, including the `SET NX` acquire.
- [ ] `tests/unit/test_sdlc_lease_heartbeat.py::TestReleaseRaceIsClosed` — CREATE: delete the lock key between the heartbeat's peek and its extend; assert the key is still absent afterward and the heartbeat takes its lease-lost exit. This is Race 1's structural proof.
- [ ] **Stage-marker suite (`tests/unit/test_sdlc_stage_marker.py`)** — UPDATE: a successful `MERGE`/`completed` write calls `release_run`; a repeated (idempotent, already-completed) MERGE write does **not**; a `release_run` exception does not fail the marker write.

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

### Risk 1: The shortened unsupervised ceiling lapses a live lease mid-stage (#2446 regression)
**Impact:** A run whose supervisor is unresolvable but genuinely alive, in a BUILD
stage longer than 90 minutes, would let its lease lapse mid-run — precisely the
failure the heartbeat exists to prevent, and the worst possible regression here.
**Mitigation:** The 90-minute ceiling applies **only when the supervisor pid is
unresolvable**. On every normal local run `CLAUDE_PID` resolves (spike-1), so the
4h ceiling is what binds and today's behavior is unchanged. The shortened ceiling
only *stops renewing* — it never deletes — so the lease still survives its full
1800s TTL, and `owned_run_ids` self-recognition (#2446) plus run-identity
self-heal (#2144) already cover re-binding after a lapse. Verification includes a
test asserting the 4h bound is the one selected whenever a supervisor pid is
present. This risk is strictly smaller than in round 1: the dropped beacon could
also have gone stale under a *resolvable* supervisor if the write path missed a
call site, whereas a lifetime ceiling has no such failure mode.

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
**Impact:** L2 cannot detect a stand-down (the process is alive); if L1's
skill-body leg is skipped because the model does not follow the prose step, the
lease is held until the 4h ceiling.
**Mitigation:** The happy path does not depend on the model at all — the
stage-marker leg releases on MERGE completion in the tool layer. The skill-body
leg is therefore only load-bearing for HALT / blocked / cap-reached exits, which
are precisely the cases where a *human* is about to look at the run anyway. The
skill step attaches to the mandatory Step 4 (Final Report) that already runs on
every exit. Verification greps the skill body for the invocation, and a test
asserts the tool-layer leg fires on the MERGE transition — the grep proves the
text exists, the test proves the mechanism works.
**Residual exposure:** a stood-down supervisor whose model skipped Step 5 holds
the lease to the 4h ceiling — no worse than today, and no longer on the happy
path. Honestly stated rather than claimed away.

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
**Trigger:** the release lands between the heartbeat's peek (owner matches) and
its `touch_issue_lock` extend.
**Data prerequisite:** the lock key is absent at extend time.
**State prerequisite:** the run is over.
**Round-1 status:** this was the critique BLOCKER, and the round-1 mitigation was
wrong twice over — the premise that the renewal branch is "a plain `SET`" is
false (`:1222` is `SET NX`, which *acquires* on an absent key), and the stated
escape ("the next tick sees `supervisor_dead` or `intent_stale`") cannot fire on
the stand-down path, because the supervisor process is alive and so L3 is never
consulted. The re-minted lease would renew to the 4h ceiling: #2714's own bug,
on this plan's happy path.
**Mitigation (round 2):** closed structurally by L0 rather than argued down.
Under `renew_only=True` the extend performs no `SET NX` and no "absent key means
acquired" inference, and its write is a Lua compare-and-set against the exact raw
value just read. A `GET` on an absent key returns `false`, which never equals the
expected value, so the script cannot recreate the key. The race becomes a no-op
that reports `owner_run_id is None`, taking the heartbeat's existing lease-lost
exit. Test: delete the lock key between the peek and the extend and assert the
key is **still absent** afterward and the heartbeat exits.

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

### Race 3: MERGE-completed release lands while a later write still needs the lease
**Location:** `tools/sdlc_stage_marker.py::_write_marker_impl` (MERGE/completed)
vs any subsequent state-mutating SDLC write for the same run
**Trigger:** the tool-layer leg frees the lease at MERGE completion; a straggling
`verdict` / `meta-set` write for that run then finds no lease.
**Data prerequisite:** a write attempt after MERGE is marked completed.
**State prerequisite:** the pipeline is terminal.
**Mitigation:** already the designed behavior, not a new hazard. Post-MERGE
re-establishment is *already* refused: `_pipeline_is_terminal` makes
`reestablish_run_id` decline a fresh mint and release the lease it just took
(`tools/_sdlc_run_identity.py:200-207`). Releasing at MERGE completion makes the
lease state agree with a terminal decision the ledger has already recorded. The
idempotent already-completed marker branch (`:613`) does not release, so a
repeated marker write cannot free a *successor* run's lease.

## No-Gos (Out of Scope)

- [EXTERNAL — now moot] Reaping the six pre-existing zombie heartbeats (pids
  7406, 7438, 7560, 7740, 7743, 10637). **Resolved without intervention on
  2026-08-13**: all six self-exited within ~3 minutes of their individual 4h
  marks (observed 16:24-16:28 +0700; see issue comment 5278530522). No operator
  `kill` is needed, and none was performed.
- [SEPARATE-SLUG #2714] Nothing — this plan closes #2714 itself.

Investigating *why* six heartbeats were spawned on issues closed five months ago
(the missing spawn-side terminal/closed-issue guard, and the off-pin `python@3.14`
interpreter they ran under) is a genuinely different root cause, and this plan
**does not** cover the observed symptom as fully as round 1 claimed.

**Measured 2026-08-13 (issue comment 5278530522), and it sharpens the accounting
below.** Watching the six zombies through their 4h expiry established that the
backstop bounds the *process*, not the *lease*: all six exited on schedule, and
all six leases remained **held with `orphaned_lock: False`** afterward, because
the max-lifetime exit path returns without calling `release_issue_lock`. Since
`ISSUE_LOCK_RENEWAL_FRESHNESS_SECONDS` is 1200s and renewals land every 600s, a
dead renewer leaves the *unconditional resume-stop condition* in place for a
further **10-20 minutes**, and the key itself for up to 30. The real bound on
issue unavailability was therefore ~4h20m, not the 4h the issue claims.

This is why every row below is stated as *ceiling + lease TTL* rather than
ceiling alone, and it is direct evidence for the L1 release path: a bound that
stops renewing without releasing always leaves this tail. It does **not** change
the decision that the unsupervised-ceiling exit declines to release — a timeout
is not proof of death, and releasing on one risks cutting a live run's lease.

The honest bound this fix delivers, per path:

| Supervisor state | Bound after this fix |
|---|---|
| Resolvable, run reaches MERGE | released in the tool layer, seconds |
| Resolvable, HALT/blocked/cap + Step 5 ran | released, seconds |
| Resolvable, HALT/blocked/cap + Step 5 skipped | 4h `MAX_LIFETIME_SECONDS` (unchanged from today) |
| Resolvable, killed | ≤ ~120s |
| **Unresolvable** | ≤2h (90 min ceiling + 1800s lease TTL) |

Round 1 asserted "≤2 hours regardless of how it was spawned". That is false and
the round-1 plan disproved it in its own Risk 4. **≤2h holds only on the
unresolvable-supervisor path.** A re-spawned heartbeat launched from inside a
live `claude` session resolves `CLAUDE_PID`, so L2 reports its supervisor alive
and it is bound by the unchanged 4h ceiling — better than nothing, but not the
uniform guarantee round 1 advertised.

Task 8 therefore files the spawn-side gap as its own investigation issue on the
strength of it being a **different root cause**, not on the strength of a bound
this plan does not deliver.

## Update System

- **No new dependencies, config files, or migrations.** `psutil` is already
  installed. **No new Redis key is introduced** — the round-1 beacon was dropped,
  and the pre-existing `session:issuelock:{N}` / `session:supervisedrun:{N}` keys
  are unaffected by this change (their payload shape and TTL are unchanged; only
  *who releases them, and when* moves).
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
      conditions and the `renew_only` renewal contract (a renewer never mints);
      add `session-release` and the stage-marker MERGE leg as explicit release
      paths alongside `finalize_session`.
- [ ] Update `docs/features/sdlc-pipeline.md` — the **"Explicit release"** bullet
      at `:210-214` attributes lease release solely to `finalize_session` "on
      EVERY terminal transition" and frames the `orphaned_lock` self-heal as the
      crash backstop. Both halves are now wrong here: spike-3 established that
      `finalize_session` never fires on a `/do-sdlc` supervisor exit (the reason
      L1 exists), and #2620 plus this plan establish that `orphaned_lock`
      freshness is manufactured by the heartbeat itself and so cannot serve as a
      crash backstop. Name the stage-marker MERGE leg and `session-release`
      alongside `finalize_session`, and soften the self-heal claim.
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
      `release_run`.
- [ ] Update the `touch_issue_lock` docstring's "Behavior (non-peek)" list, which
      currently states "No existing key: `SET NX EX` claims it carrying
      `run_id`". That must gain the `renew_only` exception explicitly — the
      unqualified sentence is what made the round-1 plan's Race 1 analysis wrong.
- [ ] A comment at the unsupervised-ceiling branch stating explicitly that the
      shortened bound applies **only** when the supervisor is unresolvable, and
      why (Risk 1).
- [ ] `GRAIN OF SALT` comments marking `UNSUPERVISED_MAX_LIFETIME_SECONDS`,
      `SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS`, and
      `SDLC_SUPERVISOR_DEATH_CONFIRMATIONS` as provisional/tunable with env
      overrides, per the repo's magic-number convention.

## Success Criteria

- [ ] **A renewing heartbeat can never mint.** With the lock key absent,
      `touch_issue_lock(N, run_id, renew_only=True)` leaves the key absent and
      reports `owner_run_id is None`. (BLOCKER fix; without this every criterion
      below is defeatable.)
- [ ] A run that reaches MERGE leaves no held lease, with **no** skill
      cooperation: after a `MERGE`/`completed` marker write,
      `touch_issue_lock(N, None, peek=True)` reports `owner_run_id is None`.
- [ ] A `/do-sdlc` supervisor that exits via HALT / blocked / cap-reached and runs
      Step 5 likewise leaves no held lease.
- [ ] A killed supervisor's heartbeat releases the lease and exits within
      `2 × SDLC_SUPERVISOR_CHECK_INTERVAL_SECONDS` (~120s default), verified by a
      unit test with injected clocks.
- [ ] A heartbeat with no resolvable supervisor stops renewing at
      `UNSUPERVISED_MAX_LIFETIME_SECONDS`, so the lease is free within ≤2h total
      (90 min + 30 min TTL) instead of 4h — and does **not** release.
- [ ] A heartbeat *with* a resolvable supervisor is still bound by the unchanged
      4h `MAX_LIFETIME_SECONDS`, not the 90-minute one.
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
  - Role: `sdlc-tool session-release` subcommand, dispatcher registration, the stage-marker tool-layer release leg, skill wiring
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

### 0. Renew-only lease extension (BLOCKER fix — land first)
- **Task ID**: build-renew-only
- **Depends On**: none
- **Validates**: tests/unit/test_session_lifecycle.py (or the existing issue-lock
  suite), tests/unit/test_sdlc_lease_heartbeat.py
- **Informed By**: round-1 critique BLOCKER; verified at
  `models/session_lifecycle.py:1222` (`SET NX` acquires on an absent key) and
  `:1230` (the `raw is None` branch also returns `acquired=True`)
- **Assigned To**: hb-liveness-builder
- **Agent Type**: builder
- **Domain**: Redis/concurrency
- **Parallel**: false — every other task's correctness depends on this one
- Add keyword-only `renew_only: bool = False` to `touch_issue_lock`; default
  preserves all existing callers byte-for-byte.
- Under `renew_only=True`: skip the `SET NX` at `:1222` and the `raw is None`
  "treat as acquired" branch at `:1230`. Absent key → `acquired=False,
  owner_run_id=None`.
- Replace the renewal write with a Lua compare-and-set against the exact raw
  value read in the same call, mirroring `_RELEASE_IF_VALUE_MATCHES_LUA`. Keep
  the existing `target_repo` / `machine_id` / `renewed_at` self-heal in the
  payload it sets.
- Do **not** add `xx=True` to the `:1222` `SET NX` — that statement is also
  `ensure_session`'s acquire path and #2537's contest is load-bearing there.
- Heartbeat's extend call passes `renew_only=True`; its `peek=True` call is
  unchanged.
- Test: delete the lock key between peek and extend; assert the key remains
  absent and the result reports `owner_run_id is None`.
- **Thread `renew_only` into the outer exception handler** (round-2 CONCERN).
  `models/session_lifecycle.py:1310`'s `except Exception` wraps the *entire*
  function — acquire, peek, and renewal alike — and returns
  `acquired=True, owner_run_id=run_id`. Under `renew_only` that is the same
  "report ownership without holding the key" shape the Lua CAS exists to
  eliminate: a transient Redis blip on the extend immediately after a release
  would tell the heartbeat it still owns the lease, and it would keep calling
  `write_supervised_run_signal` for a lease it does not hold. Add a
  `renew_only`-keyed branch in that handler returning
  `IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)` —
  a renewer fails **CLOSED**, because a renewer minting is precisely what is
  being prevented. Do **not** change the fail-open default for non-`renew_only`
  callers; #2446 depends on it. Test: patch the Redis call to raise and assert
  `renew_only=True` reports not-acquired while the default path still fails open.

### 1. Supervisor identity resolver
- **Task ID**: build-supervisor-identity
- **Depends On**: build-renew-only
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

### 3. `sdlc-tool session-release` + tool-layer release leg
- **Task ID**: build-session-release
- **Depends On**: build-renew-only
- **Validates**: tests/unit/test_sdlc_session_release.py (create)
- **Informed By**: spike-3 (confirmed: `release_issue_lock` is an ownership-checked CAD; no release subcommand exists)
- **Assigned To**: release-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `tools/sdlc_session_release.py` with `release_run()` + `main()` emitting typed JSON `{released, reason, issue_number, run_id}`.
- Wrap `release_issue_lock` then `clear_supervised_run_signal`. No raw Redis `DEL`.
- Register `session-release` in `scripts/sdlc-tool::ALLOWED_SUBCOMMANDS` and its `usage()` block.
- **Tool-layer leg** (round-1 History CONCERN — do not let prose be the only
  mechanism): call `release_run()` from `tools/sdlc_stage_marker.py::_write_marker_impl`
  on a successful, non-idempotent `MERGE`/`completed` write, wrapped best-effort
  so it can never fail the marker write. The already-completed idempotent branch
  (`:613`) must NOT release.
- Route the existing bare `release_issue_lock` at `tools/_sdlc_run_identity.py:204`
  through `release_run()` so that site also clears the supervised-run signal.
- Test that `release_run` fires on the MERGE transition and does not fire on a
  repeated (idempotent) MERGE marker write.

### 4. Unsupervised lifetime ceiling
- **Task ID**: build-unsupervised-ceiling
- **Depends On**: build-supervisor-identity
- **Validates**: tests/unit/test_sdlc_lease_heartbeat.py (create `TestUnsupervisedCeiling`)
- **Informed By**: round-1 Scope CONCERN (build the cheap option, not both), Risk 1
- **Assigned To**: hb-liveness-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `UNSUPERVISED_MAX_LIFETIME_SECONDS = 90 * 60`, env override
  `SDLC_HEARTBEAT_UNSUPERVISED_MAX_SECONDS`, GRAIN OF SALT comment.
- Select the bound once at startup: unsupervised constant when `supervisor_pid
  is None`, else the unchanged 4h `MAX_LIFETIME_SECONDS`. Do not lower
  `MAX_LIFETIME_SECONDS`.
- **Use an explicit-vs-default sentinel** (round-2 CONCERN — this breaks an
  existing test if done naively). `run_heartbeat`'s `max_lifetime` must become
  `max_lifetime: int | None = None`, resolved internally to one of the two
  constants **only when the caller left it unset** — the same sentinel pattern
  `interval` already uses. Without it, the literal "select the bound at startup"
  rule silently overrides a caller's explicit value whenever `supervisor_pid` is
  `None`, which is every existing test: `test_renews_when_self_owned_payload_pid_is_dead`
  passes `max_lifetime=2` with a 3-value injected clock, and a deadline silently
  bumped to 5400 lets the loop survive to a 4th `next(ticks)` and raise
  `StopIteration` — breaking a test this plan's Test Impact requires to pass
  unchanged.
- The sentinel must be threaded through **both** ends: `main()`'s
  `parser.add_argument("--max-lifetime", ..., default=MAX_LIFETIME_SECONDS)` must
  also become `default=None`, or the CLI never passes `None`, the resolver never
  fires, and the sentinel is dead code.
- On that exit: log INFO `unsupervised_max_lifetime`, `return 0`, **do not
  release**.
- Test the invariant the round-1 plan asserted but never checked: with a non-None
  `supervisor_pid`, assert the selected bound is `MAX_LIFETIME_SECONDS`, not the
  90-minute one.
- **Deleted from this plan** (round-2 scope decision): `session:runintent:{N}`,
  `touch_run_intent`, `read_run_intent`, the five write call sites, and the
  `intent-builder` team member.

### 5. `/do-sdlc` release-on-exit wiring
- **Task ID**: build-skill-wiring
- **Depends On**: build-session-release
- **Assigned To**: release-builder
- **Agent Type**: builder
- **Parallel**: false
- Add "Step 5: Release the run lease" to `.claude/skills-global/do-sdlc/SKILL.md`, scoped to the exits the tool layer cannot see: the Step 3d.4 HALT and the Step 3e blocked / cap-reached exits. The merged exit is covered by the stage-marker leg in Task 3.
- Put the concrete invocation in `docs/sdlc/do-sdlc.md` per the skill-context convention; keep the global body generic.
- Do **not** touch `.claude/skills/sdlc/SKILL.md` — the single-stage router's run must survive across invocations.

### 6. Heartbeat observability
- **Task ID**: build-heartbeat-logging
- **Depends On**: build-heartbeat-watch, build-unsupervised-ceiling
- **Informed By**: spike-4 (confirmed: `logs/sdlc_lease_heartbeat.log` has been 0 bytes since 2026-08-04)
- **Assigned To**: hb-liveness-builder
- **Agent Type**: builder
- **Parallel**: false
- `logging.basicConfig(level=INFO)` unconditionally in `main()`; DEBUG under `--verbose`.
- One INFO startup line (issue, run_id, supervisor source + pid, intervals) and one INFO exit line naming the reason (`supervisor_dead` / `unsupervised_max_lifetime` / `lease_lost` / `foreign_owner` / `max_lifetime`).
- Rewrite the module docstring to describe only the new status quo (delete the "max-lifetime is the death backstop" claim).

### 7. Tests
- **Task ID**: build-tests
- **Depends On**: build-renew-only, build-heartbeat-watch, build-session-release, build-unsupervised-ceiling, build-heartbeat-logging
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
- Use `/do-investigation-issue` to file the "six heartbeats spawned at 12:25:09 on issues closed in March, under an off-pin `python@3.14`" observation as its own issue, citing #2714 comment 5277602180 and the live 4h-backstop observation posted to #2714 on 2026-08-13.
- Justify the split on it being a **different root cause** (a missing spawn-side terminal/closed-issue guard), NOT on this plan bounding such a heartbeat to ≤2h — it does not, per the No-Gos table.
- Do not attempt the spawn-side fix in this plan.

### 9. Guarantee validation
- **Task ID**: validate-guarantees
- **Depends On**: build-tests
- **Assigned To**: hb-validator
- **Agent Type**: validator
- **Parallel**: false
- Prove #2446/#2451 survives: a live supervisor renews indefinitely; a dead payload pid never stops renewal.
- Prove #2537 survives: peek-before-renew intact, foreign owner exits, never mints on a free key.
- Prove the shortened unsupervised ceiling is unreachable whenever a supervisor pid is present (the 4h bound is selected instead).
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
| Issue-lock tests pass | `scripts/pytest-clean.sh <issue-lock suite> -q` | exit code 0 |
| Stage-marker tests pass | `scripts/pytest-clean.sh tests/unit/test_sdlc_stage_marker.py -q` | exit code 0 |
| Subcommand registered | `grep -q "session-release" scripts/sdlc-tool` | exit code 0 |
| Subcommand reachable | `sdlc-tool session-release --help` | exit code 0 |
| Peek-first preserved (#2537) | `grep -q "peek=True" tools/sdlc_lease_heartbeat.py` | exit code 0 |
| Renew-only wired (BLOCKER fix) | `grep -q "renew_only=True" tools/sdlc_lease_heartbeat.py` | exit code 0 |
| Tool-layer release leg exists | `grep -q "release_run" tools/sdlc_stage_marker.py` | exit code 0 |
| Pytest spawn guard intact | `grep -q "PYTEST_CURRENT_TEST" tools/sdlc_session_ensure.py` | exit code 0 |
| Docs updated | `grep -q "session-release" docs/features/sdlc-issue-ownership-lock.md` | exit code 0 |
| Release wired into /do-sdlc exit | `grep -q "session-release" .claude/skills-global/do-sdlc/SKILL.md && grep -q "session-release" docs/sdlc/do-sdlc.md` | exit code 0 |

**Anti-criteria** — every row below must fail by *exit code*, so a violation
cannot be mistaken for a passing count. Round-1 expressed these as `grep -c ...`
"match count == 0", which is unusable three ways: an escaped `\|` inside an ERE
is a **literal pipe**, not alternation (measured: `grep -cE "getppid\|ppid"`
returns `0` against a file containing `os.getppid()` — the check could never
fail); `grep -c` over two files prints `path:count` per file rather than one
integer; and `grep -c` exits `1` on zero matches, aborting any `set -e` harness
on the *success* case.

| Anti-criterion | Command | Expected |
|---|---|---|
| Heartbeat still ignores orphaned_lock (#2620) | `! grep -q "\.orphaned_lock" tools/sdlc_lease_heartbeat.py` | exit code 0 |
| `xx=True` NOT added to the shared acquire | `! grep -q "xx=True" models/session_lifecycle.py` | exit code 0 |
| /sdlc router NOT wired (scoping) | `! grep -q "session-release" .claude/skills/sdlc/SKILL.md` | exit code 0 |
| Stale docstring claim removed | `! grep -q "max-lifetime is the death backstop" tools/sdlc_lease_heartbeat.py` | exit code 0 |

The `orphaned_lock` row is anchored to the leading dot on purpose. The bare
token `orphaned_lock` is over-broad in the opposite direction from round 1's
`\|` bug: it matches the module docstring and the peek-site comment that *state*
the invariant — the two places most worth keeping, since they are the only
record of why #2537 forbids consulting the flag. `orphaned_lock` is a field on
`IssueLockResult`, so consumption is necessarily an attribute access; `.`-anchoring
makes the check fail on use and pass on explanation.

The three anti-criteria needing ERE **alternation** are written below rather than
in the table above, because a literal `|` inside a markdown table cell must be
escaped as `\|` — and copying that escape into the shell is precisely how round 1
produced a check that could never fail. Run these verbatim; each must exit 0:

```bash
! grep -qE 'getppid|ppid' tools/sdlc_lease_heartbeat.py tools/sdlc_session_ensure.py
! grep -qE '\.delete\(|DEL ' tools/sdlc_session_release.py
! grep -rqE 'runintent|touch_run_intent' tools/ agent/ models/
```

## Critique Results

### Round 1 — NEEDS REVISION (revised 2026-08-13; all 5 findings addressed)

**Verdict:** NEEDS REVISION (1 blocker, 4 concerns) — FULL war room (force-FULL: plan edits `.claude/skills-global/`).

**Revision summary.** The blocker was confirmed and found to be slightly worse
than reported: `touch_issue_lock` mints via `SET NX` at `:1222` *before* any
ownership comparison, and its `raw is None` branch at `:1230` independently
returns `acquired=True`. Both are closed by a new `renew_only=True` mode with a
Lua compare-and-set write (new Task 0, which everything else now depends on). The
release path gained a tool-layer leg on the MERGE marker write so the happy path
no longer depends on an LLM following prose. The run-intent beacon was **dropped
entirely** — a new Redis key, two helpers, five call sites, a tunable, a test
class, and a race, all replaced by one constant — and Open Questions 2 and 3 are
closed as part of that decision. The Verification anti-criteria were rewritten to
fail by exit code, with the alternation patterns moved out of the markdown table
that caused the escaping defect. No-Gos now states the per-path bound honestly
and no longer claims ≤2h unconditionally.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness | Race 1's mitigation fails on its own primary trigger. The premise that `touch_issue_lock`'s renewal branch is "a plain SET" is wrong — `models/session_lifecycle.py:1222` is already `_R.set(key, value, nx=True, ex=ttl)`, and NX gives no protection when the key is absent, so the heartbeat re-mints the lease the supervisor just released. The stated escape ("next tick sees supervisor_dead or intent_stale") cannot fire on the L1 stand-down path: the supervisor process is alive, so L2 says alive and L3 is never consulted. The re-minted lease then renews to the 4h ceiling — the exact #2714 bug, on the plan's new happy path. | **Task 0 (`build-renew-only`)** + rewritten Race 1 + Solution §L0. Confirmed and extended: `:1230`'s `raw is None` branch is a second minting path the critique did not name. `renew_only=True` skips both and writes via Lua CAS, so minting is structurally impossible rather than argued-down. Success Criteria gained it as the first bullet; every other task now depends on Task 0. | Do NOT add `xx=True` to the `_R.set(...)` at `models/session_lifecycle.py:1222` — that statement is also `ensure_session`'s acquire path and #2537's SET-NX contest is load-bearing. Add keyword-only `renew_only: bool = False` to `touch_issue_lock`; when True and the pre-SET `_R.get(key)` is `None`, return `IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)` instead of minting. Heartbeat passes `renew_only=True` and treats `owner_run_id is None` as its existing lease-lost exit. Test: delete the lock key between peek and extend, assert the key stays absent. |
| CONCERN | Risk & Robustness | Two Verification anti-criterion greps can never fail. In an ERE, an escaped pipe is a *literal* pipe, so the spike-2 row matches only the literal string, not `os.getppid()` (measured: escaped form returns 0 on a file containing `os.getppid()`; unescaped alternation returns 1). Same defect in the raw-Redis-DEL row. Three rows also run `grep -c` over two files, which prints `path:count` per file, so "output > 1" is not evaluable, and `grep -c` exits 1 on zero matches, aborting any `set -e` harness. | **Verification rewritten.** Independently reproduced the escaped-pipe defect (`grep -cE "getppid\|ppid"` returns 0 against a file containing `os.getppid()`). Every anti-criterion is now `! grep -q...`, failing by exit code. Root cause named in the plan: markdown tables require `\|`, so alternation patterns were moved out of the table into a fenced block to be run verbatim. Multi-file rows split or `&&`-chained. | Use unescaped ERE alternation in both anti-criterion rows (keep the `\(` escape, drop only the pipe escape). Express every "match count == 0" row as `! grep -qE '<pattern>' <files>` so a violation fails by exit code. Split the multi-file rows one-per-file, or use `grep -ho '<pattern>' f1 f2 \| wc -l` for a single integer. |
| CONCERN | History & Consistency | L1 reverses a documented decision and reinstates a pattern already recorded as failed here. `tools/sdlc_session_ensure.py:150` states "Wiring stays in the tool layer -- NO `/do-sdlc` skill-body edit required"; the plan makes a skill-body prose step the primary release mechanism without refuting it. `docs/sdlc/do-plan-critique.md` records #1654's identical failure (a barrier that "lived only in prose aimed at an LLM"). Risk 4 concedes the exposure but mitigates only with a grep, which proves the text exists, not that it ran. | **L1 split into two legs (Task 3).** Accepted, with a stronger primary leg than proposed: release fires on the `MERGE`/`completed` marker write in `tools/sdlc_stage_marker.py`, an unavoidable tool-layer event on the happy path for *every* driver. The suggested `_sdlc_run_identity.py:204` site is also routed through `release_run` but noted as narrow cleanup, not the primary leg. Skill-body Step 5 is now scoped to HALT / blocked / cap only, and a test — not a grep — proves the mechanism. | Give L1 a tool-layer leg: `tools/_sdlc_run_identity.py:204` already calls `release_issue_lock` on its terminal-pipeline guard — route that site through the new `release_run()` so it also calls `clear_supervised_run_signal`, releasing on MERGE-stage completion with zero skill cooperation. Reserve skill-body Step 5 for exits the tool layer cannot see (3d.4 HALT, 3e blocked/cap). Add a test asserting `release_run` fires on the terminal transition. |
| CONCERN | History & Consistency | No-Gos asserts a bound the plan disproves, and defers Task 8 on it. It claims the fix "bounds any such heartbeat's life to ≤2 hours regardless of how it was spawned"; Risk 4 says the resolvable-supervisor path is "held until the 4h ceiling". The ≤2h figure holds only on the L3 unresolvable path, and a re-spawned heartbeat is more likely to resolve a live `CLAUDE_PID` than not. | **No-Gos rewritten** with a per-path bound table; the unqualified ≤2h claim is retracted in the plan text. Open Question 3 closed the other way than the critique's suggestion: `MAX_LIFETIME_SECONDS` stays 4h because lowering it uniformly reintroduces #2446 for a live long BUILD. The shortened bound is gated on "supervisor unresolvable" instead. Task 8's deferral now rests on different-root-cause, not on the bound. | Restate No-Gos as the honest split: ≤2h only with no resolvable supervisor; otherwise bounded by `MAX_LIFETIME_SECONDS`. To make ≤2h unconditional instead, close Open Question 3 by setting `MAX_LIFETIME_SECONDS` (`tools/sdlc_lease_heartbeat.py:79`) to `2 * 60 * 60` — but only alongside the Task 2 cadence split, since the ceiling then binds live long runs. Task 8's deferral must not rest on the unqualified claim. |
| CONCERN | Scope & Value | L3 is a third backstop for a path spike-1 says never occurs, and it is the largest cost in a Medium plan: a new Redis key, two helpers, five write call sites, a new tunable, a new test class, and a new Race 3 — to move a never-observed path from 4h to 2h, while `MAX_LIFETIME_SECONDS` is retained as a backstop for the same path and Open Question 3 asks whether lowering it would suffice. The plan builds the expensive option without deciding against the one-line one. | **Decided: L3 dropped.** The beacon, its Redis key, both helpers, all five write call sites, the tunable, `TestIntentStaleness`, Race 3, and the `intent-builder` team member are removed. Replaced by `UNSUPERVISED_MAX_LIFETIME_SECONDS` (Task 4), which buys the same ≤2h bound for one constant. The critique's "if keeping L3" branch is moot, but its underlying point — that the load-bearing invariant was asserted and never tested — is honored: `TestUnsupervisedCeiling` asserts a resolvable supervisor selects the 4h bound, not the 90-minute one. | Resolve Open Question 3 before build. If dropping L3: delete Task 4 and default `SDLC_LEASE_HEARTBEAT_MAX_LIFETIME_SECONDS` to `5400` at `tools/sdlc_lease_heartbeat.py:79`, keeping the GRAIN OF SALT comment. If keeping L3: add the missing check for its load-bearing invariant (Task 4 states the heartbeat consults intent only when `supervisor_pid is None`, but no Verification row tests it) — in `TestIntentStaleness`, patch `read_run_intent` with a `Mock` and assert `call_count == 0` when `run_heartbeat` is called with a non-None `supervisor_pid`. |

**Structural checks:** required sections PASS (Documentation / Update System / Agent Integration / Test Impact all present and substantive); task numbering PASS (1-11 contiguous); dependencies PASS (all `Depends On` IDs resolve, no cycles); file paths PASS (20 of 24 exist; the 4 absent are the modules this plan creates); prerequisites PASS (psutil, Redis, `sdlc-tool` all verified live); cross-references PASS except the two gaps captured above. Plan file:line claims re-verified against `f614124110`: `_maybe_launch_lease_heartbeat` at `:143`, `MAX_LIFETIME_SECONDS` at `:79`, the single call site at `:576`, and `release_issue_lock` at `:1326` all hold.

### Round 2 — READY TO BUILD (with concerns)

**Verdict:** READY TO BUILD (with concerns) — 0 blockers, 4 concerns. FULL war room
(force-FULL: the plan edits `.claude/skills-global/`), roster 3/3 complete and grounded.

**Round-1 disposition: all five findings verified closed.** The Task 0 `renew_only=True`
+ Lua CAS fix was independently confirmed against `models/session_lifecycle.py` — the
`SET NX` at `:1222` and the `raw is None` branch at `:1230` are both real minting paths
and both are structurally removed under `renew_only`. The tool-layer release leg is a
genuine mechanism rather than the round-1 prose barrier, and correctly cites the #1654
precedent it avoids. The run-intent beacon is deleted from every operative section
(grep-verified: zero `runintent` / `touch_run_intent` / `read_run_intent` /
`intent-builder` / `TestIntentStaleness` hits outside this history table). The
Verification anti-criteria were re-run live in both the fixed and the round-1 broken
form and behave exactly as the plan claims. The No-Gos per-path bound table is
internally consistent with Risk 1, Risk 4, and the Success Criteria, and no surviving
sentence outside this history table still asserts the retracted unconditional ≤2h claim.

The four concerns below are all implementation-precision gaps, not design defects. None
blocks the build; each carries an Implementation Note the revision pass embeds so the
builder does not have to re-investigate.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| CONCERN | Risk & Robustness | L3's "select the bound once at startup" has no sentinel distinguishing "caller explicitly passed `max_lifetime`" from "caller left it default". `supervisor_pid` defaults to `None`, and every existing test calls `run_heartbeat` with an explicit small `max_lifetime` and no `supervisor_pid` — so the literal L3 rule silently replaces the caller's value with the 5400s unsupervised bound. Traced concretely: `test_renews_when_self_owned_payload_pid_is_dead` feeds a 3-value injected clock sized for `max_lifetime=2`; with the deadline bumped to 5400 the loop survives and a 4th `next(ticks)` raises `StopIteration` — breaking a test the plan's own Test Impact requires to keep passing unchanged. | **Task 4.** `max_lifetime: int | None = None` sentinel added, resolved internally only when the caller left it unset, with the `main()` `default=None` half called out explicitly so the sentinel is not dead code. | Give `max_lifetime` the same explicit-vs-default sentinel `interval` already uses: `max_lifetime: int \| None = None` on `run_heartbeat`, resolved internally to `MAX_LIFETIME_SECONDS` or `UNSUPERVISED_MAX_LIFETIME_SECONDS` ONLY when the caller left it unset. This must touch BOTH ends — `main()`'s `parser.add_argument("--max-lifetime", ..., default=MAX_LIFETIME_SECONDS)` must also become `default=None`, or the CLI path never passes `None` either and the sentinel is dead code. |
| CONCERN | Risk & Robustness | L0's Lua CAS closes the two minting paths inside the function body, but `touch_issue_lock`'s outer `except Exception` handler wraps the ENTIRE body — including the new CAS `EVAL` — and returns `acquired=True, owner_run_id=run_id` on any error regardless of `renew_only`. That is the same "report ownership without holding the key" shape Race 1 is meant to structurally eliminate. A transient Redis blip on the extend call immediately after L1 released the lease tells the heartbeat it still owns the lease for one more tick, and it keeps calling `write_supervised_run_signal` for a lease it does not hold. Neither L0, Task 0, nor the Race 1 writeup mentions this path. | **Task 0.** Accepted as a real gap, not an accepted residual: the handler gains a `renew_only`-keyed branch that fails CLOSED for a renewer, with the fail-open default preserved for all other callers per #2446, plus a test asserting both halves. | `models/session_lifecycle.py:1310` `except Exception as e:` is the single catch-all for the whole function (acquire, peek, and renewal alike) and the `renew_only` flag is not threaded into it at all. Either add a `renew_only`-keyed branch inside that handler returning `IssueLockResult(acquired=False, owner_session_id=None, owner_run_id=None)` (fail CLOSED for a renewer, since a renewer minting is the thing being prevented), or state explicitly in the plan why the one-tick self-heal (next peek reads the still-absent key and takes the lease-lost exit) is an accepted residual. Do NOT change the fail-open default for the non-`renew_only` callers — #2446 depends on it. |
| CONCERN | Scope & Value | The Update System section still reads "the two new Redis keys are plain TTL'd strings that need no migration and self-populate on first use" — an uncorrected round-1 artifact from when L3 minted `session:runintent:{N}`. It directly contradicts Architectural Impact's correct "No new Redis key is introduced". This is the only surviving beacon residue in the document. | **Fixed.** The Update System bullet now states no new Redis key is introduced and names the two pre-existing keys as unaffected, matching Architectural Impact. | Edit the bullet under `## Update System` beginning "**No new dependencies, config files, or migrations.**" — delete the "two new Redis keys" clause outright, or replace it with a statement consistent with zero new keys (the pre-existing `session:issuelock` / `session:supervisedrun` keys are unaffected by the update). Cross-check against the correct wording under `## Architectural Impact`. |
| CONCERN | History & Consistency | The Documentation checklist omits `docs/features/sdlc-pipeline.md`, whose "Explicit release" bullet attributes lease release solely to `finalize_session` "on EVERY terminal transition" and frames the `orphaned_lock` self-heal as the crash backstop. Spike-3 found `finalize_session` never fires on a `/do-sdlc` supervisor exit (which is why L1 exists), and L2/L3 exist precisely to stop trusting `orphaned_lock`-manufactured freshness — so that doc will read as authoritative while describing the model this plan replaces. | **Added to the Documentation checklist** with the `:210-214` line reference and both halves of the correction (the `finalize_session` attribution and the `orphaned_lock`-as-crash-backstop claim). In scope for Task 10. | `grep -n "Explicit release" docs/features/sdlc-pipeline.md` locates the bullet. Add it to the Documentation → Feature Documentation checklist and treat it as in-scope for Task 10's `/do-docs` cascade: name the stage-marker MERGE leg and `session-release` alongside `finalize_session`, and soften the `orphaned_lock` self-heal claim per #2620 and this plan. |

**Structural checks (round 2):** required sections PASS (Documentation / Update System / Agent Integration / Test Impact all present and substantive); task numbering PASS (0-11 contiguous); dependencies PASS (all 12 `Depends On` IDs resolve, no cycles); file paths PASS (18 of 21 exist; the 3 absent — `tools/sdlc_session_release.py`, `tests/unit/test_sdlc_session_release.py`, `tools/sdlc_supervisor_identity.py` — are modules this plan creates); prerequisites PASS (psutil importable, Redis reachable via Popoto, `sdlc-tool` on PATH — all re-run live); cross-references PASS except the Update System contradiction captured above. Every round-2 file:line claim about `touch_issue_lock`, the heartbeat loop, `_maybe_launch_lease_heartbeat`, `_write_marker_impl`'s idempotent branch, `_sdlc_run_identity.py`'s terminal guard, and `scripts/sdlc-tool::ALLOWED_SUBCOMMANDS` was re-verified against the working checkout and holds.

---

## Open Questions

All three round-1 questions are **closed** with chosen defaults; none blocks
build. Recorded here with their reasoning so a reviewer can reopen one on the
merits rather than rediscovering it.

1. **Is the `/do-sdlc`-only release scoping right?** — **CLOSED: the asymmetry is
   gone, and the question is largely moot.** Round 1 made the skill body the only
   release mechanism, which forced the question. The round-2 tool-layer leg
   (release on the `MERGE`/`completed` marker write) is path-agnostic: it fires
   for `/do-sdlc`, for the `/sdlc` router, and for worker-driven pipelines alike,
   with no skill cooperation. What remains skill-scoped is only the HALT /
   blocked / cap-reached exits, which are `/do-sdlc` concepts that no other
   driver has. `/sdlc`'s SKILL.md is still deliberately not wired (its run must
   survive across invocations) and that stays an explicit anti-criterion.
2. **Is 90 minutes the right intent-staleness default?** — **CLOSED as obsolete.**
   The intent beacon is dropped. The 90-minute figure survives as
   `UNSUPERVISED_MAX_LIFETIME_SECONDS`, env-overridable and marked
   provisional/tunable, binding only the unresolvable path.
3. **Should the 4h `MAX_LIFETIME_SECONDS` be lowered?** — **CLOSED: no, not
   uniformly.** Lowering it globally would make a live supervisor's >2h BUILD
   stage lapse its own lease, reintroducing #2446 to buy tidiness. The shortened
   bound is gated on "supervisor unresolvable", which puts it exactly where the
   risk is and leaves the resolvable path's behavior unchanged. This is what
   allowed L3 to collapse from a Redis key plus five call sites into one
   constant.
