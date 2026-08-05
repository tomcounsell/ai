---
status: Ready
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-04
tracking: https://github.com/tomcounsell/ai/issues/2518
last_comment_id:
revision_applied: true
revision_applied_at: 2026-08-04T15:05:23Z
---

# Durability M1 Fence: Canary Findings, Hotfixes, and Permanent Regression Tests

## Problem

PR #2516 (merged 2026-08-04 17:15 +0700) replaced `AgentSession`'s pid trio with a fenced execution record `(exec_pid, pid_create_time)` and shipped `agent/pid_fence.py::fence_is_live` — a `create_time` compare that answers "is this pid still *our* process?". Issue #2518 planned a 6-job canary to validate the change on one machine before rolling `/update` to the fleet.

**The canary has already run.** `/update` applied `strip_pid_fields` on this machine (Tom's MacBook Air) at 11:48 UTC, roughly 90 minutes after the merge. So the premise of #2518 shifts: the work is no longer "run the canary and see", it is **fix what the canary found, re-verify, then roll**.

The substance of what it found is the fence, not the migration:

**The fence was built but not finished.** PR #2516 introduced `fence_is_live` and applied it correctly at nine sites. At six other sites it rebound the pid source to `fence.get("pid")` and discarded `fence.get("create_time")` sitting in the same dict. **Six unfenced consumers, two of them HIGH** — matching spike-3 exactly. An earlier draft of this section said "four HIGH-severity defects" and described both HIGH sites as under-killing. Both halves of that were wrong; the corrected account follows. The two HIGH sites fail in **opposite directions**, and that asymmetry decides which one needs a staged rollout:

| HIGH site | Direction | What a recycled `exec_pid` does today | Effect of fencing it |
|-----------|-----------|----------------------------------------|----------------------|
| `_tier2_reprieve_signal` (`agent/session_health.py:1832-1854`) | **Under-kills** | An unrelated process probes as `"progressing"` → `return gate` grants a reprieve every tick. `:1854`'s `return "alive" if pid is not None` is permanently true since #2494 stopped clearing the fence, so it no longer discriminates anything. A dead session is held alive indefinitely. | Nulling the pid yields `"unknown"`, which routes to the count-based escalation guard (`reprieve_count >= MAX_NO_OUTPUT_REPRIEVES` → `None`) and makes `:1854` return `None`. **Strictly kill-increasing** — this is the one site that gets the Phase A shadow. |
| `_has_progress` → `subprocess_hang_verdict` (`agent/session_health.py:1719-1734`) | **Over-kills** | An unrelated process that probes as `"hung"` bypasses the sticky-field honor at `if _verdict != "hung":`, falls through to the child check, and prematurely releases a session with real progress to Tier-2 recovery. | Nulling the pid yields `"unknown"`, which `if _verdict != "hung":` treats **identically to `"progressing"`** — sticky fields honored, `True` returned. **Strictly kill-reducing**, so it needs no Phase A gating and can never produce a shadow-log hit. |

The second row is the correction that matters for build: fencing `_has_progress` **cannot** close a "false progressing blocks recovery indefinitely" gap, because `"progressing"` and `"unknown"` are the same outcome at that branch. The in-code comment shipped with #2516 (`:1710-1716`) already states this. **Do not extend Phase A gating to `_has_progress`.**

**A secondary, low-severity observation about the migration.** A dry run on this machine reports 23 records still carrying stale `claude_pid`/`pm_pid`/`harness_pid`/`expectations` hash fields (20 terminal, 3 deferred ledger rows), despite `strip_pid_fields` being recorded complete. Per the migration's own docstring (`scripts/migrate_strip_pid_fields.py:10-12`) these are *"orphaned data, not a crash hazard"* — Popoto ignores unknown hash fields on load. This is storage noise, not a failed cutover, and it is explicitly **not** the headline. See "Migration: what is and is not established" below for the evidence and the open root cause.

**Current behavior:**
- A recycled `exec_pid` can (a) hold a dead session alive through unbounded reprieves, (b) prematurely release a *progressing* session to Tier-2 recovery when the unrelated occupant probes as hung, (c) get an unrelated process SIGKILLed a tick later, (d) shadow a live session so its harness is SIGTERM'd, or (e) protect a genuine orphan from reaping — all silently. Note (a) and (b) point opposite ways; see the HIGH-site table above.
- `/update`'s own stale-session cleanup finalizes `running` sessions with the reason `"stale cleanup (no live process)"` while never consulting the fence that now authoritatively answers that question. **Blast radius is narrower than an earlier draft implied** (critique NIT): `_cleanup_stale_sessions` already skips any session whose `updated_at` falls inside `RECENT_ACTIVITY_WINDOW` (30 min, `scripts/update/run.py:183-185`), and `HEARTBEAT_WRITE_INTERVAL` is 60s (`agent/session_health.py:451`) on an independent asyncio loop (`agent/session_executor.py:2080`), so a genuinely live session stays well inside the window and is already protected by recency. `finalize_session` also marks the row terminal rather than signalling the process; any kill arrives indirectly, once the terminal row stops resolving through `find_live_session_by_pid`'s `NON_TERMINAL_STATUSES` scan and the orphan reaper claims the pid. **This is a correct refinement of a truthful reason string, not a rescue from an active fleet-rollout hazard**, and it is not what gates the rollout.
- The highest-risk change in the PR — the orphan-reaper forward scan — is covered exclusively by tests that mock the scan away.

**Desired outcome:**
- The fence is consulted everywhere a fenced pid drives a kill, a reprieve, or an ownership claim. Where `create_time` is unreadable, the code says so and falls back deliberately rather than assuming valid.
- The migration harness stops discarding its own evidence, so the next cutover can answer "did it strip anything?" from the logs instead of by forensics.
- The three canary jobs worth keeping become permanent tests that exercise the *real* code paths, not mocks of them.
- The canary machine is re-verified under real worker traffic, and only then does `/update` roll to the MacBook Pro.

## Migration: what is and is not established

An earlier draft of this plan led with "the migration self-certified — it recorded itself complete having stripped nothing." **That claim was wrong and is retracted.** The corrected account:

**Established (verified directly on this machine):**

| # | Finding | Evidence |
|---|---------|----------|
| 1 | The wrapper genuinely runs the migration in apply mode. | `scripts/update/migrations.py:238` passes `--apply`. There is no mechanism by which it records completion without executing the work. |
| 2 | Post-cutover code cannot write the stale fields. | Current `models/agent_session.py` defines none of `claude_pid`/`pm_pid`/`harness_pid`/`expectations` (grep returns zero). |
| 3 | Every stale record was written *after* the migration completed. | All **23 of 23** stale records have `updated_at` later than both candidate completion times (11:48:59Z and 10:17:00Z). 0 predate either. |
| 4 | Those timestamps are genuine session writes, not migration artifacts. | The script calls base `popoto.Model.save` specifically to preserve `updated_at` as loaded (`:143-144`, docstring `:30-32`). |
| 5 | Terminal rows *are* being rewritten, contradicting the migration's stated safety premise. | The docstring asserts *"the worker never writes terminal rows"* (`:24`). Observed otherwise: every record's `updated_at` moved as a ~60ms batch from 12:59:17Z to 14:23:19Z between two reads minutes apart, `logs/update.log` mtime matching 14:23 exactly. The periodic `/update` job rewrites terminal rows. |
| 6 | Five worktrees hold pre-cutover checkouts sharing `localhost:6379`. | `.worktrees/{sdlc-2138,sdlc-2140,sdlc-2144,sdlc-2146,simplify-merge-gate}` each define `claude_pid` (7-8 refs). **No such process was running at inspection time**, so this is a demonstrated *capability*, not an observed *cause*. |

**Not established — the root cause is genuinely open.** The `updated_at` classification (finding 3) is necessary but **not sufficient** to prove "stripped, then re-contaminated". A normal popoto save writes the model's fields via `HSET` and does **not** delete orphaned hash fields — that is precisely why this migration needs delete+recreate rather than a plain save (`:14-17`). So both competing hypotheses predict exactly the observation:

- **H-A (re-contamination):** the migration stripped 20 terminal rows; a pre-cutover writer later re-added the fields. → `updated_at` after, fields present. ✓
- **H-B (blinded scan):** the migration observed zero rows (the `query.all()` / `rebuild_indexes` class-set window documented at `agent/index_drift.py:1-12`, #1720) and recorded complete; post-cutover writers have since bumped `updated_at` while leaving the orphaned fields untouched. → `updated_at` after, fields present. ✓

Nothing currently observable separates them, **because `run_pending_migrations` discards the migration's stdout on success** (`scripts/update/migrations.py:239-247` returns `None` and never logs `result.stdout`). The script logs a per-record `STRIP <id>` line and a final stats dict; all of it is thrown away unless the process exits non-zero. That is the one unambiguous, actionable defect in this area, and it is why a question this simple required forensics.

**Consequences for the plan:** Task 1 is scoped to *capture the evidence*, not to fix an unproven root cause. The zero-record guard is retained as cheap insurance against H-B, explicitly labelled as insurance rather than a fix, and it is not justified by the retracted claim. Severity is LOW — orphaned data, ignored on load.

## Freshness Check

**Baseline commit:** `3a5f1b5085aaa0532963bdea7d7982d52b7689a9`
**Issue filed at:** 2026-08-04T03:37:50Z
**Disposition:** Minor drift (with a large defect payload — see Recon)

**File:line references re-verified:**
- `agent/pid_fence.py` — 81 lines, `fence_is_live` at `:46`, `CREATE_TIME_TOLERANCE_S = 1e-3` at `:27` — holds.
- `agent/session_runner/runner.py:669` — `stamp_execution_spawn` call site — holds (`_on_turn_spawn`, `:633-677`).
- `scripts/migrate_strip_pid_fields.py` — 188 lines — holds.
- `ui/data/sdlc.py:356` (`exec_pid` field), `:1051` (`_check_process_alive`) — holds.
- `tests/unit/session_runner/test_pid_fence.py` — **gone / never existed.** Actual path is `tests/unit/test_pid_fence.py`. Corrected throughout this plan.
- The issue's "forward scan over the status index" is `AgentSession.find_live_session_by_pid` (`models/agent_session.py:1219-1268`), not a `session_health.py` function as the phrasing implies.

**Cited sibling issues/PRs re-checked:**
- **PR #2516** — MERGED 2026-08-04T10:15:45Z as `df6097fe`. The issue was filed ~6.5h *before* the merge, anticipating it; the premise now holds.
- **#2494** (durability milestone) — open; this plan fills its Task 9 "Validate Milestone 1", which is a bare gate stub with no defined content in `docs/plans/durability-room-job-agentrun.md:601-607`. No conflict.
- **#2098** (closed) — "session-liveness-check false-kills live sessions" — direct prior art for D5 and the over-reap class. Its lesson (a liveness signal that is not authoritative in the process reading it) is the same shape as the unfenced consumers here.

**Commits on main since issue was filed (touching referenced files):**
- `df6097fe` Durability M1 (#2516) — **this is the subject**, not drift.
- `a419b277` hook interpreter resolution (#2503) — touched `scripts/update/migrations.py` (+185 lines) but only to add hook-registration migrations; `strip_pid_fields` registration at `:983` is untouched. Irrelevant.
- `3a5f1b50` `$HOME`-relative path hotfix — docs/skills only. Irrelevant.

**Active plans in `docs/plans/` overlapping this area:** `durability-room-job-agentrun.md` (the parent). Overlap is intentional and complementary — that plan's Task 9 is the empty gate this one fills. Milestones 2-3 (Room/Job models) are untouched here.

**Notes:** This machine already ran `/update` four times post-merge (17:17, 17:47, 19:50, 20:21 per `logs/update.log`), so `strip_pid_fields` is permanently skipped here. That is what makes a *renamed* re-run migration the right mechanism rather than an operator instruction to hand-edit `data/migrations_completed.json`.

## Prior Art

- **PR #2516** — *Durability M1: fenced execution record + pid-field deletion.* Merged. Introduced everything this plan validates and repairs. Both re-reviews returned APPROVE; the gaps here are of the "applied the new primitive at some call sites but not all" kind, which review is structurally bad at catching.
- **#2098 (closed)** — *Make session-liveness-check single-owner: out-of-process reflection false-kills live sessions.* Root cause of the #2091 double-owner incident: `_agent_session_health_check` ran in two processes, and the decisive liveness signal (`_active_workers`) was process-local, so the reflection subprocess always concluded "dead". **Directly relevant:** D5 is the same pattern — `/update` runs as a standalone subprocess where `_active_workers` is empty by design (`scripts/update/run.py:199-201` admits this), leaving `updated_at` recency as the only signal while the authoritative fence goes unread.
- **#1720 / `agent/index_drift.py`** — documents the exact failure mode behind D1: *"`AgentSession.query.all()` returned 0 with no exception, while 11 AgentSession hashes still existed"* during a `rebuild_indexes()` class-set window. `index_drift.py:28-35` explicitly refuses to call repair for this reason — while a migration in the same PR calls the unguarded version.
- **#2101 / #2207** — the phantom-re-inflation shim that `repair_indexes()` installs and raw `rebuild_indexes()` bypasses (D6).
- **#1218** — the in-process orphan reap whose fence gate at `session_health.py:4325` has no legacy fallback (D8/M2).
- **#2149** — the fast-reap ownership gate whose "no over-reap" tests all mock `find_live_session_by_pid`.

## Research

**Queries used:**
- psutil Process create_time precision macOS stability pid reuse fence

**Key findings:**
- psutil builds process identity as `(pid, create_time)` for exactly this reason and documents `create_time()` precision as **0.01s on Linux**; macOS uses a monotonic boot-relative start time with clock-change adjustment, so the fence is sound there. psutil *disables* the reuse check on FreeBSD/OpenBSD/SunOS/AIX because ctime moves with the system clock. Source: [psutil #2396](https://github.com/giampaolo/psutil/issues/2396), [psutil docs](https://psutil.readthedocs.io/). **Informs the plan:** `CREATE_TIME_TOLERANCE_S = 1e-3` is *tighter* than psutil's own documented precision. A too-tight tolerance produces false negatives ("not ours" for our own process), which fail safe (skip the kill) but silently degrade ownership claims. Task 7 pins the constant with an explicit test and a comment recording that it is deliberately tighter than the documented Linux precision because this system is darwin-only.
- The canonical guidance is: *treat an unfetchable `create_time` on **either** side as "unknown → fall back", never as "assume valid".* **Informs the plan:** this is precisely D8's asymmetry — `session_health.py:2930` skips the fence check when `_snap_ct is None` and passes the raw pid to a real SIGTERM→SIGKILL. The fix direction is prescribed by upstream practice, not invented here.
- `psutil.Popen` overrides `send_signal()`/`terminate()`/`kill()` specifically to avoid signalling a recycled pid (BPO-6973). **Informs the plan:** confirms the "guard every signal site, not just some" principle behind Tasks 2-4.

## Spike Results

The five parallel recon investigations (see the issue's Recon Summary) served as code-read spikes. Recorded here because their findings are load-bearing for the task list.

### spike-1: Is the fence stamped by the live worker, or only in tests?
- **Assumption**: "A unit-green test does not prove the production `claude -p` path fires."
- **Method**: code-read
- **Finding**: It fires. Full production chain confirmed: `agent/session_runner/harness/claude.py` (`on_sdk_started(proc.pid)` immediately after `asyncio.create_subprocess_exec`) → `role_driver.py:407` (TURN_SPAWNED dispatch) → `runner.py:521` (`on_spawn=` wiring) → `runner.py:669` (`stamp_execution_spawn`). Re-stamped per turn; never nulled. **But** `stamp_execution_spawn` wraps its save in a bare `except Exception` logging at DEBUG (`models/agent_session.py:1216-1217`) — a stamping failure in production is invisible.
- **Confidence**: high
- **Impact on plan**: Job 1 becomes EXTEND, not CREATE. Also adds the fail-silent save as a thing worth asserting (Task 5).

### spike-2: Where is the "forward scan" and how is it covered?
- **Assumption**: "The rebuilt reverse-lookup is the highest-risk change."
- **Method**: code-read
- **Finding**: It is `AgentSession.find_live_session_by_pid` (`models/agent_session.py:1219-1268`), iterating `NON_TERMINAL_STATUSES` and matching **pid alone**. Every reaper test mocks it (40+ `patch.object` sites). The one test of the scan itself (`test_pid_fence.py:160`) fakes `AgentSession.query` with `SimpleNamespace`. No test writes a real row to Redis and resolves through the real status index.
- **Confidence**: high
- **Impact on plan**: confirms "highest-risk"; drives Task 4 (fence the scan) and Task 8 (integration test with real rows, scan unmocked).

### spike-3: Are there unfenced consumers of the fenced pid?
- **Assumption**: "PR #2516 applied the fence everywhere it matters."
- **Method**: code-read
- **Finding**: **Refuted.** Six unfenced consumers; two HIGH: `_tier2_reprieve_signal` (`:1832-1854`), where a recycled pid returns `"progressing"` and blocks the no-progress kill every tick indefinitely, and `_has_progress` (`:1719-1734`), which fails the **opposite** way. This spike's first write-up described both as under-killing; critique traced `if _verdict != "hung":` and refuted that half. At `_has_progress`, `"progressing"` and `"unknown"` are the same outcome, so the live defect is a recycled process probing as `"hung"` and prematurely releasing a progressing session to Tier-2 recovery. See the HIGH-site table in Problem. Nine correctly-fenced reference sites exist to copy from.
- **Confidence**: high
- **Impact on plan**: this is the bulk of Task 2-3 and the reason appetite is Large. The direction split is why only `_tier2_reprieve_signal` gets the Phase A shadow.

### spike-4: Is `create_time_fn` the right test seam?
- **Assumption**: "Job 3's permanent test should drive pid-recycle through `create_time_fn`." (the issue's own open question)
- **Method**: code-read
- **Finding**: **Refuted.** No production call site threads `create_time_fn` through; `fence_is_live` is called positionally at every consumer. A `create_time_fn` test can only re-test `fence_is_live` in isolation, which `test_pid_fence.py:49-59` already does. The seam that *reaches* production is `patch("agent.pid_fence.proc_create_time")`, late-bound at `pid_fence.py:72` and already proven through `session_health`'s lazy import at `test_pid_fence.py:81,97`.
- **Confidence**: high
- **Impact on plan**: **answers the issue's open question** — use a seam, but the right one. Explicitly do NOT widen production signatures with a `create_time_fn` param for testability.

### spike-5: Did the migration work on this machine?
- **Assumption**: "`strip_pid_fields` in `migrations_completed.json` means the machine is clean."
- **Method**: prototype (read-only dry run against live Redis) + timestamp classification
- **Finding**: The machine is **not** clean — 23 records still carry stale fields (20 terminal, 3 deferred ledger). But the *reason* is undetermined; see "Migration: what is and is not established". A first-pass conclusion of "never stripped" was **wrong and retracted**: the wrapper does pass `--apply`, post-cutover code cannot write these fields, and all 23 stale records postdate the migration. The `last_authored_at` argument used to support it does not discriminate — that field is equally consistent with strip-then-rewrite. Equally, the timestamp evidence does not prove re-contamination, because a plain popoto save leaves orphaned hash fields in place. The blocking gap is that migration stdout is discarded on success.
- **Confidence**: high on the observations, **low on the root cause** (deliberately unresolved)
- **Impact on plan**: Task 1 becomes "capture the evidence", not "fix the root cause". Severity drops to LOW (orphaned data, ignored on load). Does **not** gate fleet rollout.

## Data Flow

Fenced-pid decision flow, showing where the compare is present (✅) and absent (❌) today:

1. **Spawn** — `agent/session_runner/harness/claude.py` fires `on_sdk_started(proc.pid)` after `create_subprocess_exec` → `role_driver.py:407` → `runner.py:669` `stamp_execution_spawn(pid, create_time=proc_create_time(pid), cwd, harness, generation)`.
2. **Persist** — `models/agent_session.py:1199-1215`: append to `spawn_history`, denormalize `exec_pid`/`pid_create_time`/`exec_cwd`/`exec_harness`, partial `save(update_fields=[...])`. Fail-silent.
3. **Read** — `AgentSession.live_fence` (`:1151-1171`) returns newest `spawn_history` entry, else reconstructs from scalars.
4. **Consume** — the fence dict fans out to nine decision sites:
   - ✅ `_terminate_detached_harness` `:111-118` — SIGTERM gate
   - ✅ `_sweep_dead_worker_sessions` `:1244-1247` — worker-boot sweep (with legacy fallback)
   - ✅ `_apply_recovery_transition` pre-cancel snapshot `:2925-2931`
   - ✅ in-process orphan reap `:4320-4325`
   - ✅ staged orphan SIGKILL drain `:5680-5705`
   - ✅ `_session_has_live_fence` `:774-795`
   - ❌ **`_has_progress` → `subprocess_hang_verdict`** `:1719-1725` → false "progressing"
   - ❌ **`_tier2_reprieve_signal`** `:1832-1854` → unbounded reprieve
   - ❌ **`_owned_task_hang_check`** `agent_session_queue.py:1991-1993`
5. **Ownership resolution** — ❌ `find_live_session_by_pid` (`agent_session.py:1219`) matches pid only → feeds `_reap_orphan_session_processes` `:5781-5805` and `_oneshot_owner_is_live` `:5570`.
6. **Deferred kill** — ❌ `_pending_sigkill` (`set[int]`, `:689`) staged at `:4374`, SIGKILLed unfenced at `:3945-3947` one 300s tick later.
7. **Operator surface** — ❌ `ui/data/sdlc.py:1051` bare `os.kill(pid, 0)` → `ui/app.py:760-766` → dashboard JSON. `PipelineProgress` has no `pid_create_time` field, so the compare is structurally impossible.
8. **Cutover** — ❌ `scripts/update/run.py:188` `_cleanup_stale_sessions` finalizes `running` → `killed` on recency alone, reason `"stale cleanup (no live process)"`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2516 | Introduced `fence_is_live`, applied it at 9 sites, deleted the pid trio | Migrated pid *readers* to `fence.get("pid")` but left the `create_time` compare unwritten at 6 sites. The value is in the same dict at every one of them. Review APPROVEd twice because each individual site looks like a faithful mechanical rebind; only an exhaustive consumer census reveals the omissions. |
| #2098 fix | Made session-liveness-check single-owner | Fixed the reflection-process instance of "liveness signal is not authoritative in the reading process". Did not generalize the lesson, so `/update`'s `_cleanup_stale_sessions` (D5) kept the same shape — it even documents that `_active_workers` is empty in its process and proceeds anyway. |
| `strip_pty_fields`, `schema_diet_fields` migrations | Same delete+recreate strip pattern | Both call unguarded `rebuild_indexes()` and neither is tested. #2516 copied the template faithfully, inheriting both traits. All three run under a harness that discards their stdout, so none of them has ever produced a durable record of what it did. |

**Root cause pattern:** a correct new primitive is introduced alongside its old counterpart, applied at the sites the author was looking at, and the remaining sites keep compiling and keep passing tests because the old shape is still *valid code* — just wrong. Nothing mechanically enumerates "every consumer of a fenced pid". Task 9 adds that enumeration as an anti-criterion so the next omission fails CI instead of review.

**A second pattern, from the migration episode:** when an operation's output is discarded, its failure mode becomes indistinguishable from its success mode, and the next person to ask "did this work?" has to reconstruct the answer from timestamps. Both a wrong first answer (mine) and a plausible-but-unproven second answer came out of that vacuum. Capturing migration stdout costs one line and removes the whole class.

## Architectural Impact

- **New dependencies**: none. Every fix uses `agent.pid_fence.fence_is_live`, already imported at nine sites.
- **Interface changes**: `find_live_session_by_pid(pid)` gains an optional `create_time: float | None = None`; passing it requires a fence match, omitting it preserves today's pid-only behavior for legacy callers. `_pending_sigkill` changes from `set[int]` to `set[tuple[int, float | None]]` (module-private). `PipelineProgress` gains a `pid_create_time` field. No public/CLI surface changes.
- **Coupling**: decreases. Today six sites reimplement "is this pid live" ad hoc; after this they route through one predicate.
- **Data ownership**: unchanged. The migration rename means `data/migrations_completed.json` gains one entry per machine.
- **Reversibility**: high. Every fence application is a guard that can only *reduce* the set of processes signalled — reverting is deleting a condition. The migration rename is additive and idempotent. The one irreversible act is the migration's delete+recreate on terminal rows, which is atomic and already shipped.

## Appetite

**Size:** Large

**Team:** Solo dev, PM, code reviewer

**Interactions:**
- PM check-ins: 2-3 (scope of the defect list; the canary-clean gate before fleet rollout)
- Review rounds: 2+ (HIGH-severity changes in the recovery path warrant a second pass)

The coding is not large — most fixes are one to five lines. The cost is in the census being exhaustive, the tests exercising real paths instead of mocks, and the rollout being gated on a human at the second machine.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis reachable | `.venv/bin/python -c "from models.agent_session import AgentSession; list(AgentSession.query.all())"` | Migration + integration tests need the live keyspace |
| psutil present | `.venv/bin/python -c "import psutil; psutil.Process().create_time()"` | The fence's entire mechanism |
| Canary machine is this one | `test "$(scutil --get ComputerName)" = "Tom’s MacBook Air"` | Task 12 re-verification runs here; fleet rollout does not |

## Solution

### Key Elements

- **Zero-record guard + re-run migration**: the strip migration refuses to report success on an empty scan, and re-registers under a new name so every machine re-runs it once.
- **Fence census + application**: every site that turns a fenced pid into a kill, a reprieve, or an ownership claim consults `fence_is_live`, with an explicit documented decision when `create_time` is unreadable.
- **Fenced ownership resolution**: `find_live_session_by_pid` accepts the observed `create_time` and requires a match, with a pid-only fallback only when nothing was recorded.
- **Cutover safety**: `/update`'s stale-session cleanup asks the fence before killing anything, so a fleet rollout cannot disturb a live session.
- **Real-path regression tests**: the three promoted canary jobs exercise the production code with the seam that actually reaches it, plus an integration test that resolves through the real status index rather than a mock.
- **Anti-criterion**: a CI-enforced census so a future unfenced consumer fails the build.

### Flow

**Canary machine (this one)** → fix D1 → re-run migration → **canary Redis clean** → fix D2-D9 → tests green → **canary re-verified** → human runs `/update` on the Pro → **fleet clean**

### Technical Approach

**Capture the migration's evidence (the actual defect).** Two facts, both established empirically by critique, define this fix:

1. **The output is on stderr, not stdout.** `scripts/migrate_strip_pid_fields.py:49` calls `logging.basicConfig(level=logging.INFO, format=...)` with no `stream=`, so Python's default `StreamHandler` writes to **stderr**. Measured on this machine: stdout is **0 bytes**; stderr carries every `WOULD strip` / `DEFER` line and the final `Stats:` dict. Logging `result.stdout` alone would faithfully write an empty string to `logs/update.log` — and Task 12's gate artifact, the thing the user reads before authorizing Phase B and the fleet rollout, would be blank.
2. **The wrapper is `_migrate_strip_pid_fields`, not `run_pending_migrations`.** `run_pending_migrations` calls `fn(project_dir)` under the `MIGRATIONS` contract `dict[str, tuple[callable, str]]` where the callable returns `str | None` (`scripts/update/migrations.py:942`). The subprocess and its captured streams live entirely inside each per-migration helper (`_migrate_strip_pid_fields`, `:220-249`). `run_pending_migrations` never sees stdout and structurally cannot log it.

So the fix is two-sided and **scoped to this one migration**: add `stream=sys.stdout` at `migrate_strip_pid_fields.py:49` (`sys` is already imported at `:44`), **and** log both `result.stdout` and `result.stderr` from `_migrate_strip_pid_fields`, so a future script that logs to stderr is still captured. The earlier draft's claim that this "generalizes to every migration" is withdrawn — generalizing it means widening the `MIGRATIONS` value contract to return `(error, output)` and updating every helper, which this appetite does not budget. That generalization is routed to `[SEPARATE-SLUG #2524]` alongside the analogous zero-record-guard generalization.

Verification must assert *captured non-empty output*, not the presence of a `result.stdout` token — a `grep -c 'result.stdout'` row goes green on exactly the broken state described above.

**Migration re-run mechanism.** `strip_pid_fields` is already recorded complete on this machine and will be on the Pro after its next `/update`. Rather than instruct operators to hand-edit `data/migrations_completed.json` (unauditable, easy to get wrong, and impossible on a machine nobody is sitting at), register the script under a **new migration name** — `strip_pid_fields_v2` — in `scripts/update/migrations.py`. `run_pending_migrations()` skips by name, so a new name re-runs everywhere exactly once, automatically, on the next `/update`. Selection semantics are unchanged; it is idempotent, so a genuinely clean machine gets a fast no-op.

**The v2 run does not discriminate H-A from H-B, and this plan no longer claims it does.** An earlier draft asserted that "records to strip" implies H-B and "zero" implies H-A. That is wrong for the same reason the Migration section already gives: *nothing currently observable separates them*. Running v2 now observes that same present state. A dry run on this machine reports `{'total_records': 24, 'clean': 1, 'stripped': 20, 'deferred_non_terminal': 3, 'errors': 0}` — which is precisely what **both** hypotheses predict. Compounding it, the recorded decision to leave the five pre-cutover worktrees in place pre-excuses any non-clean result, so no observation from this machine can falsify either branch. What output capture actually buys is **provenance for future migrations**: the next cutover answers "did it strip anything?" from `logs/update.log` instead of by forensics. H-A vs H-B is not recoverable from this machine's current data, and Success Criterion 2's "or explicitly left open" is the expected outcome, not the fallback.

**Zero-record guard — insurance, not a fix.** `migrate()` gains: if `total_records == 0`, exit non-zero with a distinct message. `run_pending_migrations` already refuses to record a migration whose subprocess exited non-zero, so the next `/update` retries. This is *fail-closed on ambiguity*: a genuinely empty database is indistinguishable from a blinded scan (#1720). **It is retained on its own merits — cheap, safe, and it closes H-B if H-B is real — and explicitly not justified by the retracted "self-certified" claim.**

**The retry is unbounded, and that is an accepted, documented condition.** An earlier draft said "retrying costs one subprocess", which understates it as a one-off. It is not: `run_pending_migrations` records completion only when the helper returns `None`, and `scripts/update/run.py:1073-1076` appends every failure to `result.errors` and logs `FAIL:` with `always=True`. On a machine whose `AgentSession` keyspace is *legitimately* empty — a fresh install — `strip_pid_fields_v2` therefore fails on **every** `/update`, forever, emitting recurring `FAIL:` noise. Deliberate choice: **accept and document rather than bound it.** A consecutive-observation counter would need its own persisted state beside `data/migrations_completed.json`, which is new durable state in service of a case neither existing machine is in (this one has 24 rows; the Pro has more). The acceptance is written into Task 1 and the No-Gos so an operator on a future fresh install reads the recurring `FAIL:` as expected output, not as a live regression.

**Guarded index repair.** Replace `AgentSession.rebuild_indexes()` with the repo's `repair_indexes()` wrapper (`models/agent_session.py:2210`), which clears `$IndexF` keys and installs the phantom-re-inflation shim. It is also arguably removable entirely — the delete+save pipeline maintains every index via `on_delete`/`on_save` — but keep the guarded call rather than deleting, since the migration's whole purpose is reclaiming rows whose index state is suspect.

**Fence application shape.** At each unfenced consumer, the change is the same three lines already used at `session_health.py:2925-2931`:
```
pid, ct = fence.get("pid"), fence.get("create_time")
if pid is not None and not fence_is_live(pid, ct):
    pid = None   # not ours — treat as absent
```
Applied at `_has_progress` (`:1719`), `_tier2_reprieve_signal` (`:1832`), `_owned_task_hang_check` (`agent_session_queue.py:1991`). At `:1854` the `return "alive" if pid is not None else None` predicate is additionally replaced with a fence check, because since #2494's deliberate non-clear, `pid is not None` is permanently true for any session that ever spawned and no longer discriminates anything.

**Mark the log-only fence reads so a mechanical census cannot mistake them for consumers.** Two sites read a fenced pid and drive nothing: `agent/session_health.py:3198` (`_fence_pid = (getattr(entry, "live_fence", None) or {}).get("pid")`, interpolated into a `finalize_session` reason string) and `:3216` (the same expression inline inside a `logger.warning`). The Data Flow census correctly omits both by eye, but they are textually indistinguishable from a real consumer, so Task 9's script would flag them and Task 9's red-state proof would "pass" against these legitimate sites instead of a real fixture. Annotate both with `# fence-census: log-only, not a decision consumer` and have the census script honor that exact marker. **This annotation must land before the census script is written**, or the red-state proof is meaningless.

**Reprieve fencing ships log-only first (user decision) — for `_tier2_reprieve_signal` only.** Withdrawing reprieves there makes the no-progress killer strictly more willing to act, and a wrongly-killed live session is a visible failure. `_has_progress` is the opposite case (see the HIGH-site table in Problem): fencing it is strictly kill-reducing, so it ships enforcing in Task 2 with no shadow branch, no `PHASE A` marker, and no expectation of shadow-log hits. So `_tier2_reprieve_signal`'s fence — and nothing else — lands in two steps:

- **Phase A (log-only).** The fence is evaluated and its verdict logged — `[fence-shadow] would withdraw reprieve for {session_id}: exec_pid={pid} recycled (recorded_ct={a}, live_ct={b})` — while the *return value is unchanged*, so behavior is identical to today. Observe on this machine across at least one full canary cycle and confirm the shadow log fires only on genuinely recycled pids.
- **Phase B (enforce).** Delete the shadow branch; the fence verdict drives the return value.

**Build this as a removable phase, not a config flag.** No `TimeoutSettings` field, no env var, no `if ENFORCE_FENCE:` branch — those rot in place and become permanent forks in the logic. Phase A is a literal `# PHASE A — DELETE IN PHASE B` block that Phase B removes in one commit, with a Verification row asserting the marker is gone by the end of the plan. The switch is `git`, and the evidence is the shadow log.

**Legacy-row policy — make it explicit and consistent.** Today `:1247` falls back to `_pid_is_alive` when `recorded_ct is None`, `:2930` skips the fence entirely (fail-open into a real SIGKILL), and `:4325` refuses to signal at all (fail-closed, never reaps a legacy row). Three behaviors for one condition. Adopt one rule, matching upstream psutil practice: **an unreadable `create_time` on either side means "unknown", and unknown never authorizes a kill** — but it may authorize the *gentler* action already in place. Concretely: `:2930` gains the missing guard (stop fail-open), `:4325` gains the same legacy fallback `:1247` has (stop the never-reaps gap), and the rule is written down in `agent/pid_fence.py`'s module docstring so the next consumer inherits it.

**Fenced ownership scan.** `find_live_session_by_pid(pid, create_time=None)`: when `create_time` is provided and a candidate row records one, require `fence_is_live`-equivalent agreement; when either side recorded nothing, fall back to pid-only match (today's behavior) so legacy rows still resolve. Restore the multi-match WARNING the old `find_by_claude_pid` had — silent non-deterministic resolution across a `frozenset` iteration order is the sharpest failure mode here. Also route the scan through `_filter_hydrated_sessions`, per that helper's own stated contract, and narrow the per-status `except` so one poisoned cohort cannot silently blind the whole pass without a WARNING.

**`_pending_sigkill` fencing.** Promote `set[int]` → `set[tuple[int, float | None]]`, mirroring `_pending_sigkill_orphans` one screen away, and re-verify at drain. The existing comment's probabilistic defence ("macOS recycles PIDs in ~5 minutes") is void when the tick interval is 300s.

**`/update` cutover safety.** `_cleanup_stale_sessions` consults the fence before finalizing: a session whose fence is live is skipped regardless of `updated_at` age, and the reason string stops claiming "no live process" when nothing verified that. Recency stays as the fallback for rows with no fence.

**UI.** Add `pid_create_time` to `PipelineProgress`, thread it through `_session_to_pipeline` (which already reads `_fence` and discards it), and have `_check_process_alive` take both. `tests/unit/test_dashboard_liveness_probe.py` encodes the pre-fence contract in its module docstring and must be rewritten, not patched around.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `models/agent_session.py:1216-1217` — `stamp_execution_spawn`'s bare `except Exception` logging at DEBUG. Add a test asserting an observable signal (raise `log.warning`, not DEBUG, and assert it) so a production stamping failure is not invisible. This is the fence's single point of entry; silent failure there disables every downstream guard.
- [ ] `models/agent_session.py:1247-1255` — per-status query `except` in the forward scan. Add a test asserting a WARNING is logged and, per the Task 4 decision, that a blinded cohort does not silently unprotect live sessions.
- [ ] `agent/pid_fence.py:42` and `:80` — the two deliberate blanket catches. Already covered by `test_pid_fence.py:57 test_never_raises_on_bad_input`; extend with the `create_time` type-error path.
- [ ] `scripts/migrate_strip_pid_fields.py` per-record `except` — assert a record-level error increments `errors` and yields a non-zero exit, so `run_pending_migrations` does not record completion.

### Empty/Invalid Input Handling
- [ ] `fence_is_live(None, ...)`, `(pid, None)`, `("not-a-float")` — covered at `test_pid_fence.py:41,46,57`. Add `(pid, float("nan"))`, which today returns `False` via the comparison but silently.
- [ ] `find_live_session_by_pid(None)` — covered (`test_pid_fence.py:160`). Add `create_time=None` against a row that *does* record one, and vice versa.
- [ ] **Zero-record migration scan** — the D1 regression. `total_records == 0` must exit non-zero.
- [ ] `_check_process_alive(None, None)` and `(pid, None)` — the UI's legacy-row path must render "unknown", not "alive".

### Error State Rendering
- [ ] Dashboard modal: a session with a recycled fence must render as not-live (or explicitly "unknown"), not as a green live chip. `ui/static/style.css:296` still documents the ghost badge as "dead per `os.kill(pid, 0)` probe" — update that copy with the behavior.
- [ ] `/update` run summary must report skipped-because-fence-live sessions distinctly from skipped-because-recent, so an operator rolling the fleet can see the gate working.

## Test Impact

- [ ] `tests/unit/test_worker_session_sweep.py:39-62` — UPDATE: the helper hardcodes `live_fence = {"pid": ..., "create_time": None}` with a comment saying it does so to reach the legacy branch. Parameterize so both branches are covered; all 13 existing tests currently exercise only the fallback.
- [ ] `tests/unit/test_dashboard_liveness_probe.py` — REPLACE: module docstring (lines 1-22) codifies the pre-fence `os.kill(pid, 0)` contract. Rewrite for the fenced two-argument probe, adding the recycled-pid case it has never had.
- [ ] `tests/integration/test_dashboard_liveness_endpoint.py:45,104` — UPDATE: fixture sets `exec_pid=os.getpid()` with **no** `pid_create_time` and asserts `process_alive is True`; it would pass against a fully broken fence. Add `pid_create_time` and a recycled-pid case.
- [ ] `tests/unit/test_ui_sdlc_data.py:407-427` — UPDATE: extend the `PipelineProgress` identity-surface assertions to include `pid_create_time`.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py:485,828,955` — DELETE or REPLACE: `test_stale_oneshot_terminal_owner_still_reaped`, `test_terminal_owner_stale_oneshot_reaped`, `test_terminal_owner_returns_false` assert behavior for a terminal owner, but post-#2516 the scan iterates `NON_TERMINAL_STATUSES` and can never return a terminal session. They pass on a fiction. Re-purpose as "owner-not-found" cases or delete.
- [ ] `tests/unit/test_session_health_orphan_reap.py:135-138` — UPDATE: stubs `agent.pid_fence.fence_is_live` to `lambda pid, ct: pid is not None`, so the real fence is never exercised in the in-process reap path. Use the real predicate with `proc_create_time` patched.
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` (40+ sites) — UPDATE: keep the mocked-scan unit tests for gate logic, but they must no longer be the *only* coverage; Task 8 adds the unmocked integration counterpart.
- [ ] `tests/unit/test_migrations.py::TestMigrationRegistration:265` — UPDATE: add `strip_pid_fields_v2`, and assert it is registered after `strip_pid_fields` and before `purge_phantom_agent_sessions`.
- [ ] `tests/unit/session_runner/test_runner_liveness.py:34-46` — UPDATE: the file-local `FakeSession` lacks fence fields and `stamp_execution_spawn`; add them (or import the richer one from `test_runner_turns.py`) so the `on_spawn` wiring test can live here beside its `on_stdout_event` sibling.
- [ ] `tests/unit/test_pid_fence.py` — EXTEND (no breakage): add the tolerance-pin and NaN cases.

## Rabbit Holes

- **Do not add `create_time_fn` parameters to production functions for testability.** Spike-4 established that `patch("agent.pid_fence.proc_create_time")` already reaches every consumer. Widening `_sweep_dead_worker_sessions`/`_terminate_detached_harness` signatures buys nothing and permanently couples production shape to test convenience.
- **Do not chase real pid recycling in a test.** It cannot be forced on demand, and `pyproject.toml:174` runs `-n auto --dist=loadfile`, so process-timing tests are parallel-hostile. `tests/README.md:35` already mandates mocking liveness probes because a real worker on the dev box masks fabricated processes. The interesting half of the fence is *only* reachable by faking `create_time`.
- **Do not implement a `pidfd` equivalent or a shim for macOS.** `agent/pid_fence.py:9-18` already documents that macOS has no `pidfd` and that `create_time` is the ceiling. The residual TOCTOU window is irreducible and accepted; re-litigating it is a research project.
- **Do not refactor `session_health.py`.** It is ~6000 lines and every fix here is a localized guard. A structural cleanup would swamp the diff and make the HIGH-severity fixes unreviewable.
- **Do not bound `spawn_history` in this plan.** It is unbounded by count by deliberate design (`models/agent_session.py:335-336`), bounded by session TTL. Worth an explicit test pinning current behavior so a future cap is a conscious choice, but capping it is a separate decision with its own forensic tradeoff.
- **Do not fix the sibling strip migrations here.** They are already recorded complete on every machine, so edits are inert; see No-Gos.

## Risks

### Risk 1: Fencing `_tier2_reprieve_signal` makes the killer more aggressive
**Scope correction (critique BLOCKER).** An earlier draft framed this risk as covering "`_has_progress` and `_tier2_reprieve_signal`", on the premise that both return `"progressing"` for a recycled pid and both get more aggressive when fenced. That is true of `_tier2_reprieve_signal` and **false of `_has_progress`**, where `"progressing"` and `"unknown"` are handled identically and fencing is strictly kill-*reducing*. This risk is scoped to `_tier2_reprieve_signal` alone. `_has_progress` carries the inverse risk, tracked as Risk 6.

**Impact:** `_tier2_reprieve_signal` currently returns a reprieve gate for a recycled pid probing as `"progressing"`, and `:1854` reprieves on `pid is not None` — permanently true since #2494. Fencing withdraws reprieves that were being granted. If any *legitimately* live session was relying on a fence-mismatch path to survive, it now gets killed.
**Mitigation:** Primarily the two-phase rollout the user directed — Phase A logs what it *would* withdraw without acting (Task 2), the shadow log is observed on this machine under real traffic (Task 12), and enforcement lands only after the user reviews it (Task 13). Structurally, the fence only reclassifies a pid as "not ours"; sessions whose fence genuinely matches are unaffected, and sessions with no recorded `create_time` take the legacy fallback unchanged. Task 6's tests assert both directions explicitly.

### Risk 6: Fencing `_has_progress` masks a genuine hang
**Impact:** The inverse of Risk 1, and the reason the two sites cannot share one rollout. Nulling a recycled pid at `_has_progress` moves the verdict to `"unknown"`, which honors the sticky fields (`turn_count`, `log_path`, `claude_session_uuid`) and returns `True`. A session whose *own* subprocess is genuinely hung, but whose fence is unreadable or mismatched, is held as "progressing" for longer than today.
**Mitigation:** Bounded and already-existing. `"unknown"` was always reachable at this site — a `None` or malformed fence pid coerces to `None` and produces exactly this verdict today (the in-code comment at `:1710-1716` states it), so fencing widens an existing path rather than opening a new one. The 1800s freshness deadline and the Tier-2 escalation guard still apply downstream. Because the change can only *withhold* a kill, it needs no Phase A shadow and ships enforcing in Task 2. Task 6's `_has_progress` regression test pins the intended behavior: with `agent.pid_fence.proc_create_time` patched so a recycled pid would probe as hung, `_has_progress` must still return `True` when the sticky fields show real progress.

### Risk 2: The renamed migration re-runs on a machine mid-session
**Impact:** `strip_pid_fields_v2` runs at `/update` Step 3.6, before the service restart, i.e. against a live worker — the same window that plausibly caused D1.
**Mitigation:** The migration is terminal-only, so no live row is rewritten; and the delete+recreate is one MULTI/EXEC with no non-existence window (verified). The zero-record guard now makes the dangerous case (blinded scan) fail loudly and retry rather than self-certify.

### Risk 3: The fenced ownership scan protects fewer orphans, or more
**Impact:** Requiring a `create_time` match in `find_live_session_by_pid` changes which processes the reaper considers owned. Too strict → live harnesses reaped. Too loose → orphans leak.
**Mitigation:** Fall back to pid-only whenever either side recorded no `create_time`, so the change is strictly a *refinement* of a pid match, never a broadening. Task 8's integration test asserts the live-session-not-reaped case against real Redis rows with the scan unmocked — the assertion that has never existed.

### Risk 4: The defect list is large enough that one PR becomes unreviewable
**Impact:** Nine defects across migration, recovery, update, and UI in one diff invites a rubber-stamp review — the same failure mode that let #2516 ship with six unfenced consumers.
**Mitigation:** The user has decided to keep the full scope in one PR, so the mitigation is review ergonomics rather than splitting. The fence-application tasks share one mechanical shape, stated once in Technical Approach, so review is "is the census complete" rather than nine separate judgements — and Task 9's anti-criterion answers that question mechanically instead of by eye. **That mitigation only holds if the anti-criterion is a per-site adjacency check, not a count.** Critique was right that the plan previously left it unspecified while the only concrete shape shown was `grep -c 'fence_is_live' … | output > 11`; a threshold count is satisfied by any fenced site, so a future PR that adds one unfenced consumer while removing one guarded site leaves the count unchanged and green — exactly the #2516 failure mode this table diagnoses. Task 9 now specifies `tools/check_fence_census.py` with per-site, function-scoped adjacency and a `file:line` failure report, and Risk 4's mitigation stands on that, not on a grep count. Task 1 (migration) is independently verifiable and touches no shared code with Tasks 2-5, so it can be reviewed as a separable unit within the same PR. The retracted-claim Verification row guards against the corrected diagnosis silently reverting during build.

### Risk 5: Canary and fleet diverge because only one machine is observed
**Impact:** The Air is worker-only (no bridge); the Pro runs the bridge and 18 projects. A defect that only manifests under bridge traffic would pass the canary.
**Mitigation:** State it plainly rather than pretend otherwise — the canary covers worker, migration, reaper, and recovery, and does **not** cover bridge-side session intake. Task 12 records this limit explicitly so the fleet rollout is done with eyes open, and the rollout stays a human-gated step.

## Race Conditions

### Race 1: Migration scan vs. live worker index rebuild (candidate hypothesis H-B, unproven)
**Location:** `scripts/migrate_strip_pid_fields.py:109` (`query.all()`) vs. `models/agent_session.py:2213-2221` (`repair_indexes` → popoto `rebuild_indexes` deleting `$Class:AgentSession`), invoked from the worker health check every tick.
**Trigger:** `/update` Step 3.6 runs migrations **before** the service restart (`scripts/update/run.py:1065-1067`), so the migration subprocess and the live worker are concurrent. If `query.all()` reads the class set mid-rebuild, it returns 0 rows with no exception — documented verbatim at `agent/index_drift.py:1-12` (#1720).
**Data prerequisite:** `$Class:AgentSession` fully populated before the scan reads it.
**State prerequisite:** no concurrent `rebuild_indexes()` in flight.
**Mitigation:** Cannot lock across processes cheaply here, so fail closed on the ambiguous observation: `total_records == 0` exits non-zero, the migration is not recorded, and the next `/update` retries. Convergence is guaranteed because the window is short and `/update` runs repeatedly. Additionally use guarded `repair_indexes()` rather than raw `rebuild_indexes()` so the migration is not itself a source of this window. **Status: this race is real and documented, but whether it actually fired here is unproven** — see "Migration: what is and is not established".

### Race 2: Terminal-row rewrites vs. the migration's stated safety premise
**Location:** `scripts/migrate_strip_pid_fields.py:22-29` asserts *"The worker never writes terminal rows, so this can never clobber a concurrent write."*
**Trigger:** Observation contradicts the premise — every record's `updated_at`, terminal ones included, moved as a ~60ms batch between two reads minutes apart (12:59:17Z → 14:23:19Z), coinciding with the periodic `/update` job. Something does write terminal rows.
**Data prerequisite:** the migration's terminal-only exemption assumes terminal rows are quiescent.
**State prerequisite:** no concurrent writer of a terminal row during the delete+recreate.
**Mitigation:** The delete+recreate is a single MULTI/EXEC, so a record can never be lost regardless — the atomicity argument holds independently of the quiescence premise. But the premise itself is false and must stop being cited as a safety property. Task 1 identifies the terminal-row writer and corrects the docstring; if that writer turns out to be a pre-cutover checkout, it also resolves H-A.

### Race 3: Fence stamp vs. orphan reaper (mid-spawn gap)
**Location:** `agent/session_runner/runner.py:665-677` (stamp happens *after* fork) vs. `agent/session_health.py:5748` reaper.
**Trigger:** Between `create_subprocess_exec` and the `save()`, the pid exists but resolves to no session. A reaper pass in that window sees an unowned `claude -p`.
**Data prerequisite:** the fence row must be persisted before the pid is reapable.
**State prerequisite:** the process must be parented by a live worker.
**Mitigation:** Already mitigated by the `ppid == 1` gate (a live worker is still the parent during the window), **except** on the `_parent_is_orphaned_shell_wrapper` branch (`:5514`). Task 8 adds a test for the mid-spawn state; if it proves reachable, the fix is an age floor on that branch rather than moving the stamp.

### Race 4: Staged SIGKILL across a tick that equals the recycle window
**Location:** `agent/session_health.py:4374` (stage) → `:3945-3947` (drain), 300s apart; `AGENT_SESSION_HEALTH_CHECK_INTERVAL` at `:442`.
**Trigger:** The staged pid exits and the OS recycles it within the tick; the drain SIGKILLs the new occupant.
**Data prerequisite:** the identity captured at stage time must still hold at drain time.
**State prerequisite:** none — this is purely a time window, and the comment at `:3941` cites ~5min macOS recycling against a 300s tick.
**Mitigation:** Task 3 promotes the set to `(pid, create_time)` tuples and re-verifies at drain, exactly as `_pending_sigkill_orphans` already does at `:5680-5705`.

### Race 5: Duplicate fence pid resolved by frozenset iteration order
**Location:** `models/agent_session.py:1219-1268`, iterating `NON_TERMINAL_STATUSES` (`models/session_lifecycle.py:72-84`, a `frozenset[str]`).
**Trigger:** A stale dormant row and a live running row both carry `exec_pid=P` (the fence is never cleared on dormant/paused/superseded either). Iteration order of a `frozenset[str]` varies per process under hash randomization, so which row wins is nondeterministic across restarts.
**Data prerequisite:** at most one non-terminal row should claim a given live pid.
**State prerequisite:** ownership must be decided by identity, not by scan order.
**Mitigation:** Task 4 requires a `create_time` match, which disambiguates the two rows deterministically, and restores the multi-match WARNING that `find_by_claude_pid` had and the rewrite dropped.

## No-Gos (Out of Scope)

- [EXTERNAL] **Running `/update` on the MacBook Pro.** The fleet rollout requires a human at the second physical machine; the agent cannot reach it. Task 13 prepares and documents the rollout, and the human performs it after the canary gate clears.
- [EXTERNAL] **Observing bridge-side behavior under real Telegram traffic.** This machine is worker-only by design (no bridge activated), so canary Job 5's SDLC-render check and any bridge-intake path can only be exercised on the Pro. Recorded as a stated coverage limit rather than a silent gap.
- [EXTERNAL] **Pruning or removing the five pre-cutover worktrees.** `.worktrees/{sdlc-2138, sdlc-2140, sdlc-2144, sdlc-2146, simplify-merge-gate}` each hold a checkout whose `models/agent_session.py` still defines `claude_pid` (7-8 refs), and all share `localhost:6379`. They are a demonstrated *capability* to re-add stale hash fields; no such process was running at inspection time, so they are not an observed cause. **User decision: leave them alone — no pruning, no removal.** Recorded here as a known, accepted condition: while these worktrees exist, a stale-field reappearance is an expected outcome and not evidence of a migration defect. Any future `strip_pid_fields` verification must be read with that in mind. Removing them is a workspace-hygiene call for a human, not this plan.
- [SEPARATE-SLUG #2524] **Applying the zero-record guard and guarded `repair_indexes()` to the sibling strip migrations** (`scripts/migrate_strip_pty_fields.py:161`, `scripts/migrate_schema_diet_fields.py:230`). Both share D1/D6 by template inheritance, but both are already recorded complete on every machine, so edits here would be inert code changes with no runtime effect — they need their own rename-and-rerun decision, which is a separate judgement about whether their stale fields are worth reclaiming at all.
- [SEPARATE-SLUG #2524] **Generalizing the zero-record guard into `run_pending_migrations()` itself** so every future migration inherits it, rather than each script implementing its own.
- [SEPARATE-SLUG #2524] **Generalizing migration output capture to every migration.** The `MIGRATIONS` contract is `dict[str, tuple[callable, str]]` with the callable returning `str | None`; the subprocess and its streams live inside each per-migration helper, so `run_pending_migrations` structurally cannot log them. Making capture universal means widening that value contract to `(error, output)` and updating every helper — materially more than the one-line change this plan budgets. Task 1 fixes `_migrate_strip_pid_fields` only.
- [ACCEPTED CONDITION] **Bounding the zero-record guard's retries.** On a machine with a legitimately empty `AgentSession` keyspace (a fresh install), `strip_pid_fields_v2` exits non-zero on every `/update` and is never recorded, so `logs/update.log` shows a recurring `FAIL:` line indefinitely. This is accepted, not fixed: bounding it needs new persisted state beside `data/migrations_completed.json` for a case no current machine is in. **Operator note:** on a fresh install, a recurring `strip_pid_fields_v2` failure with the zero-record message is expected output, not a live regression. Task 1 records the same note in the code comment.

## Update System

This work changes `/update` in two ways, both of which propagate automatically via `git pull`:

- **New migration registration.** `strip_pid_fields_v2` is added to the `MIGRATIONS` dict in `scripts/update/migrations.py`. `run_pending_migrations()` iterates that dict and skips by recorded name, so every machine runs the corrected migration exactly once on its next `/update`, with no operator action and no hand-editing of `data/migrations_completed.json`.
- **Fenced stale-session cleanup.** `_cleanup_stale_sessions` in `scripts/update/run.py` gains the fence check (D5). This changes `/update`'s own behavior during Step 5.5: sessions with a live fence are skipped regardless of age, and the run summary distinguishes fence-live skips from recency skips.

No new dependencies, config files, or secrets. No changes to `scripts/remote-update.sh` or the launchd plists. The migration ordering constraint (`strip_pid_fields_v2` before `purge_phantom_agent_sessions`) is asserted by a test so a future insertion cannot silently reorder it.

## Agent Integration

No agent integration required — every change is internal to the worker, the session-health sweeps, the update script, and the dashboard renderer. No new CLI entry point in `pyproject.toml [project.scripts]`, no new MCP tool, and no bridge import.

The one operator-facing surface that changes is the existing dashboard: the session modal's Liveness section and the row-level ghost badge become fence-accurate. That is served by the existing `ui/app.py` route, so no new wiring.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/dev-7f56f953.md` (the fenced-execution-record doc) with: the canonical legacy-row rule ("unreadable `create_time` on either side means unknown; unknown never authorizes a kill"), the full consumer census, and the `find_live_session_by_pid` signature change.
- [ ] Rename `docs/features/dev-7f56f953.md` → `docs/features/agent-session-fenced-execution-record.md`. It is currently named after the throwaway branch slug `session/dev-7f56f953`, which tells a future reader nothing. Update the `docs/features/README.md:13` link and any inbound references.
- [ ] Add a "Canary findings" section recording what the post-merge validation found, so the next milestone's cutover inherits the checklist rather than rediscovering it. Include the migration episode honestly: the observation, the retracted first diagnosis, why `updated_at` alone cannot discriminate, and the accepted worktree condition.
- [ ] Document the migration-observability fix (`run_pending_migrations` now logs stdout) wherever the update flow is described, since it changes what operators can expect to find in `logs/update.log`.
- [ ] Update `docs/features/README.md:13` row text to reflect fenced ownership resolution and the fenced update-time cleanup.

### Inline Documentation
- [ ] `agent/pid_fence.py` module docstring — write down the legacy-row rule so the next consumer inherits it rather than inventing a fourth behavior.
- [ ] `agent/session_health.py:3941` — replace the "macOS recycles PIDs in ~5 minutes" probabilistic comment with the actual fence guarantee once `_pending_sigkill` carries `create_time`.
- [ ] `scripts/migrate_strip_pid_fields.py:26-29` — correct the docstring's false claim that deferred rows age out via `Meta.ttl`; `is_ledger=True` rows are re-saved every ~30s and their TTL is refreshed indefinitely (D9).
- [ ] `ui/static/style.css:296` — update the ghost-badge comment, which still describes an `os.kill(pid, 0)` probe.

### Test Documentation
- [ ] Add the new test files to the `tests/README.md` index table with feature markers.

## Success Criteria

- [ ] `migrate_strip_pid_fields.py` logs to stdout (`stream=sys.stdout`), `_migrate_strip_pid_fields` captures **both** streams, and `logs/update.log` shows a **non-empty** record of what `strip_pid_fields_v2` did — asserted by a test on the captured text, not by a `grep` for `result.stdout`.
- [ ] `strip_pid_fields_v2` registered and run on the canary, with its output recorded in this plan. The H-A/H-B root cause is **expected to remain explicitly open**: no observation available on this machine discriminates the two, and the plan no longer claims the v2 run does.
- [ ] The migration docstring's "the worker never writes terminal rows" claim is corrected, and the actual terminal-row writer is named.
- [ ] Zero-record scan exits non-zero and is NOT recorded complete, labelled in-code as insurance rather than a fix, with the unbounded-retry acceptance noted in the same comment.
- [ ] Every consumer of a fenced pid that drives a kill, reprieve, or ownership claim calls `fence_is_live` — enforced by `tools/check_fence_census.py`'s per-site, function-scoped adjacency check, never by an occurrence count. The two log-only reads at `:3198`/`:3216` carry the `fence-census: log-only` marker and the script honors it.
- [ ] `_pending_sigkill` carries `create_time` and re-verifies at drain (D2).
- [ ] `_has_progress` and `_owned_task_hang_check` treat a recycled pid as absent (D3). `_has_progress` shipped **enforcing, unshadowed** — a regression test pins that a recycled pid probing as hung still yields `True` when the sticky fields show progress, so the corrected direction cannot silently revert.
- [ ] `_tier2_reprieve_signal` — and only it — shipped log-only (Phase A), was observed on this machine, and only then enforced (Phase B); `:1854` no longer reprieves on `pid is not None` alone. No `PHASE A` marker or fence config flag survives.
- [ ] `find_live_session_by_pid` accepts `create_time`, requires a match when both sides have one, logs multi-match, and routes through `_filter_hydrated_sessions` (D4).
- [ ] `/update`'s `_cleanup_stale_sessions` skips fence-live sessions and no longer claims "no live process" without checking (D5).
- [ ] Legacy-row policy is consistent across `:1247`, `:2930`, `:4325` and documented in `agent/pid_fence.py` (D8).
- [ ] `PipelineProgress` carries `pid_create_time`; the dashboard reports a recycled pid as not-live (D7).
- [ ] The three promoted regression tests exist and exercise real paths: runner-path stamping (Job 1), fence-branch sweep (Job 3), unmocked forward-scan no-over-reap (Job 4).
- [ ] The three terminal-owner tests asserting an impossible state are deleted or re-purposed.
- [ ] Canary machine re-verified under real worker traffic; results recorded in this plan. "Clean" is scoped to the Job 1-4 results and the shadow-log review — **not** to a zero-strippable-records dry run, which the retained pre-cutover worktrees make unfalsifiable.
- [ ] Tests pass (`/do-test` via `scripts/pytest-clean.sh`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (migration)**
  - Name: `migration-builder`
  - Role: D1/D6/D9 — zero-record guard, guarded index repair, re-run registration, docstring correction
  - Agent Type: builder
  - Resume: true

- **Builder (fence application)**
  - Name: `fence-builder`
  - Role: D2/D3/D4/D8 — apply the fence at every unguarded consumer; unify the legacy-row policy
  - Agent Type: builder
  - Domain: Redis/Popoto data + async/concurrency
  - Resume: true

- **Builder (cutover + UI)**
  - Name: `surface-builder`
  - Role: D5/D7 — `/update` stale cleanup fence; `PipelineProgress` threading and dashboard probe
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `fence-test-engineer`
  - Role: the three promoted regression tests, the defect regression tests, and the Test Impact dispositions
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `fence-validator`
  - Role: verify the consumer census is exhaustive and every Verification row passes
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `fence-documentarian`
  - Role: feature doc rename + rewrite, inline docs, tests README
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Migration: capture the evidence, then re-run
- **Task ID**: build-migration
- **Depends On**: none
- **Validates**: tests/unit/test_migrate_strip_pid_fields.py (create), tests/unit/test_migrations.py
- **Informed By**: spike-5 (root cause deliberately unresolved — see "Migration: what is and is not established")
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: true
- **Primary (a) — send the migration's own logs to stdout.** `scripts/migrate_strip_pid_fields.py:49` calls `logging.basicConfig(level=logging.INFO, format=...)` with no `stream=`, so every `WOULD strip` / `STRIP` / `DEFER` line and the final `Stats:` dict go to **stderr**; measured stdout is 0 bytes. Add `stream=sys.stdout` (`sys` is already imported at `:44`). Without this, (b) captures an empty string.
- **Primary (b) — stop discarding migration output, in the right function.** The capture site is `_migrate_strip_pid_fields` (`scripts/update/migrations.py:220-249`), **not** `run_pending_migrations` — the latter calls `fn(project_dir)` under a `str | None` contract and never sees the subprocess streams. Log **both** `result.stdout` and `result.stderr` there (at minimum the final stats dict), so `logs/update.log` records what the migration did and a future script that logs to stderr is still captured. **Scoped to this migration only** — do not claim or attempt generalization; that is routed to `[SEPARATE-SLUG #2524]`.
- **Prove the capture is non-empty, not merely present.** A `grep -c 'result.stdout'` check goes green on the exact broken state above. The Verification row runs the migration and asserts `logs/update.log` gains a line matching `Stats: {'total_records'`.
- **Identify the terminal-row writer.** The migration's docstring (`:22-29`) asserts "the worker never writes terminal rows"; observation contradicts it (Race 2). Find what rewrites terminal rows during `/update`, and correct the docstring. If it turns out to be a pre-cutover checkout, record that in this plan — but note it still does not settle H-A vs H-B (see below).
- Register `strip_pid_fields_v2` in `scripts/update/migrations.py` pointing at the same script, positioned after `strip_pid_fields` and before `purge_phantom_agent_sessions`. **Do not describe its run as an H-A/H-B discriminator** — both hypotheses predict the same output (verified: a dry run here reports `{'total_records': 24, 'clean': 1, 'stripped': 20, 'deferred_non_terminal': 3, 'errors': 0}`, which is what each predicts). Its value is provenance for future cutovers.
- Add a `total_records == 0` guard to `migrate()` — exit non-zero with a distinct message so completion is not recorded and the next `/update` retries. **Label it in the code comment as insurance against the #1720 class-set window, not as a fix for a proven cause**, and record in the same comment that on a legitimately empty keyspace this fails on every `/update` indefinitely by accepted design (see the No-Gos entry), so an operator does not read the recurring `FAIL:` as a regression.
- Replace `AgentSession.rebuild_indexes()` (`:158-164`) with the guarded `repair_indexes()` wrapper.
- Correct the `:26-29` docstring claim about `Meta.ttl` aging out deferred rows — false for `is_ledger=True` records.
- Confirm a record-level exception increments `errors` and produces a non-zero exit.
- **Do not** write the retracted "self-certified / never stripped" claim into any comment, docstring, or commit message.

### 2. Fence the deferred SIGKILL and the reprieve path
- **Task ID**: build-fence-kill-reprieve
- **Depends On**: none
- **Validates**: tests/unit/test_session_health_orphan_reap.py, tests/unit/test_session_health_reprieve_fence.py (create)
- **Informed By**: spike-3 (confirmed: 6 unfenced consumers, 2 HIGH)
- **Assigned To**: fence-builder
- **Agent Type**: builder
- **Domain**: async/concurrency
- **Parallel**: true
- Promote `_pending_sigkill` (`agent/session_health.py:689`) from `set[int]` to `set[tuple[int, float | None]]`; stage `(pid, create_time)` at `:4374`; re-verify with `fence_is_live` at the `:3945-3947` drain, mirroring `_pending_sigkill_orphans` at `:5680-5705`. Replace the probabilistic comment at `:3941`.
- Apply the fence-then-null pattern at `_has_progress` (`:1719-1725`) so a recycled pid yields no hang verdict. **Ships enforcing, not shadowed.** This site is strictly kill-*reducing* (see the HIGH-site table in Problem): nulling the pid moves the verdict from `"progressing"`/`"hung"` to `"unknown"`, and `if _verdict != "hung":` (`:1726`) treats `"unknown"` exactly like `"progressing"`. Do **not** wrap it in a `PHASE A` block, do not log a `[fence-shadow]` line here, and do not expect it in Task 12's shadow output. The defect it closes is premature Tier-2 release, not a blocked recovery.
- Apply the same pattern at `_owned_task_hang_check` (`agent/agent_session_queue.py:1991-1993`).
- **Annotate the two log-only fence reads so Task 9's census can exclude them:** `agent/session_health.py:3198` (`_fence_pid` in a `finalize_session` reason string) and `:3216` (the same expression inline in a `logger.warning`). Add `# fence-census: log-only, not a decision consumer` at both. Neither drives a kill, reprieve, or ownership claim, and neither gains a fence check. **This must land before Task 9's script is written**, or the red-state proof finds these legitimate sites instead of a real violating fixture.
- **`_tier2_reprieve_signal` (`:1832-1854`) ships Phase A only in this task** — evaluate the fence, log `[fence-shadow] would withdraw reprieve for {session_id}: exec_pid={pid} recycled (recorded_ct={a}, live_ct={b})`, and **return the unchanged value**. Behavior is identical to today. Mark the block `# PHASE A — DELETE IN PHASE B`. No config flag, no env var, no `if ENFORCE:` branch.
- The `:1854` `return "alive" if pid is not None else None` fix is part of the same Phase A shadow (log what it *would* return), since the bare predicate is permanently true since #2494 stopped clearing the fence.

### 3. Unify the legacy-row policy
- **Task ID**: build-legacy-policy
- **Depends On**: build-fence-kill-reprieve
- **Validates**: tests/unit/test_worker_session_sweep.py, tests/unit/test_session_health_orphan_reap.py
- **Assigned To**: fence-builder
- **Agent Type**: builder
- **Parallel**: false
- Adopt one rule for `recorded_create_time is None`: unknown never authorizes a kill, but may authorize the gentler action already present. Write it into `agent/pid_fence.py`'s module docstring.
- `session_health.py:2930` — add the missing guard so a `None` snapshot `create_time` no longer fails open into a real SIGTERM→SIGKILL.
- `session_health.py:4325` — add the legacy fallback its sibling at `:1244-1247` already has, closing the "legacy row never reaped" gap (M2).
- Leave `:1247` as-is; it is the reference behavior.

### 4. Fence the ownership forward scan
- **Task ID**: build-fenced-scan
- **Depends On**: none
- **Validates**: tests/unit/test_pid_fence.py, tests/integration/test_orphan_reap_forward_scan.py (create)
- **Informed By**: spike-2 (confirmed: pid-only match; every existing test mocks the scan away)
- **Assigned To**: fence-builder
- **Agent Type**: builder
- **Domain**: Redis/Popoto data
- **Parallel**: true
- Add `create_time: float | None = None` to `AgentSession.find_live_session_by_pid` (`models/agent_session.py:1236` — the plan previously cited `:1219`, off by 17 lines); require agreement when both sides record one, fall back to pid-only otherwise.
- Pass the psutil-observed `create_time` (already read at `session_health.py:5753`) into the scan from `_reap_orphan_session_processes` (`:5781-5805`) and `_oneshot_owner_is_live` (`:5570`).
- Restore the multi-match WARNING that `find_by_claude_pid` had; nondeterministic `frozenset` ordering must not resolve ownership silently.
- Route the scan through `_filter_hydrated_sessions` per its stated contract (`session_health.py:299-322`).
- Narrow the per-status `except` at `models/agent_session.py:1261-1270`. **Correction:** the WARNING this bullet previously asked for **already exists** there (`"find_live_session_by_pid scan failed for status=%s"`) and the old `:1247-1255` citation was off by ~16 lines. The remaining work is narrower than stated: replace the blanket `except Exception` with the specific lookup errors, and decide the Task 4 question of whether a blinded cohort should fail toward reapable or toward protected — the WARNING itself is not missing.

### 5. Cutover safety and operator surface
- **Task ID**: build-cutover-ui
- **Depends On**: none
- **Validates**: tests/unit/test_update_stale_session_fence.py (create), tests/unit/test_dashboard_liveness_probe.py, tests/integration/test_dashboard_liveness_endpoint.py
- **Informed By**: spike-3; prior art #2098
- **Assigned To**: surface-builder
- **Agent Type**: builder
- **Parallel**: true
- `scripts/update/run.py:188` `_cleanup_stale_sessions` — consult the fence before finalizing; skip fence-live sessions regardless of age; keep recency as the fallback for rows with no fence; stop asserting "no live process" in the reason string when nothing verified it; report fence-live skips distinctly in the run summary.
- `ui/data/sdlc.py` — add `pid_create_time` to `PipelineProgress` (`:356`), thread it through `_session_to_pipeline` (`:1034-1039`, which already reads `_fence` and discards `create_time`), and make `_check_process_alive` (`:38-74`) fence-aware with an explicit "unknown" for legacy rows.
- `ui/app.py:757-766` — carry the new field into the dashboard JSON.
- Update `ui/static/style.css:296`'s ghost-badge comment.

### 6. Promoted regression tests (the three durable canary jobs)
- **Task ID**: build-regression-tests
- **Depends On**: build-migration, build-legacy-policy, build-fenced-scan, build-cutover-ui
- **Validates**: all new and updated test files
- **Informed By**: spike-1 (Job 1 is EXTEND), spike-4 (use `proc_create_time`, not `create_time_fn`)
- **Assigned To**: fence-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- **Job 1 — runner-path stamping.** Extend `tests/unit/session_runner/test_runner_preempt.py`: patch `agent.pid_fence.proc_create_time`, drive a turn, assert the full record — `exec_pid`, **non-None `pid_create_time`**, `exec_cwd`, `exec_harness == "claude"`, one `spawn_history` entry with `generation`, and the 5-field `save(update_fields=...)`. Add a two-turn case asserting the re-stamp. Add `test_build_driver_wires_on_spawn_adapter` to `tests/unit/session_runner/test_runner_liveness.py` mirroring its `on_stdout_event` sibling at `:266`. Note `_on_turn_spawn` early-returns when `_current_handle is None` (`runner.py:640`) — a naive wiring test passes vacuously.
- **Job 3 — fence-branch sweep.** Parameterize the `tests/unit/test_worker_session_sweep.py` helper so `create_time` is settable, then cover: dead fence → swept; **recycled fence (alive pid, mismatched `create_time`) → swept** (the branch no test reaches today); matching fence → skipped. Assert status-keyed scoping via `query.filter.assert_called_once_with(status="running")` and exactly-once via a second pass finding the row terminal.
- **Job 4 — unmocked forward scan.** Create `tests/integration/test_orphan_reap_forward_scan.py` with real Redis rows and `find_live_session_by_pid` **not mocked**: live running session with a fresh heartbeat and a stamped fence is **not** reaped (the canary assertion); orphan with no row is reaped; duplicate fence pid across a dormant and a running row resolves to the live one regardless of iteration order; a raising status cohort does not unprotect live sessions.
- **`_has_progress` direction regression (critique BLOCKER).** Patch `agent.pid_fence.proc_create_time` so the session's `exec_pid` reads as recycled and the unrelated occupant would probe as `"hung"`; assert `_has_progress` still returns `True` when `turn_count`/`log_path`/`claude_session_uuid` show real progress. Add the converse: an unrecycled pid genuinely probing as `"hung"` still bypasses the sticky fields. This pins the corrected direction so a later refactor cannot quietly restore the premature Tier-2 release.
- Add the D1 zero-record regression, the D2 staged-SIGKILL fence test, and the `stamp_execution_spawn` observable-failure test.
- Add the migration output-capture test: run `_migrate_strip_pid_fields` against a populated keyspace and assert the captured text is **non-empty** and contains the `Stats: {'total_records'` marker. Asserting the presence of a `result.stdout` token would pass against the stderr bug.
- Apply every Test Impact disposition, including deleting/re-purposing the three impossible-state terminal-owner tests.

### 7. Pin the tolerance constant
- **Task ID**: build-tolerance-pin
- **Depends On**: build-regression-tests
- **Validates**: tests/unit/test_pid_fence.py
- **Informed By**: Research (psutil documents 0.01s precision on Linux; `CREATE_TIME_TOLERANCE_S` is 1e-3)
- **Assigned To**: fence-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add a test pinning `CREATE_TIME_TOLERANCE_S` and asserting the false-negative direction fails safe (a too-tight tolerance yields "not ours", which skips kills rather than authorizing them).
- Record in the constant's comment that it is deliberately tighter than psutil's documented Linux precision because this system is darwin-only, replacing the current "provisional / grain of salt" note with the reasoning.
- Add the NaN and `spawn_history`-unbounded pins.

### 8. Validate the census
- **Task ID**: validate-census
- **Depends On**: build-regression-tests, build-tolerance-pin
- **Assigned To**: fence-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run the consumer census independently: every site reading a fenced pid must either call `fence_is_live` or be justified in the census comment.
- Run every Verification row and report pass/fail.
- Confirm no production signature gained a `create_time_fn` parameter (rabbit hole guard).

### 9. Anti-criterion: enforce the census in CI
- **Task ID**: build-anticriterion
- **Depends On**: validate-census
- **Assigned To**: fence-builder
- **Agent Type**: builder
- **Parallel**: false
- **Write `tools/check_fence_census.py` — a per-site adjacency checker, not a count.** A threshold row like `grep -c 'fence_is_live' … | output > 11` is satisfied by any fenced site, so a future PR that adds one unfenced consumer while removing one guarded site stays green. That is the #2516 failure mode verbatim. The script must instead: walk every read of `.get("pid")` off a variable sourced from `.live_fence`, require a `fence_is_live(` call on that pid **within the enclosing function body**, and fail with the specific `file:line` of each unguarded site. Function-scoped adjacency is the load-bearing property; a global occurrence count cannot express it.
- Honor the `# fence-census: log-only, not a decision consumer` marker from Task 2 as the sole exemption mechanism. Any new exemption is a deliberate annotation at the site, reviewable in the diff.
- Register the script as a Verification row invoking it directly (exit code 0), replacing the count-threshold row.
- Demonstrate it FAILS against a deliberately-violating input first (red-state proof) and paste that output into the PR description. **The Task 2 markers must already be in place**, or the "failure" is just the two legitimate log-only sites and proves nothing.

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-census
- **Assigned To**: fence-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Rename `docs/features/dev-7f56f953.md` → `docs/features/agent-session-fenced-execution-record.md`; update `docs/features/README.md:13` and inbound references.
- Add the consumer census, the legacy-row rule, and a "Canary findings" section.
- Apply the inline-doc corrections listed in the Documentation section.
- Add new test files to the `tests/README.md` index.

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: build-anticriterion, document-feature
- **Assigned To**: fence-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the full suite via `scripts/pytest-clean.sh`.
- Verify every Success Criterion.

### 12. Canary re-verification (this machine)
- **Task ID**: verify-canary
- **Depends On**: validate-all
- **Assigned To**: fence-validator
- **Agent Type**: validator
- **Parallel**: false
- Apply `strip_pid_fields_v2` on this machine and record the follow-up dry run's counts (see the falsifiability note below — record, do not gate).
- Run canary Jobs 1-4 against the live worker with `test-`/`dbg-`-prefixed sessions, deleted via the ORM afterward, scoped by that prefix.
- Observe the Phase A `[fence-shadow]` log across at least one full canary cycle. **The shadow log covers `_tier2_reprieve_signal` only** — `_has_progress` ships enforcing in Task 2 because its fencing is strictly kill-reducing, so it emits nothing here by design; do not read its absence as a gap. Record every shadow hit: session, `exec_pid`, recorded vs live `create_time`, and whether the pid was genuinely recycled. Zero hits is a valid and informative result — it means no live session is currently relying on a fence-mismatch reprieve.
- Run `strip_pid_fields_v2` and record its now-captured output. **This does not discriminate H-A from H-B** — both hypotheses predict the same result on this machine's current data, and the recorded decision to leave the five pre-cutover worktrees in place means a non-clean result is pre-excused. Record the output as provenance and leave the root cause explicitly open (Success Criterion 2's stated escape hatch, and the expected outcome).
- **Falsifiability note.** For the same reason, "confirm a follow-up dry run reports zero strippable terminal records" is not a pass/fail gate while the worktrees remain — record the number, do not gate on it. The gate for this task is the shadow-log review and the Job 1-4 canary results.
- Record results in this plan document, including the stated coverage limit: this machine is worker-only, so bridge-intake and Job 5's SDLC render are not exercised here.
- **This task is the gate. Neither Phase B nor fleet rollout begins until it passes and the results are reviewed by the user.**

### 13. Reprieve fencing Phase B (enforce)
- **Task ID**: build-reprieve-enforce
- **Depends On**: verify-canary
- **Validates**: tests/unit/test_session_health_reprieve_fence.py
- **Assigned To**: fence-builder
- **Agent Type**: builder
- **Parallel**: false
- **Gated on the user reviewing the Phase A shadow log from Task 12.** Do not start this task on agent judgement alone.
- Delete the `# PHASE A — DELETE IN PHASE B` block; the fence verdict now drives `_tier2_reprieve_signal`'s return value and the `:1854` predicate.
- Update the tests from shadow-assertions to behavior-assertions: matching fence → reprieve preserved; recycled fence → reprieve withdrawn.
- Verify no `PHASE A` marker, shadow branch, or fence config flag survives anywhere.

### 14. Prepare the fleet rollout
- **Task ID**: prepare-rollout
- **Depends On**: build-reprieve-enforce
- **Assigned To**: fence-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Write the rollout steps for the MacBook Pro into this plan: what to run, what to check afterward (`strip_pid_fields_v2` recorded, dry run clean, no session disturbed), and how to roll back.
- The rollout itself is [EXTERNAL] — a human runs `/update` on that machine.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Migration logs to stdout | `grep -c 'stream=sys.stdout' scripts/migrate_strip_pid_fields.py` | output > 0 |
| Migration output is captured **non-empty** | `scripts/pytest-clean.sh tests/unit/test_migrations.py -k migration_output_captured -q` | exit code 0 — runs the migration and asserts the captured text matches `Stats: {'total_records'`. A `grep -c 'result.stdout'` row cannot distinguish "captured" from "captured empty" and is deliberately not used. |
| Migration re-registered | `grep -c 'strip_pid_fields_v2' scripts/update/migrations.py` | output > 0 |
| Zero-record guard present | `grep -c 'total_records.*==.*0' scripts/migrate_strip_pid_fields.py` | output > 0 |
| No unguarded rebuild in migration | `grep -c 'rebuild_indexes' scripts/migrate_strip_pid_fields.py` | match count == 0 |
| Retracted claim absent from code | `grep -rniE 'self-certif\|never stripped' scripts/ agent/ models/` | exit code 1 |
| Phase A shadow fully removed | `grep -rn 'PHASE A' agent/session_health.py` | exit code 1 |
| No fence config flag | `grep -rniE 'ENFORCE_FENCE\|FENCE_SHADOW\|fence_enabled' agent/ config/ scripts/` | exit code 1 |
| Staged SIGKILL is fenced | `grep -c '_pending_sigkill: set\[int\]' agent/session_health.py` | match count == 0 |
| Log-only fence reads are marked | `grep -c 'fence-census: log-only' agent/session_health.py` | output == 2 (the `:3198` reason-string read and the `:3216` `logger.warning` read) |
| Fence census complete (replaces the count row) | `.venv/bin/python tools/check_fence_census.py` | exit code 0. **The old `grep -c 'fence_is_live' … \| output > 11` row is deleted** — it already returns 15 at HEAD, so it passed vacuously before any work, and a count cannot express function-scoped adjacency. |
| Ownership scan takes create_time | `.venv/bin/python -c "import inspect; from models.agent_session import AgentSession; assert 'create_time' in inspect.signature(AgentSession.find_live_session_by_pid).parameters"` | exit code 0. **Replaces `grep -c 'def find_live_session_by_pid'`**, which returned 1 at HEAD and matched the `def` line whether or not the parameter existed — it could not detect the change it was named for. |
| Update cleanup consults fence | `grep -c 'fence_is_live' scripts/update/run.py` | output > 0 |
| UI carries pid_create_time | `grep -c 'pid_create_time' ui/data/sdlc.py` | output > 0 |
| No create_time_fn in production | `grep -rn 'create_time_fn' agent/session_health.py agent/agent_session_queue.py scripts/ ui/` | exit code 1 |
| Impossible-state tests removed | `grep -c 'test_terminal_owner_returns_false' tests/unit/test_session_health_orphan_process_reap.py` | match count == 0 |
| Feature doc renamed | `test -f docs/features/agent-session-fenced-execution-record.md` | exit code 0 |
| Old feature doc gone | `test -f docs/features/dev-7f56f953.md` | exit code != 0 |
| Fence tests present | `scripts/pytest-clean.sh tests/integration/test_orphan_reap_forward_scan.py -q` | exit code 0 |

## Critique Results

**Confirming pass, 2026-08-05, against plan commit `1e70a4fc`.** Depth: FULL (`appetite: Large` forces the 3-critic roster). Critics: Risk & Robustness, Scope & Value, History & Consistency, plus driver structural checks and independent source verification. Roster gate: 3/3 complete, 3/3 grounded.

The prior war room (2026-08-04, plan commit `0fae51f6`, critique commit `6cb79cf4`) returned 11 findings — 2 BLOCKER, 6 CONCERN, 3 NIT. **All 11 are confirmed landed in the plan body**, verified independently by all three critics and by the driver against source, not merely asserted in a disposition table. The two reversals were re-verified at the source: `migrate_strip_pid_fields.py:49`'s `basicConfig` carries no `stream=` (so stdout-only capture would have produced a blank Task 12 gate artifact), and `agent/session_health.py:1726`'s `if _verdict != "hung":` handles `"unknown"` and `"progressing"` identically (so fencing `_has_progress` is strictly kill-reducing). The D5 downgrade is confirmed: `HEARTBEAT_WRITE_INTERVAL = 60` (`agent/session_health.py:451`) against a 30-minute `RECENT_ACTIVITY_WINDOW` (`scripts/update/run.py:183`). The full prior table with per-finding dispositions is preserved in git at commit `1e70a4fc`.

This pass raises **3 new findings, no blockers** — all cross-reference and freshness defects introduced by task renumbering and by a day's drift in the canary machine's state. None of them touches a design decision or a code-change specification.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness, Driver | Four stale `Task 8` cross-references point at test work that no longer lives there, and Race 3's mitigation has no owning task. Task 8 is now `validate-census` (a read-only validator with no test-writing bullet); the unmocked forward-scan integration test moved to Task 6 / Job 4. Lines 132 (spike-2 impact), 306 (Test Impact), 338 (Risk 3 mitigation) and 369 (Race 3 mitigation) all still say "Task 8". Worse than a bad pointer: Race 3's *"Task 8 adds a test for the mid-spawn state; if it proves reachable, the fix is an age floor on that branch"* names a deliverable no task produces — Task 6 / Job 4 enumerates exactly four assertions and none exercise the `create_subprocess_exec`→`save()` window or the `_parent_is_orphaned_shell_wrapper` branch at `agent/session_health.py:5514` that Race 3 flags as the one gap the `ppid == 1` gate does not cover. | pending | Retarget lines 132, 306 and 338 to "Task 6" (Job 4 is the unmocked-scan deliverable). For line 369, add a fifth assertion to Task 6's Job 4 bullet in `tests/integration/test_orphan_reap_forward_scan.py`: construct an `AgentSession` row *before* `stamp_execution_spawn` has run (fence dict absent or empty) with a child process whose parent is not pid 1, and assert the reaper does not reap it inside the stamp window. If that assertion fails, the window is reachable and the age-floor fix belongs in the `_parent_is_orphaned_shell_wrapper` branch at `:5514`, gated by Task 3's "unknown never authorizes a kill" rule. Do not move the stamp. |
| CONCERN | Driver | The Prerequisites table's canary-machine check cannot pass, and it gates the task the plan declares to be *the* gate. Row 3 runs `test "$(scutil --get ComputerName)" = "Tom's MacBook Air"`; the actual `ComputerName` on this machine is `Valor the Cowboy`, so the command exits non-zero. No machine in `~/Desktop/Valor/projects.json` is named "MacBook Air" or "MacBook Pro" — the fleet is `Valor the Cowboy`, `Valor the Captain`, `Valor the Bald`, and the `valor` project (this repo) is owned by `Valor the Cowboy`. `hw.model` here is `Mac16,13`, a MacBook Air, so this is very likely the intended physical machine under a different `ComputerName`; the check string is wrong rather than the machine. But as written a builder reaching Task 12 halts on a failing prerequisite, and Tasks 13 (Phase B) and 14 (fleet rollout) sit behind it. | pending | Replace the literal in the Prerequisites row with the real value — `test "$(scutil --get ComputerName)" = "Valor the Cowboy"` — or, preferably, key it off ownership rather than a display name, which drifts: assert the `valor` project's `machine` field in `~/Desktop/Valor/projects.json` equals the local `ComputerName`. Note the plan's literal also uses a typographic apostrophe (U+2019), which would fail the compare even against a correctly-named machine. Then rename "the MacBook Pro" in the No-Gos and Task 14 to the actual fleet target machine name, since a human is being asked to walk to it. |
| NIT | Driver | The dry-run counts the plan cites as measured evidence no longer reproduce. `{'total_records': 24, 'clean': 1, 'stripped': 20, 'deferred_non_terminal': 3, 'errors': 0}` appears in four places (Technical Approach, Task 1, Task 12, Open Question 1); a dry run on this machine today reports `{'total_records': 15, 'clean': 15, 'stripped': 0, 'deferred_non_terminal': 0, 'errors': 0}` — a fully clean keyspace. The five pre-cutover worktrees named in the No-Gos (`sdlc-2138`, `sdlc-2140`, `sdlc-2144`, `sdlc-2146`, `simplify-merge-gate`) are also gone. Terminal-row TTL expiry and worktree GC explain both. This does not change any conclusion — the plan already downgraded "zero strippable records" from gate to recorded observation and already declared H-A/H-B unresolvable, so it degrades gracefully — but a builder re-measuring will find the cited numbers wrong. | pending | n/a |



---

## Decisions Recorded

All three questions from the plan draft were answered by the user on 2026-08-04. They are settled inputs to critique and build, not open items.

1. **Scope: all nine defects in one PR.** The user chose the full scope over splitting the migration item out as a standalone hotfix. Critique should weigh the migration item on its corrected premise (LOW severity, root cause open, the real defect being discarded stdout) rather than on the retracted "self-certified" framing — but the decision to keep it in this plan is made.

2. **Reprieve: log-only canary cycle before enforcing.** `_tier2_reprieve_signal` fencing ships as Phase A (shadow log, unchanged behavior) in Task 2, is observed on this machine in Task 12, and is enforced in Task 13 only after the user reviews the shadow log. Built as a removable `# PHASE A — DELETE IN PHASE B` block with a Verification row asserting its removal — explicitly **not** a config flag, which would rot into a permanent fork.

3. **Worktrees: leave them alone.** No pruning, no removal. The five pre-cutover checkouts are recorded as a known, accepted condition in the No-Gos; a stale-field reappearance while they exist is an expected outcome, not evidence of a migration defect.

**Standing constraint (user):** ample testing and hotfixing happen on this machine before any fleet rollout. Nothing rolls to another machine until the canary results are reviewed. Task 12 is the gate; Tasks 13 and 14 sit behind it. What this worker-only machine cannot exercise — bridge intake and Job 5's SDLC render — is stated plainly in the No-Gos and in Task 12's output rather than papered over.

## Open Questions

1. **Migration root cause — closed as permanently unresolvable on this machine, not open.** Nothing observable separates "stripped then re-contaminated" (H-A) from "blinded scan" (H-B). Critique confirmed this is not fixable by running the migration again: a dry run reports `{'total_records': 24, 'clean': 1, 'stripped': 20, 'deferred_non_terminal': 3, 'errors': 0}`, which is exactly what **both** hypotheses predict, and the decision to retain the five pre-cutover worktrees pre-excuses any non-clean result. The earlier claim that Task 12's `strip_pid_fields_v2` run "produces the discriminating evidence" is withdrawn throughout the plan. What Task 1 buys is **provenance for future cutovers**, not a retroactive answer. No decision is needed and none is pending — this is recorded so a builder does not go looking for a discriminator that does not exist.
