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
