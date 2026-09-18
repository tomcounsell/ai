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

### spike-1: Does git already refuse to delete a branch that a live worktree has checked out?
- **Assumption**: "Cleanup can destroy the branch the worktree is standing on, so the fix must add a checked-out-branch refusal."
- **Method**: prototype (throwaway repo + linked worktree, executed 2026-09-18)
- **Finding**: **git already refuses, for both `-d` and `-D`.** `git branch -d feat` and `git branch -D feat` both exit 1 with `error: cannot delete branch 'feat' used by worktree at '<path>'`. The assumption is **false as stated**.
- **Confidence**: high (executed, not read)
- **Impact on plan**: This **reframes acceptance criterion 2**. The damage in the incident was never "deleted the branch the worktree was on" — git would have blocked that. The damage was the *inverse*: cleanup deleted `session/dev-6db9c943`, a branch that was **not** checked out anywhere and therefore deletable, while the branch actually holding the work (`session/cruft-auditor-exception-checks`) was never considered. The fix is therefore about **naming the right target**, not about adding protection to the wrong one. The plan still adds an explicit pre-check (Task 3) so the refusal is a named, logged, tested decision rather than an incidental git error string — AC 2 must be provable by a test that does not depend on git's error message.

### spike-2: What does the branch read return under a detached `HEAD`?
- **Assumption**: "`git rev-parse --abbrev-ref HEAD` yields a usable branch name."
- **Method**: prototype (same throwaway repo, `git checkout --detach`)
- **Finding**: it returns the literal string **`HEAD`**. `checkpoint_branch_state` (`agent_session_queue.py:608-616`) does not special-case this, so a detached lane records `branch_name = "HEAD"` on the ORM row.
- **Confidence**: high (executed)
- **Impact on plan**: the resolver must treat `"HEAD"` as *no branch*, never as a branch name. Any consumer handed `None` must degrade safely: cleanup skips entirely (nothing nameable to delete), and the launch guard must not attempt `git checkout HEAD`. This is not a corner case — spike-3 shows detached lanes are the majority of the divergence.

### spike-3: How common is slug/branch divergence in the live fleet?
- **Assumption**: "Divergence is rare and exotic."
- **Method**: code-read + measurement — `git worktree list --porcelain` compared against `session/{slug}`, run 2026-09-18 on this machine
- **Finding**: **7 of 31 lanes (23%) diverge.** Five are detached `HEAD` (`nightly-baseline`, `nightly-triage-adea87c6`, `pr-2856-review`, `pr-3080-review`, `pr-3140-review`), two are on a differently-named branch (`eval-code-simplifier` → `eval/code-simplifier`, `pr-3171-review` → `redis-client-accessor-3003`). This reproduces the issue's recon number exactly.
- **Confidence**: high (measured)
- **Impact on plan**: confirms the detached-`HEAD` path (spike-2) is the dominant shape and must be a first-class case, not an afterthought. Also supplies the AC-6 sweep command verbatim.

### spike-4: What does the #1646 predicate do when handed a branch that does not exist?
- **Assumption**: "A stale branch name could cause the unmerged guard to mis-fire dangerously."
- **Method**: code-read — `merged_via_ancestor` (`worktree_manager.py:54-62`) runs `git merge-base --is-ancestor <branch> <base>` and returns `returncode == 0`.
- **Finding**: a nonexistent branch makes git exit non-zero, so the predicate returns `False`, and `safe_delete_branch` takes the `skipped_unmerged` fail-safe path. **The guard fails safe on a bogus name** — it preserves rather than deletes.
- **Confidence**: high
- **Impact on plan**: the #1646 defect here is not "deletes unmerged work"; it is "**silently guards nothing**". It reports `[unmerged-branch-guard] branch 'X' preserved` about a branch that may not even exist, while the real work branch goes unexamined. AC 3 must therefore assert the predicate is *invoked with* the work-holding branch, not merely that nothing was deleted.

## Data Flow

Branch identity across one turn, today (the numbers are `session_executor.py` lines unless noted):

