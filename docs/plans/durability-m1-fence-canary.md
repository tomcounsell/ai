---
status: Planning
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-04
tracking: https://github.com/tomcounsell/ai/issues/2518
last_comment_id:
---

# Durability M1 Fence: Canary Findings, Hotfixes, and Permanent Regression Tests

## Problem

PR #2516 (merged 2026-08-04 17:15 +0700) replaced `AgentSession`'s pid trio with a fenced execution record `(exec_pid, pid_create_time)` and shipped `agent/pid_fence.py::fence_is_live` — a `create_time` compare that answers "is this pid still *our* process?". Issue #2518 planned a 6-job canary to validate the change on one machine before rolling `/update` to the fleet.

**The canary has already run.** `/update` applied `strip_pid_fields` on this machine (Tom's MacBook Air) at 17:17, roughly an hour after the merge. It returned a negative result and a defect list. So the premise of #2518 shifts: the work is no longer "run the canary and see", it is **fix what the canary found, re-verify, then roll**.

Two themes run through every defect:

1. **The migration self-certified.** It recorded itself complete having stripped nothing. Verified by dry run on this machine right now: `{'total_records': 24, 'clean': 1, 'stripped': 20, 'deferred_non_terminal': 3}` — 20 terminal records still carry all four stale fields, `strip_pid_fields` is in `data/migrations_completed.json`, and it will never run again.
2. **The fence was built but not finished.** PR #2516 introduced `fence_is_live` and applied it correctly at nine sites. At six other sites it rebound the pid source to `fence.get("pid")` and discarded `fence.get("create_time")` sitting in the same dict. Two of those six are in the no-progress reprieve path, where a recycled pid reads as "progressing" and blocks recovery indefinitely.

**Current behavior:**
- `strip_pid_fields` is marked complete on a machine where it stripped zero records; 20 terminal + 3 immortal ledger records keep orphaned `claude_pid`/`pm_pid`/`harness_pid`/`expectations` hash fields forever.
- A recycled `exec_pid` can (a) hold a dead session alive through unbounded reprieves, (b) get an unrelated process SIGKILLed a tick later, (c) shadow a live session so its harness is SIGTERM'd, or (d) protect a genuine orphan from reaping — all silently.
- `/update`'s own stale-session cleanup kills `running` sessions with the reason `"stale cleanup (no live process)"` while never consulting the fence that now authoritatively answers that question. This fires during the very `/update` run that performs a fleet rollout.
- The highest-risk change in the PR — the orphan-reaper forward scan — is covered exclusively by tests that mock the scan away.

**Desired outcome:**
- The fence is consulted everywhere a fenced pid drives a kill, a reprieve, or an ownership claim. Where `create_time` is unreadable, the code says so and falls back deliberately rather than assuming valid.
- The strip migration cannot record success on a scan that saw nothing, and re-runs on every machine to reclaim what the first pass missed.
- The three canary jobs worth keeping become permanent tests that exercise the *real* code paths, not mocks of them.
- The canary machine is clean and re-verified, and only then does `/update` roll to the MacBook Pro.

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
- **Finding**: It fires. Full production chain confirmed: `harness/claude.py:1042` (`on_sdk_started(proc.pid)` immediately after `asyncio.create_subprocess_exec`) → `role_driver.py:407` (TURN_SPAWNED dispatch) → `runner.py:521` (`on_spawn=` wiring) → `runner.py:669` (`stamp_execution_spawn`). Re-stamped per turn; never nulled. **But** `stamp_execution_spawn` wraps its save in a bare `except Exception` logging at DEBUG (`models/agent_session.py:1216-1217`) — a stamping failure in production is invisible.
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
- **Finding**: **Refuted.** Six unfenced consumers; two HIGH (`_has_progress` `:1719-1725`, `_tier2_reprieve_signal` `:1832-1854`) where a recycled pid returns `"progressing"` and blocks the no-progress kill every tick indefinitely. Nine correctly-fenced reference sites exist to copy from.
- **Confidence**: high
- **Impact on plan**: this is the bulk of Task 2-3 and the reason appetite is Large.

### spike-4: Is `create_time_fn` the right test seam?
- **Assumption**: "Job 3's permanent test should drive pid-recycle through `create_time_fn`." (the issue's own open question)
- **Method**: code-read
- **Finding**: **Refuted.** No production call site threads `create_time_fn` through; `fence_is_live` is called positionally at every consumer. A `create_time_fn` test can only re-test `fence_is_live` in isolation, which `test_pid_fence.py:49-59` already does. The seam that *reaches* production is `patch("agent.pid_fence.proc_create_time")`, late-bound at `pid_fence.py:72` and already proven through `session_health`'s lazy import at `test_pid_fence.py:81,97`.
- **Confidence**: high
- **Impact on plan**: **answers the issue's open question** — use a seam, but the right one. Explicitly do NOT widen production signatures with a `create_time_fn` param for testability.

### spike-5: Did the migration work on this machine?
- **Assumption**: "`strip_pid_fields` in `migrations_completed.json` means the machine is clean."
- **Method**: prototype (read-only dry run against live Redis)
- **Finding**: **Refuted, and this is the headline.** Dry run reports 20 terminal records still carrying all four stale fields, plus 3 deferred `is_ledger=True` rows. One stripped-candidate row carries `last_authored_at` — a field added by #2516 Task 8 — proving it was full-saved by post-cutover code, so this is "never stripped", not "stripped then re-contaminated".
- **Confidence**: high
- **Impact on plan**: drives Task 1 and the renamed re-run migration; makes fleet rollout conditional.

## Data Flow

Fenced-pid decision flow, showing where the compare is present (✅) and absent (❌) today:

1. **Spawn** — `harness/claude.py:1042` fires `on_sdk_started(proc.pid)` after `create_subprocess_exec` → `role_driver.py:407` → `runner.py:669` `stamp_execution_spawn(pid, create_time=proc_create_time(pid), cwd, harness, generation)`.
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
| `strip_pty_fields`, `schema_diet_fields` migrations | Same delete+recreate strip pattern | Both call unguarded `rebuild_indexes()` and both lack a zero-record guard. #2516 copied the template faithfully, inheriting both defects. Neither was tested. |

**Root cause pattern:** a correct new primitive is introduced alongside its old counterpart, applied at the sites the author was looking at, and the remaining sites keep compiling and keep passing tests because the old shape is still *valid code* — just wrong. Nothing mechanically enumerates "every consumer of a fenced pid". Task 9 adds that enumeration as an anti-criterion so the next omission fails CI instead of review.

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

**Migration re-run mechanism.** `strip_pid_fields` is already recorded complete on this machine and will be on the Pro after its next `/update`. Rather than instruct operators to hand-edit `data/migrations_completed.json` (unauditable, easy to get wrong, and impossible on a machine nobody is sitting at), register the corrected script under a **new migration name** — `strip_pid_fields_v2` — in `scripts/update/migrations.py`. `run_pending_migrations()` skips by name, so a new name re-runs everywhere exactly once, automatically, on the next `/update`. The script itself is unchanged in selection semantics; it is idempotent, so machines that genuinely were clean get a fast no-op.

**Zero-record guard.** `migrate()` gains: if `total_records == 0`, exit non-zero with a distinct message. `run_pending_migrations` already refuses to record a migration whose subprocess exited non-zero, so the next `/update` retries — converging once the class-set window closes. This is a *fail-closed on ambiguity* choice: a genuinely empty database is indistinguishable from a blinded scan, and retrying costs one subprocess.

**Guarded index repair.** Replace `AgentSession.rebuild_indexes()` with the repo's `repair_indexes()` wrapper (`models/agent_session.py:2210`), which clears `$IndexF` keys and installs the phantom-re-inflation shim. It is also arguably removable entirely — the delete+save pipeline maintains every index via `on_delete`/`on_save` — but keep the guarded call rather than deleting, since the migration's whole purpose is reclaiming rows whose index state is suspect.

**Fence application shape.** At each unfenced consumer, the change is the same three lines already used at `session_health.py:2925-2931`:
```
pid, ct = fence.get("pid"), fence.get("create_time")
if pid is not None and not fence_is_live(pid, ct):
    pid = None   # not ours — treat as absent
```
Applied at `_has_progress` (`:1719`), `_tier2_reprieve_signal` (`:1832`), `_owned_task_hang_check` (`agent_session_queue.py:1991`). At `:1854` the `return "alive" if pid is not None else None` predicate is additionally replaced with a fence check, because since #2494's deliberate non-clear, `pid is not None` is permanently true for any session that ever spawned and no longer discriminates anything.

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

### Risk 1: Fencing the reprieve path makes the killer more aggressive
**Impact:** `_has_progress` and `_tier2_reprieve_signal` currently return "progressing" for recycled pids. Fencing them removes reprieves that were being granted. If any *legitimately* live session was relying on a fence-mismatch path to survive, it now gets killed.
**Mitigation:** The fence only reclassifies a pid as "not ours"; sessions whose fence genuinely matches are unaffected. Sessions with no recorded `create_time` take the legacy fallback, unchanged. Task 6's tests assert both directions explicitly (matching fence → reprieve preserved; recycled → reprieve withdrawn). Re-verify on the canary under real traffic (Task 12) before rolling.

### Risk 2: The renamed migration re-runs on a machine mid-session
**Impact:** `strip_pid_fields_v2` runs at `/update` Step 3.6, before the service restart, i.e. against a live worker — the same window that plausibly caused D1.
**Mitigation:** The migration is terminal-only, so no live row is rewritten; and the delete+recreate is one MULTI/EXEC with no non-existence window (verified). The zero-record guard now makes the dangerous case (blinded scan) fail loudly and retry rather than self-certify.

### Risk 3: The fenced ownership scan protects fewer orphans, or more
**Impact:** Requiring a `create_time` match in `find_live_session_by_pid` changes which processes the reaper considers owned. Too strict → live harnesses reaped. Too loose → orphans leak.
**Mitigation:** Fall back to pid-only whenever either side recorded no `create_time`, so the change is strictly a *refinement* of a pid match, never a broadening. Task 8's integration test asserts the live-session-not-reaped case against real Redis rows with the scan unmocked — the assertion that has never existed.

### Risk 4: The defect list is large enough that one PR becomes unreviewable
**Impact:** Nine defects across migration, recovery, update, and UI in one diff invites a rubber-stamp review — the same failure mode that let #2516 ship with six unfenced consumers.
**Mitigation:** Tasks are ordered so Task 1 (migration, gates rollout) is independently verifiable and could ship alone if review stalls. The fence-application tasks share one mechanical shape, stated once in Technical Approach, so review is "is the census complete" rather than nine separate judgements — and Task 9's anti-criterion answers that mechanically.

### Risk 5: Canary and fleet diverge because only one machine is observed
**Impact:** The Air is worker-only (no bridge); the Pro runs the bridge and 18 projects. A defect that only manifests under bridge traffic would pass the canary.
**Mitigation:** State it plainly rather than pretend otherwise — the canary covers worker, migration, reaper, and recovery, and does **not** cover bridge-side session intake. Task 12 records this limit explicitly so the fleet rollout is done with eyes open, and the rollout stays a human-gated step.

## Race Conditions

### Race 1: Migration scan vs. live worker index rebuild (the D1 root cause)
**Location:** `scripts/migrate_strip_pid_fields.py:109` (`query.all()`) vs. `models/agent_session.py:2213-2221` (`repair_indexes` → popoto `rebuild_indexes` deleting `$Class:AgentSession`), invoked from the worker health check every tick.
**Trigger:** `/update` Step 3.6 runs migrations **before** the service restart (`scripts/update/run.py:1065-1067`), so the migration subprocess and the live worker are concurrent. If `query.all()` reads the class set mid-rebuild, it returns 0 rows with no exception — documented verbatim at `agent/index_drift.py:1-12` (#1720).
**Data prerequisite:** `$Class:AgentSession` fully populated before the scan reads it.
**State prerequisite:** no concurrent `rebuild_indexes()` in flight.
**Mitigation:** Cannot lock across processes cheaply here, so fail closed on the ambiguous observation: `total_records == 0` exits non-zero, the migration is not recorded, and the next `/update` retries. Convergence is guaranteed because the window is short and `/update` runs repeatedly. Additionally use guarded `repair_indexes()` rather than raw `rebuild_indexes()` so the migration is not itself a source of this window.

### Race 2: Fence stamp vs. orphan reaper (mid-spawn gap)
**Location:** `agent/session_runner/runner.py:665-677` (stamp happens *after* fork) vs. `agent/session_health.py:5748` reaper.
**Trigger:** Between `create_subprocess_exec` and the `save()`, the pid exists but resolves to no session. A reaper pass in that window sees an unowned `claude -p`.
**Data prerequisite:** the fence row must be persisted before the pid is reapable.
**State prerequisite:** the process must be parented by a live worker.
**Mitigation:** Already mitigated by the `ppid == 1` gate (a live worker is still the parent during the window), **except** on the `_parent_is_orphaned_shell_wrapper` branch (`:5514`). Task 8 adds a test for the mid-spawn state; if it proves reachable, the fix is an age floor on that branch rather than moving the stamp.

### Race 3: Staged SIGKILL across a tick that equals the recycle window
**Location:** `agent/session_health.py:4374` (stage) → `:3945-3947` (drain), 300s apart; `AGENT_SESSION_HEALTH_CHECK_INTERVAL` at `:442`.
**Trigger:** The staged pid exits and the OS recycles it within the tick; the drain SIGKILLs the new occupant.
**Data prerequisite:** the identity captured at stage time must still hold at drain time.
**State prerequisite:** none — this is purely a time window, and the comment at `:3941` cites ~5min macOS recycling against a 300s tick.
**Mitigation:** Task 3 promotes the set to `(pid, create_time)` tuples and re-verifies at drain, exactly as `_pending_sigkill_orphans` already does at `:5680-5705`.

### Race 4: Duplicate fence pid resolved by frozenset iteration order
**Location:** `models/agent_session.py:1219-1268`, iterating `NON_TERMINAL_STATUSES` (`models/session_lifecycle.py:72-84`, a `frozenset[str]`).
**Trigger:** A stale dormant row and a live running row both carry `exec_pid=P` (the fence is never cleared on dormant/paused/superseded either). Iteration order of a `frozenset[str]` varies per process under hash randomization, so which row wins is nondeterministic across restarts.
**Data prerequisite:** at most one non-terminal row should claim a given live pid.
**State prerequisite:** ownership must be decided by identity, not by scan order.
**Mitigation:** Task 4 requires a `create_time` match, which disambiguates the two rows deterministically, and restores the multi-match WARNING that `find_by_claude_pid` had and the rewrite dropped.

## No-Gos (Out of Scope)

- [EXTERNAL] **Running `/update` on the MacBook Pro.** The fleet rollout requires a human at the second physical machine; the agent cannot reach it. Task 13 prepares and documents the rollout, and the human performs it after the canary gate clears.
- [EXTERNAL] **Observing bridge-side behavior under real Telegram traffic.** This machine is worker-only by design (no bridge activated), so canary Job 5's SDLC-render check and any bridge-intake path can only be exercised on the Pro. Recorded as a stated coverage limit rather than a silent gap.
- [SEPARATE-SLUG #2524] **Applying the zero-record guard and guarded `repair_indexes()` to the sibling strip migrations** (`scripts/migrate_strip_pty_fields.py:161`, `scripts/migrate_schema_diet_fields.py:230`). Both share D1/D6 by template inheritance, but both are already recorded complete on every machine, so edits here would be inert code changes with no runtime effect — they need their own rename-and-rerun decision, which is a separate judgement about whether their stale fields are worth reclaiming at all.
- [SEPARATE-SLUG #2524] **Generalizing the zero-record guard into `run_pending_migrations()` itself** so every future migration inherits it, rather than each script implementing its own.

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
- [ ] Add a "Canary findings" section recording what the post-merge validation found, so the next milestone's cutover inherits the checklist rather than rediscovering it.
- [ ] Update `docs/features/README.md:13` row text to reflect fenced ownership resolution and the fenced update-time cleanup.

### Inline Documentation
- [ ] `agent/pid_fence.py` module docstring — write down the legacy-row rule so the next consumer inherits it rather than inventing a fourth behavior.
- [ ] `agent/session_health.py:3941` — replace the "macOS recycles PIDs in ~5 minutes" probabilistic comment with the actual fence guarantee once `_pending_sigkill` carries `create_time`.
- [ ] `scripts/migrate_strip_pid_fields.py:26-29` — correct the docstring's false claim that deferred rows age out via `Meta.ttl`; `is_ledger=True` rows are re-saved every ~30s and their TTL is refreshed indefinitely (D9).
- [ ] `ui/static/style.css:296` — update the ghost-badge comment, which still describes an `os.kill(pid, 0)` probe.

### Test Documentation
- [ ] Add the new test files to the `tests/README.md` index table with feature markers.

## Success Criteria

- [ ] `strip_pid_fields_v2` registered; a dry run on the canary reports `stripped: 0` remaining terminal records after it applies.
- [ ] Zero-record scan exits non-zero and is NOT recorded complete (D1 regression test).
- [ ] Every consumer of a fenced pid that drives a kill, reprieve, or ownership claim calls `fence_is_live` — enforced by the Task 9 anti-criterion, not by inspection.
- [ ] `_pending_sigkill` carries `create_time` and re-verifies at drain (D2).
- [ ] `_has_progress`, `_tier2_reprieve_signal`, `_owned_task_hang_check` treat a recycled pid as absent (D3); `:1854` no longer reprieves on `pid is not None` alone.
- [ ] `find_live_session_by_pid` accepts `create_time`, requires a match when both sides have one, logs multi-match, and routes through `_filter_hydrated_sessions` (D4).
- [ ] `/update`'s `_cleanup_stale_sessions` skips fence-live sessions and no longer claims "no live process" without checking (D5).
- [ ] Legacy-row policy is consistent across `:1247`, `:2930`, `:4325` and documented in `agent/pid_fence.py` (D8).
- [ ] `PipelineProgress` carries `pid_create_time`; the dashboard reports a recycled pid as not-live (D7).
- [ ] The three promoted regression tests exist and exercise real paths: runner-path stamping (Job 1), fence-branch sweep (Job 3), unmocked forward-scan no-over-reap (Job 4).
- [ ] The three terminal-owner tests asserting an impossible state are deleted or re-purposed.
- [ ] Canary machine re-verified clean under real worker traffic; results recorded in this plan.
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

### 1. Migration: zero-record guard, guarded repair, re-run registration
- **Task ID**: build-migration
- **Depends On**: none
- **Validates**: tests/unit/test_migrate_strip_pid_fields.py (create), tests/unit/test_migrations.py
- **Informed By**: spike-5 (confirmed: 20 terminal records unstripped while recorded complete)
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a `total_records == 0` guard to `migrate()` in `scripts/migrate_strip_pid_fields.py` — exit non-zero with a distinct message so `run_pending_migrations` does not record completion and the next `/update` retries.
- Replace `AgentSession.rebuild_indexes()` (`:158-164`) with the guarded `repair_indexes()` wrapper.
- Register `strip_pid_fields_v2` in `scripts/update/migrations.py` pointing at the same script, positioned after `strip_pid_fields` and before `purge_phantom_agent_sessions`.
- Correct the `:26-29` docstring claim about `Meta.ttl` aging out deferred rows — false for `is_ledger=True` records.
- Confirm a record-level exception increments `errors` and produces a non-zero exit.

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
- Apply the fence-then-null pattern at `_has_progress` (`:1719-1725`) and `_tier2_reprieve_signal` (`:1832-1835`) so a recycled pid yields no hang verdict.
- Replace `:1854`'s `return "alive" if pid is not None else None` with a fence check — the bare predicate is permanently true since #2494 stopped clearing the fence.
- Apply the same pattern at `_owned_task_hang_check` (`agent/agent_session_queue.py:1991-1993`).

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
- Add `create_time: float | None = None` to `AgentSession.find_live_session_by_pid` (`models/agent_session.py:1219`); require agreement when both sides record one, fall back to pid-only otherwise.
- Pass the psutil-observed `create_time` (already read at `session_health.py:5753`) into the scan from `_reap_orphan_session_processes` (`:5781-5805`) and `_oneshot_owner_is_live` (`:5570`).
- Restore the multi-match WARNING that `find_by_claude_pid` had; nondeterministic `frozenset` ordering must not resolve ownership silently.
- Route the scan through `_filter_hydrated_sessions` per its stated contract (`session_health.py:299-322`).
- Narrow the per-status `except` (`:1247-1255`) so a poisoned cohort logs a WARNING rather than silently dropping every row in that status.

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
- Add the D1 zero-record regression, the D2 staged-SIGKILL fence test, and the `stamp_execution_spawn` observable-failure test.
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
- Add the census assertion as a Verification row so a future unfenced consumer fails the build rather than review. Demonstrate it FAILS against a deliberately-violating input first (red-state proof) and paste that output into the PR description.

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
- Apply `strip_pid_fields_v2` on this machine; confirm a follow-up dry run reports zero strippable terminal records.
- Run canary Jobs 1-4 against the live worker with `test-`/`dbg-`-prefixed sessions, deleted via the ORM afterward, scoped by that prefix.
- Record results in this plan document, including the stated coverage limit: this machine is worker-only, so bridge-intake and Job 5's SDLC render are not exercised here.
- **This task is the gate. Fleet rollout does not begin until it passes.**

### 13. Prepare the fleet rollout
- **Task ID**: prepare-rollout
- **Depends On**: verify-canary
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
| Migration re-registered | `grep -c 'strip_pid_fields_v2' scripts/update/migrations.py` | output > 0 |
| Zero-record guard present | `grep -c 'total_records.*==.*0' scripts/migrate_strip_pid_fields.py` | output > 0 |
| No unguarded rebuild in migration | `grep -c 'rebuild_indexes' scripts/migrate_strip_pid_fields.py` | match count == 0 |
| Canary keyspace clean | `.venv/bin/python scripts/migrate_strip_pid_fields.py \| grep -c 'WOULD strip'` | match count == 0 |
| Staged SIGKILL is fenced | `grep -c '_pending_sigkill: set\[int\]' agent/session_health.py` | match count == 0 |
| Reprieve path fenced | `grep -c 'fence_is_live' agent/session_health.py` | output > 11 |
| Ownership scan takes create_time | `grep -c 'def find_live_session_by_pid' models/agent_session.py` | output > 0 |
| Update cleanup consults fence | `grep -c 'fence_is_live' scripts/update/run.py` | output > 0 |
| UI carries pid_create_time | `grep -c 'pid_create_time' ui/data/sdlc.py` | output > 0 |
| No create_time_fn in production | `grep -rn 'create_time_fn' agent/session_health.py agent/agent_session_queue.py scripts/ ui/` | exit code 1 |
| Impossible-state tests removed | `grep -c 'test_terminal_owner_returns_false' tests/unit/test_session_health_orphan_process_reap.py` | match count == 0 |
| Feature doc renamed | `test -f docs/features/agent-session-fenced-execution-record.md` | exit code 0 |
| Old feature doc gone | `test -f docs/features/dev-7f56f953.md` | exit code != 0 |
| Fence tests present | `scripts/pytest-clean.sh tests/integration/test_orphan_reap_forward_scan.py -q` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Scope.** This issue was written as "run a 6-job canary, promote 2-3 tests." The canary has already run and returned nine defects, four of them HIGH-severity in the recovery path. This plan takes all nine in scope (minus two tagged No-Gos). The alternative is to split: land Task 1 alone as a hotfix that unblocks the fleet rollout, then take the fence-application work (Tasks 2-4) as its own issue with its own review. Given the HIGH severity — a recycled pid can block a session's recovery indefinitely — I lean toward keeping them together so the fleet rolls onto a correct fence rather than a half-fenced one. Do you agree, or would you rather ship the migration hotfix first?

2. **Reprieve aggression.** Fencing `_tier2_reprieve_signal` withdraws reprieves that are currently being granted to sessions whose pid no longer matches. That is correct, but it makes the no-progress killer strictly more willing to act, in a system where a wrongly-killed live session is a visible failure. Do you want a conservative rollout for that specific change — e.g. log-only for one canary cycle before enforcing — or is the fence's correctness enough to enforce immediately?

3. **Fleet canary coverage.** This machine is worker-only, so the canary cannot exercise bridge intake or Job 5's SDLC render. The plan records that as a stated limit and rolls anyway. Is that acceptable, or would you rather designate the Pro as a second staged canary (update it, observe, and treat the Air+Pro pair as the full gate) — noting that the Pro *is* the fleet, so there would be nothing left to roll to?
