---
status: Planning
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-18
tracking: https://github.com/tomcounsell/ai/issues/3411
last_comment_id:
---

# Lane branch identity: one recorded branch, read by the guard, the cleanup, and the checkpoint

## Problem

An agent lane is a slug binding a worktree (`.worktrees/{slug}/`), a branch (`session/{slug}`), and a task list. The executor computes the lane's branch name **once**, from the slug, at `agent/session_executor.py:1430` (`branch_name = resolved_branch`) or `:1466` (`_session_branch_name(session.session_id)`), and never rebinds it for the remaining ~1350 lines of the function.

That single stale string is then used for two very different jobs at opposite ends of the turn:

- **Launch guard** (`:1509`, issue #1377): `verify_worktree_branch(working_dir, branch_name)` — refuses to start the harness unless the worktree is on that branch.
- **End-of-turn cleanup** (`:2781`–`:2790`): `mark_work_done(working_dir, branch_name)` then `safe_delete_branch(..., branch_name, predicate=merged_via_ancestor)` — deletes that branch.

If the agent ran `git checkout` during the turn (normal, not exotic: **7 of 31 live lanes, 23%, are on something other than `session/{slug}`**), cleanup deletes the slug-derived branch — which is not where the work is — and the #1646 unmerged-branch guard evaluates the wrong branch, so it cannot protect the real commits. The next turn recomputes the same slug-derived name, finds it gone, and the guard refuses to launch.

**Current behavior** — session `tg_valor_-1003449100931_1473`, lane `dev-6db9c943`, 2026-09-16:

```
15:42:38  Created worktree: .worktrees/dev-6db9c943 (branch: session/dev-6db9c943)
          ... agent checks out session/cruft-auditor-exception-checks, opens PR #3352 ...
15:48:33.115  mark_work_done: Deleted local branch: session/dev-6db9c943
15:48:33.761  [worktree-branch-guard] expected='session/dev-6db9c943',
              actual='session/cruft-auditor-exception-checks'
              (git checkout failed: pathspec did not match) — refusing to launch
15:48:35.977  Session failed
```

646 ms between the delete and the guard that demanded it. The guard is correct; cleanup destroyed what it checks for. The user-visible symptom is silence: the work had shipped (PR #3352, merged) but the turn that would have reported it never launched, so the Telegram thread got a reaction emoji and nothing else.

**Root cause, stated precisely:** branch identity for a lane is **derived** on every read instead of **recorded** once. Three places disagree — the slug-derived `session/{slug}` (executor guard + cleanup), `AgentSession.branch_name` (written by `checkpoint_branch_state` from the live `HEAD`), and the worktree's live `HEAD` — and `derived_branch_name` actively discards the recorded value whenever a slug exists.

**Desired outcome:** a turn that ends in a worktree sitting on a branch other than `session/{slug}` leaves that lane resumable. Cleanup acts on the branch that actually holds the turn's commits, the #1646 guard evaluates that same branch, and the next turn launches.

## Freshness Check

**Baseline commit:** `bbe5dc7a1d70feecd85571d0bb266b07ae341939` (== `origin/main` at plan time)
**Issue filed at:** 2026-09-18T07:22:04Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `agent/session_executor.py:1430` — `branch_name = resolved_branch` — still holds.
- `agent/session_executor.py:1466` — `branch_name = _session_branch_name(session.session_id)` (the `else` arm) — still holds; exactly two assignment sites, mutually exclusive, never rebound.
- `agent/session_executor.py:1494-1515` — branch-mismatch guard calling `verify_worktree_branch(working_dir, branch_name)` at `:1509` — still holds.
- `agent/session_executor.py:2781-2790` — `mark_work_done(working_dir, branch_name)` then `safe_delete_branch(str(working_dir), branch_name, predicate=merged_via_ancestor, force=False)` — still holds.
- `agent/worktree_manager.py:292` — `verify_worktree_branch` — still holds; match passes, clean mismatch auto-checks-out, dirty mismatch or git failure raises.
- `agent/agent_session_queue.py:593` — `checkpoint_branch_state` — still holds; writes real `HEAD` branch + SHA to the ORM row.
- `models/agent_session.py:1840-1844` — `derived_branch_name` returns `f"session/{s}" if s else self.branch_name` — still holds.
- `agent/session_executor.py:1468-1491` — the #887 main-checkout guard — still holds (issue cites it as a constraint, not a location).

**Cited sibling issues/PRs re-checked:**
- #1377 — CLOSED. Introduced `worktree-branch-guard`. Not to be weakened.
- #1646 — CLOSED. Unmerged-branch guard (`merged_via_ancestor` + `safe_delete_branch`). Not to be weakened.
- #887 — CLOSED. Main-checkout protection. Not to be weakened.
- #3301 — OPEN. `post_merge_cleanup` reports success for a branch it never looked for under the same slug/branch divergence. Same root cause, opposite edge (silent no-op vs. destructive wrong-target). Coordination signal; see No-Gos.
- #3306 — OPEN. `checkpoint_branch_state` blocks the worker event loop on the executor raise path. This plan moves/extends that call site, so the two touch the same function. Coordination signal; see Risks.

**Commits on main since issue was filed (touching referenced files):**
`git log --since=2026-09-18T07:22:04Z -- agent/session_executor.py agent/worktree_manager.py agent/agent_session_queue.py agent/branch_manager.py models/agent_session.py` returns **nothing**. No drift.

**Active plans in `docs/plans/` overlapping this area:** none. `worktree-single-owner-dispatch.md` is the nearest neighbour (worktree ownership/dispatch, not branch identity) and does not touch the guard, the cleanup, or the checkpoint.

**Defect still present on current main:** yes — confirmed by reading the code path (reproducing end-to-end requires a live Telegram-originated eng session with a mid-turn `git checkout`, which is a worker-runtime precondition, not something a unit test on `main` can stage without the fix's own test scaffolding). The regression test in Task 1 is the executable proof and must be RED on this baseline SHA.

## Prior Art

- **#1377** (closed, PR merged 2026-05-20): "MERGE-stage dev session silently crashes when slug reuses BUILD worktree on wrong branch". Added `verify_worktree_branch` and the launch guard. **Succeeded at its own job** — it converts a silent 6-minute hang into a loud refusal. It did not address where the *expected* branch name comes from; it took the caller's string on faith.
- **#1646** (closed): "dev-session completion cleanup force-deletes unmerged session branch". Added `safe_delete_branch` + `merged_via_ancestor` with fail-safe preservation. **Succeeded at its own job** — it will not delete unmerged work. It is handed the wrong branch name, so it guards a branch nobody cares about.
- **#887** (closed): session-isolation bypass; added the main-checkout protection guard at `session_executor.py:1468-1491`. Untouched by this plan except that its guard must keep passing.
- **PR #2792** (merged 2026-08-13): "SDLC lane identity: one recorded slug, minted once (#2735, #2718)". **Directly precedential.** It fixed exactly this class of bug one level up: the *slug* used to be re-derived by every consumer, and #2792 replaced derivation with a single recorded value on `PipelineLedger.slug` read through `tools/lane_identity.py`. Its module docstring is explicit that "derivation wearing adoption's clothes" is the defect pattern. This plan applies the same remedy one level down, to the *branch*.
- **#3301** (open): `post_merge_cleanup` reports success for a branch it never looked for when a lane's worktree slug and branch slug diverge. Same divergence, non-destructive edge.
- **#3167** (closed): "Auto-preserve-before-teardown commits a half-deleted worktree". Different mechanism (teardown ordering), but same neighbourhood — evidence that worktree lifecycle edges are a recurring defect source here.

No prior attempt has targeted this specific defect. `git log -S branch_name -- agent/session_executor.py` shows no commit that rebinds `branch_name` mid-function.

## Research

The work is internal: no new dependencies, no external APIs, no ecosystem patterns. The only external surface is the `git` CLI itself, and its behavior was settled empirically in spike-1 against a real repo rather than from documentation — an executed `git branch -d` / `-D` is authoritative in a way a doc page is not.

**No relevant external findings — proceeding with codebase context and the spike results below.**

## Spike Results

_placeholder_

## Data Flow

_placeholder_

## Why Previous Fixes Failed

_placeholder_

## Architectural Impact

_placeholder_

## Appetite

_placeholder_

## Prerequisites

_placeholder_

## Solution

_placeholder_

## Failure Path Test Strategy

_placeholder_

## Test Impact

- [ ] `tests/e2e/test_context_propagation.py:152` — UPDATE: asserts `derived_branch_name == "session/my-cool-feature"` when a slug is set; the accessor's precedence is being inverted.
- [ ] `tests/unit/test_session_branch_guard.py` — UPDATE: the #887 main-checkout cases must stay RED-on-removal under the new resolver.
- [ ] `tests/unit/test_safe_delete_branch.py` — UPDATE: add the checked-out-in-a-worktree refusal case.

## Rabbit Holes

_placeholder_

## Risks

_placeholder_

## Race Conditions

_placeholder_

## No-Gos (Out of Scope)

_placeholder_

## Update System

_placeholder_

## Agent Integration

_placeholder_

## Documentation

- [ ] Create `docs/features/lane-branch-identity.md` naming the single source of truth for a lane's branch and the three consumers that read it.
- [ ] Add the entry to the `docs/features/README.md` index table.
- [ ] Update `docs/features/sdlc-lane-identity.md` with a cross-link (slug identity vs. branch identity are now explicitly distinct).

## Success Criteria

_placeholder_

## Team Orchestration

_placeholder_

## Step by Step Tasks

_placeholder_

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lint clean | `python -m ruff check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

_placeholder_
