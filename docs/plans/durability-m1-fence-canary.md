---
status: In Progress
type: bug
appetite: Large
owner: Valor Engels
created: 2026-08-04
tracking: https://github.com/tomcounsell/ai/issues/2518
last_comment_id: 5186891922
revision_applied: true
revision_applied_at: 2026-08-05T04:12:00Z
---

# Durability M1 Fence: Canary Findings, Hotfixes, and Permanent Regression Tests

## Build Record

Tasks 1-11 are complete on `session/durability-m1-fence-canary`. Task 12 (canary
re-verification) is **complete** under explicit human authorization — see the Results
block below. Tasks 13 and 14 sit behind it by the user's recorded decision.

**Where the line was drawn.** Task 12 required two things a build stage must not do
on its own: an `--apply` run of `strip_pid_fields_v2` that mutates live Redis, and a
Phase A shadow-log observation across a full canary cycle, which needs this branch's
code running in the live worker. Both are deploy-and-observe acts on unreviewed code,
so the build stopped at the dry run and Task 12 ran separately once a human authorized
the deploy.

**Migration dry run, post-fix (provenance, not a discriminator).** Measured on the
canary machine from this branch at build time:

```
Stats: {'total_records': 24, 'clean': 1, 'stripped': 20, 'deferred_non_terminal': 3, 'errors': 0}
```

stdout: 3042 bytes. stderr: 0 bytes. Before the `stream=sys.stdout` fix those numbers
were reversed, which is exactly the state that would have written a blank artifact to
`logs/update.log` and left Task 12's gate output empty. **Those counts no longer
reproduce** — see "Migration keyspace: current status quo" below. The stream
measurement is the durable part of this record; the counts are a snapshot.

**Terminal-row writer, identified (Race 2 / Task 1).** The docstring claim that "the
worker never writes terminal rows" was false, and the writer is
`agent.session_health.cleanup_corrupted_agent_sessions`: it re-saves every hydrated
record, terminal ones included, as its no-op-save corruption probe, and
`AgentSession.save()` restamps `updated_at`. `/update` invokes it at Step 5.5
(`scripts/update/run.py`), as do worker startup and the `agent-session-cleanup`
reflection. That accounts for the observed ~60ms batch timestamp move. The docstring
now states the real safety property, which is the single MULTI/EXEC delete+recreate
(atomicity), not quiescence.