1. **Entry point** — a Telegram message becomes an `AgentSession` row; the worker dequeues it and calls the executor.
2. **`:1400-1466` branch resolution** — `resolve_branch_for_stage` / slug mapping produces `resolved_branch = f"session/{slug}"`; `branch_name` is bound at `:1430` (slug path) or `:1466` (no-slug path). **This is the last write to `branch_name` in the entire function.**
3. **`:1468-1491` #887 guard** — refuses an eng session with a slug running in the repo root. Reads `working_dir`, not `branch_name`. Unaffected by this plan.
4. **`:1494-1515` #1377 launch guard** — `verify_worktree_branch(working_dir, branch_name)` compares the live `HEAD` against the derived name. Clean mismatch → auto `git checkout`; dirty mismatch or git failure → raise.
5. **Harness runs** — `claude -p` executes inside the worktree. **The agent may `git checkout` freely. Nothing observes this.**
6. **`:2781` `mark_work_done(working_dir, branch_name)`** — archives the plan, commits, returns to main, using the stale name in the commit message and as the return target.
7. **`:2787` `safe_delete_branch(working_dir, branch_name, predicate=merged_via_ancestor)`** — the destructive act, on the stale name.
8. **`finally:` `checkpoint_branch_state(session)`** (`agent_session_queue.py:593`) — reads the live `HEAD` and writes it to `AgentSession.branch_name` + `commit_sha`. **This runs *after* step 7**, so the only component that ever learns the truth learns it too late to inform the deletion.
9. **Next turn** — re-enters at step 2, re-derives `session/{slug}`, and step 4 refuses because step 7 deleted it. `derived_branch_name` (`models/agent_session.py:1841`) would have masked a row-only fix anyway: it returns `f"session/{s}"` whenever a slug exists, discarding what step 8 recorded.

**The shape of the bug is an ordering inversion plus a discarded record.** Steps 7 and 8 are in the wrong order, and step 9 throws away step 8's output. Both must change together, or the fix is partial.

## Why Previous Fixes Failed

No prior fix targeted this defect, so strictly there is nothing to post-mortem. What is worth recording is why three *correct* guards stacked on top of each other still produced a dead session — and why the one prior fix that solved this exact pattern did not generalise.

| Prior fix | What it did | Why it did not prevent this |
|-----------|-------------|-----------------------------|
| #1377 / `verify_worktree_branch` | Refuses to launch when the worktree is not on the expected branch | It trusts the caller's `expected_branch` string absolutely. A guard that validates *actual* against a *derived* expectation cannot detect that the expectation itself is wrong. |
| #1646 / `safe_delete_branch` + `merged_via_ancestor` | Refuses to delete a branch with unmerged commits | Same flaw, same cause: it guards the branch it is *handed*. Given a stale name it fails safe (spike-4) and logs a reassuring "preserved" line about a branch nobody was going to lose. |
| #887 / main-checkout guard | Refuses eng-with-slug sessions in the repo root | Correct and orthogonal. It checks the *directory*, never the branch, so it was never going to catch this. |
| PR #2792 / `tools/lane_identity.py` | Replaced slug *derivation* with a single recorded slug on `PipelineLedger.slug` | **Solved this exact pattern one level up and stopped there.** Its docstring warns that "derivation wearing adoption's clothes" is the defect, and it disciplined the slug — but the branch, which is downstream of the slug, kept being re-derived by every consumer. |

**Root cause pattern:** *a guard can only be as correct as the identity it is handed.* Every guard above was built to validate a value; none of them owns the value. As long as branch identity is re-derived at each call site, adding guards multiplies the number of places that can confidently assert the wrong thing. The remedy is the #2792 remedy: **record the identity once, read it everywhere, never re-derive.**

## Architectural Impact

- **New dependencies**: none. No new packages, services, or config. Everything is `git` and code already in the repo.
- **Interface changes**:
  - `models/agent_session.py::AgentSession.derived_branch_name` — precedence inverted (recorded value wins over the slug seed). Behavior change visible to any reader; there are exactly two (`tests/e2e/test_context_propagation.py`, and `docs/features/eng-session-architecture.md:248`).
  - `agent/worktree_manager.py::safe_delete_branch` — return dict gains a `skipped_checked_out: bool` key. Additive; existing `deleted` / `skipped_unmerged` / `branch` / `error` keys unchanged.
  - `tools/lane_identity.py` — three new public functions (`read_worktree_branch`, `lane_branch`, `refresh_lane_branch`) plus a `sweep` CLI subcommand.
  - `agent/agent_session_queue.py::checkpoint_branch_state` — no signature change; gains detached-`HEAD` normalisation.
- **Coupling**: **decreases.** Today three modules each independently know how to spell a lane's branch. After this, `tools/lane_identity.py` is the only module that does, and it is already the designated home of lane identity (PR #2792). The executor loses a hand-rolled git read; the model's accessor stops encoding policy.
- **Data ownership**: `AgentSession.branch_name` is promoted from an incidental checkpoint artifact to the lane's **recorded branch**, with `checkpoint_branch_state` as its sole writer. No schema change — the field already exists and is already populated, so no Popoto migration is required.
- **Reversibility**: high. The change is four small edits plus one new module surface; reverting restores derivation. No data is destroyed and no stored shape changes, so a revert needs no migration.

**The core invariant this change establishes, stated once:**

