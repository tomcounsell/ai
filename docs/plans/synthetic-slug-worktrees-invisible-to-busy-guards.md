---
status: Planning
type: bug
appetite: Small
owner: Valor Engels
created: 2026-09-07
tracking: https://github.com/tomcounsell/ai/issues/3176
last_comment_id:
---

# Synthetic-slug worktrees are invisible to the busy guard

## Problem

A slugless eng session is given a synthetic slug (`dev-{agent_session_id[:8]}`, issue #1272) in
`_execute_agent_session` and a worktree is provisioned for it a few lines later. Both the slug and
the resolved worktree path are **local variables**.

**Current behavior:** the session runs inside `.worktrees/dev-{aid8}/` while its stored row says
`slug=None` and `working_dir=<main checkout>`. `_scan_worktree_sessions()`
(`agent/worktree_manager.py`) matches a lane by the stored `working_dir` and nothing else, so it
never matches. `worktree_busy_check()`, `worktree_busy_probe()`, and `worktree_busy_probe_many()`
all report a `dev-*` lane **clear** while a session is live inside it. Only
`_worktree_has_live_process()` — which asks the OS, not Redis — sees the lane.

**Desired outcome:** the session-table busy predicate matches a lane resolved at execution time the
same way it matches one resolved at enqueue time, so the OS-process scan goes back to being a
backstop rather than the only working guard.

**How this plan gets there (changed by the critique).** The row *already* records the resolved
lane, in a field whose lifecycle is correct for it: `AgentSession.exec_cwd`, stamped on every
harness spawn by `stamp_execution_spawn(..., cwd=self._working_dir, ...)` and reset on every
continuation because it is listed in `_EXECUTION_FENCE_RESET_FIELDS`. The fix is therefore to
**teach the busy scan to read `exec_cwd` alongside `working_dir`**, and to have the executor stamp
`exec_cwd` a few lines earlier than the runner does so the lane is visible before the harness
launches. The earlier draft persisted the lane into `working_dir` instead; that field is
enqueue-scoped, survives continuations, and is read by five other consumers, so a persisted lane
path outlived the lane it named. See spike-7 and spike-8.

## Freshness Check

**Baseline commit:** `de229ee469fe7b2b176b371b6cd19646fce401a0`
**Issue filed at:** 2026-09-05T12:54:14Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `agent/session_executor.py:1314` — synthesizes `dev-{aid[:8]}` into a local — **still holds, exact line.**
- `agent/session_executor.py:1377-1378` — `get_or_create_worktree` + local `working_dir` rebind — **still holds, exact lines.**
- `agent/session_runner/role_driver.py:198` — `self.working_dir = working_dir` — **still holds**; it is a driver constructor attribute, not a session row.
- No production code writes `AgentSession.working_dir` after creation — **re-verified**: `grep -rn '\.working_dir *=' --include='*.py'` over non-test, non-venv code returns only `role_driver.py:198`.
- `agent/worktree_manager.py:433` (`_scan_worktree_sessions`) — **drifted to `:503`.**
- `agent/worktree_manager.py:483-513` (the `working_dir` match) — **drifted to `:579-614`.** The predicate is unchanged: it still compares only the stored `working_dir`.
- `tools/disk_reclaim.py:407/:415` (process scan before the session probe) — **drifted to `:417`/`:429`**, and the single-slug `worktree_busy_probe` re-probe is now at `:467`. Both still sit behind `_worktree_has_live_process`.

**Cited sibling issues/PRs re-checked:**
- **#2712** — closed 2026-09-05T17:52Z by **PR #3179** ("Busy-guard scan: narrow the query, unamplify the sweep"), which merged *after* this issue was filed. It replaced `query.all()` with `query.filter(status__in=NON_TERMINAL_STATUSES)`, added `_fetch_live_sessions()` and `worktree_busy_probe_many()`, and is the sole cause of the line drift above. It did not touch the match predicate, so this blind spot is unchanged — exactly as #3176 predicted.
- **#2305** — closed 2026-07-27. `_worktree_has_live_process` (defect 3) is present and is still the only guard that sees a synthetic lane.
- **#1272** — closed 2026-05-05. Introduced the synthetic slug; its cleanup hook at `agent/session_executor.py:2744-2795` is a direct dependency of this work.

**Commits on main since issue was filed (touching referenced files):**
- `9c75c2e08` Busy-guard scan: narrow the query, unamplify the sweep (#3179) — **line drift only; root cause unchanged.**
- `2ec83a447` Auto-preserve refuses to commit a wipe (#3167) (#3188) — irrelevant to the match predicate.
- `988fac09a` Make Read-the-Room unconditional; repair the SDLC bypass (#3174) — irrelevant.

**Active plans in `docs/plans/` overlapping this area:** none. `grep -l 'worktree_busy\|_scan_worktree_sessions\|synthetic.slug' docs/plans/*.md` returns nothing.

**Bug still reproducible:** yes, by code path. The synthesis at `:1310-1318` and the rebind at
`:1377-1378` both assign to locals; the only production write site for `AgentSession.working_dir`
remains "never".

## Prior Art

- **#1272 / `docs/plans/parallel-session-checkout-guard.md`**: introduced the synthetic slug as Alternative A to close the #887 main-checkout hole. It deliberately kept the synthesis local — the goal was to make the *guard* fire, not to change the stored row. This plan finishes that thought.
- **#1357 / PR #1367** ("refuse-busy guard + cwd-vanished watchdog"): created `worktree_busy_check` and the `working_dir`-containment predicate. The predicate was correct for CLI-created slugged sessions, whose `working_dir` is set to the lane at enqueue time. It was never extended to sessions that resolve their lane at execution time.
- **#2712 / PR #3179**: narrowed the query and batched the sweep. Explicitly left this blind spot alone as pre-existing and orthogonal.
- **#2517 / PR #2681**: scheduled disk reclaim; the caller that most depends on the busy guard being real.
- **#1085**: made `slug` a `KeyField` so worker pop can filter by it. That decision is what makes persisting `slug` unsafe here (see Spike 3).
- **#1938**: session recovery deleted a worktree out from under a live subprocess. The `runner_reap_failed` skip in the synthetic cleanup block is its remedy, and the same block is where this plan's second change lands.

No prior attempt to persist the synthesized identity exists, so there is no "Why Previous Fixes Failed" section.

## Research

No relevant external findings — proceeding with codebase context. The work is entirely internal:
one attribute write on an existing Popoto model, one guard interaction, and one log upgrade. No
new libraries, APIs, or ecosystem patterns are involved.

## Spike Results

All spikes re-measured on `d786c8ad2` during the revision pass. Every code claim below was located
by symbol, never by trusting a line number from the previous draft.

### spike-1: Is the AgentSession row the executor already holds the right object to write?
- **Assumption**: "`_execute_agent_session(session)` receives something other than the stored row."
- **Method**: code-read
- **Finding**: **False.** `_execute_agent_session(session: AgentSession)` — the parameter *is* the
  model instance. The function already hydrates a second copy in its session-phase update block and
  saves `branch_name` / `task_list_id` on it with `save(update_fields=[...])`, which is the
  established partial-save precedent in this exact function.
- **Confidence**: high
- **Impact on plan**: the pre-spawn `exec_cwd` stamp is one assignment plus one entry in an
  `update_fields` list already being passed. No new query, no new plumbing.

### spike-2: Does persisting `working_dir` change how the row is keyed or indexed?
- **Assumption**: "`working_dir` is a plain field."
- **Method**: code-read
- **Finding**: **Confirmed.** `models/agent_session.py` declares `working_dir = Field()`. Not a
  `KeyField`, not indexed, not part of `_meta.key_field_names`.
- **Confidence**: high
- **Impact on plan**: **superseded by spike-8.** Being safe to *write* is not the same as being
  safe to *populate with a lane path*. The Popoto hazard is absent; the lifecycle hazard is not.

### spike-3: Does persisting `slug` change how the row is keyed?
- **Assumption**: "`slug` is a plain indexed field, so writing it is as safe as `working_dir`."
- **Method**: code-read plus live metadata read
- **Finding**: **False.** `models/agent_session.py` declares `slug = KeyField(null=True)` (#1085).
  `AgentSession._meta.key_field_names` resolves to `['chat_id', 'id', 'parent_agent_session_id',
  'project_key', 'session_type', 'slug']` with `db_key_length == 7`. Popoto concatenates key-field
  values into the Redis primary key, so assigning `slug` on a hydrated instance and saving writes a
  **new row at a new db_key** and orphans the original at the `slug=None` key — the duplicate-row
  condition `models/session_lifecycle.get_authoritative_session()` exists to tie-break, plus a
  leaked `status=running` orphan that never finalizes.
- **Confidence**: high
- **Impact on plan**: **do not persist `slug`.** Recorded as a comment at the synthesis site so the
  next reader does not "finish the job".
- **Side finding (out of scope, filed as #3210)**: `bridge/session_transcript.py` already does this
  — `if slug: s.slug = slug` on a hydrated `AgentSession.rows_for_session_id(...)[0]`, unguarded,
  three lines above a `chat_id` branch that documents the exact hazard and handles it correctly.

### spike-4: Would matching on `slug` instead be a safer predicate?
- **Assumption**: "Adding a `slug ==` arm to `_scan_worktree_sessions` is cheap defense-in-depth."
- **Method**: code-read
- **Finding**: **False — it would break post-merge cleanup.** MERGE is a main-checkout stage
  (`AgentSession.worker_key` docstring; `_ENG_WORKTREE_STAGES` allowlist), so the MERGE-stage eng
  session for slug X runs on main and calls `cleanup_after_merge(X)` → `remove_worktree` →
  `worktree_busy_check`. With a `slug ==` arm, that session would block its own lane's removal on
  every merge.
- **Confidence**: high
- **Impact on plan**: slug-matching stays in Rabbit Holes, anchored by an anti-criterion.
- **Note on the chosen field**: `exec_cwd` does *not* reintroduce this hazard. A MERGE-stage
  session's `exec_cwd` is the main checkout — it never spawned in `.worktrees/X` — so it cannot
  match its own lane's probe. That is the same property that made `working_dir` the right *kind* of
  predicate: it distinguishes "owns the slug" from "is standing in the lane".

### spike-5: What does a newly-visible row do to the end-of-session synthetic cleanup? (revised)
- **Assumption**: "Making the lane visible has no effect on the cleanup that deletes it."
- **Method**: code-read
- **Finding**: **False, and the earlier draft got the scope wrong.** The synthetic cleanup calls
  `cleanup_after_merge` → `remove_worktree(..., delete_branch=False)` → `worktree_busy_check`. Once
  the lane is visible, that check matches whenever the row is still non-terminal at cleanup time,
  and `cleanup_after_merge` returns `blocked_by_session` instead of removing.
  The earlier draft reasoned only about the completion exit and concluded the finalize guard always
  runs first. It does not. Re-measured on `d786c8ad2`: `_execute_agent_session` has exactly **one**
  top-level `try:`, exactly **one** top-level `finally:`, and **no** top-level `except:`
  (`awk 'NR>=1160 && NR<=2740 && /^    (except|finally|else)/' agent/session_executor.py` returns
  the `finally` and nothing else). The unconditional completion-exit finalize guard (#2007) sits
  inside `if not chat_state.defer_reaction:` on the normal-return path, so on **every raising or
  cancelled exit** the row is still `running` when the `finally` reaches the cleanup — and this
  cleanup is the only pass that ever runs for a synthetic lane (`sweep_worktrees` requires
  `merged_via_tree`, which a `session/dev-*` branch never satisfies). The leak would be permanent,
  not bounded.
- **Confidence**: high
- **Impact on plan**: a **pre-finalize guard** hoisted ahead of `cleanup_after_merge`, predicated on
  `status == "running"`, is in scope. See spike-9 for why that predicate is what makes hoisting it
  out of the `defer_reaction` conditional safe.

### spike-6: Who else reads the stored `working_dir`? (revised — the earlier census was incomplete)
- **Assumption**: "`_scan_worktree_sessions` is the only consumer, so a write is invisible elsewhere."
- **Method**: code-read
- **Finding**: **False, and the earlier draft found one of five.** Production readers of
  `AgentSession.working_dir`, re-enumerated on `d786c8ad2`:
  1. `agent/session_executor.py` — `working_dir = Path(session.working_dir)`, the seed for the next
     run's `validate_workspace` and `get_or_create_worktree`. **The executor is itself a reader.**
  2. `agent/agent_session_queue.py::checkpoint_branch_state` — runs `git -C working_dir rev-parse`.
  3. `agent/agent_session_queue.py::restore_branch_state` — runs `git -C working_dir checkout`.
  4. `agent/agent_session_queue.py` finally block — `working_dir=str(Path(session.working_dir))`
     into the crash snapshot.
  5. `agent/session_pickup.py` — `_get_git_summary(working_dir=chosen.working_dir, ...)`, gated on
     `working_dir` being set.
  Plus the copier the earlier draft did find: `tools/agent_session_scheduler.py` inherits
  `parent_session.working_dir` onto a scheduled child.
- **Confidence**: high
- **Impact on plan**: this is the census that kills the `working_dir` write. Four of the five
  readers run shell `git` against the stored path; a lane path that has since been deleted turns
  each into a failure, and reader 1 turns it into the re-provisioning hazard of spike-8.
- **Scheduler note**: the inheritance bug is **real independently of this plan** — `valor-session
  create` sets `working_dir` to `.worktrees/{slug}` for slugged sessions today, so a scheduled child
  of any real SDLC lane already inherits a lane path it does not own. It is no longer *caused* by
  this plan's write (there is none), but it is a four-line fix found by this plan's evidence and it
  stays in scope with its provenance stated honestly.

### spike-7: Does `AgentSession.exec_cwd` already carry the resolved lane, with the right lifecycle?
- **Assumption**: "There is no existing field that records where a session actually ran, so the
  lane must be persisted into `working_dir` or a new field must be added." (The earlier draft's
  Rabbit Hole rejecting "a second field `active_worktree_dir`" rested on this.)
- **Method**: code-read
- **Finding**: **False — the field exists, is already populated, and has the correct lifecycle.**
  - `models/agent_session.py` declares `exec_cwd = Field(null=True)`, documented "Absolute working
    dir the spawn ran in (resume is cwd-scoped)".
  - `AgentSession.stamp_execution_spawn(..., cwd=...)` assigns it and persists it with
    `save(update_fields=["exec_pid", "pid_create_time", "exec_cwd", "exec_harness",
    "spawn_history"])`.
  - `agent/session_runner/runner.py::_on_turn_spawn` calls it with `cwd=self._working_dir`, and
    `self._working_dir` is the executor's **resolved** lane — the executor constructs the runner
    with `working_dir=str(working_dir)` after the `get_or_create_worktree` rebind.
  - `agent/agent_session_queue.py::_EXECUTION_FENCE_RESET_FIELDS` contains `"exec_cwd"` with the
    comment "Working dir that spawn ran in; resume is cwd-scoped", and
    `continuation_agent_session_fields` resets every field in that set to its declared default. So a
    continuation row never inherits the previous run's lane path. `working_dir` is **not** in that
    set.
  - Production readers of `exec_cwd` outside the model: none. Inside the model, only
    `live_fence()`, and that reader is guarded — it returns the newest `spawn_history` entry, or a
    reconstruction gated on `if self.exec_pid is not None:`. A stamp that writes `exec_cwd` alone,
    with no pid and no history append, is invisible to it.
- **Confidence**: high
- **Impact on plan**: **adopted.** `exec_cwd` becomes the field the busy scan reads, and the
  executor pre-stamps it. This closes the blind spot with none of spike-8's lifecycle hazard,
  requires no model change, and needs no migration.
- **The one real trade, measured rather than assumed**: the runner stamps `exec_cwd` at
  `_on_turn_spawn`, which is *after* the first harness subprocess exists. An `exec_cwd`-only
  variant would therefore leave the Race 1 window wider than the earlier draft's write did
  (worktree creation → first spawn, rather than worktree creation → session-phase save). The
  pre-stamp in the executor's existing session-phase save block removes that difference: it lands at
  the same point the earlier draft's `working_dir` write would have, so Race 1 is exactly as narrow
  as the earlier draft claimed, and the runner's later stamp overwrites it with the identical value.

### spike-8: Does a persisted `working_dir` outlive the lane it names?
- **Assumption**: "A stale lane path in a terminal row is ignored by every reader" (the earlier
  draft's Architectural Impact reversibility claim).
- **Method**: code-read
- **Finding**: **False.** The synthetic cleanup **deletes** `.worktrees/dev-{aid8}` in
  `_execute_agent_session`'s `finally`, and three paths re-execute or copy the same row afterwards:
  - `tools/valor_session.py` `resume` transitions the **same row** back to `pending` via
    `transition_status(session, "pending", ..., reject_from_terminal=False)` — it does not go
    through `continuation_agent_session_fields`, so nothing is reset.
  - the nudge requeue in `agent/agent_session_queue.py` copies the row's fields forward.
  - `retry_agent_session` copies via `clone_agent_session_fields`, whose docstring is explicit:
    "Copies everything ... anything omitted here is destroyed." `working_dir` is copied and is not
    in `_EXECUTION_FENCE_RESET_FIELDS`.
  On the next run, `working_dir = Path(session.working_dir)` names a deleted directory.
  `validate_workspace` fails invariant 1 ("does not exist or is not a directory") and returns
  `allowed_root`, which the executor set to `Path.home() / "src"` — not a git repository. The
  synthetic branch then re-enters `if needs_wt and (WORKTREES_DIR not in str(working_dir) or not
  working_dir.exists())` and calls `get_or_create_worktree(~/src, slug)`, i.e. `git worktree add`
  outside any repository; failing that, the #887 main-checkout guard refuses the session outright.
  Both outcomes are unreachable today.
- **Confidence**: high
- **Impact on plan**: **`working_dir` is never written.** This is the finding that converts the
  earlier draft's central mechanism into an anti-criterion.

### spike-9: Is hoisting the pre-finalize guard out of `if not chat_state.defer_reaction:` safe?
- **Assumption**: "The `defer_reaction` gate exists to protect something the hoisted guard would
  also break."
- **Method**: code-read
- **Finding**: **The gate's stated reason is satisfied by a status predicate alone.** The comment on
  the completion-exit guard says the nudge re-enqueue path is excluded because "`_enqueue_nudge`
  already writes the authoritative post-nudge state (status=pending) itself; finalizing here would
  clobber it." Tracing `_enqueue_nudge`: its main path calls `transition_status(session, "pending",
  ...)`, and its fallback path creates a fresh `pending` row from
  `continuation_agent_session_fields`. So on the deferred path the authoritative row is `pending`,
  not `running`, by the time the `finally` runs — and a guard predicated on `status == "running"`
  no-ops there without needing the `defer_reaction` condition at all. In the fallback-path case
  where the original row was left `running`, `get_authoritative_session`'s tie-break prefers the
  `running` record, and finalizing that dead row is the correct outcome.
- **Confidence**: high
- **Impact on plan**: the pre-finalize guard is written **unconditionally** in the `finally`,
  predicated on `status == "running"`, and the `defer_reaction` gate on the completion-exit guard is
  left untouched. Changing that gate is #3209.
- **Second finding — `task` may be unbound.** `task` is assigned partway through the body
  (`task = BackgroundTask(...)`), well after the several exits that can raise. In the `finally` it
  is therefore not guaranteed to exist, and `_runner_final_status(task.error, agent_session)` would
  raise `NameError`. The guard must resolve it defensively (`locals().get("task")`,
  `locals().get("agent_session")`) exactly as the neighbouring cleanup already does for `slug` and
  `working_dir`, and degrade to `"failed"` when `task` never came into being — which is the honest
  status for a session that raised before it started a runner.

### spike-10: Does the newly-visible lane change what happens on an auto-continue exit?
- **Assumption**: "The deferred/auto-continue exit behaves the same as before."
- **Method**: code-read
- **Finding**: **It gets better, and the change must be pinned.** Today, a deferred exit enqueues a
  continuation and then the `finally` deletes `.worktrees/dev-{aid8}` anyway — out from under a
  continuation that carries the same `agent_session_id`, hence the same synthetic slug, hence the
  same lane. With the lane visible, the continuation's row is `pending` (non-terminal), the busy
  check matches, and `cleanup_after_merge` **preserves** the lane the continuation is about to
  re-enter. The continuation's `get_or_create_worktree` then finds it already present.
- **Confidence**: high
- **Impact on plan**: this is a behavior change on a path the earlier draft did not consider. It is
  the desired behavior, and it gets its own test.

## Data Flow

Located by symbol on `d786c8ad2`; line numbers below are pointers, not the citation.

1. **Entry point**: the worker pops an eng `AgentSession` with `slug=None` and
   `working_dir=<main checkout>` and calls `_execute_agent_session(session)`.
2. **`working_dir = Path(session.working_dir)`** — the local is seeded from the stored row and
   validated by `validate_workspace(working_dir, Path.home() / "src", is_worktree=...)`.
3. **Synthetic-slug synthesis** — `slug = f"dev-{_aid_for_slug[:8]}"`, `is_synthetic_slug = True`.
   Local only, and it stays that way (spike-3).
4. **`resolve_branch_for_stage`** then the synthetic override: `resolved_branch = f"session/{slug}"`,
   `needs_wt = True`.
5. **`get_or_create_worktree(working_dir, slug)`** creates `.worktrees/{slug}` and the local
   `working_dir` is rebound to it. **This is where the row and reality diverge today.**
6. **#887 main-checkout guard** passes, because it reads the *local* `working_dir`.
7. **`verify_worktree_branch(working_dir, branch_name)`** confirms the lane is on `session/{slug}`.
8. **Session-phase save block**: the row is re-hydrated and `branch_name` / `task_list_id` are
   persisted with `save(update_fields=[...])`. **After this plan, `exec_cwd` is stamped here too.**
9. **Runner construction**: `SessionRunner(..., working_dir=str(working_dir), ...)`, so
   `self._working_dir` is the same resolved lane.
10. **Harness launch**: `claude -p` runs with `cwd=.worktrees/{slug}`, and `_on_turn_spawn` calls
    `stamp_execution_spawn(..., cwd=self._working_dir, ...)`, re-writing `exec_cwd` to the identical
    value alongside the pid fence.
11. **Concurrent reader** — a `tools/disk_reclaim.py` sweep, `reap_idle_worktree`, or an interactive
    `remove_worktree` — calls `_scan_worktree_sessions(repo_root, slug)`.
    - **Today**: it reads `working_dir` (main checkout), fails the segment-prefix match, returns
      `("clear", "", "")`. The lane reads clear while a session is live in it; only
      `_worktree_has_live_process` prevents deletion.
    - **After this plan**: it reads `exec_cwd` first, matches the segment prefix, and returns
      `("busy", session_id, agent_session_id)`.
12. **Session end, `finally` block**: the pre-finalize guard flips a still-`running` row terminal,
    then the synthetic cleanup runs. A terminal row no longer matches the scan (status filter), so
    `cleanup_after_merge` removes the worktree and branch exactly as it does today.
13. **Continuation**: `continuation_agent_session_fields` resets `exec_cwd` to its declared default,
    so the new row starts with no lane recorded and re-stamps its own at step 8. `working_dir` still
    names the main checkout, so nothing re-seeds a deleted directory (spike-8).

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none. No function signature changes, no new fields, no schema change —
  `exec_cwd` is an existing `Field(null=True)` on `AgentSession`, already written on every spawn.
- **Coupling**: the busy scan gains a second source for the same fact ("where is this session
  running"). The two sources answer different lifecycle questions and are read in priority order:
  `exec_cwd` (execution-scoped, reset per continuation) first, `working_dir` (enqueue-scoped) as
  the existing fallback.
- **Data ownership**: `exec_cwd` gains a second writer — the executor, immediately before harness
  launch — writing the identical value `stamp_execution_spawn` writes moments later from the same
  local. No other production code reads `exec_cwd` except `AgentSession.live_fence()`, which is
  guarded by `spawn_history` / `exec_pid` and is therefore unaffected by a pre-spawn stamp that
  carries neither (verified: `grep -rn exec_cwd` over the production tree returns
  `agent_session_queue.py` (the reset list), `models/agent_session.py`, and nothing else).
- **What is deliberately NOT persisted**: `working_dir` and `slug`. `slug` is a `KeyField` and a
  mid-flight write forks the Redis row (spike-3). `working_dir` is enqueue-scoped and is read by
  the executor itself to seed the next run, by `checkpoint_branch_state` / `restore_branch_state`
  (`git -C <working_dir>`), by `session_pickup`'s git summary, and by the crash-snapshot writer;
  a lane path written there outlives the lane and re-seeds a deleted directory (spike-8).
- **Reversibility**: high. Reverting the scan loop restores today's behavior exactly. Nothing is
  persisted that a later reader cannot cope with: a stale `exec_cwd` in a terminal row is skipped
  by the scan's status filter, and a continuation resets the field to its declared default.

## Appetite

**Size:** Small

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies. It touches three files already in the
repo and runs against the existing unit suite.

## Solution

### Key Elements

- **Two-field lane match (`agent/worktree_manager.py`)**: `_scan_worktree_sessions` reads
  `exec_cwd` and `working_dir` in that order, running its existing normalize + segment-prefix match
  over each and returning `("busy", ...)` on the first hit. `exec_cwd` is the execution-scoped
  truth; `working_dir` is the enqueue-scoped one it already read.
- **Pre-spawn `exec_cwd` stamp (`agent/session_executor.py`)**: the executor stamps the resolved
  lane onto `exec_cwd` in the session-phase save block it already performs, so the lane is visible
  before the harness launches rather than after the first spawn.
- **Nothing is persisted into `working_dir` or `slug`, on purpose**: `slug` is a `KeyField` and a
  mid-flight write forks the Redis row (spike-3); `working_dir` is enqueue-scoped, read by five
  production consumers, and a lane path written there outlives the lane (spike-8). Both are
  anchored by anti-criteria in the Verification table, and the `slug` reason is recorded as a
  comment at the synthesis site.
- **Pre-finalize guard (`agent/session_executor.py`)**: ahead of the synthetic cleanup in the
  `finally`, a still-`running` authoritative row is finalized, so an exception or cancellation exit
  cannot leave a permanently-blocked lane (spike-5, spike-9).
- **Loud cleanup block (`agent/session_executor.py`)**: when `cleanup_after_merge` returns
  `blocked_by_session`, that becomes a `[synthetic-slug]` WARNING naming the session and the manual
  reclamation command, matching the shape of the neighbouring `runner_reap_failed` skip.
- **Inheritance guard (`tools/agent_session_scheduler.py`)**: a scheduled child no longer inherits a
  parent `working_dir` that points inside `.worktrees/`. Independent of the rest of this plan
  (spike-6), fixed here because this plan's evidence found it.

### Flow

Worker pops slugless eng session → executor synthesizes `dev-{aid8}` and provisions
`.worktrees/dev-{aid8}` → **executor stamps `exec_cwd` with the resolved lane** → harness launches
and re-stamps the same value with the pid fence → a concurrent sweep asks `worktree_busy_probe` →
**`busy`, lane skipped** → session ends → **pre-finalize guard flips a still-`running` row terminal**
→ cleanup re-asks → `clear` → worktree and branch removed. On an auto-continue exit the row is
`pending`, the cleanup is refused, and the lane is preserved for the continuation that is about to
re-enter it (spike-10).

### Technical Approach

- **The scan (`agent/worktree_manager.py::_scan_worktree_sessions`).** Replace the single
  `wd = getattr(session, "working_dir", None)` read with a loop
  `for wd in (getattr(session, "exec_cwd", None), getattr(session, "working_dir", None)):`, keeping
  the existing `if not wd: continue` guard (which already covers the `None` a never-spawned row
  carries), the existing normalize/`os.path.isabs` branch, and the existing segment-prefix
  comparison. Hoist the status check above the loop so it is evaluated once per row rather than
  twice. Return on the first hit. Extend the docstring to record that two fields are read, in which
  order, and why `slug` is not one of them (spike-4's post-merge self-block).
- **The stamp (`agent/session_executor.py`, session-phase save block).** In the block that already
  hydrates the row and calls `save(update_fields=["updated_at", "branch_name", "task_list_id"])`,
  add `agent_session.exec_cwd = str(working_dir)` and `"exec_cwd"` to `update_fields`. Reuse the
  `agent_session` that block already resolved — **do not** swap its lookup for
  `get_authoritative_session`. Two reasons: (i) that resolver applies no status filter, so the
  `agent_session` it returns would make `_session_type` non-`None` on paths where it is `None`
  today, and `_session_type` drives the harness `SESSION_TYPE` env, the ENG/TEAMMATE permission
  branch, and runner dispatch; (ii) the wrong-duplicate risk the swap was meant to fix is benign for
  this predicate — `_scan_worktree_sessions` iterates **every** non-terminal row, so a stamp that
  lands on any duplicate still makes the lane read busy. The swap was scope creep with unmeasured
  blast radius; it is dropped.
- **Failure posture of the stamp.** The enclosing `try` already exists and already swallows. Upgrade
  its handler from `logger.debug("AgentSession update failed (non-fatal): ...")` to `logger.warning`
  with the literal marker `[lane-writeback]`, naming the session id and the resolved path. A lane
  that stays invisible is exactly the condition this plan exists to detect, and it must be
  greppable. Raising instead would turn a Redis blip into a failed eng session, which is strictly
  worse than falling back to the OS-process scan.
- **Scope of the stamp.** Every session, not only synthetic ones — because that is already true of
  `exec_cwd`, which the runner stamps unconditionally on every spawn. There is no new rule to
  narrow and no untested half: the real-slug case is the same code path with a different slug, and
  it gets its own test row.
- **The pre-finalize guard (`agent/session_executor.py`, `finally`).** Immediately before the
  `cleanup_after_merge` call and inside the block's existing `try`:
  `_auth = get_authoritative_session(session.session_id)`; when
  `_auth is not None and _auth.status == "running"`, call
  `finalize_session(_auth, <status>, reason="synthetic-cleanup pre-finalize")` wrapped in
  `except StatusConflictError: pass` — the same shape as the completion-exit guard, hoisted out of
  the `if not chat_state.defer_reaction:` conditional. `<status>` is
  `_runner_final_status(_task.error, _agent_session)` where `_task = locals().get("task")` and
  `_agent_session = locals().get("agent_session")`, degrading to `"failed"` when `task` never came
  into being (spike-9: `task` is bound partway through the body and is not guaranteed to exist in
  the `finally`). The `status == "running"` predicate is what makes the hoist safe on the nudge
  path, where the row is already `pending`.
  **Do NOT use `remove_worktree(force=True)`.** Forcing is the deletion-under-a-live-subprocess
  failure #1938 produced, and the `_session_recorded_reap_failure` skip above this block must keep
  winning.
- **Cleanup logging.** `cleanup_after_merge` sets `result["blocked_by_session"] = session_id` when
  `remove_worktree` returns `("blocked", session_id)`. When that key is present, emit a WARNING
  containing the literal `[synthetic-slug]` and the words `cleanup blocked`, naming the session id
  and the manual `git worktree prune` + directory removal, mirroring the neighbouring
  `runner_reap_failed` message. No behavioral change at the call site.
- **Scheduler guard.** In `tools/agent_session_scheduler.py`, import `WORKTREES_DIR` from
  `agent.worktree_manager` and inherit `parent_session.working_dir` only when the path does not
  contain that segment.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/session_executor.py` session-phase save handler (`except Exception as e:` around the
      `agent_session.save(update_fields=[...])`) — add a test that forces `save()` to raise and
      asserts the `[lane-writeback]` WARNING is emitted and the session still proceeds (observable
      behavior, not `pass`).
- [ ] `agent/session_executor.py` synthetic-cleanup `except Exception as cleanup_err:` — add a test
      that a raising `cleanup_after_merge` does not propagate as a session failure.
- [ ] The pre-finalize guard's `except StatusConflictError: pass` — add a test that a concurrent
      finalize (CAS conflict) does not propagate, and that the cleanup still runs afterwards.
- [ ] The pre-finalize guard when `task` is unbound — a session that raises before `task` is
      assigned must finalize to `"failed"` rather than raise `NameError` inside the `finally`.
- [ ] `agent/worktree_manager.py::_scan_worktree_sessions` per-row `except Exception` — unchanged in
      shape; the two-field loop stays inside it, so a row whose `exec_cwd` read raises is skipped
      exactly as today.
- [ ] `tools/agent_session_scheduler.py` — the inheritance guard adds no exception handler; the
      comparison is a pure string check on an already-read value.

### Empty/Invalid Input Handling
- [ ] `exec_cwd` is `None` on every row that has never spawned. The scan's existing
      `if not wd: continue` guard covers it, and the loop must not treat a `None` first element as
      a reason to skip the `working_dir` read — pinned by a test where `exec_cwd is None` and
      `working_dir` is the lane.
- [ ] `exec_cwd` may be a relative path if a caller ever stamps one. The scan's existing
      `os.path.isabs(wd)` branch resolves relative values against `repo_root`; the loop inherits it
      unchanged, and a relative-`exec_cwd` row gets a test row.
- [ ] `get_authoritative_session` returns `None` when no row matches. The pre-finalize guard must
      no-op rather than raise `AttributeError`.
- [ ] `parent_session.working_dir` may be `""` or `None` in the scheduler; the existing
      `if parent_session.working_dir:` truthiness check already handles both, and the new guard is
      evaluated after it.

### Error State Rendering
- [ ] This feature has no user-visible output. Its observable surface is the log:
      `[lane-writeback]` on a failed stamp and `[synthetic-slug] ... cleanup blocked` on a refused
      removal. Both are asserted with `caplog`, so a silently-swallowed failure fails the suite.

## Test Impact

- [ ] `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py` — UPDATE: add cases for the
      two-field match. The existing `working_dir`-only cases must keep passing unchanged; that is
      the regression guard on the fallback arm.
- [ ] `tests/unit/test_session_isolation_bypass.py` — UPDATE, **source assertions only**. Every test
      in this file is either a static `_should_block` logic mirror or a source-text regex; it drives
      no executor. Its one addition here is the "slug stays local" source assertion. The behavioral
      assertions the earlier draft assigned to this file move to a real-Redis test (below).
- [ ] `tests/unit/test_teammate_cold_start_finalize.py` — no change, but it is the **template**: a
      real `AgentSession.create(...)` under the `redis_test_db` fixture, `_patch_runner()` to stub
      the harness, `await _execute_agent_session(session)`, then re-read by stable `id` and assert
      persisted state. Every behavioral assertion in this plan uses that shape.
- [ ] `tests/unit/test_session_executor_reap_marker.py` — UPDATE: extend the synthetic-cleanup
      source guard to cover the pre-finalize guard and the blocked-by-session WARNING.
- [ ] `tests/unit/test_pipeline_integrity.py` — no change, but it is load-bearing for this plan: it
      already asserts `exec_cwd` is reset by `continuation_agent_session_fields`. That assertion is
      what makes spike-8's hazard structurally absent for `exec_cwd`, so it must not be weakened.
- [ ] `tests/unit/session_runner/test_runner_liveness.py`, `test_runner_turns.py`,
      `test_runner_preempt.py` — no change expected: they assert `session.exec_cwd == "/tmp/wd"`
      after a spawn. The executor's pre-stamp writes the same value from the same local, so the
      runner's stamp is still what these observe. If any of them starts failing, the pre-stamp is
      writing a different path than the runner and that is a real defect, not a test to update.
- [ ] `tests/unit/test_valor_session_working_dir_resolution.py` — no change: it pins
      `valor-session create`, which re-derives `working_dir` from the project key.
- [ ] `tests/unit/test_session_executor_guards.py` — no change: it pins the `None`-field
      preconditions ahead of `Path(session.working_dir)`, which this plan does not move.
- [ ] `tests/unit/test_agent_session_scheduler_worktree_inheritance.py` — CREATE.

## Rabbit Holes

- **Persisting the lane into `working_dir`.** This was the earlier draft's central mechanism and it
  is wrong: `working_dir` is enqueue-scoped, is not in `_EXECUTION_FENCE_RESET_FIELDS`, and is read
  by five production consumers including the executor's own next-run seed. A lane path written
  there outlives the lane the `finally` deletes (spike-8). Anchored by an anti-criterion.
- **Persisting the synthetic `slug` "for symmetry".** It looks like the obvious other half of the
  fix and it forks the Redis row (spike-3). Anchored by an anti-criterion, and by a comment at the
  synthesis site.
- **Adding a `slug ==` arm to `_scan_worktree_sessions`.** It reads as free defense-in-depth and it
  deadlocks post-merge cleanup against its own MERGE session (spike-4). Anchored by an
  anti-criterion.
- **Adding a new model field (`active_worktree_dir`).** The earlier draft rejected this as "two
  competing answers to where this session is running, plus a model change". The rejection was right
  about the model change and wrong about the premise: the field already exists as `exec_cwd`, is
  already populated on every spawn, and already has the lifecycle this problem needs (spike-7).
  Adding a *new* one remains out of scope and anchored by an anti-criterion.
- **Swapping the session-phase hydration for `get_authoritative_session`.** Considered and dropped
  on measurement: the resolver applies no status filter, so it would change `_session_type` — which
  drives the harness `SESSION_TYPE` env, the ENG/TEAMMATE permission branch, and runner dispatch —
  on paths where it is `None` today, and the duplicate-row hazard it was meant to fix is benign for
  a predicate that scans every non-terminal row.
- **Reworking the `defer_reaction`-gated completion-exit finalize guard.** Filed as **#3209**. The
  pre-finalize guard in this plan makes the refusal survivable without touching that gate's
  semantics, which is the right seam.
- **Fixing `bridge/session_transcript.py`'s unguarded `s.slug =` write.** Filed as **#3210**. Same
  KeyField hazard spike-3 documents, different file, no overlap with this plan's changed paths.
- **Auditing every `AgentSession.query.filter(...)` linear scan.** Out of scope; this plan no longer
  changes even the one it previously proposed to.

## Risks

### Risk 1: A newly-visible row blocks a removal that used to succeed
**Impact:** any caller that previously saw a lane as `clear` now sees `busy` while the row is
non-terminal. Concretely: `remove_worktree` returns `("blocked", session_id)`, `reap_idle_worktree`
returns `(False, "live_session:...")`, and the daily `tools/disk_reclaim.py` sweep skips the lane.
**Mitigation:** this is the guard doing its job — every one of those refusals is protecting a
directory a live process is sitting in. The two cases that could surprise are the session's own
end-of-run cleanup (spike-5) and the auto-continue exit (spike-10). The first is closed by the
pre-finalize guard; the second is the desired behavior and now preserves a lane a continuation is
about to re-enter. Both get pinning tests, and every refusal is logged under `[synthetic-slug]`
with the manual reclamation command.

### Risk 2: A lane leaks when a row never reaches a terminal status
**Impact:** if a row is stuck `running` or `pending`, the busy guard holds its worktree.
`sweep_worktrees` will not reclaim it either — it requires `merged_via_tree`, and a synthetic
`session/dev-*` branch is never merged.
**Mitigation:** the permanent-leak case the critique identified — every raising and cancelled exit,
where the completion-exit finalize guard never ran and no later pass exists — is closed inside this
plan by the pre-finalize guard. What remains is a row that is genuinely still alive or still queued,
where holding the directory is correct, plus a row stuck by something the health checker has not yet
swept, where the leak is bounded by that sweep. Until then the `[synthetic-slug] ... cleanup
blocked` WARNING names the session id and the exact `git worktree prune` + directory removal,
mirroring the precedent set by the `runner_reap_failed` skip in the same block. Leaking a directory
is the correct trade against deleting one out from under a live subprocess (#1938).

### Risk 3: The pre-finalize guard finalizes a row that should have stayed alive
**Impact:** if the guard fired on a row that a continuation still owns, it would mark a live
pipeline dead.
**Mitigation:** the guard is predicated on `status == "running"`, and every path that hands a row
forward leaves it `pending` first — `_enqueue_nudge`'s main path via `transition_status(..., "pending")`,
its fallback path via a fresh `pending` row (spike-9). A `running` row reaching this `finally` has no
owner: the runner's synchronous reap has already confirmed the subprocess dead ahead of this block.
Pinned by a test asserting a deferred (auto-continue) exit leaves the continuation's `pending` row
untouched.

### Risk 4: The executor's pre-stamp disagrees with the runner's stamp
**Impact:** if the executor wrote a different path than `stamp_execution_spawn` later writes, the
scan would match a lane the harness is not in.
**Mitigation:** structurally impossible in the current code — both read the same local. The executor
stamps `str(working_dir)` and constructs the runner with `working_dir=str(working_dir)` from that
same local, which becomes `self._working_dir` and is passed as `cwd=`. The existing runner tests
(`tests/unit/session_runner/test_runner_liveness.py` and siblings) assert the post-spawn value; if
the pre-stamp ever diverges, they fail. Test Impact records that a failure there is a defect, not a
test to update.

### Risk 5: A pre-spawn `exec_cwd` is misread as a liveness fence
**Impact:** `exec_cwd` is one of the fenced-execution-record fields; a value present without a pid
could be read as evidence a process exists.
**Mitigation:** measured, not assumed. `grep -rn exec_cwd` over the production tree returns exactly
three sites: the reset list in `agent/agent_session_queue.py`, the field declaration and
`stamp_execution_spawn` in `models/agent_session.py`, and `AgentSession.live_fence()`. `live_fence`
returns the newest `spawn_history` entry, or a reconstruction gated on `if self.exec_pid is not
None:` — a stamp that writes `exec_cwd` alone, appending no history and setting no pid, is invisible
to both. Pinned by a test asserting `live_fence()` is `None` on a row that has only the pre-stamp.

### Risk 6: Scheduled children inherit a lane path
**Impact:** a scheduled child of a parent whose row carries a lane path inherits it, synthesizes its
own slug, skips worktree provisioning because the inherited path already looks like a worktree, and
then fails `verify_worktree_branch` against a lane another session is live in.
**Mitigation:** the inheritance guard in `tools/agent_session_scheduler.py`, with a test that pins a
worktree-rooted parent path being declined. Note this is a **pre-existing** defect, not one this
plan creates: `valor-session create` sets `working_dir` to `.worktrees/{slug}` for slugged sessions
today (spike-6).

## Race Conditions

### Race 1: Busy read between worktree creation and the `exec_cwd` stamp
**Location:** `get_or_create_worktree` (worktree created) through the session-phase save block
(`exec_cwd` stamped) in `agent/session_executor.py`.
**Trigger:** a sweep or an interactive `remove_worktree` runs against `.worktrees/{slug}` inside that
window. The directory exists; no row names it yet.
**Data prerequisite:** the stamped `exec_cwd` must be visible before any external reader can
conclude "clear".
**State prerequisite:** none beyond the row existing.
**Mitigation:** the window cannot be closed by ordering alone — the worktree must exist before its
path can be recorded — so it is narrowed and backstopped rather than eliminated. The executor's
pre-stamp is what narrows it: relying on the runner's `_on_turn_spawn` stamp alone would leave the
window open through harness startup (spike-7's measured trade). `_worktree_has_live_process` covers
the remaining interval, and a bare `git worktree add` with no process in it is genuinely idle and
safe to reap. This is a strict improvement on today, where the window is the entire session.

### Race 2: Cleanup versus a not-yet-finalized row
**Location:** the completion-exit finalize guard and the synthetic cleanup in
`agent/session_executor.py`'s `finally`.
**Trigger:** the cleanup runs while the row is still `running` — the exit raised or was cancelled,
so the completion-exit guard (inside `if not chat_state.defer_reaction:` on the normal-return path)
never ran.
**Data prerequisite:** the row's terminal status must be committed before the busy check reads it.
**State prerequisite:** the subprocess must be confirmed dead, which the runner's synchronous reap
already guarantees ahead of this block.
**Mitigation:** the pre-finalize guard imposes exactly this ordering — finalize, then cleanup —
inside the same `try`. If it cannot finalize (resolver returns `None`, or a CAS conflict means
someone else already did), the guard fails closed: the worktree is preserved and the refusal is
logged. Preservation is the correct outcome for a session whose state is unclear.

### Race 3: The executor's stamp versus the runner's stamp
**Location:** the session-phase save block and `stamp_execution_spawn`.
**Trigger:** both write `exec_cwd` for the same row within milliseconds.
**Data prerequisite:** none — they write the identical value from the identical local (Risk 4).
**State prerequisite:** none.
**Mitigation:** both use `save(update_fields=[...])`, which writes only the named hash fields. The
later write is idempotent with respect to `exec_cwd`, and the runner's write additionally lands the
pid fence, which the executor's does not touch.

### Race 4: Concurrent write to the same row
**Location:** the session-phase save block.
**Trigger:** the health checker or a steering write saves the same row while the executor stamps
`exec_cwd`.
**Data prerequisite:** none.
**State prerequisite:** none.
**Mitigation:** `save(update_fields=[...])` writes only the named hash fields, so a concurrent
writer touching other fields cannot be clobbered and cannot clobber this one. `exec_cwd` is
additionally on `_UPDATED_AT_OMISSION_OK_FIELDS`, so a partial save carrying it produces no
`updated_at`-omission warning noise.

## No-Gos (Out of Scope)

Three things are deliberately deferred. Each is a real defect this plan's evidence found; each is
filed so it is tracked rather than forgotten, and none is a prerequisite for this lane.

- **The `defer_reaction` gate on the completion-exit finalize guard — filed as #3209.** That gate is
  why a raising or cancelled session reaches its own cleanup while still `running`. This plan
  installs a narrower, local pre-finalize guard ahead of the synthetic cleanup and leaves the gate's
  semantics alone; changing the gate has a wider blast radius than this lane's appetite.
- **`bridge/session_transcript.py`'s unguarded `s.slug =` write — filed as #3210.** The same
  KeyField row-forking hazard spike-3 documents, already live in `bridge/`. This plan changes no
  file in `bridge/`.
- **Growing `sweep_worktrees` an explicit synthetic-lane path.** The permanent-leak case that made
  this look necessary is closed inside this plan by the pre-finalize guard, so the sweep's
  `merged_via_tree` requirement no longer strands anything that was not stranded before.

The remaining tempting adjacent work — persisting `working_dir` or `slug`, adding a `slug ==` match
arm, adding a new `active_worktree_dir` field, swapping the session-phase hydration for
`get_authoritative_session`, auditing the file's other linear scans — is listed under Rabbit Holes
with the measurement that rules each one out, and each is anchored by a Verification row naming the
forbidden code-level outcome.

## Update System

No update system changes required — this feature is purely internal. It adds no dependency, no
config file, no entry point, and no schema change, so `scripts/remote-update.sh` and the `/update`
skill are unaffected. Existing installations need nothing beyond the ordinary code sync: the write
is to an existing `Field()` on an existing model, and rows written before this change simply carry
the old (main-checkout) value until their session next runs.

## Popoto Schema Migration

None required. `working_dir` is an existing `Field()` (`models/agent_session.py:186`) and `slug` is
deliberately not written. No field is added, removed, renamed, or re-typed, so no entry in
`scripts/update/migrations.py` is warranted. Every write in this plan goes through
`instance.save(update_fields=[...])`; no raw Redis operation is introduced.

## Agent Integration

No agent integration required — this is a bridge/worker-internal change. It adds no CLI entry
point to `pyproject.toml [project.scripts]`, no MCP surface, and no new import for
`bridge/telegram_bridge.py`. The behavior is exercised by the worker's ordinary session-execution
path, which the existing unit suite drives directly.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/session-isolation.md`: state that the session-table busy predicate reads
      `exec_cwd` (execution-scoped) before `working_dir` (enqueue-scoped), that the executor stamps
      `exec_cwd` before harness launch so a lane resolved at execution time is visible, and that
      neither `slug` nor `working_dir` is ever written back — `slug` because it is a `KeyField` in
      the Redis primary key, `working_dir` because it is enqueue-scoped and survives continuations.
- [ ] Update `docs/features/worktree-manager.md`: record the two-field match in
      `_scan_worktree_sessions`, the read order, and why `slug` is not a third arm (the post-merge
      self-block, spike-4).
- [ ] Update `docs/features/scheduled-disk-reclaim.md`: note that the AgentSession probe is no
      longer blind to execution-time lanes, so the OS-process scan is a backstop rather than the sole
      working guard.
- [ ] Update `docs/features/agent-session-fenced-execution-record.md`: `exec_cwd` now has a second
      writer (the executor, pre-spawn) and a second reader (the busy scan). Record that a pre-spawn
      stamp carries no pid and no `spawn_history` entry, so `live_fence()` is unaffected.
- [ ] `docs/features/README.md` needs no new row — all four pages already have entries. Confirm
      during the docs task rather than assuming.

### External Documentation Site
- [ ] Not applicable; this repo publishes no external docs site.

### Inline Documentation
- [ ] Comment at the synthetic-slug synthesis site in `agent/session_executor.py` explaining that
      `slug` stays local because it is a `KeyField` in the Redis primary key and a mid-flight write
      forks the row.
- [ ] Docstring update on `_scan_worktree_sessions` recording the two-field read order, that
      `exec_cwd` is execution-scoped and reset per continuation, and why `slug` is not read.
- [ ] Comment on the executor's `exec_cwd` stamp noting it writes the same value
      `stamp_execution_spawn` writes moments later, and exists to narrow the Race 1 window through
      harness startup.
- [ ] Comment on the scheduler guard naming the concrete failure it prevents.

### Deploy Note
- [ ] This lane changes `agent/`. After the merge, bridge and worker machines need
      `./scripts/valor-service.sh restart`. Verify with `tail -5 logs/bridge.log` showing "Connected
      to Telegram". This must appear in the PR body as well as here.

## Success Criteria

- [ ] `_scan_worktree_sessions` returns `busy` for a `dev-*` lane held by a non-terminal, `slug=None`
      row whose `exec_cwd` names the lane and whose `working_dir` names the main checkout
- [ ] `_scan_worktree_sessions` still returns `busy` for the pre-existing `working_dir`-only shape
      (real slug, lane in `working_dir`, `exec_cwd` unset) — the fallback arm does not regress
- [ ] A slugless eng session's row carries `exec_cwd=.worktrees/dev-{aid8}` before the harness
      launches, not only after the first spawn
- [ ] The row's `working_dir` and `slug` are unchanged by execution — no production code in this
      plan's changed files assigns either on a hydrated `AgentSession`
- [ ] A pre-spawn `exec_cwd` stamp leaves `AgentSession.live_fence()` returning `None`
- [ ] A raising or cancelled session is finalized before the synthetic cleanup runs, so the lane is
      removed rather than permanently blocked
- [ ] An auto-continue exit leaves the continuation's `pending` row untouched and preserves the lane
- [ ] The end-of-session synthetic cleanup logs a named `[synthetic-slug] ... cleanup blocked`
      WARNING when removal is refused
- [ ] A failed `exec_cwd` stamp is logged at WARNING under `[lane-writeback]` and does not fail the
      session
- [ ] A scheduled child never inherits a parent `working_dir` that points inside `.worktrees/`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (executor)**
  - Name: `executor-builder`
  - Role: the lane write-back and the cleanup log upgrade in `agent/session_executor.py`
  - Agent Type: builder
  - Domain: Redis/Popoto data
  - Resume: true

- **Builder (scheduler)**
  - Name: `scheduler-builder`
  - Role: the working_dir inheritance guard in `tools/agent_session_scheduler.py`
  - Agent Type: builder
  - Resume: true

- **Test engineer**
  - Name: `guard-tester`
  - Role: tests pinning the busy-guard match, both cleanup branches, the failure log, and the inheritance guard
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: `lane-validator`
  - Role: runs the Verification table and confirms every success criterion
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `lane-documentarian`
  - Role: the three feature-doc updates
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

**Testing discipline for every task below:** scoped tests only, never the full suite. Use
`scripts/pytest-clean.sh`, never bare `pytest`. Read the passed count off the summary line — exit 0
with "0 passed" is a FAILED verification. Any `AgentSession` created for manual testing uses a
`test-` or `dbg-` `project_key` prefix and is deleted afterwards through the ORM scoped by that key;
never a raw Redis operation on a Popoto-managed key, never an unscoped bulk operation.

**Re-verify before editing:** `agent/worktree_manager.py` changed via #3162, #3167, and #2712/#3179
this weekend. Locate every symbol named below by name, never by the line numbers in this document.

### 1. Teach the busy scan to read `exec_cwd`
- **Task ID**: build-scan
- **Depends On**: none
- **Validates**: tests/unit/worktree_manager/test_worktree_manager_busy_guards.py
- **Informed By**: spike-7 (`exec_cwd` is already populated with the resolved lane and reset per
  continuation), spike-4 (`exec_cwd` does not reintroduce the post-merge self-block)
- **Assigned To**: scan-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/worktree_manager.py::_scan_worktree_sessions`, hoist the `status` /
  `TERMINAL_STATUSES` check above the path comparison so it runs once per row, then loop
  `for wd in (getattr(session, "exec_cwd", None), getattr(session, "working_dir", None)):` around
  the existing normalize + segment-prefix match, returning `("busy", session_id, agent_session_id)`
  on the first hit. Keep the existing `if not wd: continue`, the `os.path.isabs` branch, and the
  per-row `except Exception` exactly as they are.
- Extend the docstring: two fields are read, in that order; `exec_cwd` is execution-scoped and reset
  per continuation while `working_dir` is enqueue-scoped; `slug` is deliberately not a third arm
  because a MERGE-stage session running on main would block its own lane's removal (spike-4).
- Do not touch `agent/session_executor.py` or `tools/agent_session_scheduler.py`.

### 2. Stamp `exec_cwd` before the harness launches, and make the cleanup honest
- **Task ID**: build-executor
- **Depends On**: none
- **Validates**: tests/unit/test_session_executor_lane_visibility.py (create),
  tests/unit/test_session_executor_reap_marker.py
- **Informed By**: spike-1 (the parameter is the model instance and the save block already exists),
  spike-3 (`slug` is a `KeyField` — never write it), spike-5 and spike-9 (the pre-finalize guard and
  why the `status == "running"` predicate makes hoisting it safe), spike-8 (`working_dir` is never
  written)
- **Assigned To**: executor-builder
- **Agent Type**: builder
- **Parallel**: true
- In the session-phase save block, add `agent_session.exec_cwd = str(working_dir)` and `"exec_cwd"`
  to the existing `update_fields` list. **Leave that block's hydration exactly as it is** — do not
  substitute `get_authoritative_session`; the Rabbit Holes section records the measurement.
- Upgrade that block's `except Exception` handler from `logger.debug` to `logger.warning` carrying
  the literal marker `[lane-writeback]`, the session id, and the resolved path.
- Add the comment at the synthetic-slug synthesis site recording that `slug` stays local because it
  is a `KeyField` in the Redis primary key.
- In the `finally`, immediately before `cleanup_after_merge` and inside the block's existing `try`,
  add the pre-finalize guard: `_auth = get_authoritative_session(session.session_id)`; when
  `_auth is not None and _auth.status == "running"`, call `finalize_session(_auth, <status>,
  reason="synthetic-cleanup pre-finalize")` wrapped in `except StatusConflictError: pass`.
  `<status>` is `_runner_final_status(_task.error, _agent_session)` with
  `_task = locals().get("task")` and `_agent_session = locals().get("agent_session")`, degrading to
  `"failed"` when `task` never came into being (spike-9 — `task` is bound partway through the body
  and is not guaranteed to exist in the `finally`).
- Do NOT pass `force=True` to `remove_worktree`, and do not weaken the
  `_session_recorded_reap_failure` skip above the cleanup.
- When `cleanup_result` carries `blocked_by_session`, emit a WARNING containing the literal
  `[synthetic-slug]` and the words `cleanup blocked`, naming the session id and the manual
  `git worktree prune` + directory removal, mirroring the neighbouring `runner_reap_failed` message.
- Do not touch `agent/worktree_manager.py` or `tools/agent_session_scheduler.py`.

### 3. Guard the scheduled-child working_dir inheritance
- **Task ID**: build-scheduler-guard
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_scheduler_worktree_inheritance.py (create)
- **Informed By**: spike-6 (the inherited lane path collides with the child's own synthesized slug;
  pre-existing, since `valor-session create` already sets `working_dir` to the lane for slugged
  sessions)
- **Assigned To**: scheduler-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/agent_session_scheduler.py`, import `WORKTREES_DIR` from `agent.worktree_manager` and
  inherit `parent_session.working_dir` only when the path does not contain that segment.
- Comment the guard with the concrete failure it prevents: a child synthesizing its own
  `dev-{aid8}` slug, skipping provisioning because the inherited path already looks like a worktree,
  then failing `verify_worktree_branch` against another session's live lane.
- Do not touch `agent/session_executor.py` or `agent/worktree_manager.py`.

### 4. Pin the behavior with tests
- **Task ID**: build-tests
- **Depends On**: build-scan, build-executor, build-scheduler-guard
- **Validates**: every file listed in Test Impact
- **Assigned To**: guard-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- **Scan tests** — in `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`:
  - synthetic shape: non-terminal row, `slug=None`, `exec_cwd=".worktrees/dev-abcd1234"`,
    `working_dir=<main checkout>` → `busy`.
  - real-slug shape: non-terminal row, `slug="sdlc-1218"`, `exec_cwd=".worktrees/sdlc-1218"`,
    `working_dir=<main checkout>` → `busy`. This is the half the earlier draft asserted and never
    tested.
  - fallback arm unchanged: `exec_cwd=None`, `working_dir=".worktrees/sdlc-1218"` → `busy`. A `None`
    first element must not short-circuit the second read.
  - relative `exec_cwd` (`".worktrees/dev-abcd1234"` with no leading slash) resolves against
    `repo_root` and matches.
  - negative: `exec_cwd=".worktrees/dev-abcd1234-other"` → `clear` (the segment-prefix guard,
    Risk 5 of #2712).
  - terminal row with a matching `exec_cwd` → `clear`.
  - Mutation-check each: revert the loop to the `working_dir`-only read and confirm the synthetic
    and real-slug cases fail; restore and re-measure.
- **Executor tests** — create `tests/unit/test_session_executor_lane_visibility.py` using the
  `tests/unit/test_teammate_cold_start_finalize.py` template (real `AgentSession.create(...)` under
  the `redis_test_db` fixture, `_patch_runner()`, `await _execute_agent_session(session)`, then
  re-read by stable `id`):
  - a slugless eng session's row carries `exec_cwd` naming `.worktrees/dev-{aid8}`, while
    `reloaded.slug is None` and `reloaded.working_dir` is unchanged from what was created.
  - `live_fence()` is `None` on a row carrying only the pre-stamp (no pid, no `spawn_history`).
  - a raising `save()` in the session-phase block produces the `[lane-writeback]` WARNING via
    `caplog` and the session still proceeds.
  - terminal-row cleanup branch: the worktree is removed.
  - raising-exit branch: the pre-finalize guard finalizes the row, so the worktree is removed rather
    than blocked, and no `NameError` escapes when `task` was never bound.
  - deferred/auto-continue exit: the continuation's `pending` row is untouched, the cleanup is
    refused, and the `[synthetic-slug] ... cleanup blocked` WARNING is emitted.
- **Source-level test** — in `tests/unit/test_session_isolation_bypass.py`, add only the "slug stays
  local" source assertion. Do not add behavioral assertions to this file; every test in it is a
  logic mirror or a source regex.
- **Scheduler test** — create `tests/unit/test_agent_session_scheduler_worktree_inheritance.py`
  asserting a worktree-rooted parent `working_dir` is declined and a plain-checkout one is still
  inherited.
- Run scoped: `scripts/pytest-clean.sh <the files above> -q`, and report the passed count from the
  summary line.

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: lane-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Apply the four feature-doc updates listed in the Documentation section.
- Confirm `docs/features/README.md` already indexes all four pages; add a row only if one is
  missing.
- Carry the deploy note into the PR body: this lane touches `agent/`, so the merge needs
  `./scripts/valor-service.sh restart` on bridge and worker machines.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: build-scan, build-executor, build-scheduler-guard, build-tests, document-feature
- **Assigned To**: lane-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table and report each result, including the passed count from
  the test row's summary line.
- Confirm each Success Criteria checkbox against observed output, not against the diff.

## Verification

Run every row from the lane worktree. Greps use `/usr/bin/grep` explicitly: an interactive shell on
this machine resolves `grep` to `ugrep`, which honors `.gitignore` and can disagree with the runner
about what it searched.

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `scripts/pytest-clean.sh tests/unit/worktree_manager/test_worktree_manager_busy_guards.py tests/unit/test_session_executor_lane_visibility.py tests/unit/test_session_executor_reap_marker.py tests/unit/test_session_isolation_bypass.py tests/unit/test_agent_session_scheduler_worktree_inheritance.py -q` | summary line reads `N passed` with `N > 0` and zero failed/errored. Exit code alone is NOT sufficient — 0 collected also exits 0. |
| Lint clean | `.venv/bin/python -m ruff check agent/session_executor.py agent/worktree_manager.py tools/agent_session_scheduler.py` | exit code 0 |
| Format clean | `.venv/bin/python -m ruff format --check agent/session_executor.py agent/worktree_manager.py tools/agent_session_scheduler.py` | exit code 0 |
| Scan reads `exec_cwd` | `/usr/bin/grep -cE 'getattr\(session, "exec_cwd"' agent/worktree_manager.py` | `> 0` |
| Executor stamps `exec_cwd` | `/usr/bin/grep -c 'exec_cwd' agent/session_executor.py` | `> 0` |
| Write-failure marker present | `/usr/bin/grep -c '\[lane-writeback\]' agent/session_executor.py` | `> 0` |
| Pre-finalize guard present | `/usr/bin/grep -c 'synthetic-cleanup pre-finalize' agent/session_executor.py` | `> 0` |
| Cleanup block is loud | `/usr/bin/grep -c 'cleanup blocked' agent/session_executor.py` | `> 0` |
| Scheduler guard present | `/usr/bin/grep -c 'WORKTREES_DIR' tools/agent_session_scheduler.py` | `> 0` |
| **Anti:** neither `slug` nor `working_dir` is assigned on a hydrated row in this plan's changed files | `/usr/bin/grep -nE '^[[:space:]]*[A-Za-z_][A-Za-z0-9_]*\.(slug\|working_dir)[[:space:]]*=[^=]' agent/session_executor.py agent/worktree_manager.py tools/agent_session_scheduler.py \| wc -l` | `0` |
| **Anti:** no `slug` arm in the busy scan | `/usr/bin/grep -cE 'getattr\(session, .slug.' agent/worktree_manager.py` | `0` |
| **Anti:** no new lane field on the model | `/usr/bin/grep -c 'active_worktree_dir' models/agent_session.py` | `0` |
| **Anti:** the session-phase hydration is unchanged (the resolver swap stayed out) | `/usr/bin/grep -c 'AgentSession.query.filter(project_key=session.project_key, status="running")' agent/session_executor.py` | `1` |
| **Structural:** `exec_cwd` is still reset on continuation (this is what makes the stale-lane hazard absent) | `/usr/bin/grep -c '"exec_cwd",  # Working dir' agent/agent_session_queue.py` | `1` |

**Red-state proof (measured on `d786c8ad2`, before any implementation).** Every positive row was run
against main and returned a failing value; every anti-criterion pattern was proved to bite against a
seeded violation; every structural row was confirmed at its expected value.

| Row | Value on main | Verdict |
|---|---|---|
| `getattr(session, "exec_cwd"` in worktree_manager.py | 0 | FAIL (expected `> 0`) |
| `exec_cwd` in session_executor.py | 0 | FAIL (expected `> 0`) |
| `[lane-writeback]` | 0 | FAIL (expected `> 0`) |
| `synthetic-cleanup pre-finalize` | 0 | FAIL (expected `> 0`) |
| `cleanup blocked` | 0 | FAIL (expected `> 0`) |
| `WORKTREES_DIR` in scheduler | 0 | FAIL (expected `> 0`) |
| anti: `.slug` / `.working_dir` assignment in changed files | 0 on main; **1** against a seeded `agent_session.working_dir = str(working_dir)`; **1** against a seeded `agent_session.slug = slug` | pattern bites in both directions |
| anti: `getattr(session, .slug.` | 0 on main; **1** against a seeded slug arm | pattern bites |
| anti: `active_worktree_dir` | 0 on main; **1** against a seeded field declaration | pattern bites |
| anti: session-phase hydration literal | **1** on main | already at its expected value; this row fails if the resolver swap is reintroduced |
| structural: `exec_cwd` in the continuation reset list | **1** on main | already at its expected value; this row fails if the reset is removed |

The working tree was restored byte-for-byte after each seeded mutation (`git diff --stat` empty).

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Risk & Robustness | The persisted lane path outlives the lane. The synthetic cleanup deletes `.worktrees/dev-{aid8}` in `_execute_agent_session`'s `finally` (session_executor.py:2753-2790) but the row keeps pointing at it. `valor-session resume` (valor_session.py:1157-1162, `reject_from_terminal=False`), the nudge requeue (agent_session_queue.py:2906-2919) and `retry_agent_session` (`clone_agent_session_fields`, agent_session_queue.py:177-201 — `working_dir` is not in `_EXECUTION_FENCE_RESET_FIELDS`) all re-execute or copy that row. Next run, `Path(session.working_dir)` (session_executor.py:1278) names a deleted dir, `validate_workspace` fails invariant 1 and falls back to `allowed_root = Path.home()/"src"` (worktree_manager.py:812-817), which is not a git repo; `get_or_create_worktree(~/src, slug)` then runs `git worktree add` outside any repository, or the #887 guard at :1417-1428 refuses the session. Unreachable today. Architectural Impact's "a stale lane path in a terminal row is ignored by every reader" is false: the executor is a reader, and spike-6 missed it. | pending | Two edits. (1) In the synthetic cleanup block, when `cleanup_result.get("worktree_removed")` is truthy, re-hydrate via `get_authoritative_session(session.session_id, project_key=session.project_key)` and if the row still names the removed lane write `_auth.working_dir = str(resolve_main_repo_root(_wd))` + `_auth.save(update_fields=["working_dir", "updated_at"])`, inside the block's existing `try` so it stays non-fatal. (2) At session_executor.py:1278, when `WORKTREES_DIR in str(session.working_dir) and not Path(session.working_dir).exists()`, re-seed from `resolve_main_repo_root(...)` BEFORE `validate_workspace` so the `~/src` fallback can never become the base passed to `get_or_create_worktree`. Pin (2) with a test that a deleted `.worktrees/dev-*` path re-provisions under the repo root, not under `Path.home()/"src"`. |
| BLOCKER | History & Consistency | The row already records the lane, in a field with the correct lifecycle. `AgentSession.exec_cwd` (models/agent_session.py:346) is stamped on every harness spawn by `stamp_execution_spawn(..., cwd=self._working_dir, ...)` (session_runner/runner.py:695-705), where `_working_dir` is the executor's resolved lane (runner.py:395), and it is persisted via `save(update_fields=[..., "exec_cwd", ...])` (agent_session.py:1287-1300). It is already excluded from continuations — `_EXECUTION_FENCE_RESET_FIELDS` lists `exec_cwd` as "Working dir that spawn ran in; resume is cwd-scoped" (agent_session_queue.py:131-143). The Rabbit Hole rejecting "a second field (`active_worktree_dir`)" misses that the second field exists, needs no model change, and is already populated. Matching the busy scan on `exec_cwd` closes the blind spot with none of the lifecycle hazard of making `working_dir` execution-scoped. | pending | In `_scan_worktree_sessions` (worktree_manager.py:579-614) replace `wd = getattr(session, "working_dir", None)` with a loop `for wd in (getattr(session, "exec_cwd", None), getattr(session, "working_dir", None)):` running the existing normalize + segment-prefix match over each and returning `("busy", ...)` on the first hit; the `if not wd: continue` guard already covers the `None` a never-spawned row carries. One real trade to measure rather than assume: `exec_cwd` lands at spawn (runner.py:699) whereas the plan's write lands at session_executor.py:1510, so an exec_cwd-only variant leaves the Race 1 window marginally wider. If that gap matters, keep both arms rather than persisting `working_dir`. |
| CONCERN | Risk & Robustness | spike-5 reasons only about the completion exit, but the block it protects is not on that path. `_execute_agent_session` has one top-level `try:` (session_executor.py:1160), one top-level `finally:` (:2736), and no top-level `except`, so the synthetic cleanup at :2753 runs on exception and cancellation exits too. The completion-exit finalize guard it relies on sits at :2532 inside `if not chat_state.defer_reaction:` on the normal-return path, so on any raising or cancelled exit nothing has flipped the row terminal. After this change every such exit yields `blocked_by_session` and leaks the lane. Risk 2's mitigation ("the health checker finalizes stuck rows, after which the next cleanup pass clears") has no next cleanup pass: this cleanup only runs inside this `finally`, and `sweep_worktrees` requires `merged_via_tree`, which a `session/dev-*` branch never satisfies. The leak is permanent, not bounded. | pending | Ahead of `cleanup_after_merge`, add `_auth = get_authoritative_session(session.session_id)` and when `_auth is not None and _auth.status == "running"` call `finalize_session(_auth, _runner_final_status(task.error, agent_session), reason="synthetic-cleanup pre-finalize")` inside `except StatusConflictError: pass` — identical in shape to :2536-2547, hoisted out of the `if not chat_state.defer_reaction:` conditional. Do NOT use `remove_worktree(force=True)`: forcing is the deletion-under-a-live-subprocess failure #1938 produced, and the `_session_recorded_reap_failure` skip above must keep winning. |
| CONCERN | Scope & Value | Swapping the linear scan for `get_authoritative_session` is scope creep with unmeasured blast radius. The `agent_session` that block produces also feeds `_session_type` (session_executor.py:1514), which drives the harness `SESSION_TYPE` env (:2216), the ENG/TEAMMATE permission branch (:2217-2222), and runner dispatch (:1649, :2176, :2255, :2308). Today `agent_session` stays `None` unless a `status="running"` row matches; `get_authoritative_session` applies no status filter (session_lifecycle.py:136-139), so `_session_type` becomes non-`None` on paths where it is `None` today. No task, success criterion, or test pins that. | pending | Write it as `_auth = get_authoritative_session(session.session_id, project_key=session.project_key)` then `agent_session = _auth if _auth is not None and _auth.status == "running" else None`, and perform the `working_dir` / `branch_name` / `task_list_id` save on `_auth`. Downstream `_session_type` reads stay byte-identical while the write still routes through the newest-wins resolver. Add one test asserting a session with a terminal duplicate and no running row still yields `_session_type is None`. |
| CONCERN | Scope & Value | The write rule is deliberately broad ("any session whose resolved `working_dir` differs from the stored value, not only synthetic ones") but only the synthetic shape is demonstrated, spiked, or tested. The claim that "a real-slug session that was enqueued against the main checkout has the identical blind spot" carries no spike, no cited call site, and no verification row; every case in Task 3 uses `dev-abcd1234` or a `slug=None` row. The untested half of the rule is also the half whose lanes and rows live longest. | pending | If narrowing, gate on the flag already in scope: `if is_synthetic_slug and str(working_dir) != (agent_session.working_dir or ""):` — `is_synthetic_slug` is set at session_executor.py:1310-1318 and stays live through the save block. If keeping the broad rule, add a real-slug case to tests/unit/worktree_manager/test_worktree_manager_busy_guards.py (`working_dir=".worktrees/sdlc-1218"`, `slug="sdlc-1218"`) so both halves are pinned. |
| CONCERN | History & Consistency | Test Impact says of tests/unit/test_session_isolation_bypass.py that its tests "mirror the synthesis logic rather than driving `_execute_agent_session`, so none break" — accurate: every test there is a static `_should_block` mirror (:65-140) or a source-text regex (:281-400). Task 3 then assigns that same file two assertions that require actually driving the executor (the row persists `working_dir` and leaves `slug` unset; a raising `save()` emits the `[lane-writeback]` WARNING via caplog). A builder following the plan literally will satisfy them with more source greps, which proves nothing about behavior and contradicts the plan's own "observable behavior, not `pass`" requirement. | pending | tests/unit/test_teammate_cold_start_finalize.py:122-175 is the working template: a real `AgentSession.create(...)` under the `redis_test_db` fixture, `_patch_runner()` to stub the harness, `await _execute_agent_session(session)`, then `AgentSession.get_by_id(...)` to assert persisted state. Use that shape for the write-back and `[lane-writeback]` cases (assert `reloaded.slug is None` alongside `reloaded.working_dir`); keep test_session_isolation_bypass.py for the "slug stays local" source assertion only. |
| CONCERN | Risk & Robustness | Verification row 1 runs `.venv/bin/python -m pytest ...`. CLAUDE.md forbids bare pytest and requires `scripts/pytest-clean.sh`, which reaps xdist workers, bounds the run, and pins `PYTHONPATH` to the invoking checkout so a lane worktree exercises worktree code. Run from `.worktrees/{slug}` the plan's command validates the wrong tree — the same confusion this plan exists to fix. The row also passes a directory and accepts "exit code 0", which cannot distinguish "tests passed" from "0 tests collected". | pending | Replace with `scripts/pytest-clean.sh tests/unit/test_session_isolation_bypass.py tests/unit/worktree_manager/test_worktree_manager_busy_guards.py tests/unit/test_session_executor_reap_marker.py tests/unit/test_agent_session_scheduler_worktree_inheritance.py -q` and set Expected to "N passed with N > 0" read off the summary line, never "exit code 0". The wrapper supplies `--timeout=420 --timeout-method=thread`, so drop those flags. |
| NIT | Scope & Value | Success Criterion 3 is repo-wide ("No production code assigns `.slug` on a hydrated `AgentSession` instance") but its anti-criterion greps only `agent/session_executor.py`. A future `.slug =` in the scheduler, the queue, or tools/valor_session.py would satisfy the table while violating the criterion. | pending | Widen the anti-criterion grep to the production tree so the probe is as wide as the claim. |
| NIT | History & Consistency | No-Gos opens "Nothing deferred — every relevant item is in scope for this plan", while Rabbit Holes defers "Reworking the `defer_reaction`-gated finalize guard" and Open Question 3 asks whether that guard is "Worth its own issue?". Something is deferred, and a reader scanning only the No-Gos header will miss it. | pending | Move the deferred finalize-guard item into No-Gos with its follow-up disposition, or reword the opening sentence to point at Rabbit Holes. |

### Rulings on the plan's Open Questions

1. **Scope of the write-back — narrow to `is_synthetic_slug`?** Narrowing does not buy safety here: the
   synthetic lane is precisely the one deleted at end of session, so it is the *source* of the
   BLOCKER-1 lifecycle hazard, not an exemption from it. Ruling: do not narrow on safety grounds. Either
   keep the broad rule and pin the real-slug half with a test, or narrow and say plainly that the
   real-slug blind spot is untested and deferred (Scope & Value concern above gives both forms).

2. **The leak in Risk 2 — is an operator-facing log enough?** No, as written. The Risk & Robustness
   concern above shows the leak is permanent rather than bounded on every exception and cancellation
   exit, because this cleanup only runs inside `_execute_agent_session`'s `finally` and there is no
   later pass. Ruling: the pre-finalize guard belongs in this plan; growing `sweep_worktrees` a
   synthetic-lane path stays out of scope.

3. **The `defer_reaction` finalize gate — worth its own issue?** Yes, file it, and keep it out of this
   plan. The pre-finalize guard in ruling 2 makes the refusal survivable without touching the gate's
   semantics, which is the right seam. Record the follow-up issue number in No-Gos rather than leaving
   the deferral only in Rabbit Holes (NIT above).

---

## Open Questions

1. **Scope of the write-back.** This plan persists `working_dir` whenever the executor's resolved path differs from the stored one, not only for synthetic slugs. That is the more correct rule and it widens the blast radius to any lane resolved at execution time. Should it be narrowed to `is_synthetic_slug` for the first landing?
2. **The leak in Risk 2.** A row stuck at `running` holds its `dev-*` lane indefinitely, and `sweep_worktrees` will not reclaim it because the synthetic branch is never merged. This plan makes that state loud rather than reclaimable. Is an operator-facing log enough, or should the sweep grow an explicit synthetic-lane path?
3. **The `defer_reaction` finalize gate.** `agent/session_executor.py:2532` skips the completion-exit finalize guard when a reaction is deferred, which is the one ordinary way a session reaches its own cleanup while still `running`. This plan treats that as out of scope and only makes the resulting refusal visible. Worth its own issue?