**Shadow log reads `create_time` once (PR #2538 re-review).** The Phase A logger used to
re-read `proc_create_time` for its `{reason}` label, while the mismatch that decided
whether to log at all was frozen earlier from a first read — with `subprocess_hang_verdict`
(which samples CPU) in between. That let the label describe a later sample than the
decision acted on, and the mislabelling ran one way: two of three paths inflated the
pro-enforcement reading and one laundered the anti-enforcement case. It also made a fourth
label, for a re-read that agreed with the recorded `create_time`, reachable — and that
state is not noise. `create_time` is immutable per process, so agreement means the pid IS
the recorded process and IS alive, i.e. the gating predicate returned a false negative on
a live, owned, progressing session that Phase B would then kill. The fix is structural
rather than editorial: `live_ct` is read once at the decision site, the mismatch is derived
from it with `create_times_match`, and the same reading is passed to the logger. Every
label now reports what the decision saw, and the fourth label is unreachable and deleted.

**Legacy-row rule, one deviation from the plan text, deliberate.** The plan's rule is
"unknown never authorizes a kill, but may authorize the gentler action already in
place", and it also asks the in-process orphan reap (`:4325`) to take the same legacy
fallback its sibling at `:1247` has. Those two halves conflict at that site, because
its action is a signal rather than a row transition. Resolution shipped: a legacy row
whose pid passes a plain liveness probe gets **SIGTERM only**, logged at WARNING, and
is **not** staged for SIGKILL escalation. Only a fence match earns the escalation.
That closes the never-reaps gap the plan wanted closed while keeping the rule's
prohibition intact for the irreversible half of the action.

**Census rule, one refinement.** `tools/check_fence_census.py` accepts a scope as
guarded if it calls `fence_is_live`/`create_times_match` **or** reads
`.get("create_time")` off the same fence. Strict adjacency alone false-positives on
`ui/data/sdlc.py::_session_to_pipeline`, which correctly reads both halves and
forwards them to `_check_process_alive`. The defect class is precisely *discarding*
the `create_time` in the same dict, which all six #2516 sites did, so the refinement
catches the class without flagging plumbing. The limitation (a scope could read the
value and ignore it) is stated in the script's docstring. Red-state proof:
`tests/fixtures/fence_census_violator/` plus `tests/unit/test_fence_census.py`.

A new shared predicate `agent.pid_fence.create_times_match` was extracted so
`find_live_session_by_pid` can fence against a `create_time` its caller already
observed, without re-reading psutil per candidate row.

### Test results, and what "Tests pass" does and does not cover here

**Targeted validation: 930 passed, 0 failed.** Every test file that touches the
changed code, run together: the whole `tests/unit/test_session_health_*` family,
`test_pid_fence`, `test_fence_census`, `test_worker_session_sweep`,
`test_stale_cleanup`, `test_update_stale_session_fence`, `test_migrations`,
`test_migrate_strip_pid_fields`, `test_dashboard_liveness_probe`,
`test_ui_sdlc_data`, `test_session_lifecycle`, `test_recovery_ownership`,
`test_reap_killlist`, `test_architectural_constraints`, all of
`tests/unit/session_runner/`, and both integration files including the new
unmocked forward scan.

**A single clean full-suite pass was not obtained, and the reason is the machine,
not the change.** Three full-suite runs were in flight from other agents on this
box while this work ran, all against the same Redis and the same machine-global
suite lock. Two attempts wedged with `[gw*] node down: Not properly terminated`
and four xdist workers at zero CPU.

**The regression question was answered by baseline diff instead.** A 133-file
`tests/unit` slice was run on this branch and again in a throwaway worktree at
`origin/main`, and the failure sets compared by name:

| | failures |
|---|---|
| `session/durability-m1-fence-canary` | 48 |
| `origin/main` (same files) | 51 |

**Regressions introduced: zero.** Every failure on this branch also fails on
main. The three extra on main are flaky run-lock collisions in
`test_nightly_regression_tests.py`. The 48 shared failures are in
`test_persona_loading`, `test_goal_gates`, `test_pipeline_state_machine`,
`test_output_handler`, `test_health_check`, `test_load_principal_context`,
`test_harness_thinking_block_sentinel` and similar — files this change does not
touch, failing identically without it. A separate `tests/unit` slice ran 2336
passed / 1 failed, the one being `test_bridge_watchdog.py::test_no_zombies_still_populates`,
which is environment-sensitive and was reading a machine full of other agents'
stray processes.

Since that baseline diff was taken, `6aa4403f3` landed on main and fixed the
repo-wide ruff gate (deprecated UP038 plus three unformatted test files). The
branch must rebase onto it before the lint Verification rows can pass.

Before merge, the full suite should get one clean pass on a quiet machine. That
is a scheduling problem, not an open defect.

### Verification table status

Every row passes except one, which is expected to fail until Task 13:

| Row | Result |
|---|---|
| Phase A shadow fully removed (`grep -rn 'PHASE A' agent/session_health.py` → exit 1) | **Fails by design.** Phase A is the shipped state; the user deferred enforcement to Phase B behind review of Task 12's shadow log. This row is Task 13's exit criterion, not this build's. |

One row needed a fix rather than an exception: the "no fence config flag" row
(`grep -rniE 'ENFORCE_FENCE|FENCE_SHADOW|fence_enabled'`) matched a helper this
build had named `_log_fence_shadow_withdrawal`. That is a name collision, not a
config flag, and the check is the one asserting a real property, so the helper
was renamed to `_log_shadow_reprieve_withdrawal`. The row now passes.

Two rows the critique flagged as vacuous at HEAD were replaced as the plan
directed and now genuinely discriminate: the census script replaces the
`fence_is_live` count threshold, and an `inspect.signature` assertion replaces
the `grep -c 'def find_live_session_by_pid'` row.

### Branch/main conflict this build must resolve (PATCH item)

The branch's Task 1 replaced the migration's trailing `AgentSession.rebuild_indexes()`
with `AgentSession.repair_indexes()`. Out-of-band hotfix `369d782c8` landed on main
and replaced the same call with `AgentSession.clean_indexes()` instead. **Main is
right and the branch is wrong**: `repair_indexes()` calls popoto's `rebuild_indexes()`
internally (`models/agent_session.py:2226-2251`), so it hits both the phantom
index-metadata `ExtraData` failure tracked as **#2536** and the #1720 class-set
window the plan elsewhere argues against. `clean_indexes()` is the documented
production-safe orphan sweep and touches neither. The branch must take main's
`clean_indexes()` call; Technical Approach and Task 1 are corrected accordingly
below. Note the Verification row `grep -c 'rebuild_indexes' … | match count == 0`
passes under both spellings and therefore did **not** catch this — it is a
substitution check, not a correctness check.

### Branch/main reconciliation — RESOLVED 2026-08-05

Both PATCH items recorded above are closed. The branch is now a **single build commit
on top of current `main`** (`d343ed81f`), with `6aa4403f3` (ruff gate) and `369d782c8`
(`clean_indexes()`) both in its ancestry.

- **Index sweep.** The rebase conflicted on `scripts/migrate_strip_pid_fields.py` at
  exactly the disputed call. Main's `clean_indexes()` was taken and the branch's
  `repair_indexes()` dropped. The surrounding comment was reworded to state the
  reasoning (#1720 class-set window, #2536 phantom metadata) **without naming the two
  rejected identifiers**, so the Verification row `grep -rniE
  'rebuild_indexes|repair_indexes' scripts/migrate_strip_pid_fields.py` → exit 1 is a
  real discriminator rather than one the comment defeats. Row re-run: exit 1. ✅
  **Amended by #2524:** that code now lives in `scripts/_strip_migration.py`, so the
  row must grep the shared engine as well as the three delegate scripts — greping
  only `migrate_strip_pid_fields.py` would pass against a file that no longer
  contains the constrained call. The constraint itself is unchanged and is now
  enforced by assertion rather than by grep
  (`tests/unit/test_strip_migration_shared.py`).
- **Rebase.** Done. Lint gate now passes on the branch: `ruff check` exit 0,
  `ruff format --check` exit 0 (1271 files already formatted).
- **Stale plan duplicate removed.** The branch carried an older Build Record that the
  `d19f6d6af` revision on main had already superseded. The branch's plan doc is now
  byte-identical to main's; the plan has exactly one Build Record.

**Verification rows re-run on the rebased branch:** `clean_indexes()` present (2
matches), no rebuild/repair spelling (exit 1), `tools/check_fence_census.py` exit 0
("11 fenced-pid consumer(s), all guarded"), `find_live_session_by_pid` carries
`create_time` (exit 0).

**Targeted tests on the rebased branch: 692 passed, 0 failed** across the fence,
migration, census, sweep, reprieve, update-cleanup, dashboard, UI, orphan-reap,
forward-scan, session-runner and architectural-constraint files. Two failures in
`test_reap_killlist.py` appear only when that file is run alongside
`test_session_lifecycle.py`/`test_recovery_ownership.py`; the same combination fails
identically on `origin/main`, and the file passes alone on both. **Pre-existing
cross-file isolation defect, not a branch regression** — verified by running the
combination on main, not assumed.

**Job 4's forward-scan coverage confirmed unmocked.** `tests/integration/
test_orphan_reap_forward_scan.py` never patches `find_live_session_by_pid`; the only
patches are `psutil.process_iter` / `_psutil_process_for_pid` (process discovery) and a
deliberate `PoisonedCohort` query stub for the blinded-cohort assertion. The scan
itself resolves through real Redis rows and the real status index.

## Post-Cutover Re-Scope (2026-08-05)

Issue comment [5186891922](https://github.com/tomcounsell/ai/issues/2518#issuecomment-5186891922)
(2026-08-05T02:37:58Z) recorded the post-cutover canary results and **narrows the
remaining scope of this plan**. It postdates the revision this plan was built from,
so it governs.

**Verified in production, removed from remaining scope:**

| Canary job | Evidence |
|---|---|
| Job 1 — fence stamping | Two real sessions ran turns under the post-merge worker (`df6097fe6`) and stamped correctly: `55239089e2` → `exec_pid=85070`, `f8df732f81` → `exec_pid=60398`, each with `pid_create_time` populated and a matching `spawn_history` entry. Pre-cutover terminal sessions correctly read `exec_pid=None` — the degraded-fence read path is confirmed too. |
| Job 3 — kill → recovery / dead fence | Regression coverage shipped with PR #2516 and is green: `test_recovery_respawn_safety.py` (69), `test_pid_fence.py`. |
| Job 4 — orphan-reaper forward scan | Regression coverage shipped with PR #2516 and is green: `test_worker_session_sweep.py` (12), `test_session_health_orphan_reap.py`, `test_session_health_orphan_process_reap.py`. |
| Job 6 — migration cutover | `strip_pid_fields` applied; every AgentSession record clean; index healthy 14/14; live dashboard consistent; worker boot SHA == HEAD. |

**Consequence:** the plan's original goal of "promote 2-3 canary jobs into permanent
regression tests" is **already met** by the coverage merged with #2516. Jobs 1, 3 and
4 need no new duplicate tests. The Task 6 "three promoted regression tests" bullets
stay in the plan only as the *defect-regression* tests they became (recycled-fence
sweep branch, unmocked forward scan, `_has_progress` direction) — not as canary
promotions.

**Remaining canary scope — Jobs 2 and 5 only:**

- **Job 2 — multi-turn steered session.** Fence persistence across turns plus steering
  drain. Not yet driven as an explicit live canary.
- **Job 5 — short SDLC job.** Lifecycle render plus at-rest owed-communication check,
  asserting no false positive. Not yet driven live.
- **Optional** — a dedicated end-to-end fence-stamping integration test that drives a
  real runner turn, rather than the current unit-level `FakeSession` assertions. This is
  **Task 15** (`build-e2e-stamping`) below, with its own Verification row; it is optional
  in the sense that the plan ships without it, not in the sense that it has nowhere to land.

**Out-of-band hotfixes folded in.** Two fixes landed direct to main outside this plan
during the cutover; both are confirmed on `origin/main` and neither needs redoing here:

- `6aa4403f3` — repo-wide ruff gate was red for every PR (deprecated UP038 plus three
  unformatted test files). Fixed on main. This branch must rebase onto it.
- `369d782c8` — `migrate_strip_pid_fields.py` used the fragile full `rebuild_indexes()`;
  changed to the production-safe `clean_indexes()`. **This supersedes this plan's
  `repair_indexes()` directive** — see the Build Record conflict note above.

**Follow-up filed.** The underlying phantom index-metadata key that makes
`rebuild_indexes()` fail with `unpack(b) received extra data` is tracked separately as
**#2536** (investigate, do not blind-purge). It is out of scope here; this plan only
consumes its mitigation (`clean_indexes()`).

## Migration keyspace: current status quo

`AgentSession` records on this machine are **clean**. A dry run today reports:

```
Stats: {'total_records': 15, 'clean': 15, 'stripped': 0, 'deferred_non_terminal': 0, 'errors': 0}
```

Zero records carry stale `claude_pid`/`pm_pid`/`harness_pid`/`expectations` hash
fields, and the index is healthy (14/14). The five pre-cutover worktrees that were the
standing re-contamination hazard (`sdlc-2138`, `sdlc-2140`, `sdlc-2144`, `sdlc-2146`,
`simplify-merge-gate`) are gone, collected by worktree GC.

**What this changes for the plan:** the migration item is now purely about
*observability and mechanism* — capturing the migration's output so a future cutover
can answer "did it strip anything?" from `logs/update.log`, keeping the zero-record
guard as insurance against the #1720 blinded-scan window, and using the production-safe
index call. There is no stale-record backlog to reclaim on this machine, and none of
the work below depends on there being one.

## Problem

PR #2516 (merged 2026-08-04 17:15 +0700) replaced `AgentSession`'s pid trio with a fenced execution record `(exec_pid, pid_create_time)` and shipped `agent/pid_fence.py::fence_is_live` — a `create_time` compare that answers "is this pid still *our* process?". Issue #2518 planned a 6-job canary to validate the change on one machine before rolling `/update` to the fleet.

**The canary has already run.** `/update` applied `strip_pid_fields` on this machine (`Valor the Cowboy`, the `valor` project's owner in `projects.json`) at 11:48 UTC, roughly 90 minutes after the merge. So the premise of #2518 shifts: the work is no longer "run the canary and see", it is **fix what the canary found, re-verify, then roll**.

The substance of what it found is the fence, not the migration:

**The fence was built but not finished.** PR #2516 introduced `fence_is_live` and applied it correctly at nine sites. At six other sites it rebound the pid source to `fence.get("pid")` and discarded `fence.get("create_time")` sitting in the same dict. **Six unfenced consumers, two of them HIGH** — matching spike-3 exactly. An earlier draft of this section said "four HIGH-severity defects" and described both HIGH sites as under-killing. Both halves of that were wrong; the corrected account follows. The two HIGH sites fail in **opposite directions**, and that asymmetry decides which one needs a staged rollout:

| HIGH site | Direction | What a recycled `exec_pid` does today | Effect of fencing it |
|-----------|-----------|----------------------------------------|----------------------|
| `_tier2_reprieve_signal` (`agent/session_health.py:1832-1854`) | **Under-kills** | An unrelated process probes as `"progressing"` → `return gate` grants a reprieve every tick. `:1854`'s `return "alive" if pid is not None` is permanently true since #2494 stopped clearing the fence, so it no longer discriminates anything. A dead session is held alive indefinitely. | Nulling the pid yields `"unknown"`, which routes to the count-based escalation guard (`reprieve_count >= MAX_NO_OUTPUT_REPRIEVES` → `None`) and makes `:1854` return `None`. **Strictly kill-increasing** — this is the one site that gets the Phase A shadow. |
| `_has_progress` → `subprocess_hang_verdict` (`agent/session_health.py:1719-1734`) | **Over-kills** | An unrelated process that probes as `"hung"` bypasses the sticky-field honor at `if _verdict != "hung":`, falls through to the child check, and prematurely releases a session with real progress to Tier-2 recovery. | Nulling the pid yields `"unknown"`, which `if _verdict != "hung":` treats **identically to `"progressing"`** — sticky fields honored, `True` returned. **Strictly kill-reducing**, so it needs no Phase A gating and can never produce a shadow-log hit. |

The second row is the correction that matters for build: fencing `_has_progress` **cannot** close a "false progressing blocks recovery indefinitely" gap, because `"progressing"` and `"unknown"` are the same outcome at that branch. The in-code comment shipped with #2516 (`:1710-1716`) already states this. **Do not extend Phase A gating to `_has_progress`.**

**A secondary, low-severity observation about the migration.** The migration harness discards its own subprocess output on success, so "did `strip_pid_fields` strip anything?" was not answerable from `logs/update.log` and had to be reconstructed by forensics. That is the one actionable defect in this area, and it is explicitly **not** the headline. The keyspace itself is clean (see "Migration keyspace: current status quo"); "Migration: what is and is not established" below records the forensic episode that produced this finding.

**Current behavior:**
- A recycled `exec_pid` can (a) hold a dead session alive through unbounded reprieves, (b) prematurely release a *progressing* session to Tier-2 recovery when the unrelated occupant probes as hung, (c) get an unrelated process SIGKILLed a tick later, (d) shadow a live session so its harness is SIGTERM'd, or (e) protect a genuine orphan from reaping — all silently. Note (a) and (b) point opposite ways; see the HIGH-site table above.
- `/update`'s own stale-session cleanup finalizes `running` sessions with the reason `"stale cleanup (no live process)"` while never consulting the fence that now authoritatively answers that question. **Blast radius is narrower than an earlier draft implied** (critique NIT): `_cleanup_stale_sessions` already skips any session whose `updated_at` falls inside `RECENT_ACTIVITY_WINDOW` (30 min, `scripts/update/run.py:183-185`), and `HEARTBEAT_WRITE_INTERVAL` is 60s (`agent/session_health.py:451`) on an independent asyncio loop (`agent/session_executor.py:2080`), so a genuinely live session stays well inside the window and is already protected by recency. `finalize_session` also marks the row terminal rather than signalling the process; any kill arrives indirectly, once the terminal row stops resolving through `find_live_session_by_pid`'s `NON_TERMINAL_STATUSES` scan and the orphan reaper claims the pid. **This is a correct refinement of a truthful reason string, not a rescue from an active fleet-rollout hazard**, and it is not what gates the rollout.
- The highest-risk change in the PR — the orphan-reaper forward scan — is covered exclusively by tests that mock the scan away.

**Desired outcome:**
- The fence is consulted everywhere a fenced pid drives a kill, a reprieve, or an ownership claim. Where `create_time` is unreadable, the code says so and falls back deliberately rather than assuming valid.
- The migration harness stops discarding its own evidence, so the next cutover can answer "did it strip anything?" from the logs instead of by forensics.
- Each fixed consumer gains a defect-regression test that exercises the *real* code path, not a mock of it. (The original "promote 2-3 canary jobs into permanent tests" goal is already met by the coverage merged with PR #2516.)
- The canary machine is re-verified under real worker traffic, and only then does `/update` roll to the rest of the fleet (`Valor the Captain`, `Valor the Bald`).

## Migration: the durable findings

The keyspace is clean (see "Migration keyspace: current status quo"). Three findings from the cutover survive as inputs to the work:

| # | Finding | Evidence |
|---|---------|----------|
| 1 | **The migration harness discards its subprocess output on success.** This is the actionable defect. | `scripts/update/migrations.py:239-247` returns `None` and never logs `result.stdout`. The script emits a per-record `STRIP <id>` line and a final stats dict; all of it is thrown away unless the process exits non-zero, so "did it strip anything?" is unanswerable from `logs/update.log`. |
| 2 | **The migration's output goes to stderr, not stdout.** Capturing only stdout would capture nothing. | `scripts/migrate_strip_pid_fields.py:49` calls `logging.basicConfig(...)` with no `stream=`, so the default `StreamHandler` writes to stderr. Measured: stdout 0 bytes, stderr carries every line. |
| 3 | **Terminal rows are rewritten, so the migration's stated safety premise is false.** | The docstring asserts *"the worker never writes terminal rows"* (`:24`). The writer is `agent.session_health.cleanup_corrupted_agent_sessions`, which re-saves every hydrated record — terminal ones included — as its no-op-save corruption probe, restamping `updated_at`. `/update` invokes it at Step 5.5, as do worker startup and the `agent-session-cleanup` reflection. The real safety property is the single MULTI/EXEC delete+recreate (atomicity), not quiescence. |

**Consequences for the plan:** Task 1 is scoped to capturing evidence and hardening the mechanism, not to reclaiming a backlog — there is none. The zero-record guard is retained as cheap insurance against the #1720 blinded-scan window (`AgentSession.query.all()` can return 0 with no exception during a class-set rebuild), explicitly labelled as insurance rather than a fix. Severity LOW throughout: orphaned hash fields, had any survived, are ignored on load.

## Freshness Check

**Baseline commit:** `3a5f1b5085aaa0532963bdea7d7982d52b7689a9`
**Issue filed at:** 2026-08-04T03:37:50Z
**Disposition:** Minor drift (with a large defect payload — see Recon)

**File:line references re-verified:**
- `agent/pid_fence.py` — 81 lines, `fence_is_live` at `:46`, `CREATE_TIME_TOLERANCE_S = 1e-3` at `:27` — holds.
- `agent/session_runner/runner.py:669` — `stamp_execution_spawn` call site — holds (`_on_turn_spawn`, `:633-677`).
- `scripts/migrate_strip_pid_fields.py` — 188 lines — holds.
- `ui/data/sdlc.py:356` (`exec_pid` field), `:38` (`_check_process_alive`) — holds. (The `:1051` citation in the original was drift; corrected 2026-08-05.)
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
- **Impact on plan**: confirms "highest-risk"; drives Task 4 (fence the scan) and Task 6 / Job 4 (integration test with real rows, scan unmocked).

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
- **Finding**: `strip_pid_fields` in `migrations_completed.json` proves nothing about what the run did, because the harness discards the subprocess output on success and the script logs to stderr anyway. The migration itself is sound (the wrapper passes `--apply`; post-cutover code cannot write the stripped fields), but its evidence was unrecoverable. See "Migration: the durable findings".
- **Confidence**: high
- **Impact on plan**: Task 1 becomes "capture the evidence and harden the mechanism", not "reclaim a backlog". Severity LOW. Does **not** gate fleet rollout.

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
7. **Operator surface** — ❌ `ui/data/sdlc.py:38` (`_check_process_alive`) bare `os.kill(pid, 0)` → `ui/app.py:760-766` → dashboard JSON. `PipelineProgress` has no `pid_create_time` field, so the compare is structurally impossible.
8. **Cutover** — ❌ `scripts/update/run.py:188` `_cleanup_stale_sessions` finalizes `running` → `killed` on recency alone, reason `"stale cleanup (no live process)"`.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #2516 | Introduced `fence_is_live`, applied it at 9 sites, deleted the pid trio | Migrated pid *readers* to `fence.get("pid")` but left the `create_time` compare unwritten at 6 sites. The value is in the same dict at every one of them. Review APPROVEd twice because each individual site looks like a faithful mechanical rebind; only an exhaustive consumer census reveals the omissions. |
| #2098 fix | Made session-liveness-check single-owner | Fixed the reflection-process instance of "liveness signal is not authoritative in the reading process". Did not generalize the lesson, so `/update`'s `_cleanup_stale_sessions` (D5) kept the same shape — it even documents that `_active_workers` is empty in its process and proceeds anyway. |
| `strip_pty_fields`, `schema_diet_fields` migrations | Same delete+recreate strip pattern | Both called unguarded `rebuild_indexes()`, neither was tested, and all three ran under a harness that discarded their stdout — so none had ever produced a durable record of what it did. #2516 copied the template faithfully, inheriting every trait. **Resolved by #2524:** the scan, the zero-record guard, the production-safe `clean_indexes()` sweep and the exit-code contract now live once in `scripts/_strip_migration.py`; all three scripts are thin delegates over it; output capture is generalized to every subprocess-shaped migration via `_run_migration_script`; and both siblings are re-registered as `_v2` so every machine re-runs them once. Covered by `tests/unit/test_strip_migration_shared.py`. |

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
| Canary machine is this one | `python -c "import json,os,subprocess; m=json.load(open(os.path.expanduser('~/Desktop/Valor/projects.json')))['projects']['valor']['machine']; n=subprocess.run(['scutil','--get','ComputerName'],capture_output=True,text=True).stdout.strip(); raise SystemExit(0 if m==n else 1)"` | Task 12 re-verification runs here; fleet rollout does not |

**Machine-gate ruling (2026-08-05).** The earlier literal `test "$(scutil --get ComputerName)" = "Tom’s MacBook Air"` was **wrong, and the machine is right.** Verified: this host's `ComputerName` is `Valor the Cowboy`, its `hw.model` is `Mac16,13` (a MacBook Air), and `~/Desktop/Valor/projects.json` assigns the `valor` project's `machine` to `Valor the Cowboy`. So this **is** the intended canary machine under its real name; no machine anywhere in the fleet has ever been named "Tom's MacBook Air". (The old literal also used a typographic apostrophe, U+2019, so it could not have matched even a correctly-named host.)

**Ruling: the gate is revised, not dropped and not split out.** Task 12 is runnable on this machine and remains in the PR's critical path. The check is now keyed off *ownership* — the `valor` project's `machine` field must equal the local `ComputerName` — rather than a hard-coded display name, because display names drift and this defect is the proof. A builder reaching Task 12 no longer halts.

**Fleet target renamed.** The plan previously said "the MacBook Pro", which likewise names no real machine. The fleet is `Valor the Cowboy` (this host, `valor` + `popoto`), `Valor the Captain` (`cuttlefish`, `psyoptimal`, `royop`) and `Valor the Bald` (`cyndra`). The [EXTERNAL] rollout in Task 14 is a human running `/update` on the other two hosts; which one goes first is an operator call, and the No-Gos and Task 14 name them by their real names.

## Solution

### Key Elements

- **Zero-record guard + re-run migration**: the strip migration refuses to report success on an empty scan, and re-registers under a new name so every machine re-runs it once.
- **Fence census + application**: every site that turns a fenced pid into a kill, a reprieve, or an ownership claim consults `fence_is_live`, with an explicit documented decision when `create_time` is unreadable.
- **Fenced ownership resolution**: `find_live_session_by_pid` accepts the observed `create_time` and requires a match, with a pid-only fallback only when nothing was recorded.
- **Cutover safety**: `/update`'s stale-session cleanup asks the fence before killing anything, so a fleet rollout cannot disturb a live session.
- **Real-path defect-regression tests**: each fixed consumer gets a test that exercises the production code through the seam that actually reaches it (`patch("agent.pid_fence.proc_create_time")`), plus an integration test that resolves through the real status index rather than a mock.
- **Anti-criterion**: a CI-enforced census so a future unfenced consumer fails the build.

### Flow

**Canary machine (`Valor the Cowboy`)** → capture migration evidence (D1) → fix D2-D9 → tests green → **Jobs 2 + 5 driven live, shadow log reviewed** → **canary re-verified (the gate)** → Phase B enforce → human runs `/update` on `Valor the Captain` and `Valor the Bald` → **fleet clean**

### Technical Approach

**Capture the migration's evidence (the actual defect).** Two facts, both established empirically by critique, define this fix:

1. **The output is on stderr, not stdout.** `scripts/migrate_strip_pid_fields.py:49` calls `logging.basicConfig(level=logging.INFO, format=...)` with no `stream=`, so Python's default `StreamHandler` writes to **stderr**. Measured on this machine: stdout is **0 bytes**; stderr carries every `WOULD strip` / `DEFER` line and the final `Stats:` dict. Logging `result.stdout` alone would faithfully write an empty string to `logs/update.log` — and Task 12's gate artifact, the thing the user reads before authorizing Phase B and the fleet rollout, would be blank.
2. **The wrapper is `_migrate_strip_pid_fields`, not `run_pending_migrations`.** `run_pending_migrations` calls `fn(project_dir)` under the `MIGRATIONS` contract `dict[str, tuple[callable, str]]` where the callable returns `str | None` (`scripts/update/migrations.py:942`). The subprocess and its captured streams live entirely inside each per-migration helper (`_migrate_strip_pid_fields`, `:220-249`). `run_pending_migrations` never sees stdout and structurally cannot log it.

So the fix is two-sided and **scoped to this one migration**: add `stream=sys.stdout` at `migrate_strip_pid_fields.py:49` (`sys` is already imported at `:44`), **and** log both `result.stdout` and `result.stderr` from `_migrate_strip_pid_fields`, so a future script that logs to stderr is still captured. The earlier draft's claim that this "generalizes to every migration" is withdrawn — generalizing it means widening the `MIGRATIONS` value contract to return `(error, output)` and updating every helper, which this appetite does not budget. That generalization is routed to `[SEPARATE-SLUG #2524]` alongside the analogous zero-record-guard generalization.

Verification must assert *captured non-empty output*, not the presence of a `result.stdout` token — a `grep -c 'result.stdout'` row goes green on exactly the broken state described above.

**Migration re-run mechanism.** `strip_pid_fields` is already recorded complete on this machine and will be on the Pro after its next `/update`. Rather than instruct operators to hand-edit `data/migrations_completed.json` (unauditable, easy to get wrong, and impossible on a machine nobody is sitting at), register the script under a **new migration name** — `strip_pid_fields_v2` — in `scripts/update/migrations.py`. `run_pending_migrations()` skips by name, so a new name re-runs everywhere exactly once, automatically, on the next `/update`. Selection semantics are unchanged; it is idempotent, so a genuinely clean machine gets a fast no-op.

**What the v2 run buys is provenance, not a diagnosis.** This machine's keyspace is already clean — a dry run today reports `{'total_records': 15, 'clean': 15, 'stripped': 0, 'deferred_non_terminal': 0, 'errors': 0}` — so v2 here is a fast no-op that exercises the mechanism. Its value is that the **next** cutover, on any machine, answers "did it strip anything?" from `logs/update.log` instead of by forensics. Do not expect it to explain anything about the past run; that record population no longer exists.

**Zero-record guard — insurance, not a fix.** `migrate()` gains: if `total_records == 0`, exit non-zero with a distinct message. `run_pending_migrations` already refuses to record a migration whose subprocess exited non-zero, so the next `/update` retries. This is *fail-closed on ambiguity*: a genuinely empty database is indistinguishable from a blinded scan (#1720). **It is retained on its own merits: cheap, safe, and it makes a blinded scan loud instead of silent.**

**The retry is unbounded, and that is an accepted, documented condition.** An earlier draft said "retrying costs one subprocess", which understates it as a one-off. It is not: `run_pending_migrations` records completion only when the helper returns `None`, and `scripts/update/run.py:1073-1076` appends every failure to `result.errors` and logs `FAIL:` with `always=True`. On a machine whose `AgentSession` keyspace is *legitimately* empty — a fresh install — `strip_pid_fields_v2` therefore fails on **every** `/update`, forever, emitting recurring `FAIL:` noise. Deliberate choice: **accept and document rather than bound it.** A consecutive-observation counter would need its own persisted state beside `data/migrations_completed.json`, which is new durable state in service of a case neither existing machine is in (this one has 24 rows; the Pro has more). The acceptance is written into Task 1 and the No-Gos so an operator on a future fresh install reads the recurring `FAIL:` as expected output, not as a live regression.

**Production-safe index sweep — already landed on main as `369d782c8`.** The migration's trailing index call is `AgentSession.clean_indexes()`, the documented production-safe orphan-reference cleanup. It is **not** `rebuild_indexes()` and **not** the `repair_indexes()` wrapper: `repair_indexes()` calls `rebuild_indexes()` internally (`models/agent_session.py:2226-2251`), which tears down and rebuilds every index, opens the #1720 class-set window this plan elsewhere argues against, and currently fails outright with `unpack(b) received extra data` on pre-existing phantom index metadata (tracked as **#2536**, investigate — do not blind-purge). The per-record `delete()` + `save()` pipeline already maintains every index atomically, so the trailing call is a defensive sweep, not a functional requirement. **The branch's `repair_indexes()` version must be reverted to main's `clean_indexes()`.**

**Fence application shape.** At each unfenced consumer, the change is the same three lines already used at `session_health.py:2925-2931`:
```
pid, ct = fence.get("pid"), fence.get("create_time")
if pid is not None and not fence_is_live(pid, ct):
    pid = None   # not ours — treat as absent
```
Applied at `_has_progress` (`:1719`), `_tier2_reprieve_signal` (`:1832`), `_owned_task_hang_check` (`agent_session_queue.py:1991`). At `:1854` the `return "alive" if pid is not None else None` predicate is additionally replaced with a fence check, because since #2494's deliberate non-clear, `pid is not None` is permanently true for any session that ever spawned and no longer discriminates anything.

**Mark the log-only fence reads so a mechanical census cannot mistake them for consumers.** Two sites read a fenced pid and drive nothing: `agent/session_health.py:3198` (`_fence_pid = (getattr(entry, "live_fence", None) or {}).get("pid")`, interpolated into a `finalize_session` reason string) and `:3216` (the same expression inline inside a `logger.warning`). The Data Flow census correctly omits both by eye, but they are textually indistinguishable from a real consumer, so Task 9's script would flag them and Task 9's red-state proof would "pass" against these legitimate sites instead of a real fixture. Annotate both with `# fence-census: log-only, not a decision consumer` and have the census script honor that exact marker. **This annotation must land before the census script is written**, or the red-state proof is meaningless.

**Reprieve fencing ships log-only first (user decision) — for `_tier2_reprieve_signal` only.** Withdrawing reprieves there makes the no-progress killer strictly more willing to act, and a wrongly-killed live session is a visible failure. `_has_progress` is the opposite case (see the HIGH-site table in Problem): fencing it is strictly kill-reducing, so it ships enforcing in Task 2 with no shadow branch, no `PHASE A` marker, and no expectation of shadow-log hits. So `_tier2_reprieve_signal`'s fence — and nothing else — lands in two steps:

- **Phase A (log-only).** The fence is evaluated and its verdict logged — `[fence-shadow] would withdraw reprieve for {session_id}: exec_pid={pid} {reason} (recorded_ct={a}, live_ct={b}, granted_gate={g})` — while the *return value is unchanged*, so behavior is identical to today. Observe on this machine across at least one full canary cycle and tally the hits by `{reason}` label rather than by line count, because the three labels argue in different directions. `recycled` is the forever-reprieve the fence exists to withdraw and argues FOR enforcing. `unfenced-legacy` argues AGAINST, since unknown must not authorize a kill at a kill-increasing site. `dead-or-unreadable` usually means the process exited, which argues FOR, but does not prove it. Task 12 carries the authoritative reading protocol.
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
- [ ] `tests/unit/test_session_health_orphan_process_reap.py` (40+ sites) — UPDATE: keep the mocked-scan unit tests for gate logic, but they must no longer be the *only* coverage; Task 6 / Job 4 adds the unmocked integration counterpart.
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
**Mitigation:** Fall back to pid-only whenever either side recorded no `create_time`, so the change is strictly a *refinement* of a pid match, never a broadening. Task 6 / Job 4's integration test asserts the live-session-not-reaped case against real Redis rows with the scan unmocked — the assertion that has never existed.

### Risk 4: The defect list is large enough that one PR becomes unreviewable
**Impact:** Nine defects across migration, recovery, update, and UI in one diff invites a rubber-stamp review — the same failure mode that let #2516 ship with six unfenced consumers.
**Mitigation:** The user has decided to keep the full scope in one PR, so the mitigation is review ergonomics rather than splitting. The fence-application tasks share one mechanical shape, stated once in Technical Approach, so review is "is the census complete" rather than nine separate judgements — and Task 9's anti-criterion answers that question mechanically instead of by eye. **That mitigation only holds if the anti-criterion is a per-site adjacency check, not a count.** Critique was right that the plan previously left it unspecified while the only concrete shape shown was `grep -c 'fence_is_live' … | output > 11`; a threshold count is satisfied by any fenced site, so a future PR that adds one unfenced consumer while removing one guarded site leaves the count unchanged and green — exactly the #2516 failure mode this table diagnoses. Task 9 now specifies `tools/check_fence_census.py` with per-site, function-scoped adjacency and a `file:line` failure report, and Risk 4's mitigation stands on that, not on a grep count. Task 1 (migration) is independently verifiable and touches no shared code with Tasks 2-5, so it can be reviewed as a separable unit within the same PR. The retracted-claim Verification row guards against the corrected diagnosis silently reverting during build.

### Risk 5: Canary and fleet diverge because only one machine is observed
**Impact:** `Valor the Cowboy` is worker-only here (no bridge activated); the other fleet hosts run the bridge and their own project sets. A defect that only manifests under bridge traffic would pass the canary.
**Mitigation:** State it plainly rather than pretend otherwise — the canary covers worker, migration, reaper, and recovery, and does **not** cover bridge-side session intake. Task 12 records this limit explicitly so the fleet rollout is done with eyes open, and the rollout stays a human-gated step.

## Race Conditions

### Race 1: Migration scan vs. live worker index rebuild
**Location:** `scripts/migrate_strip_pid_fields.py:109` (`query.all()`) vs. `models/agent_session.py:2226-2251` (`repair_indexes` → popoto `rebuild_indexes` deleting `$Class:AgentSession`), invoked from the worker health check every tick.
**Trigger:** `/update` Step 3.6 runs migrations **before** the service restart (`scripts/update/run.py:1065-1067`), so the migration subprocess and the live worker are concurrent. If `query.all()` reads the class set mid-rebuild, it returns 0 rows with no exception — documented verbatim at `agent/index_drift.py:1-12` (#1720).
**Data prerequisite:** `$Class:AgentSession` fully populated before the scan reads it.
**State prerequisite:** no concurrent `rebuild_indexes()` in flight.
**Mitigation:** Cannot lock across processes cheaply here, so fail closed on the ambiguous observation: `total_records == 0` exits non-zero, the migration is not recorded, and the next `/update` retries. Convergence is guaranteed because the window is short and `/update` runs repeatedly. Additionally the migration uses `clean_indexes()` rather than any `rebuild_indexes()`-based path (hotfix `369d782c8`), so the migration is not itself a source of this window. **Status: this race is real and documented; nothing establishes that it ever fired here.**

### Race 2: Terminal-row rewrites vs. the migration's stated safety premise
**Location:** `scripts/migrate_strip_pid_fields.py:22-29` asserts *"The worker never writes terminal rows, so this can never clobber a concurrent write."*
**Trigger:** Observation contradicts the premise — every record's `updated_at`, terminal ones included, moved as a ~60ms batch between two reads minutes apart (12:59:17Z → 14:23:19Z), coinciding with the periodic `/update` job. Something does write terminal rows.
**Data prerequisite:** the migration's terminal-only exemption assumes terminal rows are quiescent.
**State prerequisite:** no concurrent writer of a terminal row during the delete+recreate.
**Mitigation:** The delete+recreate is a single MULTI/EXEC, so a record can never be lost regardless — the atomicity argument holds independently of the quiescence premise. But the premise itself is false and must stop being cited as a safety property. Task 1 identified the terminal-row writer (`agent.session_health.cleanup_corrupted_agent_sessions`) and corrects the docstring accordingly.

### Race 3: Fence stamp vs. orphan reaper (mid-spawn gap)
**Location:** `agent/session_runner/runner.py:665-677` (stamp happens *after* fork) vs. `agent/session_health.py:5748` reaper.
**Trigger:** Between `create_subprocess_exec` and the `save()`, the pid exists but resolves to no session. A reaper pass in that window sees an unowned `claude -p`.
**Data prerequisite:** the fence row must be persisted before the pid is reapable.
**State prerequisite:** the process must be parented by a live worker.
**Mitigation:** Already mitigated by the `ppid == 1` gate (a live worker is still the parent during the window), **except** on the `_parent_is_orphaned_shell_wrapper` branch (`:5514`). **Owning task: Task 6 / Job 4**, which gains a fifth assertion in `tests/integration/test_orphan_reap_forward_scan.py` for the mid-spawn state; if it proves reachable, the fix is an age floor on that branch rather than moving the stamp.

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

- [EXTERNAL] **Running `/update` on `Valor the Captain` and `Valor the Bald`.** The fleet rollout requires a human at those physical machines; the agent cannot reach them. Task 14 prepares and documents the rollout, and the human performs it after the canary gate clears.
- [EXTERNAL] **Observing bridge-side behavior under real Telegram traffic.** This machine is worker-only by design (no bridge activated), so canary Job 5's SDLC-render check and any bridge-intake path can only be exercised on the Pro. Recorded as a stated coverage limit rather than a silent gap.
- [EXTERNAL] **Worktree hygiene.** The pre-cutover worktrees that could have re-added stale hash fields are gone (worktree GC). The standing rule is unchanged: pruning worktrees is a human workspace-hygiene call, not this plan's job. If a pre-cutover checkout reappears against the same `localhost:6379`, a stale-field reappearance is an expected consequence of that checkout, not evidence of a migration defect.
- [SEPARATE-SLUG #2524] **Applying the zero-record guard and the `clean_indexes()` swap to the sibling strip migrations** (`scripts/migrate_strip_pty_fields.py:161`, `scripts/migrate_schema_diet_fields.py:230`). Both share D1/D6 by template inheritance, but both are already recorded complete on every machine, so edits here would be inert code changes with no runtime effect — they need their own rename-and-rerun decision, which is a separate judgement about whether their stale fields are worth reclaiming at all.
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
- [x] Update `docs/features/dev-7f56f953.md` (the fenced-execution-record doc) with: the canonical legacy-row rule ("unreadable `create_time` on either side means unknown; unknown never authorizes a kill"), the full consumer census, and the `find_live_session_by_pid` signature change.
- [x] Rename `docs/features/dev-7f56f953.md` → `docs/features/agent-session-fenced-execution-record.md`. It is currently named after the throwaway branch slug `session/dev-7f56f953`, which tells a future reader nothing. Update the `docs/features/README.md:13` link and any inbound references.
- [x] Add a "Canary findings" section recording what the post-merge validation found, so the next milestone's cutover inherits the checklist rather than rediscovering it: the six unfenced consumers, the two hotfixes (`6aa4403f3` ruff gate, `369d782c8` `clean_indexes()`), the #2536 follow-up, and the lesson that a migration whose output is discarded has an unfalsifiable success mode.
- [x] Document the migration-observability fix (`run_pending_migrations` now logs stdout) wherever the update flow is described, since it changes what operators can expect to find in `logs/update.log`.
- [x] Update `docs/features/README.md:13` row text to reflect fenced ownership resolution and the fenced update-time cleanup.

### Inline Documentation
- [x] `agent/pid_fence.py` module docstring — write down the legacy-row rule so the next consumer inherits it rather than inventing a fourth behavior.
- [x] `agent/session_health.py:3941` — replace the "macOS recycles PIDs in ~5 minutes" probabilistic comment with the actual fence guarantee once `_pending_sigkill` carries `create_time`.
- [x] `scripts/migrate_strip_pid_fields.py:26-29` — correct the docstring's false claim that deferred rows age out via `Meta.ttl`; `is_ledger=True` rows are re-saved every ~30s and their TTL is refreshed indefinitely (D9).
- [x] `ui/static/style.css:296` — update the ghost-badge comment, which still describes an `os.kill(pid, 0)` probe.

### Test Documentation
- [x] Add the new test files to the `tests/README.md` index table with feature markers.

## Success Criteria

- [x] `migrate_strip_pid_fields.py` logs to stdout (`stream=sys.stdout`), `_migrate_strip_pid_fields` captures **both** streams, and `logs/update.log` shows a **non-empty** record of what `strip_pid_fields_v2` did — asserted by a test on the captured text, not by a `grep` for `result.stdout`.
- [ ] `strip_pid_fields_v2` registered and run on the canary, with its output recorded in this plan as provenance that the capture path works. A clean no-op is the expected result on this machine.
- [x] The migration's trailing index call is `clean_indexes()` (main's `369d782c8`), not `rebuild_indexes()` and not `repair_indexes()`.
- [x] The migration docstring's "the worker never writes terminal rows" claim is corrected, and the actual terminal-row writer is named.
- [x] Zero-record scan exits non-zero and is NOT recorded complete, labelled in-code as insurance rather than a fix, with the unbounded-retry acceptance noted in the same comment.
- [x] Every consumer of a fenced pid that drives a kill, reprieve, or ownership claim calls `fence_is_live` — enforced by `tools/check_fence_census.py`'s per-site, function-scoped adjacency check, never by an occurrence count. The two log-only reads at `:3198`/`:3216` carry the `fence-census: log-only` marker and the script honors it.
- [x] `_pending_sigkill` carries `create_time` and re-verifies at drain (D2).
- [x] `_has_progress` and `_owned_task_hang_check` treat a recycled pid as absent (D3). `_has_progress` shipped **enforcing, unshadowed** — a regression test pins that a recycled pid probing as hung still yields `True` when the sticky fields show progress, so the corrected direction cannot silently revert.
- [ ] `_tier2_reprieve_signal` — and only it — shipped log-only (Phase A), was observed on this machine, and only then enforced (Phase B); `:1854` no longer reprieves on `pid is not None` alone. No `PHASE A` marker or fence config flag survives.
- [x] `find_live_session_by_pid` accepts `create_time`, requires a match when both sides have one, logs multi-match, and routes through `_filter_hydrated_sessions` (D4).
- [x] `/update`'s `_cleanup_stale_sessions` skips fence-live sessions and no longer claims "no live process" without checking (D5).
- [x] Legacy-row policy is consistent across `:1247`, `:2930`, `:4325` and documented in `agent/pid_fence.py` (D8).
- [x] `PipelineProgress` carries `pid_create_time`; the dashboard reports a recycled pid as not-live (D7).
- [x] The three defect-regression tests exist and exercise real paths: runner-path stamping, the recycled-fence sweep branch, and the unmocked forward-scan no-over-reap. (The original "promote 2-3 canary jobs into permanent tests" goal is already met by the coverage merged with PR #2516 — see Post-Cutover Re-Scope.)
- [x] The three terminal-owner tests asserting an impossible state are deleted or re-purposed.
- [ ] **Job 2 and Job 5** driven on the canary machine and clean; results recorded in this plan. Migration dry-run counts are recorded, not gated.
- [ ] **[GATED on human sign-off]** The Phase A shadow log is observed across a qualifying window (≥3 health-check ticks, ≥900s) and reviewed, and `strip_pid_fields_v2 --apply` is run against live Redis with its output recorded. Neither begins on agent judgement alone.
- [x] Tests pass (`/do-test` via `scripts/pytest-clean.sh`)
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
  - Role: the defect-regression tests and the Test Impact dispositions
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
- **Correct the terminal-row docstring.** The migration's docstring (`:22-29`) asserts "the worker never writes terminal rows"; that is false (Race 2). The writer is `agent.session_health.cleanup_corrupted_agent_sessions`, which re-saves every hydrated record — terminal ones included — as its no-op-save corruption probe, restamping `updated_at`. State the real safety property instead: the single MULTI/EXEC delete+recreate (atomicity), not quiescence.
- Register `strip_pid_fields_v2` in `scripts/update/migrations.py` pointing at the same script, positioned after `strip_pid_fields` and before `purge_phantom_agent_sessions`. Its value is **provenance for future cutovers**, not a retroactive diagnosis. On this machine it is a fast no-op — a dry run reports `{'total_records': 15, 'clean': 15, 'stripped': 0, 'deferred_non_terminal': 0, 'errors': 0}`.
- Add a `total_records == 0` guard to `migrate()` — exit non-zero with a distinct message so completion is not recorded and the next `/update` retries. **Label it in the code comment as insurance against the #1720 class-set window, not as a fix for a proven cause**, and record in the same comment that on a legitimately empty keyspace this fails on every `/update` indefinitely by accepted design (see the No-Gos entry), so an operator does not read the recurring `FAIL:` as a regression.
- **Index sweep: take main's `clean_indexes()`, not the branch's `repair_indexes()`.** Hotfix `369d782c8` already landed this on main. `repair_indexes()` calls `rebuild_indexes()` internally and hits the #2536 phantom-metadata failure plus the #1720 class-set window; `clean_indexes()` is the production-safe orphan sweep. Reverting the branch to main's version is the only work left here.
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
- **`_tier2_reprieve_signal` (`:1832-1854`) ships Phase A only in this task** — evaluate the fence, log `[fence-shadow] would withdraw reprieve for {session_id}: exec_pid={pid} {reason} (recorded_ct={a}, live_ct={b}, granted_gate={g})`, and **return the unchanged value**. Behavior is identical to today. Mark the block `# PHASE A — DELETE IN PHASE B`. No config flag, no env var, no `if ENFORCE:` branch.
- **`{reason}` names WHY the fence said "not ours" — it is evidence, not decoration** (#2518 review nit 1). `fence_is_live` returns the same `False` for a recycled pid, a dead pid, and an unfenced legacy row, and this log is the sole artifact the human reads to authorize Phase B, so a single `recycled` label would tell the reviewer the opposite of the truth for legacy rows. `_shadow_mismatch_reason` emits three labels: `recycled` (create_time recorded and mismatched — a proven recycle, **argues FOR** enforcing), `unfenced-legacy` (nothing recorded, identity unknown — **argues AGAINST**, since unknown must not authorize a kill at a kill-increasing site), and `dead-or-unreadable` (recorded but the live reading failed; usually the process exited, which **argues FOR**, but does not prove it).
- **One `create_time` read per evaluation** (#2518 re-review nit 1). `_tier2_reprieve_signal` binds `live_ct = proc_create_time(pid)` at the decision site, derives `_shadow_fence_mismatch` from it with `create_times_match`, and passes that same reading into `_log_shadow_reprieve_withdrawal`. Deriving the label from a second, later read mislabels one-directionally — a decision-time `None` plus a reassigned pid prints `recycled` (the strongest pro-enforcement label) for a fence that was merely unreadable — and it makes an agreeing re-read observable, which is not noise but a false negative of the gating predicate on a live, owned, progressing session, i.e. the strongest evidence AGAINST enforcing. One read removes both, so there is no fourth label. Census-safe: `create_times_match` is in `GUARD_FUNCS` and the scope still reads `.get("create_time")`; `create_time_fn` is untouched.
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
- `ui/data/sdlc.py` — add `pid_create_time` to `PipelineProgress` (`:356`), thread it through `_session_to_pipeline` (`:1065`, which already reads `_fence` and discards `create_time`), and make `_check_process_alive` (`:38-74`) fence-aware with an explicit "unknown" for legacy rows.
- `ui/app.py:757-766` — carry the new field into the dashboard JSON.
- Update `ui/static/style.css:296`'s ghost-badge comment.

### 6. Defect-regression tests

**Naming note:** these were originally framed as "promoted canary jobs". That goal is already met by the regression coverage merged with PR #2516 (see Post-Cutover Re-Scope). What remains here are *defect* regressions for the six unfenced consumers this plan fixes. The Job N labels are kept only as stable cross-reference anchors.
- **Task ID**: build-regression-tests
- **Depends On**: build-migration, build-legacy-policy, build-fenced-scan, build-cutover-ui
- **Validates**: all new and updated test files
- **Informed By**: spike-1 (Job 1 is EXTEND), spike-4 (use `proc_create_time`, not `create_time_fn`)
- **Assigned To**: fence-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- **Job 1 — runner-path stamping.** Extend `tests/unit/session_runner/test_runner_preempt.py`: patch `agent.pid_fence.proc_create_time`, drive a turn, assert the full record — `exec_pid`, **non-None `pid_create_time`**, `exec_cwd`, `exec_harness == "claude"`, one `spawn_history` entry with `generation`, and the 5-field `save(update_fields=...)`. Add a two-turn case asserting the re-stamp. Add `test_build_driver_wires_on_spawn_adapter` to `tests/unit/session_runner/test_runner_liveness.py` mirroring its `on_stdout_event` sibling at `:266`. Note `_on_turn_spawn` early-returns when `_current_handle is None` (`runner.py:640`) — a naive wiring test passes vacuously.
- **Job 3 — fence-branch sweep.** Parameterize the `tests/unit/test_worker_session_sweep.py` helper so `create_time` is settable, then cover: dead fence → swept; **recycled fence (alive pid, mismatched `create_time`) → swept** (the branch no test reaches today); matching fence → skipped. Assert status-keyed scoping via `query.filter.assert_called_once_with(status="running")` and exactly-once via a second pass finding the row terminal.
- **Job 4 — unmocked forward scan.** Create `tests/integration/test_orphan_reap_forward_scan.py` with real Redis rows and `find_live_session_by_pid` **not mocked**: live running session with a fresh heartbeat and a stamped fence is **not** reaped (the canary assertion); orphan with no row is reaped; duplicate fence pid across a dormant and a running row resolves to the live one regardless of iteration order; a raising status cohort does not unprotect live sessions; and — **owning Race 3** — an `AgentSession` row constructed *before* `stamp_execution_spawn` has run (fence dict absent or empty), whose child process's parent is not pid 1, is **not** reaped inside the stamp window. If that fifth assertion fails, the window is reachable and the fix is an age floor on the `_parent_is_orphaned_shell_wrapper` branch (`agent/session_health.py:5514`), gated by Task 3's "unknown never authorizes a kill" rule. Do not move the stamp.
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
- **Gated on human sign-off to deploy this branch and run live-mutating steps.** Do not begin the `strip_pid_fields_v2 --apply` run or the full-cycle shadow-log observation on agent judgement alone.
- **Prerequisite:** the machine gate is the ownership check in Prerequisites (`projects.json` `valor.machine` == local `ComputerName`). It passes on this host. See the machine-gate ruling for why the old display-name literal was wrong.
- **Scope is Jobs 2 and 5 only.** Jobs 1, 3, 4 and 6 were verified in production post-cutover and are recorded in "Post-Cutover Re-Scope" — do not re-run them here.

**In build scope now: Job 2, Job 5.** These drive `test-`/`dbg-`-prefixed sessions and mutate nothing outside their own scoped rows.

  - **Job 2 — multi-turn steered session.** Drive a real multi-turn session with steering; assert the fence persists and re-stamps across turns and that the steering drain is unaffected.
  - **Job 5 — short SDLC job.** Drive a short SDLC job; assert the lifecycle renders correctly and the at-rest owed-communication check produces no false positive.
  - Use `test-`/`dbg-`-prefixed `project_key`s and delete them via the ORM afterward, scoped by that prefix.

  **Blocked 2026-08-05 — same gate, discovered during build.** Both jobs are *scoped*
  harmlessly, but neither is *executable* without the gated act: the live worker
  (`com.valor.worker`, PID 17018) runs the main checkout at main's SHA, so a session
  driven today exercises `main`, not this branch, and proves nothing about the fence
  changes. Making Jobs 2 and 5 meaningful requires restarting the live worker onto this
  unmerged branch — a deploy of unreviewed code, i.e. the very act the Build Record and
  the gate bullet above carve out. Running a second worker from the worktree against the
  same Redis is not an alternative; it is split-brain contention.

  **What is covered deterministically instead** (does not substitute for the live run,
  but bounds what the live run is still needed for): Job 2's fence-persists-and-re-stamps
  half is pinned by `tests/unit/session_runner/test_runner_preempt.py::
  test_second_turn_restamps_the_fence_and_appends_to_history`, and the steering drain is
  green across `test_steering.py` / `test_steering_mechanism.py`. What remains live-only
  is steering drain under real multi-turn traffic and Job 5's SDLC lifecycle render.
  **These three bullets move behind the human sign-off gate with the other two.**

**Deploy-and-observe, gated on human sign-off.** Both bullets below require this branch's unreviewed code running in the live worker and/or mutating live Redis. A builder must stop here and report. **Sign-off was given and both bullets have been executed — see the Results block above; do not re-run them.**

- **[GATED]** Observe the Phase A `[fence-shadow]` log across at least one full canary cycle. **"One full canary cycle" is defined in ticks:** the reprieve path is evaluated once per `AGENT_SESSION_HEALTH_CHECK_INTERVAL` (300s, `agent/session_health.py:442`), so the observation window is **at least 3 consecutive health-check ticks (≥900s) of live worker traffic with at least one non-terminal session resident for the whole window**. A window shorter than one tick cannot have evaluated the branch at all and does not satisfy this bullet. **The shadow log covers `_tier2_reprieve_signal` only** — `_has_progress` ships enforcing in Task 2 because its fencing is strictly kill-reducing, so it emits nothing here by design; do not read its absence as a gap. Record every shadow hit: session, `exec_pid`, recorded vs live `create_time`, and the line's own `{reason}` label. **Tally by label, not by line count** — the labels argue in opposite directions. `recycled` hits are the forever-reprieve the fence exists to withdraw and argue FOR Phase B; `unfenced-legacy` hits are rows whose identity was never recorded, and withdrawing there would be unknown authorizing a kill at a kill-increasing site, so they argue AGAINST; `dead-or-unreadable` is a recorded fence whose live reading failed, which usually means the process exited (argues FOR) but does not prove it. **Those three labels are the whole vocabulary** — the decision site reads the live `create_time` once and both the mismatch and the label come from that one reading, so a label always reports what the decision acted on and no line can be dismissed as a re-read artifact. Zero hits across a qualifying window is a valid and informative result — it means no live session is currently relying on a fence-mismatch reprieve.
- **[GATED]** Apply `strip_pid_fields_v2` (`--apply`, against live Redis) and record its now-captured output as provenance that the capture path works end to end. On this machine it is expected to be a clean no-op; record the counts, do not gate on them.
- Record results in this plan document, including the stated coverage limit: this host is worker-only (no bridge activated), so bridge-side session intake is not exercised here.
- **This task is the gate. Neither Phase B nor fleet rollout begins until it passes and the results are reviewed by the user.**

**Results (2026-08-05, human-authorized live run).**

- **Human sign-off.** Given explicitly in-session in direct response to a question naming the risk (restarting the production worker onto unmerged branch code, and a live `--apply` migration run). Confirmed real, not inferred from automation.
- **Machine gate.** Re-checked before deploying: `projects.json` `valor.machine` == `Valor the Cowboy` == local `ComputerName`. Match.
- **Deploy.** Pre-restart: worker PID 17018, SHA `46ef3936f`. Main checkout was clean; `session/durability-m1-fence-canary` was already checked out in `.worktrees/durability-m1-fence-canary`, so the main checkout was deployed via `git checkout --detach 59ac3040b` (not a branch checkout) at 2026-08-05 10:01:05 UTC, then `scripts/valor-service.sh restart`. Post-restart: worker PID 81422, boot beacon `worker_boot_sha @ 59ac3040b` (confirmed in `logs/worker.log`), bridge log showed `Connected to Telegram`.
- **Job 2 (multi-turn steered session).** PASS. Created a `test-fence-canary-job2b`-prefixed eng session directly via `agent.agent_session_queue._push_agent_session` (the `valor-session` CLI's `--project-key` requires an entry in `projects.json`, which a disposable canary key deliberately isn't; the CLI's own `_resolve_project_working_directory("valor")` supplied the real `working_dir`/`project_config`, only the label was swapped). Prompted it to ask a clarifying question and wait; pushed a real steering message (`"blue"`) via `agent.steering.push_steering_message` while it was mid-run. Evidence, read directly from the session's own Claude Code transcript JSONL: assistant asked `"Red or blue?"`, then a `user` turn containing exactly `"blue"` (the injected steering text) appears, i.e. the steering drain delivered it as a real turn under live multi-turn traffic. The fence re-stamped between turns: turn 1 `exec_pid=83382`, `create_time=1785924455.66`, `generation=1`; turn 2 `exec_pid=83575`, `create_time=1785924497.05`, `generation=2`. Final `turn_count=5`, status `completed`.
- **Job 5 (short SDLC job).** PASS with a noted limit. Created a `test-fence-canary-job5`-prefixed eng session with `classification_type=ClassificationType.SDLC` forced explicitly (no real GitHub issue attached). `stage_states` initialized correctly by `PipelineStateMachine` (`ISSUE=ready`, all other stages `pending`) — lifecycle renders correctly. Completed in under a minute (`turn_count=2`). The at-rest owed-communication check (`agent/session_health.py:832`) only scans `NON_TERMINAL_STATUSES` sessions at each 300s tick; this session was non-terminal for well under one tick interval, so the check had little-to-no opportunity to evaluate it before it went terminal. No `[at-rest-owed]` log line fired (correct: no false positive), but this is a weak exercise of that specific check given how fast the job finished — noted rather than papered over.
- **Phase A `[fence-shadow]` shadow window.** Qualifying window observed: 4 consecutive health-check ticks post-deploy at 10:01:17, 10:06:18, 10:11:18, 10:16:18 UTC — span 901s (≥ the required 900s / 3-tick minimum). Non-terminal session `sdlc-local-2518` (this very canary task's own orchestrating session, created 05:26:16 UTC) was resident for the entire window (confirmed via `updated_at` timestamps bracketing the window). `grep -c "fence-shadow" logs/worker.log` over the window: **0 hits**. This is the expected, correct null result per the plan: no live session on this machine is currently relying on a fence-mismatch reprieve at `_tier2_reprieve_signal`. `_has_progress` emits nothing here by design (ships enforcing, not shadowed) — its silence is not a gap.
- **`strip_pid_fields_v2 --apply`.** Exit 0. stdout (196 bytes, non-empty — confirms the `stream=sys.stdout` capture fix works end to end): `Stats: {'total_records': 18, 'clean': 18, 'stripped': 0, 'deferred_non_terminal': 0, 'errors': 0}`. stderr: empty. Clean no-op as expected; not gated on the counts.
- **Cleanup.** All three `test-fence-canary-*` prefixed AgentSessions (Job 2 first attempt, Job 2b, Job 5) deleted via `AgentSession.delete()`, scoped by their `test-` project_key prefix. Their auto-provisioned `dev-*` worktrees and `session/dev-*` branches were already auto-cleaned on session completion; no orphans remained.
- **Rollback.** Per the plan's default, rolled back to `main` rather than leaving production on unmerged detached-HEAD code: `git -C /Users/valorengels/src/ai checkout main` + `scripts/valor-service.sh restart` at 10:18:06 UTC. Post-rollback: worker PID 87878, boot beacon `worker_boot_sha @ b0acfe188`, bridge log showed `Connected to Telegram`, `git branch --show-current` = `main`.
- **Final resting state.** Branch `main` @ `b0acfe188`, worker PID 87878 running main's code, bridge and worker both reconnected/healthy.
- **Coverage limit (verbatim per plan instruction).** This host is worker-only for the purposes of this canary: the bridge process does run here for real Telegram traffic, but Jobs 2 and 5 were driven by creating `AgentSession` rows directly (bypassing bridge message intake), so **bridge-side session intake is not exercised by this canary run**.
- **Deviations named explicitly.** (1) Could not use the `valor-session` CLI as originally envisioned for `test-`/`dbg-`-prefixed sessions, because its `--project-key` resolution hard-requires the key to exist in `projects.json`; used the lower-level `_push_agent_session` call directly instead, carrying the real `valor` project's `working_dir`/`project_config` so execution behaved identically to a real session, with only the `project_key` label swapped for scoping/cleanup. (2) Job 2's first attempt (`test-fence-canary-job2`, no steering) completed in a single turn before steering could be injected — discarded and redone as `test-fence-canary-job2b` with a task designed to pause for an answer; both are recorded here for completeness, and both were cleaned up.

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
- Write the rollout steps for `Valor the Captain` and `Valor the Bald` into this plan: what to run, what to check afterward (`strip_pid_fields_v2` recorded, dry run clean, no session disturbed), and how to roll back.
- The rollout itself is [EXTERNAL] — a human runs `/update` on that machine.

### 15. [OPTIONAL] End-to-end fence-stamping integration test
- **Task ID**: build-e2e-stamping
- **Depends On**: build-regression-tests
- **Validates**: tests/integration/test_fence_stamping_e2e.py (create)
- **Informed By**: spike-1 (the production chain fires; current coverage is unit-level `FakeSession`)
- **Assigned To**: fence-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- **Optional.** The plan's Success Criteria do not depend on it; it closes the gap between
  Job 1's `FakeSession` assertions and the real runner path. Skipping it is a legitimate
  outcome — this task exists so that doing it has a place to be recorded.
- Create `tests/integration/test_fence_stamping_e2e.py` driving a real runner turn end to
  end and asserting the persisted record on a real Redis row: `exec_pid`, non-None
  `pid_create_time`, `exec_cwd`, `exec_harness`, and one `spawn_history` entry.
- Use a `test-`-prefixed `project_key` and delete the row via the ORM afterward, scoped by
  that prefix.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `scripts/pytest-clean.sh tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Migration logs to stdout | `grep -c 'stream=sys.stdout' scripts/migrate_strip_pid_fields.py` | output > 0 |
| Migration output is captured **non-empty** | `scripts/pytest-clean.sh tests/unit/test_migrate_strip_pid_fields.py -k TestOutputIsCapturedNonEmpty -q` | exit code 0 — runs the migration as a subprocess and asserts the captured text matches `Stats: {'total_records'`. A `grep -c 'result.stdout'` row cannot distinguish "captured" from "captured empty" and is deliberately not used. |
| Migration re-registered | `grep -c 'strip_pid_fields_v2' scripts/update/migrations.py` | output > 0 |
| Zero-record guard present | `grep -c 'total_records.*==.*0' scripts/migrate_strip_pid_fields.py` | output > 0 |
| Migration uses the production-safe index sweep | `grep -c 'clean_indexes()' scripts/migrate_strip_pid_fields.py` | output > 0 |
| No rebuild-based index path in migration | `grep -rniE 'rebuild_indexes\|repair_indexes' scripts/migrate_strip_pid_fields.py` | exit code 1. Covers both spellings — `repair_indexes()` calls `rebuild_indexes()` internally, so a bare `rebuild_indexes` grep cannot catch it. |
| Canary machine gate passes on this host | the ownership check in Prerequisites | exit code 0 |
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
| [OPTIONAL, Task 15] End-to-end fence stamping | `scripts/pytest-clean.sh tests/integration/test_fence_stamping_e2e.py -q` | exit code 0 **if the file exists**; Task 15 is optional, so an absent file is not a failure of this plan. |

## Critique Results

**Re-critique of the revised plan, 2026-08-05, against plan commit `d19f6d6af`.** Depth: FULL (`appetite: Large` forces the 3-critic roster). Critics: Risk & Robustness, Scope & Value, History & Consistency, plus driver structural checks and independent source verification. Roster gate: 3/3 complete, 3/3 grounded.

The prior pass's verdict was stale: it ran against the pre-revision text, and the revision (`d19f6d6af`) reconciled the branch/main fork, re-scoped remaining canary work to Jobs 2 and 5, and ruled on the machine gate. This pass re-verified those three changes at the source and found them correct:

- **Index sweep.** The branch's `scripts/migrate_strip_pid_fields.py` still calls `repair_indexes()`; main (hotfix `369d782c8`) calls `clean_indexes()`. Main is right — `repair_indexes()` calls `rebuild_indexes()` internally (`models/agent_session.py:2226`). Task 1 correctly directs the branch to take main's version, and the revised Verification row covers both spellings (the old single-spelling row passed under either). Confirmed the branch is **not** rebased onto `6aa4403f3` and does **not** contain `369d782c8` — both remain open PATCH items, correctly recorded.
- **Machine gate.** The ownership check now in Prerequisites was executed by the driver and **passes**: `projects.json` `valor.machine` == local `ComputerName` == `Valor the Cowboy`. The prior literal was fictional. Not re-raised.
- **Remaining scope.** Jobs 1/3/4/6 are correctly excluded; the "promote 2-3 canary jobs into permanent tests" goal is met by merged PR #2516.

**Consumer census re-run independently (hard-STOP check): no seventh unfenced consumer.** Every non-test `live_fence` read on main maps to a site already in the plan's Data Flow table — `agent/session_health.py` 113, 785, 1219, 1719, 1832, 2927, 3198 (log-only), 3216 (log-only), 4322; `agent/agent_session_queue.py:1991`; `models/agent_session.py:1273`; `ui/data/sdlc.py:1065`.

This pass raises **3 findings, no blockers** — one CONCERN converged on by two critics, and three NITs. None touches a design decision or a code-change specification.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|----------|---------|---------|--------------|---------------------|
| CONCERN | Risk & Robustness, Scope & Value | Task 12 has no human-authorization gate, though it is the task that performs the acts the Build Record says a build stage must not do. The Build Record ("Where the line was drawn") carves out the `strip_pid_fields_v2 --apply` run against live Redis and the full-cycle Phase A shadow-log observation as "deploy-and-observe acts on unreviewed code". Task 13 downstream carries an explicit "Do not start this task on agent judgement alone". Task 12 carries none: its only dependency is `validate-all`, a test/doc-completeness gate. Its bullets present Job 2, Job 5, the shadow-log observation and the live `--apply` as one undifferentiated list, so a builder resuming Task 12 has no structural marker for which bullets are executable now and which are not. | pending | Insert a bullet immediately after Task 12's `**Depends On**: validate-all` line, mirroring Task 13's phrasing verbatim so a validator scanning for stop conditions finds the same pattern at both sites: `**Gated on human sign-off to deploy this branch and run live-mutating steps.** Do not begin the strip_pid_fields_v2 --apply run or the full-cycle shadow-log observation on agent judgement alone.` Then split the bullet list under two sub-headers — "In build scope now: Job 2, Job 5" and "Deploy-and-observe, gated: --apply run, Phase A full-cycle observation" — and narrow the line-643 Success Criterion to the Job 2 / Job 5 portion, carrying the shadow-log-review clause as a separate explicitly-gated bullet. **APPLIED 2026-08-05** exactly as specified: Task 12 carries the gate sentence, the bullets are split under the two sub-headers with `[GATED]` markers, and the Success Criterion is split into a Job 2 / Job 5 line plus a separate `[GATED on human sign-off]` line. |
| NIT | Risk & Robustness | Task 12's "Observe the Phase A `[fence-shadow]` log across at least one full canary cycle" leaves the window undefined — no duration, no tick count, no reference to `AGENT_SESSION_HEALTH_CHECK_INTERVAL = 300` (`agent/session_health.py:442`) — so the bullet can be satisfied by a window shorter than a single reprieve-check tick. | applied | Task 12's `[GATED]` shadow-log bullet now defines the window in ticks: ≥3 consecutive health-check ticks (≥900s) with a non-terminal session resident throughout, citing `AGENT_SESSION_HEALTH_CHECK_INTERVAL` at `agent/session_health.py:442`. |
| NIT | Scope & Value | The "optional" end-to-end fence-stamping integration test appears only as prose under Post-Cutover Re-Scope. Unlike every other deliverable it has no Task ID, owner, or Verification row, so a builder who does it has nowhere to record it. | applied | Added as **Task 15 `build-e2e-stamping`** (explicitly `[OPTIONAL]`, depends on `build-regression-tests`, owned by `fence-test-engineer`) with a matching `[OPTIONAL, Task 15]` Verification row. |
| NIT | Driver | Two UI line citations drifted. Data Flow step 7 cites `ui/data/sdlc.py:1051` for the bare `os.kill(pid, 0)` probe, but `_check_process_alive` is at `:38` (Task 5 cites `:38-74` correctly); Task 5 cites the `_session_to_pipeline` fence read at `:1034-1039`, actual is `:1065`. | applied | Both corrected, plus the same `:1051` drift in the Freshness Check's re-verified-references list. |

**Verdict: READY TO BUILD (with concerns).** No blockers. The remaining build work — Jobs 2 and 5, the `clean_indexes()` reconciliation, and the rebase onto `6aa4403f3` — is unblocked.

---

## Decisions Recorded

All three questions from the plan draft were answered by the user on 2026-08-04. They are settled inputs to critique and build, not open items.

1. **Scope: all nine defects in one PR.** The user chose the full scope over splitting the migration item out as a standalone hotfix. Critique should weigh the migration item on its corrected premise (LOW severity, root cause open, the real defect being discarded stdout) rather than on the retracted "self-certified" framing — but the decision to keep it in this plan is made.

2. **Reprieve: log-only canary cycle before enforcing.** `_tier2_reprieve_signal` fencing ships as Phase A (shadow log, unchanged behavior) in Task 2, is observed on this machine in Task 12, and is enforced in Task 13 only after the user reviews the shadow log. Built as a removable `# PHASE A — DELETE IN PHASE B` block with a Verification row asserting its removal — explicitly **not** a config flag, which would rot into a permanent fork.

3. **Worktrees: leave them alone.** No pruning, no removal. The five pre-cutover checkouts are recorded as a known, accepted condition in the No-Gos; a stale-field reappearance while they exist is an expected outcome, not evidence of a migration defect.

**Standing constraint (user):** ample testing and hotfixing happen on this machine before any fleet rollout. Nothing rolls to another machine until the canary results are reviewed. Task 12 is the gate; Tasks 13 and 14 sit behind it. What this worker-only machine cannot exercise — bridge intake and Job 5's SDLC render — is stated plainly in the No-Gos and in Task 12's output rather than papered over.

## Open Questions

**None.** The one prior open question — what exactly the first `strip_pid_fields` run did — is closed by the keyspace being clean and the record population being gone. Task 1 buys provenance for future cutovers, not a retroactive answer; a builder should not go looking for a discriminator that no longer has anything to discriminate.
