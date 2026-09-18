---
status: Ready
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
| Repo venv on the committed pin | `.venv/bin/python -c "import pathlib,sys; pin=pathlib.Path('.python-version').read_text().strip(); assert sys.version.startswith(pin), f'venv {sys.version.split()[0]} != pin {pin}'"` | `scripts/pytest-clean.sh` aborts on an off-pin venv |

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

### Exception Handling Coverage
- [ ] `agent/session_executor.py:2811` — `except Exception as e: logger.warning("Failed to auto-mark session done: %s")` wraps the whole cleanup block. It already logs, but nothing asserts it. Add a test that forces `refresh_lane_branch` to raise and asserts (a) the warning is emitted with the session's `project_key`, and (b) **the turn still completes** — cleanup failure must never fail the session.
- [ ] `agent/agent_session_queue.py:632` — `checkpoint_branch_state`'s `except Exception` logs a warning and returns. Add a test that a git failure leaves `branch_name` **unchanged** (not cleared), so a transient git error cannot erase a good record and strand the next turn.
- [ ] `agent/worktree_manager.py:171` — `safe_delete_branch`'s predicate-error handler already fails safe to `skipped_unmerged`. Extend the existing test to cover the new `skipped_checked_out` path raising inside the worktree scan: a scan failure must also fail safe (preserve), never delete.
- [ ] `read_worktree_branch` is new and must not raise: a missing path, a non-repo path, and a `git` subprocess timeout each return `None`. Three tests.

### Empty/Invalid Input Handling
- [ ] `lane_branch(session)` with: no slug and no record; empty-string `branch_name` (Popoto stores unset strings as `""`, not `None` — assert falsy handling, not `is None`); whitespace-only `branch_name`; `branch_name == "HEAD"`. Each must yield the seed or `None`, never a bogus branch name.
- [ ] `verify_worktree_branch` already raises `ValueError` on an empty `expected_branch`. Assert `lane_branch` never hands it one — this is the interface contract between the accessor and the guard.
- [ ] `safe_delete_branch` with a branch that does not exist: `merged_via_ancestor` returns `False` (spike-4), so the call preserves. Assert the *log* distinguishes "preserved because unmerged" from "preserved because nonexistent" — the incident's reassuring-but-meaningless log line is the thing to stop reproducing.
- [ ] Detached worktree end-to-end: cleanup skipped, record cleared, next turn launches.

### Error State Rendering
- [ ] The user-visible symptom of this bug was **silence** — a reaction emoji and no message. Add a test asserting that when the launch guard does refuse, `last_error` is populated on the `AgentSession` row so the failure is attributable rather than silent. (The guard already raises; this asserts the raise reaches the row.)
- [ ] Assert the `[lane-branch]` skip log names the lane slug and the worktree path, so an operator reading `logs/worker.log` can identify which lane skipped cleanup and why.

## Test Impact