> At the end of every turn, `AgentSession.branch_name` equals the worktree's live `HEAD` branch (or is empty if the worktree is detached). Everything that needs to know a lane's branch reads that record. Nothing re-derives it from the slug except to seed a worktree that has never been checkpointed.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1 (one decision to confirm — see Open Questions #1, whether the record belongs on `AgentSession` or `PipelineLedger`)
- Review rounds: 2 (this touches three guards that exist because of prior incidents; the reviewer's job is to confirm none of them got weaker)

Medium rather than Small because the change is small in lines but wide in blast radius: four files, three historical guards, and a cross-turn invariant that only shows up in integration-shaped tests. The coding is an afternoon; the alignment on which record is authoritative, and the proof that #887/#1377/#1646 are all still RED-on-removal, is the real cost.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `git` >= 2.38 | `git --version` | `git worktree list --porcelain` and `merge-tree --write-tree` semantics relied on by the checked-out-branch pre-check and the existing `merged_via_tree` oracle |
| Repo venv on the committed pin | `python -m tools.doctor` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

No secrets, no external services, no network. The work is entirely local git + Popoto.

## Solution

### The decision: what is authoritative

The issue asks the planner to settle this before implementing. **Settled:**

> **`AgentSession.branch_name` is the lane's recorded branch and the single source of truth. It is kept equal to the worktree's live `HEAD` by exactly one writer, `checkpoint_branch_state`. `session/{slug}` is a *seed*, used only to name a branch at worktree creation and as the fallback for a lane that has never been checkpointed. The live `HEAD` is not a competing source — it is the input the record is refreshed from.**

Why this one, and not the other two candidates:

- **Not the slug-derived name.** A slug names a *lane*; it cannot name a branch the agent chose at runtime. Deriving the branch from the slug is what PR #2792 called "derivation wearing adoption's clothes", and it is the defect here.
- **Not the live `HEAD` read at point of use.** A guard whose expectation is the live `HEAD` is tautological — it can never fail, so #1377's protection evaporates. The guard needs a *recorded* expectation to compare against. The live `HEAD` is the truth about *now*; the record is the truth about *what this lane is*.
- **Yes, the recorded field.** It already exists, is already populated, needs no Popoto migration, and has exactly one writer already. The only change is to stop throwing it away.

This makes the invariant checkable in one sentence: **at turn end, record == live `HEAD`** (empty when detached).

### Key Elements

- **`tools/lane_identity.py` (extended)**: the single home for branch identity, alongside the slug identity it already owns.
  - `read_worktree_branch(worktree_path) -> str | None` — the **only** place `git rev-parse --abbrev-ref HEAD` is spelled for a lane. Normalises the literal `"HEAD"` (detached, spike-2) to `None`. Returns `None` on a missing path or a git failure rather than raising.
  - `lane_branch(session) -> str | None` — **the accessor**. Returns the recorded `branch_name`; falls back to the `session/{slug}` seed; falls back to `_session_branch_name(session_id)` for slug-less sessions; `None` if nothing applies. Never returns `"HEAD"`.
  - `refresh_lane_branch(session, worktree_path) -> str | None` — reads the live `HEAD` and delegates the write to `checkpoint_branch_state`, returning the value now on record.
  - `sweep()` + a `python -m tools.lane_identity sweep` CLI — walks `git worktree list --porcelain`, reports every lane whose live `HEAD` diverges from its recorded branch, and exits non-zero if any lane is in a state whose next turn would be refused. This is AC 6, made executable and repeatable rather than a one-off measurement.

- **`checkpoint_branch_state` (`agent/agent_session_queue.py:593`)**: promoted to sole writer of the record. Gains one behavior: when the branch read yields `"HEAD"`, clear `branch_name` instead of storing the literal (spike-2). `commit_sha` continues to be written in both cases — a detached lane still has a SHA.

- **`derived_branch_name` (`models/agent_session.py:1840-1844`)**: precedence inverted to `self.branch_name or (f"session/{s}" if s else None)`. Without this, a fix confined to the executor is masked at the model layer — the recon called this out explicitly.

- **`safe_delete_branch` (`agent/worktree_manager.py:126`)**: gains an explicit checked-out-branch pre-check ahead of the `merged_via_ancestor` predicate. git already refuses such a delete (spike-1), but an incidental error string is not a testable guarantee; the pre-check turns it into a named, logged, asserted decision returning `skipped_checked_out: True`.

- **The executor cleanup block (`agent/session_executor.py:2781-2790`)**: stops using the turn-start `branch_name`. Refreshes the record from the live `HEAD`, then acts on it.

### Flow

Turn end, after the fix:

**Harness exits** → `refresh_lane_branch(session, working_dir)` reads live `HEAD` and records it → **branch known** → detached? → *yes*: log and skip cleanup entirely → **done** / *no*: `mark_work_done(working_dir, work_branch)` (archives plan, commits, returns worktree to `main`) → `safe_delete_branch(work_branch, predicate=merged_via_ancestor)` → checked out anywhere? *yes* → preserve + log → unmerged? *yes* → preserve + log (#1646, now about the right branch) → else delete → **`finally:` `checkpoint_branch_state`** re-reads live `HEAD` (now `main`) and updates the record → **next turn**: guard compares live `HEAD` (`main`) against record (`main`) → match → **harness launches**.

### Technical Approach

- **Refresh-act-refresh is the ordering fix.** Today the only component that learns the truth (`checkpoint_branch_state`) runs *after* the destructive act. Adding a refresh *before* cleanup — and keeping the existing one in `finally` *after* it — closes both halves. The trailing refresh is what stops the fix from re-creating the bug under a new name: after cleanup deletes the work branch and `mark_work_done` returns the worktree to `main`, the record must follow the worktree to `main`, or the next turn's guard would demand the branch this turn just deleted. **This trailing refresh is load-bearing; a reviewer who sees it removed as redundant should treat that as a blocker.**

- **The launch guard's logic is unchanged; only its input changes.** `verify_worktree_branch(working_dir, lane_branch(session))` instead of `verify_worktree_branch(working_dir, branch_name)`. Match passes, clean mismatch auto-checks-out, dirty mismatch raises — all identical. #1377's scenario (a new session reusing a worktree left on a prior stage's branch) still fails, because a new session has no record and falls back to the seed, exactly as today. **The guard is not relaxed. It is handed a true expectation instead of a guessed one.** Answering the issue's open question 3: no, the guard should not learn to accept divergence — the divergence should stop being a surprise to it.

- **`branch_name` at `:1430`/`:1466` keeps its job, which is worktree *provisioning*.** It is the seed that names a branch being created. It must simply stop being reused 1300 lines later as an identity. The build should leave the two assignment sites alone and rename the local to `seed_branch_name` so the next reader cannot make this mistake again.

- **Detached lanes skip cleanup rather than guessing.** Five of the seven divergent lanes are detached. There is no branch name to hand `merged_via_ancestor`, and inventing one is how this bug started. Skip, log at INFO with the lane slug, leave the lane resumable.

- **There is a second, undocumented writer of the record, and it must be neutered first.** `agent/session_executor.py:1566` does `agent_session.branch_name = branch_name` at turn *start*, persisting the slug-derived seed onto the row alongside `task_list_id` / `exec_cwd` / `exit_reason`. This was not in the issue's recon, and it is fatal to the scheme if left alone: every turn would clobber the record with the derived name moments before the guard reads it, and the fix would appear to do nothing. It also explains the observation the issue deliberately dropped (the row recording `branch=main` for a lane never on `main`) — two writers, last-write-wins, neither aware of the other. **Change `:1566` to seed-if-empty**: write the derived name only when `agent_session.branch_name` is falsy, leaving a recorded value untouched. `checkpoint_branch_state` remains the only component that *updates* the record.

- **Sweep every remaining consumer of the stale local, not just the two the issue names.** `branch_name` is read at `:1509` (guard), `:1536` (log), `:1544` and `:2700`/`:2819` (`save_session_snapshot`), `:1566` (the second writer above), `:1824`/`:1841`/`:1858` (`_enqueue_nudge`), and `:2781`-`:2809` (cleanup). The nudge path matters: a nudge re-enqueues the lane mid-turn, so it must carry the lane's real branch or it re-seeds the same staleness. The snapshot paths are diagnostic, but a diagnostic that records the wrong branch is how this incident stayed invisible for a day. All of them read through `lane_branch(session)` after the change. Per the repo's replicated-defect rule this closes on a clean grep sweep, not an enumerated list — the enumeration above is orientation, and the Verification table holds the sweep.

- **No partial migration.** The consumers — launch guard, cleanup, checkpoint, and the model accessor — change in one PR. A sweep (`grep -rn "abbrev-ref HEAD" --include=*.py agent/ tools/ models/`) must show `read_worktree_branch` as the only lane-scoped spelling afterwards; this is a Verification row, not a checklist item, per the repo's replicated-defect rule.

- **Integration points**: `agent/session_executor.py` (guard input, seed-if-empty write, nudge path, snapshot calls, cleanup block), `agent/agent_session_queue.py` (`checkpoint_branch_state`, and the `save_session_snapshot` call at `:3007` that also re-derives), `agent/worktree_manager.py` (`safe_delete_branch`), `models/agent_session.py` (`derived_branch_name`), `tools/lane_identity.py` (new surface). `agent/session_revival.py::_session_branch_name` stays as the slug-less seed generator and is called only through `lane_branch`.

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
