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

A slugless eng session is given a synthetic slug at `agent/session_executor.py:1314`
(`dev-{agent_session_id[:8]}`, issue #1272) and a worktree is provisioned for it at
`agent/session_executor.py:1377`. Both the slug and the resolved worktree path are **local
variables**. Neither is written back to the `AgentSession` row.

**Current behavior:** the session runs inside `.worktrees/dev-{aid8}/` while its stored row says
`slug=None` and `working_dir=<main checkout>`. `_scan_worktree_sessions()`
(`agent/worktree_manager.py:503`) matches a lane by the **stored** `working_dir` only
(`agent/worktree_manager.py:579-614`), so it never matches. `worktree_busy_check()`,
`worktree_busy_probe()`, and `worktree_busy_probe_many()` all report a `dev-*` lane **clear**
while a session is live inside it. Only `_worktree_has_live_process()` — which asks the OS, not
Redis — sees the lane.

**Desired outcome:** the AgentSession row tells the truth about where its session is running, so
the session-table busy predicate matches a synthetic lane the same way it matches a real one, and
the OS-process scan goes back to being a backstop rather than the only working guard.

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

### spike-1: Is the AgentSession row the executor already holds the right object to write?
- **Assumption**: "`_execute_agent_session(session)` receives something other than the stored row."
- **Method**: code-read
- **Finding**: **False.** `agent/session_executor.py:1117` is `async def _execute_agent_session(session: AgentSession)`. The parameter *is* the model instance. It already hydrates a second copy at `:1495-1502` and saves `branch_name` / `task_list_id` on it with `save(update_fields=[...])`, which is the established partial-save precedent in this exact function.
- **Confidence**: high
- **Impact on plan**: the write-back is a field assignment plus a partial save at a seam that already exists. No new plumbing.

### spike-2: Does persisting `working_dir` change how the row is keyed or indexed?
- **Assumption**: "`working_dir` is a plain field."
- **Method**: code-read
- **Finding**: **Confirmed.** `models/agent_session.py:186` — `working_dir = Field()`. Not a `KeyField`, not indexed, not part of `_meta.key_field_names`. A partial save mutates one hash field.
- **Confidence**: high
- **Impact on plan**: `working_dir` is the safe half of the issue's proposal.

### spike-3: Does persisting `slug` change how the row is keyed?
- **Assumption**: "`slug` is a plain indexed field, so writing it is as safe as `working_dir`."
- **Method**: code-read plus live metadata read
- **Finding**: **False, and this is the plan's pivot.** `models/agent_session.py:392` declares `slug = KeyField(null=True)` (issue #1085). `AgentSession._meta.key_field_names` resolves to `['chat_id', 'id', 'parent_agent_session_id', 'project_key', 'session_type', 'slug']` with `db_key_length == 7`. Popoto concatenates key-field values into the Redis primary key (`popoto/fields/key_field_mixin.py:150-157`). Assigning `slug` on a hydrated instance and saving therefore writes a **new row at a new db_key** and orphans the original at the `slug=None` key — the duplicate-row condition `models/session_lifecycle.get_authoritative_session()` exists to tie-break, plus a leaked `status=running` orphan that never finalizes.
- **Confidence**: high
- **Impact on plan**: **do not persist `slug`.** The issue's option (a) is half-safe. Persisting `working_dir` alone is sufficient, because `_scan_worktree_sessions` matches on `working_dir` and nothing else — the "hypothetical indexed `slug=` lookup" in the issue is genuinely hypothetical; no code performs one.

### spike-4: Would matching on `slug` instead be a safer predicate?
- **Assumption**: "Adding a `slug ==` arm to `_scan_worktree_sessions` is cheap defense-in-depth."
- **Method**: code-read
- **Finding**: **False — it would break post-merge cleanup.** MERGE is a main-checkout stage (`AgentSession.worker_key` docstring; `_ENG_WORKTREE_STAGES` allowlist), so the MERGE-stage eng session for slug X runs on main and calls `cleanup_after_merge(X)` → `remove_worktree` → `worktree_busy_check`. With a `slug ==` arm, that session would block its own lane's removal on every merge. The `working_dir` predicate is correct precisely because it distinguishes "owns the slug" from "is standing in the lane".
- **Confidence**: high
- **Impact on plan**: slug-matching moves to No-Gos with this reason recorded.

### spike-5: What does the newly-visible row do to the end-of-session synthetic cleanup?
- **Assumption**: "Making the lane visible has no effect on the cleanup that deletes it."
- **Method**: code-read
- **Finding**: **False.** `agent/session_executor.py:2787` calls `cleanup_after_merge` → `agent/worktree_manager.py:2602` `remove_worktree(..., delete_branch=False)` → `:2186` `worktree_busy_check`. Once `working_dir` is persisted, that check matches whenever the row is still non-terminal at cleanup time, and `cleanup_after_merge` returns `blocked_by_session` instead of removing. On the ordinary exit the completion-exit finalize guard (`:2532-2547`) has already flipped the row terminal, so the guard clears and behavior is unchanged. The residual case is a row that never finalized — where **preserving** the worktree is the correct outcome (#1938's whole lesson), but it is currently reported only inside an INFO-level result dict.
- **Confidence**: high
- **Impact on plan**: no behavioral change is needed at the cleanup site, but the block must be logged loudly under the existing `[synthetic-slug]` marker, and both branches must be pinned by tests.

### spike-6: Who else reads the stored `working_dir`?
- **Assumption**: "`_scan_worktree_sessions` is the only consumer, so the write is invisible elsewhere."
- **Method**: code-read
- **Finding**: **False — one more.** `tools/agent_session_scheduler.py:434-435` copies `parent_session.working_dir` onto a scheduled child. After the write-back, a scheduled child of a synthetic-slug parent would inherit `.worktrees/dev-{parent}` while synthesizing its *own* `dev-{child}` slug. The executor's `needs_wt` branch (`:1373`) then sees a path already under `.worktrees/` that exists, skips provisioning, and hands the parent's worktree to `verify_worktree_branch(working_dir, "session/dev-{child}")` — a branch mismatch against a lane another session is live in.
- **Confidence**: high
- **Impact on plan**: the inheritance needs a guard. It is ~4 lines and one test, and it is a latent bug on its own terms (the same inheritance already misfires for any parent whose row legitimately carries a lane path).

## Data Flow

1. **Entry point**: the worker pops an eng `AgentSession` with `slug=None` and `working_dir=<main checkout>` and calls `_execute_agent_session(session)` (`agent/session_executor.py:1117`).
2. **`agent/session_executor.py:1277`**: `working_dir = Path(session.working_dir)` — the local is seeded from the stored row and validated by `validate_workspace`.
3. **`:1310-1318`**: `slug` is synthesized to `dev-{aid[:8]}`; `is_synthetic_slug = True`. Local only.
4. **`:1344-1345`**: the synthetic case forces `resolved_branch = session/{slug}` and `needs_wt = True`.
5. **`:1373-1378`**: `get_or_create_worktree(working_dir, slug)` creates `.worktrees/{slug}` and the local `working_dir` is rebound to it. **This is where the row and reality diverge today.**
6. **`:1412-1430`**: the #887 main-checkout guard passes, because it reads the *local* `working_dir`.
7. **`:1440-1452`**: `verify_worktree_branch(working_dir, branch_name)` confirms the lane is on `session/{slug}`.
8. **`:1493-1510`**: the row is re-hydrated and `branch_name` / `task_list_id` are persisted with `save(update_fields=[...])`. `working_dir` is not.
9. **Harness launch**: `claude -p` runs with `cwd=.worktrees/{slug}`.
10. **Concurrent reader** — `tools/disk_reclaim.py` sweep, `reap_idle_worktree`, or an interactive `remove_worktree` — calls `_scan_worktree_sessions(repo_root, slug)`, which reads the stored `working_dir` (main checkout), fails the segment-prefix match at `agent/worktree_manager.py:604-607`, and returns `("clear", "", "")`.
11. **Output today**: the lane reads clear while a session is live in it. Only `_worktree_has_live_process` prevents deletion.
12. **Output after this plan**: step 8 also persists `working_dir`, so step 10 matches and returns `("busy", session_id, agent_session_id)`.

## Architectural Impact

- **New dependencies**: none.
- **Interface changes**: none. No function signature changes, no new fields, no schema change — `working_dir` is an existing `Field()` on `AgentSession`.
- **Coupling**: unchanged in shape, corrected in content. The busy guard already depends on `AgentSession.working_dir` being the truth; this makes it true.
- **Data ownership**: `working_dir` becomes a field the executor may update, not only the enqueue path. That is a deliberate widening, scoped to the one place the executor changes the value it was handed.
- **Reversibility**: high. Reverting the write-back restores today's behavior exactly; nothing persists a value that later code cannot cope with (a stale lane path in a terminal row is ignored by every reader, because `_scan_worktree_sessions` skips terminal statuses).

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

- **Lane write-back (`agent/session_executor.py`)**: when the executor resolves a working directory that differs from the one stored on the row, it persists the resolved path back onto the authoritative row before the harness launches.
- **Slug stays local, on purpose**: `slug` is a `KeyField` and part of the Redis primary key, so it is never written back. A comment at the synthesis site records why, so the next reader does not "finish the job" and fork the row.
- **Loud cleanup block (`agent/session_executor.py`)**: the synthetic-slug cleanup already receives `blocked_by_session` from `cleanup_after_merge`; it now surfaces that as a `[synthetic-slug]` WARNING naming the session and the manual reclamation command, matching the shape of the neighbouring `runner_reap_failed` skip.
- **Inheritance guard (`tools/agent_session_scheduler.py`)**: a scheduled child no longer inherits a parent `working_dir` that points inside `.worktrees/`, so it cannot be handed a lane another session owns.

### Flow

Worker pops slugless eng session → executor synthesizes `dev-{aid8}` and provisions `.worktrees/dev-{aid8}` → **executor persists the resolved `working_dir` onto the row** → harness runs in the lane → a concurrent sweep asks `worktree_busy_probe` → **`busy`, lane skipped** → session finalizes → cleanup re-asks → `clear` → worktree and branch removed.

### Technical Approach

- **Where the write goes.** Extend the existing session-phase save at `agent/session_executor.py:1493-1510`. It already re-hydrates the row and calls `save(update_fields=["updated_at", "branch_name", "task_list_id"])`; add `working_dir` to both the assignment set and `update_fields`. Assign only when `str(working_dir) != (agent_session.working_dir or "")`, so the ordinary case writes nothing new.
- **Which row.** That block currently hydrates via `AgentSession.query.filter(project_key=..., status="running")` and linear-scans for `session_id`. Replace that scan with `models.session_lifecycle.get_authoritative_session(session.session_id, project_key=session.project_key)` so the write lands on the newest-wins row rather than an arbitrary duplicate. This is the resolver the repo standardized on in `3c77e1eab`.
- **Failure posture.** The write is best-effort and non-fatal — the enclosing `try` already logs at debug. Upgrade the failure log for the `working_dir` case to WARNING with a stable `[lane-writeback]` marker: a lane that stays invisible is exactly the condition this plan exists to detect, and it must be greppable. Raising instead would turn a Redis blip into a failed eng session, which is strictly worse than falling back to the OS-process scan.
- **Scope of the write.** Applies to any session whose resolved `working_dir` differs from the stored value, not only synthetic ones. The rule is "the row records where the session actually runs", and a real-slug session that was enqueued against the main checkout has the identical blind spot.
- **Cleanup interaction.** No behavioral change at `agent/session_executor.py:2787`. `cleanup_after_merge` already returns `blocked_by_session` and already skips removal; only the logging changes, from an INFO dict to a named WARNING. On the ordinary exit the completion-exit finalize guard has flipped the row terminal first, so the guard clears and the worktree is removed exactly as today.
- **Scheduler guard.** In `tools/agent_session_scheduler.py:434-435`, inherit `parent_session.working_dir` only when it is not inside `.worktrees/`. Import `WORKTREES_DIR` from `agent.worktree_manager` for the comparison rather than hardcoding the literal.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/session_executor.py:1510-1511` (`except Exception as e: logger.debug(...)`) — the session-phase save's handler. Add a test that forces `save()` to raise and asserts the `[lane-writeback]` WARNING is emitted and the session still proceeds (observable behavior, not `pass`).
- [ ] `agent/session_executor.py:2793-2796` (the synthetic-cleanup `except`) — already logs; add a test that a raising `cleanup_after_merge` does not propagate as a session failure.
- [ ] `tools/agent_session_scheduler.py` — the inheritance guard adds no exception handler; the comparison is a pure string check on an already-read value.

### Empty/Invalid Input Handling
- [ ] `agent_session.working_dir` may be `None` on the stored row. The difference check must treat `None` as "differs" and write, never raise on `None.__str__` comparison — covered by comparing against `(agent_session.working_dir or "")`.
- [ ] `get_authoritative_session` returns `None` when no row matches. The write-back must no-op with the `[lane-writeback]` WARNING rather than raise `AttributeError`.
- [ ] `parent_session.working_dir` may be `""` or `None` in the scheduler; the existing `if parent_session.working_dir:` truthiness check already handles both, and the new guard is evaluated after it.

### Error State Rendering
- [ ] This feature has no user-visible output. Its observable surface is the log: `[lane-writeback]` on a failed persist and `[synthetic-slug] cleanup blocked` on a refused removal. Both are asserted by tests with `caplog`, so a silently-swallowed failure fails the suite.

## Test Impact

- [ ] `tests/unit/test_session_isolation_bypass.py` — UPDATE: the synthetic-slug tests mirror the synthesis logic rather than driving `_execute_agent_session`, so none break. Add a new case in the same file asserting the row's `working_dir` is persisted while `slug` stays `None`.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py` — UPDATE: add a case proving `_scan_worktree_sessions` matches a row whose `working_dir` is `.worktrees/dev-abcd1234` and whose `slug` is `None`, i.e. the exact synthetic shape.
- [ ] `tests/unit/test_session_executor_reap_marker.py` — UPDATE: extend the synthetic-cleanup source guard to also assert the blocked-by-session WARNING path exists.
- [ ] `tests/unit/test_valor_session_working_dir_resolution.py` — no change: it pins `valor-session create`, a different code path that already re-derives `working_dir` from the project key rather than copying the parent's.
- [ ] `tests/unit/test_session_executor_guards.py` — no change: it pins the `None`-field preconditions ahead of `Path(session.working_dir)`, which this plan does not move.

## Rabbit Holes

- **Persisting the synthetic `slug` "for symmetry".** It looks like the obvious other half of the fix and it forks the Redis row (spike-3). Stop at `working_dir`.
- **Adding a `slug ==` arm to `_scan_worktree_sessions`.** It reads as free defense-in-depth and it deadlocks post-merge cleanup against its own MERGE session (spike-4).
- **Introducing a second field (`active_worktree_dir`) to leave `working_dir` untouched.** It avoids the scheduler blast radius at the cost of two competing answers to "where is this session running", plus a model change. Considered and rejected; recorded here so critique can reopen it deliberately rather than by accident.
- **Reworking the `defer_reaction`-gated finalize guard.** The conditional at `agent/session_executor.py:2532` is why a deferred session can reach cleanup while still `running`. That is worth understanding, and it is a different bug with a different blast radius. This plan only makes the outcome loud.
- **Auditing every `AgentSession.query.filter(...)` linear scan.** Replacing the one scan inside the block being edited is in scope. Sweeping the rest of the file is not.

## Risks

### Risk 1: A newly-visible row blocks a removal that used to succeed
**Impact:** any caller that previously saw a synthetic lane as `clear` now sees `busy` while the row is non-terminal. Concretely: `remove_worktree` returns `("blocked", session_id)`, `reap_idle_worktree` returns `(False, "live_session:...")`, and the daily sweep skips the lane.
**Mitigation:** this is the guard doing its job — every one of those refusals is protecting a directory a live process is sitting in. The one case that could surprise is the session's own end-of-run cleanup, and spike-5 established that the completion-exit finalize guard flips the row terminal first on the ordinary path. Both branches get a pinning test, and the refusal is logged under `[synthetic-slug]` with the manual reclamation command.

### Risk 2: A lane leaks when a row never reaches a terminal status
**Impact:** if a row is stuck `running`, the busy guard holds its worktree forever. `sweep_worktrees` will not reclaim it either — it requires `merged_via_tree`, and a synthetic `session/dev-*` branch is never merged.
**Mitigation:** bounded and observable. The health checker finalizes stuck rows, after which the next cleanup pass clears. Until then the `[synthetic-slug] cleanup blocked` WARNING names the session id and the exact `git worktree prune` + directory removal to run, mirroring the precedent set by the `runner_reap_failed` skip in the same block. Leaking a directory is the correct trade against deleting one out from under a live subprocess (#1938).

### Risk 3: The write lands on the wrong duplicate row
**Impact:** if a `session_id` has duplicate rows, writing `working_dir` to the wrong one leaves the lane invisible anyway.
**Mitigation:** route the hydration through `get_authoritative_session`, the repo's single newest-wins resolver, instead of the current `filter(status="running")` linear scan. This narrows a pre-existing hazard rather than adding one.

### Risk 4: Scheduled children inherit a lane path
**Impact:** without the scheduler guard, a scheduled child of a synthetic-slug parent inherits `.worktrees/dev-{parent}`, synthesizes its own `dev-{child}` slug, skips worktree provisioning because the inherited path already looks like a worktree, and then fails `verify_worktree_branch` against a lane another session is live in.
**Mitigation:** the inheritance guard in `tools/agent_session_scheduler.py` is part of this plan, not a follow-up, with a test that pins a worktree-rooted parent path being declined.

## Race Conditions

### Race 1: Busy read between worktree creation and the write-back
**Location:** `agent/session_executor.py:1377` (worktree created) through `:1510` (row saved).
**Trigger:** a sweep or an interactive `remove_worktree` runs against `.worktrees/{slug}` inside that window. The directory exists; the row does not yet name it.
**Data prerequisite:** the persisted `working_dir` must be visible before any external reader can conclude "clear".
**State prerequisite:** none beyond the row existing.
**Mitigation:** the window cannot be closed by ordering alone — the worktree must exist before its path can be persisted — so it is narrowed and backstopped rather than eliminated. The write happens before the harness subprocess launches, which is the earliest point the path is known, and `_worktree_has_live_process` covers the interval (a bare `git worktree add` with no process in it is genuinely idle and safe to reap). This is a strict improvement on today, where the window is the entire session.

### Race 2: Cleanup versus a not-yet-finalized row
**Location:** `agent/session_executor.py:2532-2547` (finalize guard) and `:2787` (cleanup).
**Trigger:** the cleanup runs while the row is still `running` — either `defer_reaction` skipped the finalize guard, or the finalize raised.
**Data prerequisite:** the row's terminal status must be committed before the busy check reads it.
**State prerequisite:** the subprocess must be confirmed dead, which the runner's synchronous reap already guarantees ahead of this block.
**Mitigation:** no new ordering is imposed. The guard fails closed — the worktree is preserved — and the refusal is logged. Preservation is the correct outcome for a session that has not finished.

### Race 3: Concurrent write to the same row
**Location:** `agent/session_executor.py:1493-1510`.
**Trigger:** the health checker or a steering write saves the same row while the executor persists `working_dir`.
**Data prerequisite:** none — `working_dir` is written by no other actor (verified by the exhaustive write-site grep in the Freshness Check).
**State prerequisite:** none.
**Mitigation:** `save(update_fields=[...])` writes only the named hash fields, so a concurrent writer touching other fields cannot be clobbered and cannot clobber this one.