- [ ] `tests/e2e/test_context_propagation.py:152` — **UPDATE**: currently asserts `child.derived_branch_name == "session/my-cool-feature"` for a row that has a slug. Under the inverted precedence the recorded `branch_name` wins, so the fixture must either clear `branch_name` (to keep asserting the seed) or the assertion must move to the record. Decide by reading what the test is actually about — it is a *context propagation* test, so the seed is probably the point; clear the field in the fixture and add a sibling case asserting the record wins when set.
- [ ] `tests/e2e/test_context_propagation.py:169` — **KEEP, verify**: asserts `derived_branch_name == "feature/manual-branch"` for a row with a manual branch. This case already expects record-wins and should pass unchanged — it is a free regression check that the inversion works.
- [ ] `tests/unit/test_session_branch_guard.py` — **UPDATE**: six existing cases covering the #887 main-checkout predicate and the detached-HEAD path. They must stay green, and `test_detached_head_does_not_trigger_guard` must be re-read carefully against the new `"HEAD"` → `None` normalisation, which changes what the resolver hands the guard on that path.
- [ ] `tests/unit/test_safe_delete_branch.py` — **UPDATE**: add the `skipped_checked_out` case and a case asserting the new key is present-and-`False` on every existing path, so callers can branch on it unconditionally.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_cleanup.py` — **UPDATE**: cleanup assertions that assume the slug-derived branch is the deletion target.
- [ ] `tests/unit/test_branch_manager.py` — **REVIEW**: `mark_work_done` now receives the live branch rather than the seed; check whether any case asserts the commit message text `"Mark work as done: {branch_name}"`.
- [ ] **No xfail markers found** related to this bug — `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` filtered for branch/worktree/lane returns nothing, so there are no expected-failure markers to convert. Re-run the grep at build time in case one lands in the interim.

New tests (not existing-test impact, listed here so the build has one place to look): the RED-first regression test (Task 1), the three guard RED-on-removal proofs (Task 6), and the failure-path tests enumerated above.

## Rabbit Holes

- **Moving the branch record to `PipelineLedger`.** Architecturally tidier — the lane owns the slug there, so arguably it should own the branch too. But it needs a Popoto field, a registered idempotent migration, and a story for lanes that have no ledger (ad-hoc dev sessions with a slug). `AgentSession.branch_name` already exists, is already written, and needs no migration. Take the free fix; raise the move as Open Question #1 and let the decider rule.
- **Making `verify_worktree_branch` "smarter" about acceptable divergence.** The issue's open question 3 floats this and answers itself: the guard is the only thing between an agent and the wrong branch. Any work that makes the guard more permissive is out of scope and should be treated as a design regression.
- **Fixing `checkpoint_branch_state`'s `working_dir` resolution.** The issue's recon dropped the observation that the checkpoint recorded `branch=main` three times for a lane never on `main`. This plan explains part of it (the second writer at `:1566`), but whether `session.working_dir` ever points at the main checkout is a separate question. Do not chase it here.
- **Unifying with #3301's `post_merge_cleanup`.** Same root cause, adjacent code, and the temptation to fix both in one PR is strong. Resist: #3301 is a merge-path no-op, this is a turn-path destructive act, and bundling them doubles the review surface for three guards that each exist because of a prior incident. Coordinate, do not merge.
- **Retroactively repairing the 7 divergent lanes on this machine.** The sweep (AC 6) *reports*; it should not mutate. Repairing live lanes is a manual operator action with real data at stake, and an auto-repair that guesses wrong strands exactly the sessions it meant to save.
- **Rewriting `branch_name` threading through the whole executor function.** The function is ~1350 lines and the temptation is to refactor it. The fix is to stop reusing one local as two different concepts; renaming it to `seed_branch_name` and routing identity reads through `lane_branch()` achieves that without a restructuring nobody asked for.

## Risks

### Risk 1: The fix re-creates the bug under a new name
**Impact:** Cleanup now deletes the *work* branch (correctly, once merged). If the record is not refreshed afterwards, the next turn's guard demands the branch this turn just deleted — identical failure, different string, and harder to diagnose because the name now looks plausible.
**Mitigation:** The trailing `checkpoint_branch_state` in the `finally` block is load-bearing and is called out as such in the Technical Approach. The regression test in Task 1 runs **two** turns, not one, and the second turn's successful launch is the assertion. A one-turn test would pass while the bug survives.

### Risk 2: The second writer at `:1566` is missed or reverted
**Impact:** Total silent failure of the fix. Every turn clobbers the record with the derived seed moments before the guard reads it, so the system behaves exactly as it does today while all the new code appears to be running.
**Mitigation:** Seed-if-empty is its own task (Task 4) with its own test asserting a pre-existing `branch_name` survives a turn start. A Verification row greps for an unconditional assignment to `agent_session.branch_name` outside `checkpoint_branch_state`.

### Risk 3: A guard gets quietly weakened
**Impact:** #887, #1377, or #1646 stops refusing its original bad input. These guards exist because of three separate production incidents; a regression here costs more than the bug being fixed.
**Mitigation:** AC 5 is proven by *mutation*, not by the guards' tests passing: Task 6 removes each guard in a scratch working copy and asserts its test goes RED. A guard certifying absence is worthless until proven RED against the known-bad state. Paste the three RED outputs into the PR body.

### Risk 4: Popoto empty-string semantics defeat the falsy check
**Impact:** Popoto stores unset string fields as `""`, and booleans as the strings `"True"`/`"False"`. If `lane_branch` tests `is None` instead of truthiness, an unset record reads as a *set* record holding an empty branch name, which `verify_worktree_branch` rejects with `ValueError`. Every lane fails to launch.
**Mitigation:** Explicit test cases for `""` and whitespace-only in the Failure Path Test Strategy. `lane_branch` normalises with `.strip()` and truthiness, never identity comparison.

### Risk 5: Interaction with #3306 (checkpoint blocks the worker event loop)
**Impact:** This plan adds a second synchronous `checkpoint_branch_state`-family call per turn in the cleanup path. If #3306 lands concurrently and changes the function to async or moves it off-thread, the two changes conflict textually and semantically.
**Mitigation:** The added call sits inside the existing synchronous cleanup block, which already runs several blocking `git` subprocesses, so it introduces no new *class* of blocking. Flag #3306 as a coordination dependency in the PR body; if #3306 lands first, rebase and adopt its call shape rather than re-introducing a sync call. A clean textual merge is not a safe merge — rebuild the test set after merging either way.

### Risk 6: The change is scoped to the two sites the issue names
**Impact:** A partial migration. The nudge path and the snapshot paths keep re-deriving, so a nudged turn re-seeds the staleness and the diagnostics keep recording the wrong branch — which is how this stayed invisible for a day.
**Mitigation:** The Verification table closes this with a grep sweep over `abbrev-ref HEAD` and `session/{` construction sites, not with an enumerated checklist. Per the repo rule, replicated-value defects close on a clean sweep.

## Race Conditions

### Race 1: Two turns of the same lane overlap across the cleanup/launch boundary
**Location:** `agent/session_executor.py:2781-2812` (cleanup) vs. `:1494-1515` (next turn's guard)
**Trigger:** The incident itself. `mark_work_done` deleted the branch at 15:48:33.115 and the next turn's guard read for it at 15:48:33.761 — **646 ms apart**. The worker transitioned the session `pending→running` at 15:48:33.490, *between* the two. The turns are not serialized by anything that knows about branch state.
**Data prerequisite:** the record must equal the worktree's live `HEAD` before the next turn's guard reads it.
**State prerequisite:** the branch the guard will demand must still exist.
**Mitigation:** The refresh-act-refresh ordering makes the record and the worktree agree *before* the turn's cleanup block returns, and the guard reads the record rather than re-deriving. The 646 ms window still exists, but both endpoints now read the same value, so the window is no longer a correctness gap. This plan does **not** add a lock: per the repo's preference, ownership is made observable rather than serialized, and a lock across a turn boundary here would be a much larger change.

### Race 2: The trailing checkpoint is skipped on the raise path
**Location:** `agent/agent_session_queue.py` `finally` block calling `checkpoint_branch_state`
**Trigger:** the turn raises after cleanup deleted the work branch but before the `finally` runs to completion (process kill, lease lapse, worker restart).
**Data prerequisite:** the record must not be left naming a deleted branch.
**State prerequisite:** none.
**Mitigation:** The leading refresh (before cleanup) records the *work* branch, and the trailing one records `main`. A crash between them leaves the record naming a branch that may have been deleted — the next turn's guard would then refuse. Make this recoverable rather than fatal: when the guard's expected branch does not exist *at all* (as opposed to existing-but-mismatched), clear the record and fall back to the seed with a WARNING naming the lane, instead of raising. This is not a weakening — a nonexistent branch carries no risk of running on the *wrong* branch, which is the only thing #1377 protects against. Cover it with an explicit test.

### Race 3: Concurrent sessions sharing one worktree
**Location:** any lane where two `AgentSession` rows resolve to the same `.worktrees/{slug}/`
**Trigger:** a new session reusing a lane whose previous session still holds a record.
**Data prerequisite:** the two rows must not fight over the record.
**State prerequisite:** one worktree, one live `HEAD`.
**Mitigation:** Out of scope and explicitly not solved here — the record is per-`AgentSession`, so two rows can disagree. Today's behavior (each session falls back to its own seed) is preserved exactly. This is the strongest argument for moving the record to `PipelineLedger`; it is Open Question #1 rather than a silent choice. `worktree-single-owner-dispatch` is the plan that owns this concern.

## No-Gos (Out of Scope)

- [SEPARATE-SLUG #3413] **Why `checkpoint_branch_state` recorded `branch=main` for a lane never on `main`.** Filed during this planning pass, as the issue's recon asked. This plan explains part of the incoherence (the second writer at `:1566`) but not a recorded value of `main`, which implicates what `session.working_dir` resolves to at checkpoint time. It matters *because* this plan promotes the record to source of truth, so it is a fast-follow, not a nice-to-have.
- [SEPARATE-SLUG #3301] **`post_merge_cleanup` reporting success for a branch it never looked for.** Same slug/branch divergence, merge path instead of turn path, non-destructive instead of destructive. Coordinate with whoever picks it up; do not bundle.
- [SEPARATE-SLUG #3306] **`checkpoint_branch_state` blocking the worker event loop on the raise path.** This plan adds a call to that function's family and deliberately does not change its concurrency shape.
- [EXTERNAL] **Repairing the 7 divergent lanes currently live on this machine.** The AC-6 sweep reports; it does not mutate. Five of the seven are detached review worktrees with real uncommitted state, and deciding what each should be checked out to is an operator judgement with data at stake. The sweep's output is the handoff.
- [ORDERED] **Deploying the fix to other bridge machines.** Gated on the merge landing and `/update` running per machine; nothing in the code change can perform it.

Everything else the issue asks for — the guard, the cleanup, the checkpoint, the model accessor, the nudge and snapshot paths, the regression test, the three RED-on-removal proofs, the sweep tool, and the doc — is in scope for this plan.

## Update System

- **No update-script or update-skill changes required.** The change adds no dependency, no config file, no env key, and no `[project.scripts]` entry that the update path must propagate.
- **No Popoto migration required.** `AgentSession.branch_name` already exists and is already populated; only the read precedence and the write discipline change. Nothing in `scripts/update/migrations.py` needs a new entry. A row carrying a stale or empty `branch_name` heals itself on its next turn, because the trailing `checkpoint_branch_state` rewrites it from the live `HEAD` — the nullable-field backcompat pattern this repo already relies on.
- **Services must be restarted after merge.** This is worker and executor code, so `/update` followed by `./scripts/valor-service.sh restart` is required on each bridge machine; verify with `tail -5 logs/bridge.log` showing "Connected to Telegram". Until a machine restarts, it keeps the old derivation and keeps stranding lanes.
- **Post-deploy verification per machine:** run `python -m tools.lane_identity sweep` and confirm it exits 0 (no lane whose next turn would be refused). That is the same command AC 6 uses, which is the point of shipping it as a CLI rather than a one-off snippet.

## Agent Integration

**No new agent-facing capability is required — this is bridge/worker-internal plumbing.** The agent does not call `lane_branch()`; the executor does, on the agent's behalf, before and after the harness runs.

Two deliberate exceptions, both operator-facing rather than agent-facing:

- **`python -m tools.lane_identity sweep`** is a module CLI, not a `[project.scripts]` entry point. It is reachable from the agent's Bash tool without any wiring, which is all AC 6 and the post-deploy check need. Adding a `valor-*` console script would be gold-plating for a diagnostic run a handful of times per deploy.
- **No `.mcp.json` / `mcp_servers/` change.** Nothing here belongs in an MCP surface.

Integration test that matters: the Task 1 regression test exercises the real executor path end to end (two turns on one lane, worktree moved off the slug branch between them), so it proves the wiring rather than asserting on a mock. That is the integration coverage for this change.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/lane-branch-identity.md`. This is AC 4's deliverable — the plan names the source of truth, the doc is where it becomes durable. It must state: the invariant (record == live `HEAD` at turn end), the one writer (`checkpoint_branch_state`), the one accessor (`lane_branch`), the seed-vs-identity distinction, the detached-`HEAD` rule, and the three guards (#887, #1377, #1646) and what each does and does not protect.
- [ ] Add the entry to the `docs/features/README.md` index table.
- [ ] Update `docs/features/sdlc-lane-identity.md` with a cross-link: it owns *slug* identity (PR #2792), the new doc owns *branch* identity, and the relationship is "the slug seeds the branch once and is never consulted for it again".
- [ ] Update `docs/features/eng-session-architecture.md:248` — it currently documents `derived_branch_name` as "`session/{slug}` if slug exists", which the inversion makes false. A doc that contradicts the code is worse than no doc.

### External Documentation Site
- [ ] Not applicable — this repo has no Sphinx/MkDocs site.

### Inline Documentation
- [ ] Module docstring on the new `tools/lane_identity.py` branch-identity functions, in the register the existing slug docstring uses (it is the best example in the repo of a docstring that explains *why a rung exists*, not just what it does).
- [ ] A comment at `agent/session_executor.py:1566` explaining why the write is seed-if-empty, naming #3411 — this is the line most likely to be "simplified" back into the bug.
- [ ] A comment on the trailing `checkpoint_branch_state` marking it load-bearing, per Risk 1.

## Success Criteria

Mapped one-to-one onto the issue's acceptance criteria, with the AC number in brackets.

- [ ] **[AC 1]** A regression test proves the defect RED before the fix: a lane whose worktree is checked out to a branch other than `session/{slug}` completes a turn, and a **subsequent** turn on the same lane launches successfully. The RED run is executed against baseline `bbe5dc7a1` and its failure output is pasted into the PR body. A guard that has never been seen RED against the known-bad SHA certifies nothing.
- [ ] **[AC 2]** End-of-turn cleanup never deletes a branch the lane's worktree currently has checked out — asserted via `safe_delete_branch` returning `skipped_checked_out: True` with a named log line, not via git's incidental error string (spike-1 showed git already refuses, so a test that only observes "nothing was deleted" would pass on unfixed code too).
- [ ] **[AC 3]** The #1646 predicate is invoked **with** the branch holding the turn's commits. Asserted on the call argument, not on the outcome — spike-4 showed the predicate fails safe on a bogus name, so outcome-only assertions are satisfied by the bug.
- [ ] **[AC 4]** One documented source of truth, named in this plan (`AgentSession.branch_name`, sole writer `checkpoint_branch_state`, sole accessor `lane_branch`) and recorded in `docs/features/lane-branch-identity.md`.
- [ ] **[AC 5]** #887 and #1377 both still refuse their original bad inputs, proven by mutation: each guard removed in a scratch copy, its test observed RED, output pasted into the PR. #1646 gets the same treatment.
- [ ] **[AC 6]** `python -m tools.lane_identity sweep` exits 0 on this machine after the fix — no live lane left in a state whose next turn would be refused.
- [ ] All four branch-identity consumers changed in one PR; the grep sweep in Verification shows no surviving re-derivation site.
- [ ] Tests pass (`scripts/pytest-clean.sh`, never bare `pytest`).
- [ ] Lint and format clean (`python -m ruff check`, `python -m ruff format`).
- [ ] Documentation updated (`/do-docs`).
- [ ] No xfail conversions needed — confirmed none exist for this bug; re-check at build time.

## Team Orchestration

The lead agent orchestrates and never builds directly.

### Team Members

- **Builder (lane-identity surface)**
  - Name: `identity-builder`
  - Role: owns `tools/lane_identity.py` (the three new functions + sweep CLI) and `models/agent_session.py::derived_branch_name`. Nothing else.
  - Agent Type: `builder`
  - Domain: Redis/Popoto — Popoto stores unset strings as `""` and booleans as `"True"`/`"False"` strings; use truthiness, never `is None`. Reads and writes go through the ORM, never raw Redis.
  - Resume: true

- **Builder (executor + cleanup)**
  - Name: `executor-builder`
  - Role: owns `agent/session_executor.py` (guard input, seed-if-empty at `:1566`, nudge/snapshot sweep, cleanup block), `agent/agent_session_queue.py::checkpoint_branch_state`, and `agent/worktree_manager.py::safe_delete_branch`.
  - Agent Type: `builder`
  - Domain: async/concurrency — the cleanup block is synchronous inside an async function; do not introduce a new blocking shape (see Risk 5 and #3306).
  - Resume: true

- **Test engineer (regression + guard mutation)**
  - Name: `guard-tester`
  - Role: the two-turn RED-first regression test, the failure-path tests, and the three RED-on-removal mutation proofs.
  - Agent Type: `test-engineer`
  - Resume: true

- **Validator**
  - Name: `identity-validator`
  - Role: verifies the invariant holds, the sweep is clean, and no guard weakened.
  - Agent Type: `validator`
  - Resume: true

- **Documentarian**
  - Name: `identity-documentarian`
  - Role: the Documentation section's four doc tasks.
  - Agent Type: `documentarian`
  - Resume: true

## Step by Step Tasks

### 1. RED-first regression test
- **Task ID**: `test-red-regression`
- **Depends On**: none
- **Validates**: `tests/unit/test_lane_branch_identity.py` (create)
- **Informed By**: spike-1 (git already refuses deleting a checked-out branch, so the test must assert on the *target name*, not on "nothing was deleted"); spike-3 (divergence is 23% of live lanes)
- **Assigned To**: `guard-tester`
- **Agent Type**: `test-engineer`
- **Parallel**: true
- Write a test that stages a lane whose worktree is moved off `session/{slug}` mid-turn, runs the end-of-turn cleanup, then runs the next turn's launch guard.
- Assert the second turn **launches**. This is the whole test; a single-turn assertion would pass while the bug survives (Risk 1).
- Run it against baseline `bbe5dc7a1` with `scripts/pytest-clean.sh` and capture the failure output verbatim for the PR body. **Do not proceed until it is observed RED.**
- Run only this test file; a full `tests/unit/` run takes ~20 minutes and leaks xdist workers.

### 2. Lane branch-identity surface
- **Task ID**: `build-lane-identity`
- **Depends On**: none
- **Validates**: `tests/unit/test_lane_branch_identity.py`
- **Informed By**: spike-2 (`rev-parse --abbrev-ref HEAD` returns the literal `"HEAD"` when detached); PR #2792 (the module's existing docstring register)
- **Assigned To**: `identity-builder`
- **Agent Type**: `builder`
- **Parallel**: true
- Add `read_worktree_branch`, `lane_branch`, `refresh_lane_branch`, and a `sweep()` + `python -m tools.lane_identity sweep` CLI to `tools/lane_identity.py`.
- `read_worktree_branch` is the **only** lane-scoped spelling of `rev-parse --abbrev-ref HEAD`; it normalises `"HEAD"` to `None` and returns `None` (never raises) on a missing path, a non-repo path, or a subprocess timeout.
- `lane_branch` uses truthiness with `.strip()`, never `is None` (Risk 4), and never returns `"HEAD"` or an empty string.
- `sweep()` reports divergence and exits non-zero if any lane's next turn would be refused. It **reports only** — no mutation (see Rabbit Holes).

### 3. Invert `derived_branch_name`
- **Task ID**: `build-model-accessor`
- **Depends On**: `build-lane-identity`
- **Validates**: `tests/e2e/test_context_propagation.py`
- **Assigned To**: `identity-builder`
- **Agent Type**: `builder`
- **Parallel**: false
- `models/agent_session.py:1840-1844` → `self.branch_name or (f"session/{s}" if s else None)`, with the same `"HEAD"`/empty normalisation.
- Update the two affected cases in `tests/e2e/test_context_propagation.py` per the Test Impact dispositions; the `:169` case should pass unchanged and is a free check that the inversion works.

### 4. Seed-if-empty, and the executor sweep
- **Task ID**: `build-executor-identity`
- **Depends On**: `build-lane-identity`
- **Validates**: `tests/unit/test_lane_branch_identity.py`, `tests/unit/test_session_branch_guard.py`
- **Informed By**: the second-writer finding at `:1566` (not in the issue's recon — read the Solution's dedicated bullet before touching this line)
- **Assigned To**: `executor-builder`
- **Agent Type**: `builder`
- **Parallel**: false
- Rename the local `branch_name` (`:1430`, `:1466`) to `seed_branch_name` so it can only read as a provisioning seed.
- `:1566` — write the seed only when `agent_session.branch_name` is falsy. Comment it, naming #3411 (Risk 2).
- `:1509` — the guard's expected branch becomes `lane_branch(session)`.
- Sweep the remaining consumers to read through `lane_branch(session)`: `:1536`, `:1544`, `:1824`/`:1841`/`:1858` (`_enqueue_nudge`), `:2700`/`:2819` (`save_session_snapshot`), and `agent_session_queue.py:3007`.
- Add the Race-2 recovery: when the guard's expected branch does not exist at all, clear the record, fall back to the seed, and log a WARNING naming the lane — instead of raising.

### 5. Cleanup path and `safe_delete_branch`
- **Task ID**: `build-cleanup`
- **Depends On**: `build-executor-identity`
- **Validates**: `tests/unit/test_safe_delete_branch.py`, `tests/unit/worktree_manager/test_worktree_manager_cleanup.py`
- **Informed By**: spike-1 (the pre-check makes an existing git refusal testable); spike-4 (`merged_via_ancestor` fails safe on a nonexistent branch, so the log must distinguish "unmerged" from "nonexistent")
- **Assigned To**: `executor-builder`
- **Agent Type**: `builder`
- **Parallel**: false
- `session_executor.py:2781` — call `refresh_lane_branch(session, working_dir)` first and act on its return. Detached (`None`) → log at INFO with slug and path, skip cleanup entirely.
- Pass the refreshed branch to `mark_work_done` and `safe_delete_branch`.
- `worktree_manager.py::safe_delete_branch` — add a checked-out-in-any-worktree pre-check ahead of the predicate, returning `skipped_checked_out: True`; the key is present-and-`False` on every other path. A scan failure fails safe (preserve).
- `checkpoint_branch_state` — clear `branch_name` rather than storing the literal `"HEAD"`; leave the record unchanged on a git error (never clear on failure).
- Add the load-bearing comment on the trailing `checkpoint_branch_state` (Risk 1).

### 6. Guard mutation proofs and failure-path tests
- **Task ID**: `test-guards`
- **Depends On**: `build-cleanup`, `build-model-accessor`
- **Validates**: `tests/unit/test_session_branch_guard.py`, `tests/unit/test_safe_delete_branch.py`, `tests/unit/test_lane_branch_identity.py`
- **Assigned To**: `guard-tester`
- **Agent Type**: `test-engineer`
- **Parallel**: false
- Prove AC 5 by mutation: remove the #887 guard, then the #1377 guard, then the #1646 predicate, each in a scratch working copy, and observe the covering test go RED. Capture all three outputs for the PR body.
- Implement the Failure Path Test Strategy items: the four exception-handler assertions, the four empty/invalid-input cases, and the two error-state assertions (`last_error` populated on refusal; `[lane-branch]` skip log names slug and path).
- Confirm the RED test from Task 1 is now GREEN.

### 7. Documentation
- **Task ID**: `document-feature`
- **Depends On**: `test-guards`
- **Assigned To**: `identity-documentarian`
- **Agent Type**: `documentarian`
- **Parallel**: false
- Execute the four tasks in the Documentation section, including correcting `docs/features/eng-session-architecture.md:248`.
- Note: a docs commit triggers a re-review row in this repo's gate. Audit the docs in one pass and pay that cost knowingly rather than dribbling commits.

### 8. Final validation
- **Task ID**: `validate-all`
- **Depends On**: `test-guards`, `document-feature`
- **Assigned To**: `identity-validator`
- **Agent Type**: `validator`
- **Parallel**: false
- Run every row of the Verification table.
- Confirm all six ACs, with the RED evidence for AC 1 and AC 5 present in the PR body.
- Run `python -m tools.lane_identity sweep` on this machine (AC 6) and report the output.

## Verification

Run each row from the repo root on the build branch. Test rows use `scripts/pytest-clean.sh`, never bare `pytest`, and name specific files — a full `tests/unit/` run takes ~20 minutes and leaks xdist workers.

| Check | Command | Expected |
|-------|---------|----------|
| Regression test green (AC 1) | `scripts/pytest-clean.sh tests/unit/test_lane_branch_identity.py -q` | exit code 0 |
| Guard tests green (AC 2, AC 5) | `scripts/pytest-clean.sh tests/unit/test_session_branch_guard.py tests/unit/test_safe_delete_branch.py tests/unit/test_branch_manager.py -q` | exit code 0 |
| Worktree manager tests green | `scripts/pytest-clean.sh tests/unit/worktree_manager/ -q` | exit code 0 |
| Context propagation green (accessor inversion) | `scripts/pytest-clean.sh tests/e2e/test_context_propagation.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lane sweep clean (AC 6) | `python -m tools.lane_identity sweep` | exit code 0 |
| Accessor inverted (AC 4) | `grep -A4 "def derived_branch_name" models/agent_session.py` | output contains `self.branch_name or` |
| `skipped_checked_out` exists (AC 2) | `grep -c "skipped_checked_out" agent/worktree_manager.py` | output > 1 |
| Seed-if-empty guard present (Risk 2) | `grep -c "if not agent_session.branch_name" agent/session_executor.py` | output > 0 |
| Source-of-truth doc exists (AC 4) | `test -f docs/features/lane-branch-identity.md && grep -c "checkpoint_branch_state" docs/features/lane-branch-identity.md` | output > 0 |
| Docs index updated | `grep -c "lane-branch-identity" docs/features/README.md` | output > 0 |
| **Anti-criterion** — no surviving stale local named `branch_name` in the executor (Risk 6) | `grep -cE "^ *branch_name = " agent/session_executor.py` | match count == 0 |
| **Anti-criterion** — no lane-scoped HEAD read outside the single spelling (Risk 6) | `grep -rc -- "--abbrev-ref" agent/session_executor.py agent/agent_session_queue.py` | match count == 0 |
| **Anti-criterion** — the sweep reports, never mutates ([EXTERNAL] No-Go) | `grep -cE '"(checkout\|reset\|push\|prune\|remove)"' tools/lane_identity.py` | match count == 0 |
| **Anti-criterion** — checkpoint concurrency shape unchanged ([SEPARATE-SLUG #3306] No-Go) | `grep -c "async def checkpoint_branch_state" agent/agent_session_queue.py` | match count == 0 |
| **Anti-criterion** — `post_merge_cleanup` untouched ([SEPARATE-SLUG #3301] No-Go) | `git diff origin/main -- agent/ \| grep -c "post_merge_cleanup"` | match count == 0 |

Baseline measurements taken on `bbe5dc7a1` so the anti-criteria are known to be meaningful rather than vacuously true: `^ *branch_name = ` in the executor currently matches **2**; `--abbrev-ref` currently matches **2** in `agent_session_queue.py` and **0** in `session_executor.py`; `async def checkpoint_branch_state` currently matches **0** (this row is a guard against regression, not a change to make). Each anti-criterion must be demonstrated FAIL against a deliberately-violating input before the PR, with the FAIL output pasted into the PR description.

### Evidence required in the PR body (not a check table)

| Evidence | Why |
|---|---|
| Task 1 test output, RED, run against `bbe5dc7a1` | AC 1. A regression test never seen RED on the known-bad SHA certifies nothing. |
| Three mutation outputs, RED, one per guard (#887, #1377, #1646) | AC 5. Proves each guard still refuses its original bad input. |
| `python -m tools.lane_identity sweep` output, before and after | AC 6, and it makes the 23% divergence figure reproducible. |
| Anti-criterion FAIL outputs against violating inputs | Proves the inverse rows can actually detect a violation. |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Should the lane's branch record live on `AgentSession.branch_name` or on `PipelineLedger` next to the slug?** This plan chooses `AgentSession.branch_name` because it exists, is already written, and needs no Popoto migration — and the fix is urgent, since 23% of live lanes are one turn away from this failure. But the *lane* owns the slug on `PipelineLedger` (PR #2792), and branch identity is arguably lane-scoped too. The cost of choosing wrong is Race 3: two sessions sharing one worktree can hold disagreeing records. Today's behavior is preserved either way, so this is "take the free fix now, move it later" vs. "do it once, properly, with a migration". **I recommend the former** and would take a ruling rather than assume.

2. **Is the Race-2 recovery (a nonexistent expected branch clears the record and falls back to the seed, with a WARNING, instead of raising) acceptable?** I argue it is not a weakening of #1377: a branch that does not exist carries no risk of running on the *wrong* branch, which is the only thing that guard protects against. But #1377 exists because of a production incident, and I would rather hear "yes, that reading is right" than discover later that the raise was load-bearing for a reason not written down.

3. **Should #3413 (the `branch=main` checkpoint anomaly, filed during this planning pass) block this work, or follow it?** This plan promotes `AgentSession.branch_name` to source of truth while #3413 describes a way that field can be written from the wrong directory's `HEAD`. Sequencing #3413 first is the conservative call; shipping this first fixes 23% of lanes sooner. I lean toward shipping this first and treating #3413 as a fast-follow, because the failure #3413 describes predates this change and is not made worse by it — but that is a judgement about acceptable exposure, not a technical fact.
