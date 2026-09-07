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

## No-Gos (Out of Scope)

Nothing deferred — every relevant item is in scope for this plan. The tempting adjacent work
(persisting `slug`, adding a `slug ==` match arm, a second `active_worktree_dir` field, reworking
the `defer_reaction`-gated finalize guard, auditing the file's other linear scans) is listed under
Rabbit Holes with the reason each one is wrong or separable, and each is anchored by a Verification
row where it names a forbidden code-level outcome.

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
- [ ] Update `docs/features/session-isolation.md`: state that the executor persists the resolved lane path onto the `AgentSession` row, and that `slug` is deliberately never written back because it is a `KeyField` participating in the Redis primary key.
- [ ] Update `docs/features/worktree-manager.md`: record that the session-table busy predicate matches on the stored `working_dir` only, and that this is now populated for lanes resolved at execution time.
- [ ] Update `docs/features/scheduled-disk-reclaim.md`: note that the AgentSession probe is no longer blind to execution-time lanes, so the OS-process scan is a backstop rather than the sole working guard.
- [ ] `docs/features/README.md` needs no new row — all three pages already have entries.

### External Documentation Site
- [ ] Not applicable; this repo publishes no external docs site.

### Inline Documentation
- [ ] Comment at the synthesis site (`agent/session_executor.py:1310-1318`) explaining that `slug` stays local because it is a `KeyField` and a mid-flight write forks the Redis row.
- [ ] Docstring note on `_scan_worktree_sessions` recording that `working_dir` is the deliberate predicate and why `slug` is not (the post-merge self-block in spike-4).

## Success Criteria

- [ ] A slugless eng session's `AgentSession` row carries `working_dir=.worktrees/dev-{aid8}` while it runs
- [ ] `_scan_worktree_sessions` returns `busy` for a `dev-*` lane held by a non-terminal, `slug=None` session
- [ ] No production code assigns `.slug` on a hydrated `AgentSession` instance
- [ ] The end-of-session synthetic cleanup still removes the worktree when the row is terminal, and logs a named WARNING when it is not
- [ ] A scheduled child never inherits a parent `working_dir` that points inside `.worktrees/`
- [ ] A failed write-back is logged at WARNING under `[lane-writeback]` and does not fail the session
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

### 1. Persist the resolved lane path
- **Task ID**: build-writeback
- **Depends On**: none
- **Validates**: tests/unit/test_session_isolation_bypass.py, tests/unit/test_session_executor_reap_marker.py
- **Informed By**: spike-1 (the parameter is the model instance), spike-2 (`working_dir` is a plain `Field()`), spike-3 (`slug` is a `KeyField` — never write it), spike-5 (the cleanup already handles the blocked result)
- **Assigned To**: executor-builder
- **Agent Type**: builder
- **Parallel**: true
- In `agent/session_executor.py`, replace the `AgentSession.query.filter(project_key=..., status="running")` linear scan in the session-phase update block with `get_authoritative_session(session.session_id, project_key=session.project_key)`.
- Assign `agent_session.working_dir = str(working_dir)` only when it differs from `(agent_session.working_dir or "")`, and add `"working_dir"` to `update_fields`.
- On a failed or skipped write (resolver returned `None`, or `save()` raised), log at WARNING with the literal marker `[lane-writeback]`, naming the session id and the resolved path. Do not raise.
- Add the comment at the synthesis site recording that `slug` stays local because it is a `KeyField` in the Redis primary key.
- Upgrade the synthetic-slug cleanup log: when `cleanup_result` carries `blocked_by_session`, emit a WARNING containing the literal `[synthetic-slug]` and the words `cleanup blocked`, naming the session id and the manual `git worktree prune` reclamation, mirroring the neighbouring `runner_reap_failed` message.
- Do not touch `tools/agent_session_scheduler.py` — `scheduler-builder` owns that file.

### 2. Guard the scheduled-child working_dir inheritance
- **Task ID**: build-scheduler-guard
- **Depends On**: none
- **Validates**: tests/unit/test_agent_session_scheduler_worktree_inheritance.py (create)
- **Informed By**: spike-6 (the inherited lane path collides with the child's own synthesized slug)
- **Assigned To**: scheduler-builder
- **Agent Type**: builder
- **Parallel**: true
- In `tools/agent_session_scheduler.py`, import `WORKTREES_DIR` from `agent.worktree_manager` and inherit `parent_session.working_dir` only when the path does not contain that segment.
- Comment the guard with the concrete failure it prevents: a child synthesizing its own `dev-{aid8}` slug, skipping provisioning because the inherited path already looks like a worktree, then failing `verify_worktree_branch` against another session's live lane.
- Do not touch `agent/session_executor.py` — `executor-builder` owns that file.

### 3. Pin the behavior with tests
- **Task ID**: build-tests
- **Depends On**: build-writeback, build-scheduler-guard
- **Validates**: tests/unit/test_session_isolation_bypass.py, tests/unit/worktree_manager/test_worktree_manager_busy_guards.py, tests/unit/test_agent_session_scheduler_worktree_inheritance.py
- **Assigned To**: guard-tester
- **Agent Type**: test-engineer
- **Parallel**: false
- In `tests/unit/worktree_manager/test_worktree_manager_busy_guards.py`, add a case where a non-terminal row has `slug=None` and `working_dir=".worktrees/dev-abcd1234"` and assert `_scan_worktree_sessions` returns `busy`. Mutation-check it: revert the match to the pre-fix stored value and confirm the test fails.
- In `tests/unit/test_session_isolation_bypass.py`, add a case asserting the executor persists `working_dir` and leaves `slug` unset on the row.
- Add a case asserting a raising `save()` produces the `[lane-writeback]` WARNING via `caplog` and the session continues.
- Add both synthetic-cleanup branches: terminal row → worktree removed; non-terminal row → not removed, and the `[synthetic-slug]` `cleanup blocked` WARNING is emitted.
- Create `tests/unit/test_agent_session_scheduler_worktree_inheritance.py` asserting a worktree-rooted parent `working_dir` is declined and a plain-checkout one is still inherited.

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: lane-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Apply the three feature-doc updates listed in the Documentation section.
- Confirm `docs/features/README.md` already indexes all three pages and add nothing new.

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-writeback, build-scheduler-guard, build-tests, document-feature
- **Assigned To**: lane-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every row of the Verification table and report each result.
- Confirm each Success Criteria checkbox against observed output, not against the diff.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Targeted tests pass | `.venv/bin/python -m pytest tests/unit/test_session_isolation_bypass.py tests/unit/worktree_manager/ tests/unit/test_session_executor_reap_marker.py tests/unit/test_agent_session_scheduler_worktree_inheritance.py -q --timeout=420 --timeout-method=thread` | exit code 0 |
| Lint clean | `.venv/bin/python -m ruff check agent/session_executor.py tools/agent_session_scheduler.py` | exit code 0 |
| Format clean | `.venv/bin/python -m ruff format --check agent/session_executor.py tools/agent_session_scheduler.py` | exit code 0 |
| working_dir is persisted | `grep -c "agent_session.working_dir = " agent/session_executor.py` | output > 0 |
| Write-back marker present | `grep -c "\[lane-writeback\]" agent/session_executor.py` | output > 0 |
| Anti-criterion: the duplicate-prone linear scan is gone | `grep -c 'AgentSession.query.filter(project_key=session.project_key, status="running")' agent/session_executor.py` | match count == 0 |
| Cleanup block is loud | `grep -c "cleanup blocked" agent/session_executor.py` | output > 0 |
| Scheduler guard present | `grep -c "WORKTREES_DIR" tools/agent_session_scheduler.py` | output > 0 |
| Anti-criterion: slug is never written back | `grep -cE "\.slug *= *[^=]" agent/session_executor.py` | match count == 0 |
| Anti-criterion: no slug arm in the busy scan | `grep -cE "getattr\(session, .slug." agent/worktree_manager.py` | match count == 0 |
| Anti-criterion: no second lane field added | `grep -c "active_worktree_dir" models/agent_session.py` | match count == 0 |
| Scheduler guard predicate present | `grep -c "WORKTREES_DIR not in" tools/agent_session_scheduler.py` | output > 0 |

**Red-state proof (measured on `de229ee46`, before any implementation).** Every positive row
above was run against main and returned a failing value, and every anti-criterion pattern was
proved to bite against a seeded violation:

| Row | Value on main | Verdict |
|---|---|---|
| `agent_session.working_dir = ` | 0 | FAIL (expected `> 0`) |
| `[lane-writeback]` | 0 | FAIL (expected `> 0`) |
| `cleanup blocked` | 0 | FAIL (expected `> 0`) |
| `WORKTREES_DIR` in scheduler | 0 | FAIL (expected `> 0`) |
| `WORKTREES_DIR not in` in scheduler | 0 | FAIL (expected `> 0`) |
| linear-scan anti-criterion | 1 | FAIL (expected `match count == 0`) |
| `.slug *= *[^=]` | 0 on main; **1** against seeded `agent_session.slug = "x"` | pattern bites |
| `getattr\(session, .slug.` | 0 on main; **1** against a seeded slug arm | pattern bites |
| `active_worktree_dir` | 0 on main; **1** against a seeded field declaration | pattern bites |

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
