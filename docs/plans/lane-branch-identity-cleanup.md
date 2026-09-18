---
status: Ready
type: bug
appetite: Medium
owner: Valor Engels
created: 2026-09-18
tracking: https://github.com/tomcounsell/ai/issues/3411
last_comment_id:
revision_applied: true
revision_applied_at: 2026-09-18T09:41:00Z
revision_passes: 3
---

# Lane branch identity: one recorded branch, read by the guard, the cleanup, and the checkpoint

## Problem

An agent lane is a slug binding a worktree (`.worktrees/{slug}/`), a branch (`session/{slug}`), and a task list. The executor computes the lane's branch name **once**, from the slug, at `agent/session_executor.py:1430` (`branch_name = resolved_branch`) or `:1466` (`_session_branch_name(session.session_id)`), and never rebinds it for the remaining ~1350 lines of the function.

That single stale string is then used for two very different jobs at opposite ends of the turn:

- **Launch guard** (`:1509`, issue #1377): `verify_worktree_branch(working_dir, branch_name)` — refuses to start the harness unless the worktree is on that branch.
- **End-of-turn cleanup** (`:2781`–`:2790`): `mark_work_done(working_dir, branch_name)` then `safe_delete_branch(..., branch_name, predicate=merged_via_ancestor)`.

**Which of those two deletes the branch is the crux, and the issue names it wrong.** The issue attributes the deletion to `safe_delete_branch` at `:2787`. The 2026-09-16 worker log shows the opposite: `safe_delete_branch` evaluated the branch as unmerged and **preserved** it — it behaved correctly. The deletion came one line earlier, inside `mark_work_done` (`:2781`) → `agent/branch_manager.py:443-451`, which runs `git branch -d {branch_name}` gated only on `switched and branch_name.startswith("session/")`: **no worktree-branch check and no merge predicate.**

So the #1646 unmerged-branch guard is bypassed by **ordering**, not by an override or a flag. It runs second, against a branch that is already gone. Any fix confined to `safe_delete_branch` would have changed nothing about the incident that motivated this issue.

If the agent ran `git checkout` during the turn (normal, not exotic: **7 of 31 live lanes, 23%, are on something other than `session/{slug}`**), `mark_work_done` deletes the slug-derived branch — which is not where the work is — unguarded, and `safe_delete_branch` then fail-safes over the corpse. The next turn recomputes the same slug-derived name, finds it gone, and the guard refuses to launch.

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

646 ms between the delete and the guard that demanded it. The guard is correct; cleanup destroyed what it checks for. Note the deleting line names its own author — `mark_work_done`, not `safe_delete_branch`. The evidence was in the log from the start; the issue's narrative simply attributed it to the next call down.

One further trap in that line: `branch_manager.py:445` passes neither `check=True` nor any `returncode` inspection, so `logger.info(f"Deleted local branch: {branch_name}")` at `:451` fires whether the delete succeeded or failed. **The log string is not evidence of deletion** — here it happened to be true, but nothing in the code guarantees that, and any future triage that leans on this line is reading an unconditional print. See Task 5b. The user-visible symptom is silence: the work had shipped (PR #3352, merged) but the turn that would have reported it never launched, so the Telegram thread got a reaction emoji and nothing else.

**Root cause, stated precisely:** branch identity for a lane is **derived** on every read instead of **recorded** once. Three places disagree — the slug-derived `session/{slug}` (executor guard + cleanup), `AgentSession.branch_name` (written by `checkpoint_branch_state` from the live `HEAD`), and the worktree's live `HEAD` — and `derived_branch_name` actively discards the recorded value whenever a slug exists.

**A fourth source exists and is deliberately owned elsewhere: [#3417](https://github.com/tomcounsell/ai/issues/3417).** `agent/session_revival.py:27-30` derives a branch as `f"session/{sanitize_branch_name(session_id)}"` — a *session-id* derivation, not the slug derivation above — and feeds it to the same `mark_work_done` this plan is fixing, via `bridge/telegram_bridge.py:2456`. This plan unifies the three sources it enumerates; #3417 is blocked by this one and adopts whatever resolution lands here. The count in this section is "three, plus a fourth owned by #3417", not "three, and that is all of them" — the distinction matters because the invariant below is stated as repo-wide and is not yet true on the revival path.

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
- **Confidence**: high (corroborated by the incident log: `safe_delete_branch` preserved, correctly)
- **Impact on plan**: this is exactly what happened in the incident, and it explains why the guard looks innocent in the log. By the time `safe_delete_branch` ran at `:2787`, `mark_work_done` had already deleted the branch at `:2781`, so the predicate was handed a name that no longer resolved, returned `False`, and logged a reassuring `preserved` line about a branch that was **already gone**. The #1646 defect here is therefore not "deletes unmerged work" and not merely "guards the wrong branch" — it is "**runs too late to matter**". The remedy is an ordering one: no deletion may be attempted before the merge predicate has been evaluated. AC 2 and AC 3 are both written against ordering for this reason.

## Data Flow

Branch identity across one turn, today (the numbers are `session_executor.py` lines unless noted):

1. **Entry point** — a Telegram message becomes an `AgentSession` row; the worker dequeues it and calls the executor.
2. **`:1400-1466` branch resolution** — `resolve_branch_for_stage` / slug mapping produces `resolved_branch = f"session/{slug}"`; `branch_name` is bound at `:1430` (slug path) or `:1466` (no-slug path). **This is the last write to `branch_name` in the entire function.**
3. **`:1468-1491` #887 guard** — refuses an eng session with a slug running in the repo root. Reads `working_dir`, not `branch_name`. Unaffected by this plan.
4. **`:1494-1515` #1377 launch guard** — `verify_worktree_branch(working_dir, branch_name)` compares the live `HEAD` against the derived name. Clean mismatch → auto `git checkout`; dirty mismatch or git failure → raise.
5. **Harness runs** — `claude -p` executes inside the worktree. **The agent may `git checkout` freely. Nothing observes this.**
6. **`:2781` `mark_work_done(working_dir, branch_name)`** — archives the plan, commits, returns to main using the stale name, and then, at `agent/branch_manager.py:443-451`, runs `git branch -d {branch_name}` on that stale name. **This is the destructive act.** Its only gate is `switched and branch_name.startswith("session/")` — no worktree-branch check, no merge predicate, and no inspection of the subprocess `returncode` (so the `Deleted local branch` log at `:451` is unconditional).
7. **`:2787` `safe_delete_branch(working_dir, branch_name, predicate=merged_via_ancestor)`** — runs **after** the branch is already deleted. The predicate is handed a name that no longer resolves, returns `False`, and the call fail-safes to `skipped_unmerged`, logging `preserved` about a branch that no longer exists. The #1646 guard is bypassed by ordering, never by an override.
8. **`:2589` `complete_transcript(...)` → `finalize_session` → `checkpoint_branch_state(session)`** — this is where the record is written, and **it runs at `:2589`, *before* the cleanup block at `:2781`.** `checkpoint_branch_state` (`agent_session_queue.py:593`) has exactly **one** production caller: `models/session_lifecycle.py:620`, step 3 of `finalize_session`, gated on `skip_checkpoint` and wrapped in a `try/except` that logs at DEBUG. It reads the live `HEAD` and writes it to `AgentSession.branch_name` (`commit_sha` is assigned at `:624` but omitted from the `update_fields` list at `:625`, so the SHA half is silently not persisted — see Task 5).
9. **`:2867` `finally:` → `_finalize_if_still_running`** — the executor's only `finally`-scoped finalizer. It is keyed on `status == "running"`, so on the normal path — where step 8 already finalized the row — **it is a no-op**. Nothing re-reads the live `HEAD` after cleanup.
10. **Next turn** — re-enters at step 2, re-derives `session/{slug}`, and step 4 refuses because step 7 deleted it. `derived_branch_name` (`models/agent_session.py:1841`) would have masked a row-only fix anyway: it returns `f"session/{s}"` whenever a slug exists, discarding what step 8 recorded.

**The shape of the bug is a record written too early, then discarded.** Step 8 writes the truth *before* step 7 destroys the state it describes, and step 10 throws that record away regardless. There is **no post-cleanup checkpoint anywhere in the executor** — the `finally:` at step 9 cannot supply one, because on the normal path the row is no longer `running`.

**Verified 2026-09-18 on `origin/main` (`ba24bfd0c`), correcting an earlier draft of this section** that asserted a turn-end `finally:` checkpoint after cleanup. It does not exist:

```
grep -rn "checkpoint_branch_state" --include="*.py" agent models worker bridge tools
  agent/agent_session_queue.py:593:  def checkpoint_branch_state(...)   # definition
  models/session_lifecycle.py:618:   from agent.agent_session_queue import ...
  models/session_lifecycle.py:620:   checkpoint_branch_state(session)   # sole production call
```

This is load-bearing for the fix: the trailing refresh is something the build must **add**, not something it must **preserve**. A build that assumes it already exists ships refresh-before-cleanup only and re-creates the bug under a new name (Risk 1).

## Why Previous Fixes Failed

No prior fix targeted this defect, so strictly there is nothing to post-mortem. What is worth recording is why three *correct* guards stacked on top of each other still produced a dead session — and why the one prior fix that solved this exact pattern did not generalise.

| Prior fix | What it did | Why it did not prevent this |
|-----------|-------------|-----------------------------|
| #1377 / `verify_worktree_branch` | Refuses to launch when the worktree is not on the expected branch | It trusts the caller's `expected_branch` string absolutely. A guard that validates *actual* against a *derived* expectation cannot detect that the expectation itself is wrong. |
| #1646 / `safe_delete_branch` + `merged_via_ancestor` | Refuses to delete a branch with unmerged commits | **It was never reached.** The fix hardened one deletion site while an older, unguarded one (`mark_work_done` → `branch_manager.py:443`) sat one line earlier in the same block. #1646 guarded the second-to-last door. A guard added to a path is only as good as the enumeration of paths that preceded it, and no sweep was done for other `git branch -d` callers. |
| #887 / main-checkout guard | Refuses eng-with-slug sessions in the repo root | Correct and orthogonal. It checks the *directory*, never the branch, so it was never going to catch this. |
| PR #2792 / `tools/lane_identity.py` | Replaced slug *derivation* with a single recorded slug on `PipelineLedger.slug` | **Solved this exact pattern one level up and stopped there.** Its docstring warns that "derivation wearing adoption's clothes" is the defect, and it disciplined the slug — but the branch, which is downstream of the slug, kept being re-derived by every consumer. |

**Root cause pattern:** *a guard can only be as correct as the identity it is handed, and only as useful as its position in the call order.* Every guard above was built to validate a value; none of them owns the value. As long as branch identity is re-derived at each call site, adding guards multiplies the number of places that can confidently assert the wrong thing. The remedy is the #2792 remedy: **record the identity once, read it everywhere, never re-derive.**

**Second pattern, specific to #1646:** a guard placed on one call site is worthless if an unguarded site runs first. The sweep that #1646 should have done — and that this plan does — is for *every* local branch deletion. Run 2026-09-18 against `ba24bfd0c`:

```
$ grep -rn '"branch", "-d"\|"branch", "-D"' --include="*.py" agent models worker bridge tools scripts
agent/branch_manager.py:446:  ["git", "branch", "-d", branch_name],
```

Exactly **two** deletion sites exist repo-wide: this one and `safe_delete_branch`'s (which builds its argv dynamically, hence its absence from the literal grep). #1646 hardened one and left the other. Task 5a brings both under the same predicate; the Verification table holds the sweep so a third site cannot be added silently.

## Architectural Impact

- **New dependencies**: none. No new packages, services, or config. Everything is `git` and code already in the repo.
- **Interface changes**:
  - `models/agent_session.py::AgentSession.derived_branch_name` — precedence inverted (recorded value wins over the slug seed). Behavior change visible to any reader; there are exactly two (`tests/e2e/test_context_propagation.py`, and `docs/features/eng-session-architecture.md:248`).
  - `agent/worktree_manager.py::safe_delete_branch` — return dict gains a `skipped_checked_out: bool` key. Additive; existing `deleted` / `skipped_unmerged` / `branch` / `error` keys unchanged.
  - `tools/lane_identity.py` — three new public functions (`read_worktree_branch`, `resolve_lane_branch`, `refresh_lane_branch`) plus a `sweep` CLI subcommand.
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

**Merge order: this lane lands third, behind #3091 and #2652.** Not a code dependency — the sequencing is a collision-surface decision made outside this plan. Build and review may proceed in parallel with those two; only the merge is ordered. Before merging, rebuild the test set against the post-#3091/#2652 `main` rather than trusting this lane's green: a clean merge-tree proves textual safety only. Re-run the Verification table's measured-RED rows after the rebase, since every "measured N today" figure in it is pinned to `ba24bfd0c`.

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
  - `resolve_lane_branch(session) -> str | None` — **the accessor**. Returns the recorded `branch_name`; falls back to the `session/{slug}` seed (obtained by calling the module's existing `lane_branch_name(slug)`, never by re-spelling the prefix); falls back to `_session_branch_name(session_id)` for slug-less sessions; `None` if nothing applies. Never returns `"HEAD"`.

    **Named `resolve_lane_branch`, not `resolve_lane_branch`, deliberately.** The module already exports `lane_branch_name(slug) -> str | None` at `tools/lane_identity.py:95`. A `lane_branch(session)` sitting beside a `lane_branch_name(slug)` — two public functions, near-identical names, different parameter types, different answers — is precisely the confusion this plan exists to remove. `resolve_lane_branch` reads as "work out what this lane's branch actually is"; `lane_branch_name` reads as "spell a branch name from a slug". The two docstrings must cross-reference each other so a reader landing on either is told the other exists and when to prefer it.

    `lane_branch_name`'s docstring currently claims the `session/` prefix "is applied here and nowhere else", which is **false today**: `agent/session_executor.py` builds `f"session/{slug}"` inline at **two** sites, `:1407` and `:1428`. Task 4 routes both through `lane_branch_name(slug)`, which makes the docstring true rather than amending it downward — the claim is the right one, it just was not enforced. The claim holds for the executor only; `agent/session_revival.py:29` is a third spelling, owned by [#3417](https://github.com/tomcounsell/ai/issues/3417).
  - `refresh_lane_branch(session, worktree_path) -> str | None` — reads the live `HEAD` and delegates the write to `checkpoint_branch_state`, returning the value now on record.
  - `sweep()` + a `python -m tools.lane_identity sweep` CLI — walks `git worktree list --porcelain`, reports every lane whose live `HEAD` diverges from its recorded branch, and exits non-zero if any lane is in a state whose next turn would be refused. This is AC 6, made executable and repeatable rather than a one-off measurement.

- **`checkpoint_branch_state` (`agent/agent_session_queue.py:593`)**: promoted to sole writer of the record. Gains three behaviors: the `rev-parse --abbrev-ref HEAD` at `:608` is replaced by a `read_worktree_branch` call; when that read yields `"HEAD"`, `branch_name` is cleared instead of storing the literal (spike-2); and **`"commit_sha"` is added to the `update_fields` list at `:625`**, because `session.commit_sha` is assigned at `:624` and then not saved — so the SHA half of the checkpoint does not persist today. A plan that promotes this function to source of truth cannot leave half of it unwritten. `restore_branch_state` (`:657`) reads a missing `commit_sha` as "no checkpoint data" and early-returns `True`, which is why the omission has been invisible.

- **`derived_branch_name` (`models/agent_session.py:1840-1844`)**: precedence inverted to `self.branch_name or (f"session/{s}" if s else None)`. Without this, a fix confined to the executor is masked at the model layer — the recon called this out explicitly.

- **`safe_delete_branch` (`agent/worktree_manager.py:126`)**: gains an explicit checked-out-branch pre-check ahead of the `merged_via_ancestor` predicate. git already refuses such a delete (spike-1), but an incidental error string is not a testable guarantee; the pre-check turns it into a named, logged, asserted decision returning `skipped_checked_out: True`.

- **`mark_work_done` (`agent/branch_manager.py:443-451`)**: the actual deleting site, and the one the incident turned on. Its bare `git branch -d {branch_name}` is **removed**, not merely re-pointed. Branch deletion becomes `safe_delete_branch`'s sole responsibility, so there is exactly one deletion site in the repo and the merge predicate cannot be outrun. `mark_work_done` keeps its archive/commit/return-to-main duties and receives the same refreshed branch as every other consumer. This is the change that would have prevented the incident; the `safe_delete_branch` hardening below is defense in depth, not the fix.

- **The executor cleanup block (`agent/session_executor.py:2781-2790`)**: stops using the turn-start `branch_name`. Refreshes the record from the live `HEAD`, then acts on it.

### Flow

Turn end, after the fix:

**Harness exits** → `refresh_lane_branch(session, working_dir)` reads live `HEAD` and records it → **branch known** → detached? → *yes*: log and skip cleanup entirely → **done** / *no*: `mark_work_done(working_dir, work_branch)` (archives plan, commits, returns worktree to `main`, **deletes nothing**) → `safe_delete_branch(work_branch, predicate=merged_via_ancestor)` — now the **only** deletion site, and the first code to attempt a delete → checked out anywhere? *yes* → preserve + log → unmerged? *yes* → preserve + log (#1646, now reached in time and about the right branch) → else delete, with the log line gated on the subprocess result → **trailing `refresh_lane_branch(session, working_dir)`, added inline at the end of the cleanup block** (not in the executor's `finally:`, which no-ops on the normal path — see Data Flow step 9) re-reads live `HEAD` (now `main`) and updates the record → **next turn**: guard compares live `HEAD` (`main`) against record (`main`) → match → **harness launches**.

### Technical Approach

- **Refresh-act-refresh is the ordering fix, and the trailing refresh must be BUILT, not preserved.** Today the only component that learns the truth (`checkpoint_branch_state`) runs at `:2589` via `complete_transcript` → `finalize_session`, *before* the destructive act at `:2781`. The executor's `finally:` at `:2867` calls `_finalize_if_still_running`, which is keyed on `status == "running"` and is therefore a **no-op on the normal path** — the row was finalized at `:2589`. **There is no post-cleanup checkpoint to keep.** The fix needs both halves:
  - a refresh *before* cleanup, so the destructive act names the branch that actually holds the commits;
  - an **explicitly added** refresh *after* cleanup, inline at the end of the cleanup block, so that once `mark_work_done` has returned the worktree to `main` and the work branch is deleted, the record follows the worktree to `main`.

  Without the second, the next turn's guard demands the branch this turn just deleted — identical failure, new string (Risk 1). Put the trailing refresh in the cleanup block itself rather than routing it through `finalize_session` again: the row is already terminal by then, `finalize_session` would raise `StatusConflictError`, and the DEBUG-level `try/except` at `session_lifecycle.py:621` would swallow the miss silently. **This trailing refresh is load-bearing; a reviewer who sees it removed as redundant should treat that as a blocker.**

- **`skip_checkpoint=True` paths are out of this fix's reach, and that is acceptable.** Three production sites pass it (`tools/agent_session_scheduler.py:1166`, `:1198`, `tools/sdlc_session_ensure.py:1199`) — all kill/reap paths that finalize a row whose worktree the caller is not standing in. None of them runs the executor's cleanup block, so none of them deletes a branch; the record simply goes un-refreshed, and the next turn heals it via the leading refresh. The trailing refresh being inline in the cleanup block (rather than inside `finalize_session`) is what makes this true: the two paths do not share a code path at all.

- **The launch guard's logic is unchanged; only its input changes.** `verify_worktree_branch(working_dir, resolve_lane_branch(session))` instead of `verify_worktree_branch(working_dir, branch_name)`. Match passes, clean mismatch auto-checks-out, dirty mismatch raises — all identical. #1377's scenario (a new session reusing a worktree left on a prior stage's branch) still fails, because a new session has no record and falls back to the seed, exactly as today. **The guard is not relaxed. It is handed a true expectation instead of a guessed one.** Answering the issue's open question 3: no, the guard should not learn to accept divergence — the divergence should stop being a surprise to it.

- **`branch_name` at `:1430`/`:1466` keeps its job, which is worktree *provisioning*.** It is the seed that names a branch being created. It must simply stop being reused 1300 lines later as an identity. The build should leave the two assignment sites alone and rename the local to `seed_branch_name` so the next reader cannot make this mistake again.

- **Detached lanes skip cleanup rather than guessing.** Five of the seven divergent lanes are detached. There is no branch name to hand `merged_via_ancestor`, and inventing one is how this bug started. Skip, log at INFO with the lane slug, leave the lane resumable.

- **There is a second, undocumented writer of the record, and it must be neutered first.** `agent/session_executor.py:1566` does `agent_session.branch_name = branch_name` at turn *start*, persisting the slug-derived seed onto the row alongside `task_list_id` / `exec_cwd` / `exit_reason`. This was not in the issue's recon, and it is fatal to the scheme if left alone: every turn would clobber the record with the derived name moments before the guard reads it, and the fix would appear to do nothing. It also explains the observation the issue deliberately dropped (the row recording `branch=main` for a lane never on `main`) — two writers, last-write-wins, neither aware of the other. **Change `:1566` to seed-if-empty**: write the derived name only when `agent_session.branch_name` is falsy, leaving a recorded value untouched. `checkpoint_branch_state` remains the only component that *updates* the record.

- **Sweep every remaining consumer of the stale local, not just the two the issue names.** `branch_name` is read at `:1509` (guard), `:1536` (log), `:1544` and `:2700`/`:2819` (`save_session_snapshot`), `:1566` (the second writer above), `:1824`/`:1841`/`:1858` (`_enqueue_nudge`), and `:2781`-`:2809` (cleanup). The nudge path matters: a nudge re-enqueues the lane mid-turn, so it must carry the lane's real branch or it re-seeds the same staleness. The snapshot paths are diagnostic, but a diagnostic that records the wrong branch is how this incident stayed invisible for a day. All of them read through `resolve_lane_branch(session)` after the change. Per the repo's replicated-defect rule this closes on a clean grep sweep, not an enumerated list — the enumeration above is orientation, and the Verification table holds the sweep.

- **No partial migration.** The consumers — launch guard, cleanup, checkpoint, and the model accessor — change in one PR. A sweep (`grep -rn "abbrev-ref HEAD" --include=*.py agent/ tools/ models/`) must show `read_worktree_branch` as the only lane-scoped spelling afterwards; this is a Verification row, not a checklist item, per the repo's replicated-defect rule.

- **Integration points**: `agent/session_executor.py` (guard input, seed-if-empty write, nudge path, snapshot calls, cleanup block), `agent/agent_session_queue.py` (`checkpoint_branch_state`, and the `save_session_snapshot` call at `:3007` that also re-derives), **`agent/branch_manager.py` (`mark_work_done` — its `branch_name` input on the same footing as the guard's, and its unguarded `git branch -d` at `:443-451` removed)**, `agent/worktree_manager.py` (`safe_delete_branch`), **`bridge/telegram_bridge.py` (`:2450-2456`, the revival-dormancy path — `mark_work_done`'s second production caller, which loses its deletion in Task 5a and gains a guarded `safe_delete_branch` in its place)**, `models/agent_session.py` (`derived_branch_name`), `tools/lane_identity.py` (new surface). `agent/session_revival.py::_session_branch_name` is a third, session-id-derived spelling of the prefix and is **not** unified by this plan — it is owned by [#3417](https://github.com/tomcounsell/ai/issues/3417), which is blocked on this one.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `agent/session_executor.py:2811` — `except Exception as e: logger.warning("Failed to auto-mark session done: %s")` wraps the whole cleanup block. It already logs, but nothing asserts it. Add a test that forces `refresh_lane_branch` to raise and asserts (a) the warning is emitted with the session's `project_key`, and (b) **the turn still completes** — cleanup failure must never fail the session.
- [ ] `agent/agent_session_queue.py:632` — `checkpoint_branch_state`'s `except Exception` logs a warning and returns. Add a test that a git failure leaves `branch_name` **unchanged** (not cleared), so a transient git error cannot erase a good record and strand the next turn.
- [ ] `agent/agent_session_queue.py:624-625` — assert `commit_sha` **round-trips**: checkpoint a session, reload the row through the ORM (`AgentSession.query.filter(...)`, never raw Redis), and compare. This test is RED today because `"commit_sha"` is missing from `update_fields`. Pair it with a `restore_branch_state` case proving the early-return-on-missing-SHA path at `:657` no longer silently swallows a good checkpoint.
- [ ] `agent/worktree_manager.py:171` — `safe_delete_branch`'s predicate-error handler already fails safe to `skipped_unmerged`. Extend the existing test to cover the new `skipped_checked_out` path raising inside the worktree scan: a scan failure must also fail safe (preserve), never delete.
- [ ] `read_worktree_branch` is new and must not raise: a missing path, a non-repo path, and a `git` subprocess timeout each return `None`. Three tests.

### Empty/Invalid Input Handling
- [ ] `resolve_lane_branch(session)` with: no slug and no record; empty-string `branch_name` (Popoto stores unset strings as `""`, not `None` — assert falsy handling, not `is None`); whitespace-only `branch_name`; `branch_name == "HEAD"`. Each must yield the seed or `None`, never a bogus branch name.
- [ ] `verify_worktree_branch` already raises `ValueError` on an empty `expected_branch`. Assert `resolve_lane_branch` never hands it one — this is the interface contract between the accessor and the guard.
- [ ] `safe_delete_branch` with a branch that does not exist: `merged_via_ancestor` returns `False` (spike-4), so the call preserves. Assert the *log* distinguishes "preserved because unmerged" from "preserved because nonexistent" — the incident's reassuring-but-meaningless log line is the thing to stop reproducing.
- [ ] Detached worktree end-to-end: cleanup skipped, record cleared, next turn launches.

### Error State Rendering
- [ ] The user-visible symptom of this bug was **silence** — a reaction emoji and no message. Add a test asserting that when the launch guard does refuse, `last_error` is populated on the `AgentSession` row so the failure is attributable rather than silent. (The guard already raises; this asserts the raise reaches the row.)
- [ ] Assert the `[lane-branch]` skip log names the lane slug and the worktree path, so an operator reading `logs/worker.log` can identify which lane skipped cleanup and why.

## Test Impact

- [ ] `tests/e2e/test_context_propagation.py:152` — **UPDATE**: currently asserts `child.derived_branch_name == "session/my-cool-feature"` for a row that has a slug. Under the inverted precedence the recorded `branch_name` wins, so the fixture must either clear `branch_name` (to keep asserting the seed) or the assertion must move to the record. Decide by reading what the test is actually about — it is a *context propagation* test, so the seed is probably the point; clear the field in the fixture and add a sibling case asserting the record wins when set.
- [ ] `tests/e2e/test_context_propagation.py:169` — **KEEP, verify**: asserts `derived_branch_name == "feature/manual-branch"` for a row with a manual branch. This case already expects record-wins and should pass unchanged — it is a free regression check that the inversion works.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_venv_provisioning.py` — **UPDATE**: this is where `verify_worktree_branch` is actually covered (`# Issue #1377: verify_worktree_branch`, class at `:51`, cases at `:60`/`:78`). Cases must stay green under the new input (`resolve_lane_branch(session)` instead of the derived local), and this file is the **named target for AC 5's #1377 mutation proof**.
- [ ] `tests/unit/test_session_isolation_bypass.py` — **UPDATE / mutation target**: the worker-side #887 coverage (its own docstring: "Fix A: Worktree enforcement guard in `_execute_agent_session()`"). This is the **named target for AC 5's #887 mutation proof**.
- [ ] `tests/unit/test_session_executor_runner_dispatch.py`, `tests/unit/test_session_executor_lane_visibility.py`, `tests/unit/test_agent_session_scheduler_worktree_inheritance.py`, `tests/integration/test_merge_stage_slug_reuse.py` — **REVIEW**: the remaining call sites of `verify_worktree_branch`. Each passes an expected branch; confirm none breaks when the executor's argument changes from the derived local to the resolved record.
- [ ] `tests/unit/test_session_branch_guard.py` — **NOT AFFECTED. Do not target it.** Despite the name, it subprocesses `.githooks/pre-commit` against ephemeral repos to test the **bash git-side hook for issue #1288**, and its docstring explicitly defers the worker-side path to `tests/unit/test_session_isolation_bypass.py`. It exercises none of `verify_worktree_branch`, `checkpoint_branch_state`, or `derived_branch_name`. An earlier draft of this plan named it as the validation target for Tasks 4 and 6 and as an AC 5 mutation target; that was wrong in every place it appeared. Listed here so the mistake is not made a third time.
- [ ] `tests/unit/test_session_lifecycle_consolidation.py` — **REVIEW**: ~20 cases patch `checkpoint_branch_state`, including `:196` asserting `skip_checkpoint` suppresses it. Task 5 changes that function's body (detached normalisation, `commit_sha` persistence); the patches are at the boundary so they should hold, but the `skip_checkpoint` case is the one to read against the Technical Approach's `skip_checkpoint` bullet.
- [ ] `tests/unit/test_safe_delete_branch.py` — **UPDATE**: add the `skipped_checked_out` case and a case asserting the new key is present-and-`False` on every existing path, so callers can branch on it unconditionally.
- [ ] `tests/unit/worktree_manager/test_worktree_manager_cleanup.py` — **UPDATE**: cleanup assertions that assume the slug-derived branch is the deletion target.
- [ ] `tests/unit/test_branch_manager.py` — **UPDATE (upgraded from REVIEW in revision 2)**: `mark_work_done` both receives the live branch instead of the seed *and* stops deleting branches (Task 5a). Any existing case asserting that it deletes `session/*`, or asserting the `Deleted local branch` log, now asserts the opposite: `mark_work_done` leaves the branch intact and performs no `git branch -d`. Also check whether any case asserts the commit message text `"Mark work as done: {branch_name}"`. Add a case proving no deletion occurs, which is the unit-level half of AC 2.
- [ ] `tests/unit/test_session_revival.py` (or the nearest existing revival-path test module; create it if none exists) — **NEW, covering `mark_work_done`'s second caller.** Three cases on the bridge revival-dormancy path (Task 5a): (a) a **merged** branch handed to the new `safe_delete_branch` call is deleted, so `check_revival`'s branch-existence check at `session_revival.py:108-124` drops it and the prompt does not re-fire; (b) an **unmerged** branch is **preserved** — asserting the deliberate behaviour change, since today the bridge deletes it outright and destroys unmerged work; (c) a branch **checked out in a worktree** hits the new `skipped_checked_out` path and is preserved. Each asserts a distinguishable log line, per the gated-log rule. This is the only test coverage the bridge caller has; a literal grep sweep will never surface it, because `bridge/telegram_bridge.py` contains neither `"branch", "-d"` nor `--abbrev-ref`.
- [ ] **No xfail markers found** related to this bug — `grep -rn 'pytest.mark.xfail\|pytest.xfail(' tests/` filtered for branch/worktree/lane returns nothing, so there are no expected-failure markers to convert. Re-run the grep at build time in case one lands in the interim.

New tests (not existing-test impact, listed here so the build has one place to look): the RED-first regression test (Task 1), the three guard RED-on-removal proofs (Task 6), and the failure-path tests enumerated above.

## Rabbit Holes

- **Moving the branch record to `PipelineLedger`.** Architecturally tidier — the lane owns the slug there, so arguably it should own the branch too. But it needs a Popoto field, a registered idempotent migration, and a story for lanes that have no ledger (ad-hoc dev sessions with a slug). `AgentSession.branch_name` already exists, is already written, and needs no migration. Take the free fix; raise the move as Open Question #1 and let the decider rule.
- **Making `verify_worktree_branch` "smarter" about acceptable divergence.** The issue's open question 3 floats this and answers itself: the guard is the only thing between an agent and the wrong branch. Any work that makes the guard more permissive is out of scope and should be treated as a design regression.
- **Fixing `checkpoint_branch_state`'s `working_dir` resolution.** The issue's recon dropped the observation that the checkpoint recorded `branch=main` three times for a lane never on `main`. This plan explains part of it (the second writer at `:1566`), but whether `session.working_dir` ever points at the main checkout is a separate question. Do not chase it here.
- **Unifying with #3301's `post_merge_cleanup`.** Same root cause, adjacent code, and the temptation to fix both in one PR is strong. Resist: #3301 is a merge-path no-op, this is a turn-path destructive act, and bundling them doubles the review surface for three guards that each exist because of a prior incident. Coordinate, do not merge.
- **Retroactively repairing the 7 divergent lanes on this machine.** The sweep (AC 6) *reports*; it should not mutate. Repairing live lanes is a manual operator action with real data at stake, and an auto-repair that guesses wrong strands exactly the sessions it meant to save.
- **Rewriting `branch_name` threading through the whole executor function.** The function is ~1350 lines and the temptation is to refactor it. The fix is to stop reusing one local as two different concepts; renaming it to `seed_branch_name` and routing identity reads through `resolve_lane_branch()` achieves that without a restructuring nobody asked for.

## Risks

### Risk 1: The fix re-creates the bug under a new name
**Impact:** Cleanup now deletes the *work* branch (correctly, once merged). If the record is not refreshed afterwards, the next turn's guard demands the branch this turn just deleted — identical failure, different string, and harder to diagnose because the name now looks plausible.
**Mitigation:** An **explicitly added** trailing refresh at the end of the cleanup block. This is the highest-risk item in the plan, because the trailing refresh does not exist today and the first draft of this plan wrongly assumed it did (see Data Flow step 9): the executor's `finally:` at `:2867` is keyed on `status == "running"` and no-ops on the normal path, and `checkpoint_branch_state`'s sole production caller is `models/session_lifecycle.py:620` inside `finalize_session`, which already ran at `:2589`. A build that reads "keep the existing one" and finds nothing to keep ships half the fix.

The proof is behavioural, not structural: the regression test in Task 1 runs **two** turns, not one, and the second turn's successful launch is the assertion. A one-turn test passes while the bug survives. Task 5 carries a dedicated bullet for the trailing refresh and a Verification row greps for it, so its absence fails the gate rather than being noticed in review.

### Risk 2: The second writer at `:1566` is missed or reverted
**Impact:** Total silent failure of the fix. Every turn clobbers the record with the derived seed moments before the guard reads it, so the system behaves exactly as it does today while all the new code appears to be running.
**Mitigation:** Seed-if-empty is its own task (Task 4) with its own test asserting a pre-existing `branch_name` survives a turn start. A Verification row greps for an unconditional assignment to `agent_session.branch_name` outside `checkpoint_branch_state`.

### Risk 3: A guard gets quietly weakened
**Impact:** #887, #1377, or #1646 stops refusing its original bad input. These guards exist because of three separate production incidents; a regression here costs more than the bug being fixed.
**Mitigation:** AC 5 is proven by *mutation*, not by the guards' tests passing: Task 6 removes each guard in a scratch working copy and asserts its test goes RED. A guard certifying absence is worthless until proven RED against the known-bad state. Paste the three RED outputs into the PR body.

### Risk 4: Popoto empty-string semantics defeat the falsy check
**Impact:** Popoto stores unset string fields as `""`, and booleans as the strings `"True"`/`"False"`. If `resolve_lane_branch` tests `is None` instead of truthiness, an unset record reads as a *set* record holding an empty branch name, which `verify_worktree_branch` rejects with `ValueError`. Every lane fails to launch.
**Mitigation:** Explicit test cases for `""` and whitespace-only in the Failure Path Test Strategy. `resolve_lane_branch` normalises with `.strip()` and truthiness, never identity comparison.

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

**No new agent-facing capability is required — this is bridge/worker-internal plumbing.** The agent does not call `resolve_lane_branch()`; the executor does, on the agent's behalf, before and after the harness runs.

Two deliberate exceptions, both operator-facing rather than agent-facing:

- **`python -m tools.lane_identity sweep`** is a module CLI, not a `[project.scripts]` entry point. It is reachable from the agent's Bash tool without any wiring, which is all AC 6 and the post-deploy check need. Adding a `valor-*` console script would be gold-plating for a diagnostic run a handful of times per deploy.
- **No `.mcp.json` / `mcp_servers/` change.** Nothing here belongs in an MCP surface.

Integration test that matters: the Task 1 regression test exercises the real executor path end to end (two turns on one lane, worktree moved off the slug branch between them), so it proves the wiring rather than asserting on a mock. That is the integration coverage for this change.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/lane-branch-identity.md`. This is AC 4's deliverable — the plan names the source of truth, the doc is where it becomes durable. It must state: the invariant (record == live `HEAD` at turn end), the one writer (`checkpoint_branch_state`), the one accessor (`resolve_lane_branch`), the seed-vs-identity distinction, the detached-`HEAD` rule, and the three guards (#887, #1377, #1646) and what each does and does not protect.
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
- [ ] **[AC 2, reframed in revision — ordering, not refusal]** **No deletion of a lane's branch is attempted before the merge predicate has been evaluated**, and the branch any deletion targets is the worktree's actual checked-out branch. Asserted by instrumenting the call order across the whole cleanup block (`mark_work_done` then `safe_delete_branch`) and proving no `git branch -d/-D` executes ahead of `merged_via_ancestor`.
  - **Why not the obvious phrasing.** "Cleanup never deletes a branch the worktree has checked out" and "`safe_delete_branch` returns `skipped_checked_out: True`" both **pass today against unfixed code**: `safe_delete_branch` already refuses (spike-1: git itself refuses; spike-4: the predicate fail-safes). The incident happened anyway, because `mark_work_done` deleted the branch one line earlier without consulting anything. An AC satisfied by the code that produced the bug is not an AC.
  - **Falsifiability requirement:** write this test so it **fails on current `main`**. If it passes unmodified against `ba24bfd0c`, it is testing the wrong thing and must be rewritten before the fix lands. Record the RED output in the PR body alongside AC 1's.
- [ ] **[AC 3]** The #1646 predicate is both **reached** and **invoked with** the branch holding the turn's commits. Asserted on the call argument and on call order, not on the outcome — spike-4 showed the predicate fails safe on a bogus name and that in the incident it ran after the branch was already gone, so outcome-only assertions are satisfied by the bug.
- [ ] **[AC 4]** One documented source of truth, named in this plan (`AgentSession.branch_name`, sole writer `checkpoint_branch_state`, sole accessor `resolve_lane_branch`) and recorded in `docs/features/lane-branch-identity.md`.
- [ ] **[AC 5]** #887 and #1377 both still refuse their original bad inputs, proven by mutation: each guard removed in a scratch copy, its **named** covering test observed RED, output pasted into the PR. #1646 gets the same treatment. The three guard→test pairings are fixed in Task 6's table; `tests/unit/test_session_branch_guard.py` is **not** one of them (it tests the #1288 bash pre-commit hook).
- [ ] **[AC 7, added in revision]** The user-facing symptom the Problem section leads with — a Telegram thread receiving a reaction emoji and nothing else — is covered. Every other AC is a unit test, a mutation proof, or a grep; none of them would have caught the silence. The Failure Path Test Strategy's `last_error` assertion is the mechanism: when the launch guard refuses, the `AgentSession` row carries an attributable error rather than the turn dying mute. Assert it on the row through the ORM.
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
  - Role: owns `agent/session_executor.py` (guard input, seed-if-empty at `:1566`, nudge/snapshot sweep, cleanup block), `agent/agent_session_queue.py::checkpoint_branch_state`, `agent/worktree_manager.py::safe_delete_branch`, **`agent/branch_manager.py::mark_work_done`** (the deletion removal, Task 5a), and **`bridge/telegram_bridge.py:2450-2456`** (the replacement guarded delete on the revival-dormancy path, Task 5a).
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
- Add `read_worktree_branch`, `resolve_lane_branch`, `refresh_lane_branch`, and a `sweep()` + `python -m tools.lane_identity sweep` CLI to `tools/lane_identity.py`.
- `read_worktree_branch` is the **only** lane-scoped spelling of `rev-parse --abbrev-ref HEAD`; it normalises `"HEAD"` to `None` and returns `None` (never raises) on a missing path, a non-repo path, or a subprocess timeout.
- `resolve_lane_branch` uses truthiness with `.strip()`, never `is None` (Risk 4), and never returns `"HEAD"` or an empty string.
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
- **Validates**: `tests/unit/test_lane_branch_identity.py`, `tests/unit/worktree_manager/test_worktree_manager_venv_provisioning.py`, `tests/unit/test_session_isolation_bypass.py`
- **Informed By**: the second-writer finding at `:1566` (not in the issue's recon — read the Solution's dedicated bullet before touching this line)
- **Assigned To**: `executor-builder`
- **Agent Type**: `builder`
- **Parallel**: false
- Rename the local `branch_name` (`:1430`, `:1466`) to `seed_branch_name` so it can only read as a provisioning seed.
- **Both** inline `f"session/{slug}"` constructions become `lane_branch_name(slug)`: `:1407` (the `is_synthetic_slug` arm) and `:1428` (the stageless-eng-with-worktree arm). They are mutually exclusive branches of the same resolution block, both assigning `resolved_branch` before `branch_name = resolved_branch` at `:1430`. Treat this as a class, not two enumerated sites — the closing evidence is a clean `grep -c 'f"session/{slug}"' agent/session_executor.py` over the whole file, which is the Verification row's assertion. Fixing only `:1428` leaves the count at 1 and the row RED. This is what makes `lane_branch_name`'s "applied here and nowhere else" docstring true *for this file*; `agent/session_revival.py:29` spells the prefix a third time and is owned by [#3417](https://github.com/tomcounsell/ai/issues/3417), so do not restate the docstring's claim as repo-wide.
- `:1566` — write the seed only when `agent_session.branch_name` is falsy. Comment it, naming #3411 (Risk 2).
- `:1509` — the guard's expected branch becomes `resolve_lane_branch(session)`.
- Sweep the remaining consumers to read through `resolve_lane_branch(session)`: `:1536`, `:1544`, `:1824`/`:1841`/`:1858` (`_enqueue_nudge`), `:2700`/`:2819` (`save_session_snapshot`), and `agent_session_queue.py:3007`.
- **DO NOT implement the Race-2 recovery in this task.** It is split out to Task 4b, gated on a ruling on Open Question #2. Task 4 leaves `verify_worktree_branch`'s raise behaviour exactly as it is today.

### 4b. Race-2 recovery — GATED on Open Question #2
- **Task ID**: `build-race2-recovery`
- **Depends On**: `build-executor-identity`
- **Validates**: `tests/unit/worktree_manager/test_worktree_manager_venv_provisioning.py`, `tests/unit/test_lane_branch_identity.py`
- **Assigned To**: `executor-builder`
- **Agent Type**: `builder`
- **Parallel**: false
- **DO NOT START without a recorded answer to Open Question #2.** This task changes what the #1377 guard does on a bad input, and the Rabbit Holes section forbids making that guard "smarter about acceptable divergence". Scheduling it unconditionally — as an earlier draft of this plan did — is the plan implementing behaviour it simultaneously says needs a ruling.
- **If the ruling is yes:** when the guard's expected branch does not exist *at all* (as distinct from existing-but-mismatched), clear the record, fall back to the seed, and log a WARNING naming the lane, instead of raising. The existing-but-mismatched path is untouched — that is the case #1377 actually protects.
- **If the ruling is yes**, Task 6's #1377 mutation proof must exercise the Race-2 path specifically, and the PR body must carry the explicit sign-off quoted, not the builder's own judgement.
- **If the ruling is no, or none arrives:** skip this task entirely and file the Race-2 window as a follow-up issue. The rest of the plan ships without it; Race 2 is a crash-window recovery, not a correctness requirement for the main fix.

### 5. Cleanup path and `safe_delete_branch`
- **Task ID**: `build-cleanup`
- **Depends On**: `build-executor-identity`, `build-remove-unguarded-delete`
- **Validates**: `tests/unit/test_safe_delete_branch.py`, `tests/unit/worktree_manager/test_worktree_manager_cleanup.py`
- **Informed By**: spike-1 (the pre-check makes an existing git refusal testable); spike-4 (`merged_via_ancestor` fails safe on a nonexistent branch, so the log must distinguish "unmerged" from "nonexistent")
- **Assigned To**: `executor-builder`
- **Agent Type**: `builder`
- **Parallel**: false
- `session_executor.py:2781` — call `refresh_lane_branch(session, working_dir)` first and act on its return. Detached (`None`) → log at INFO with slug and path, skip cleanup entirely.
- Pass the refreshed branch to `mark_work_done` and `safe_delete_branch`.
- **The removal of `mark_work_done`'s unguarded deletion is split out to Task 5a below**, because it changes a shared function with two production callers and the second one needs its own replacement path. Task 5 depends on 5a having landed the removal; the two are assigned to the same builder and run in sequence.
- **ADD the trailing refresh.** After `safe_delete_branch` returns and inside the same `try`, call `refresh_lane_branch(session, working_dir)` a second time so the record follows the worktree back to `main`. **This call does not exist today and there is nothing to "keep" — write it.** Do not route it through `finalize_session`: the row is already terminal by `:2781` (finalized at `:2589` via `complete_transcript`), so `finalize_session` would raise `StatusConflictError` into the DEBUG-level `try/except` at `session_lifecycle.py:621` and the miss would be invisible. Call `refresh_lane_branch` directly. Comment it as load-bearing, naming #3411 and Risk 1.
- `worktree_manager.py::safe_delete_branch` — add a checked-out-in-any-worktree pre-check ahead of the predicate, returning `skipped_checked_out: True`; the key is present-and-`False` on every other path. A scan failure fails safe (preserve).
- The gated-log rule lives in Task 5a and applies to every site this plan touches, including `safe_delete_branch`'s existing (already-correct) logging.
- `checkpoint_branch_state` (`agent_session_queue.py:593-637`) — three changes:
  - Replace the inline `subprocess.run([... "rev-parse", "--abbrev-ref", "HEAD"])` at `:608` with a `read_worktree_branch` call. Normalising `"HEAD"` inside the existing subprocess block is **not sufficient** — the anti-criterion row requires the spelling itself to be gone, and leaving it is how a second spelling survives the migration.
  - Clear `branch_name` rather than storing the literal `"HEAD"`; leave the record unchanged on a git error (never clear on failure).
  - **Add `"commit_sha"` to the `update_fields` list at `:625`.** `session.commit_sha` is assigned at `:624` but omitted from `save(update_fields=["branch_name", "session_events", "updated_at"])`, so the SHA half of the checkpoint is not persisted today. This plan promotes the function to sole source of truth and asserts the SHA is written in both cases, so the assertion must be made true. `restore_branch_state` (`:657`) treats a missing `commit_sha` as "no checkpoint data" and returns `True` early, which is why this has stayed invisible. Assert the fix by reloading the row through the ORM and comparing — never by reading Redis directly.
- `restore_branch_state` (`agent_session_queue.py:640-673`) — route its own `rev-parse --abbrev-ref HEAD` at `:667` through `read_worktree_branch` as well. It is a resume-time read-and-checkout, distinct from the checkpoint, and it is the second of the two `--abbrev-ref` sites in this file. Leaving it makes the anti-criterion row unsatisfiable on even fully correct code. Behaviour is otherwise unchanged: the detached case now compares `None` against the recorded branch instead of the literal `"HEAD"`, which is the correct reading.

### 5a. Remove `mark_work_done`'s unguarded deletion — and cover both callers
- **Task ID**: `build-remove-unguarded-delete`
- **Depends On**: `build-executor-identity`
- **Validates**: `tests/unit/test_branch_manager.py`, `tests/unit/test_safe_delete_branch.py`, `tests/unit/test_session_revival.py`
- **Informed By**: the incident timeline in Root Cause (this deletion ran 646ms before the guard demanded the branch); spike-4 (`merged_via_ancestor` returns `False` on a nonexistent branch, so a second delete attempt against an already-gone branch is silent, which is exactly how the bypass hid)
- **Assigned To**: `executor-builder`
- **Agent Type**: `builder`
- **Parallel**: false

**The removal.** Delete the whole `if switched and branch_name.startswith("session/")` block in `agent/branch_manager.py:443-451`. Today it runs `git branch -d {branch_name}` gated only on that prefix test — no worktree-branch check, no merge predicate — one line *before* `safe_delete_branch` gets to evaluate anything, which is why the #1646 guard was bypassed by **ordering** rather than by an override. `mark_work_done` keeps archiving, committing, and returning to `main`, and stops deleting branches entirely, leaving `safe_delete_branch` as the repo's sole deletion site. Comment the removal site naming #3411 so it is not reinstated.

**`mark_work_done` has exactly two production callers, and the plan must carry both.**

- **Caller 1 — `agent/session_executor.py:2781` (safe as-is).** The removal needs no replacement here, and for a stronger reason than revision 2 gave: the cleanup block runs only under `not task.error and not _is_non_clean_runner_exit(agent_session) and not chat_state.defer_reaction` (`:2764-2772`), i.e. after `complete_transcript` finalized the row at `:2589` — and `check_revival` enumerates candidate branches **only** from sessions with `status` in `("pending", "running")` (`session_revival.py:74-81`). A terminal session's branch is never a revival candidate at all, so the terminal-sibling filter at `:86-104` is not even the thing protecting this path. `safe_delete_branch` at `:2787` remains the deletion for merged work; an unmerged branch is preserved, which is #1646's entire point.
- **Caller 2 — `bridge/telegram_bridge.py:2456` (needs a replacement path).** This one fires immediately after the revival prompt is sent, under the comment "Mark the stale work as dormant so it doesn't re-trigger", and there is **no `safe_delete_branch` anywhere in that module**. The branch it hands in came from a pending-or-running session by construction, so the terminal-sibling filter does not cover it; the only thing that stops the prompt re-firing is the `git branch -d`, because `check_revival`'s branch-existence check (`session_revival.py:108-124`) is what drops a candidate from the list. Archive, commit and return-to-main do not remove a branch from `git branch --list`. Remove the deletion without a replacement here and a lane re-fires its revival prompt every `REVIVAL_COOLDOWN_SECONDS` (86400, `session_revival.py:23`) indefinitely — and because that cooldown is **per-chat**, each false revival also suppresses genuine ones for 24h.

**The replacement (shape (a), chosen).** Immediately after the `mark_work_done` call at `bridge/telegram_bridge.py:2456`, inside the same `try`, add:

```python
from agent.worktree_manager import merged_via_ancestor, safe_delete_branch

result = safe_delete_branch(
    working_dir_str,
    revival_info["branch"],
    predicate=merged_via_ancestor,
    force=False,
)
```

and log the outcome distinguishably (deleted / preserved-unmerged / preserved-checked-out / failed), per the gated-log rule below. Three things a builder must not assume here:

- `working_dir_str` on this path resolves from `project.get("working_directory")` and is the **shared project checkout**, not a `.worktrees/{slug}/` path. `refresh_lane_branch` and `verify_worktree_branch` are lane-scoped and do **not** apply; do not reach for them.
- The import is new to this module. `agent/session_revival.py:10-15` already imports `safe_delete_branch` and `merged_via_tree`, so the dependency direction is established, but `bridge/telegram_bridge.py` has neither import today.
- Behaviour deliberately changes for **unmerged** branches: today they are deleted (an outright #1646 violation on this path — the bridge destroys unmerged work to achieve dormancy), and after this change they are preserved and the revival prompt can re-fire once per 24h per chat. That re-nag is the designed cadence for genuinely unfinished work, not a regression. The merged-but-stuck case, which is the one that nags pointlessly, goes away because the branch is deleted.

**Shape (b) was considered and not chosen.** Moving dormancy out of git entirely — recording the dismissed branch in a persisted dormant set that `check_revival` filters on alongside the terminal-branches filter — removes the bridge path's dependency on deletion altogether and also spares unmerged branches the re-nag. It is the better end state and it introduces a new persisted store, a new filter, and its own staleness question, none of which this lane scopes. Not filed as a follow-up issue by this plan; noted here so the choice is legible if someone revisits it.

**Gate every "deleted" log on the actual subprocess result.** The defect being removed is not only that the delete was unguarded but that it was **unverified**: the `subprocess.run` at `branch_manager.py:445` passes neither `check=True` nor any `returncode` inspection, so `logger.info(f"Deleted local branch: {branch_name}")` at `:451` fires identically on success and failure. That line is what triage reads during an incident, and it is an unconditional print. It dies with the block. At the surviving site no change is needed — **verified 2026-09-18 against `ba24bfd0c`**, `safe_delete_branch` (`worktree_manager.py:206-221`) branches on `result.returncode == 0`, returns `deleted: True` only on success, and on failure logs `[unmerged-branch-guard] git branch %s '%s' failed: %s` with stderr and returns `deleted: False` plus a populated `error`. **Do not "fix" it.** Stated positively: after this plan, every log line claiming a branch was deleted is downstream of a checked `returncode`, and the four outcomes are four distinguishable lines. The new bridge call site must meet the same bar.

### 6. Guard mutation proofs and failure-path tests
- **Task ID**: `test-guards`
- **Depends On**: `build-cleanup`, `build-model-accessor`
- **Validates**: `tests/unit/test_session_isolation_bypass.py`, `tests/unit/worktree_manager/test_worktree_manager_venv_provisioning.py`, `tests/unit/test_safe_delete_branch.py`, `tests/unit/test_lane_branch_identity.py`
- **Assigned To**: `guard-tester`
- **Agent Type**: `test-engineer`
- **Parallel**: false
- Prove AC 5 by mutation. Each guard has a **named** covering test — an earlier draft pointed all three at `tests/unit/test_session_branch_guard.py`, which covers none of them (it tests the `.githooks/pre-commit` bash hook for #1288). Remove each guard in a scratch working copy and observe its named test go RED:

  | Guard | Removal site | Covering test (run this, observe RED) |
  |---|---|---|
  | #887 main-checkout | `agent/session_executor.py:1468-1491` | `tests/unit/test_session_isolation_bypass.py` (Fix A: worktree enforcement guard) |
  | #1377 branch-mismatch | `agent/worktree_manager.py:292` (`verify_worktree_branch` raise) | `tests/unit/worktree_manager/test_worktree_manager_venv_provisioning.py` — the `verify_worktree_branch` class at `:51`, specifically the mismatch case at `:78` asserting `ei.value.actual_branch` |
  | #1646 unmerged-branch | `agent/worktree_manager.py::merged_via_ancestor` predicate | `tests/unit/test_safe_delete_branch.py` |

  If a removal does **not** turn its named test RED, stop: the guard has no real coverage and the proof must be built before the claim is made. Capture all three RED outputs verbatim for the PR body.
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
- **Before running the table, re-take every "measured N today" figure against the rebased `main`** and update the plan if any moved. The figures are pinned to `ba24bfd0c` and this lane merges third; a figure that has moved to the expected value means the row is now vacuous and must be **re-anchored, not ticked**. Report any figure that moved, with the new count, in the validation output.
- Run every row of the Verification table.
- Confirm all six ACs, with the RED evidence for AC 1 and AC 5 present in the PR body.
- Run `python -m tools.lane_identity sweep` on this machine (AC 6) and report the output.

## Verification

Run each row from the repo root on the build branch. Test rows use `scripts/pytest-clean.sh`, never bare `pytest`, and name specific files — a full `tests/unit/` run takes ~20 minutes and leaks xdist workers.

| Check | Command | Expected |
|-------|---------|----------|
| Regression test green (AC 1) | `scripts/pytest-clean.sh tests/unit/test_lane_branch_identity.py -q` | exit code 0 |
| Guard tests green (AC 2, AC 5) | `scripts/pytest-clean.sh tests/unit/worktree_manager/test_worktree_manager_venv_provisioning.py tests/unit/test_session_isolation_bypass.py tests/unit/test_safe_delete_branch.py tests/unit/test_branch_manager.py -q` | exit code 0 |
| Lifecycle checkpoint patches still hold | `scripts/pytest-clean.sh tests/unit/test_session_lifecycle_consolidation.py -q` | exit code 0 |
| Worktree manager tests green | `scripts/pytest-clean.sh tests/unit/worktree_manager/ -q` | exit code 0 |
| Context propagation green (accessor inversion) | `scripts/pytest-clean.sh tests/e2e/test_context_propagation.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Lane sweep clean (AC 6) | `python -m tools.lane_identity sweep` | exit code 0 |
| Accessor inverted (AC 4) | `grep -A4 "def derived_branch_name" models/agent_session.py` | output contains `self.branch_name or` |
| `skipped_checked_out` exists (defense in depth, not AC 2) | `grep -c "skipped_checked_out" agent/worktree_manager.py` | output > 1 |
| **Anti-criterion, the load-bearing one** — `mark_work_done` no longer deletes branches | `grep -c '"branch", "-d"' agent/branch_manager.py` | match count == 0 — **RED on the branch today, measured `1`** at `:446`. This is the site the incident actually turned on; the issue misattributed it to `safe_delete_branch:2787`. |
| **Anti-criterion** — exactly one deletion site repo-wide | `grep -rn '"branch", "-d"\|"branch", "-D"' --include="*.py" agent models worker bridge tools scripts` | output does not contain `branch_manager.py`. Anchored on the file rather than a count because `safe_delete_branch` builds its argv via a `flag` variable and so does not match this literal; the row exists to stop a *third* deletion site appearing, which is the failure mode #1646 had. |
| **The second `mark_work_done` caller gained a guarded replacement** (Blocker 1, revision 3) | `grep -c 'safe_delete_branch' bridge/telegram_bridge.py` | output > 0 — measured `0` today. This is a **positive** row, not an anti-criterion, and it exists because the literal-grep sweeps cannot reach this file: `bridge/telegram_bridge.py` contains neither `"branch", "-d"` nor `--abbrev-ref`, so removing `mark_work_done`'s deletion without noticing this caller would pass every other row in this table while leaving a lane re-firing its revival prompt every 24h. |
| No ungated deletion log survives (log-is-not-evidence) | `grep -c "Deleted local branch" agent/branch_manager.py` | match count == 0 — measured `1` today at `:451`, firing regardless of `returncode`. Dies with Task 5a's block removal. |
| Seed-if-empty guard present (Risk 2) | `grep -c "if not agent_session.branch_name" agent/session_executor.py` | output > 0 |
| Source-of-truth doc exists (AC 4) | `test -f docs/features/lane-branch-identity.md && grep -c "checkpoint_branch_state" docs/features/lane-branch-identity.md` | output > 0 |
| Docs index updated | `grep -c "lane-branch-identity" docs/features/README.md` | output > 0 |
| **Anti-criterion** — no surviving stale local named `branch_name` in the executor (Risk 6) | `grep -cE "^ *branch_name = " agent/session_executor.py` | match count == 0 |
| **Anti-criterion** — no lane-scoped HEAD read in the queue module (Risk 6) | `grep -c -- "--abbrev-ref" agent/agent_session_queue.py` | match count == 0 — **RED on the branch today, measured `2`**: `:608` in `checkpoint_branch_state` and `:667` in `restore_branch_state`. Task 5 routes **both** through `read_worktree_branch`. Split into its own single-file row because the earlier `grep -rc` over two paths emits `path:count` lines, not a bare count, and so could never satisfy `match count == 0` on any tree. |
| **Anti-criterion** — no lane-scoped HEAD read in the executor (Risk 6) | `grep -c -- "--abbrev-ref" agent/session_executor.py` | match count == 0 — measured `0` today, so this row is a regression guard, not a change to make. |
| Single spelling has a real home (anti-vacuity for the two rows above) | `grep -c "abbrev-ref" tools/lane_identity.py` | output > 0 — without this, both anti-criteria are satisfied by deleting the read entirely rather than relocating it. |
| Trailing refresh exists (Risk 1 — the highest-risk item) | `grep -c "refresh_lane_branch" agent/session_executor.py` | output > 1 — the leading call before cleanup and the added trailing call after it. A build that ships only the leading refresh scores 1 and fails this row. Measured `0` today. |
| `commit_sha` actually persisted (critique CONCERN) | `grep -c 'update_fields=\["branch_name", "commit_sha"' agent/agent_session_queue.py` | output > 0 — measured `0` today; `:624` assigns the SHA and `:625` omits it from `update_fields`. |
| Accessor named to not collide with `lane_branch_name` (critique CONCERN) | `grep -c "def resolve_lane_branch" tools/lane_identity.py` | output > 0 |
| Prefix literal has one home (makes `lane_branch_name`'s docstring true) | `grep -c 'f"session/{slug}"' agent/session_executor.py` | match count == 0 — **RED on the branch today, measured `2`**: `:1407` (the `is_synthetic_slug` arm) and `:1428` (the stageless-eng arm). Both are live code. Task 4 routes **both** through `lane_branch_name(slug)`; a build that fixes only `:1428` scores 1 and fails this row. Corrected in revision 3 — revision 2 stated `1` and named only `:1428`, which would have made this row fail on a correct build. |
| **Anti-criterion** — the sweep reports, never mutates ([EXTERNAL] No-Go) | `grep -cE '"(checkout\|reset\|push\|prune\|remove)"' tools/lane_identity.py` | match count == 0 |
| **Anti-criterion** — checkpoint concurrency shape unchanged ([SEPARATE-SLUG #3306] No-Go) | `grep -c "async def checkpoint_branch_state" agent/agent_session_queue.py` | match count == 0 |
| **Anti-criterion** — `post_merge_cleanup` untouched ([SEPARATE-SLUG #3301] No-Go) | `git diff origin/main -- agent/ \| grep -o "post_merge_cleanup\|+++ b/agent/"` | output does not contain `post_merge_cleanup` — the diff header is the anchor, so a `git` error or an unmodified path empties stdout and the gate **rejects** rather than certifying absence against a diff it never read. `grep -c` cannot be used behind a pipe: it emits `0` on empty input, so the empty-stdout gate never fires and the row passes vacuously. |

Baseline measurements re-taken on `ba24bfd0c` (`origin/main`, 2026-09-18) so the anti-criteria are known to be meaningful rather than vacuously true: `^ *branch_name = ` in the executor matches **2**; `--abbrev-ref` matches **2** in `agent_session_queue.py` (`:608`, `:667`) and **0** in `session_executor.py`; `f"session/{slug}"` in the executor matches **2** (`:1407` and `:1428`); `safe_delete_branch` in `bridge/telegram_bridge.py` matches **0** (and so do `"branch", "-d"` and `--abbrev-ref` in that file — which is precisely why the bridge caller needs a positive row rather than a sweep); `refresh_lane_branch` matches **0**; `async def checkpoint_branch_state` matches **0** (this row is a guard against regression, not a change to make). None of the files this plan touches changed between `bbe5dc7a1` and `ba24bfd0c`, so the Freshness Check above still holds. Each anti-criterion must be demonstrated FAIL against a deliberately-violating input before the PR, with the FAIL output pasted into the PR description.

**Re-take every figure in this paragraph before running the Verification table.** These counts are pinned to `ba24bfd0c`, and Prerequisites sequences this lane to merge third, behind #3091 and #2652. Someone else's merged change can turn a RED-today anti-criterion GREEN without this lane doing anything, and a figure that has moved to the expected value means the row is now **vacuous and must be re-anchored, not ticked**. The exposure is not hypothetical: revision 3 corrected `f"session/{slug}"` from a stated `1` to an actual `2`, a pinned figure that was already wrong before any rebase. Task 8 (`validate-all`) carries this as an explicit bullet; the Prerequisites merge-order paragraph keeps it too, but this paragraph — where the figures live — is the operative location.

**On the `grep -c` exit-code question (critique NIT, partly incorrect — recorded so it is not "fixed" back the wrong way).** The nit reads `grep -c … ` + `match count == 0` as broken because `grep -c` exits 1 when it matches nothing. It is not: for that expectation form the harness reads **stdout**, and the exit code is ignored. This exact reasoning was raised and reversed on #2652 one day before this critique — see `ba24bfd0c`, which states it "corrects the round-2 narrative, which moved these rows the wrong way on the belief that `grep -c`'s exit 1 defeats the check". `grep -c PATH` against a single file is the repo's **preferred** anti-criterion idiom precisely because a missing path yields empty stdout, which the gate rejects, whereas `! grep -q` exits 0 on grep's exit 2 and silently certifies absence against a tree it never read.

The nit is right about **one** row, for a different reason: behind a pipe, `grep -c` emits `0` on empty input, so the empty-stdout gate cannot fire and the row passes vacuously. That is the `git diff | grep -c` row, converted above to the anchored `output does not contain` form. The nit missed the row that was genuinely unsatisfiable — `grep -rc` over two paths emits `path:count` lines rather than a bare count — which is split into two single-file rows above.

### Evidence required in the PR body (not a check table)

| Evidence | Why |
|---|---|
| Task 1 test output, RED, run against `bbe5dc7a1` | AC 1. A regression test never seen RED on the known-bad SHA certifies nothing. |
| Three mutation outputs, RED, one per guard (#887, #1377, #1646) | AC 5. Proves each guard still refuses its original bad input. |
| `python -m tools.lane_identity sweep` output, before and after | AC 6, and it makes the 23% divergence figure reproducible. |
| Anti-criterion FAIL outputs against violating inputs | Proves the inverse rows can actually detect a violation. |

## Critique Results

War room round 2, FULL depth, independent roster (3 critics: Risk & Robustness, Scope & Value, History & Consistency) plus driver structural checks. Verdict: **NEEDS REVISION** (2 blockers, 5 concerns, 1 nit).

Rounds 1 and 2 were re-verified as applied before this round: no section still attributes the deletion to `safe_delete_branch`, the `test_session_branch_guard.py` mis-targeting is corrected everywhere it appeared, the trailing refresh is written as ADD rather than keep, `restore_branch_state` is in Task 5's scope, and Race-2 is gated behind Task 4b. Those findings are closed and are not re-litigated below. Structural checks pass except where noted: all referenced source and test paths exist (`tests/unit/test_lane_branch_identity.py` is intentionally new), the task dependency graph is acyclic with no invalid references, and `git 2.50.1` satisfies the prerequisite.

Blocker 1 was found independently by two critics (Risk & Robustness as a BLOCKER, History & Consistency as a CONCERN) and corroborated by the driver against the live tree; independent convergence is why it is recorded at blocker severity. Blocker 2 is a driver measurement that contradicts a figure the plan states as verified.

| Severity | Critics | Finding | Addressed By | Implementation Note |
|---|---|---|---|---|
| BLOCKER | Risk & Robustness, History & Consistency, Driver (measured) | **Task 5a removes a shared function's only branch-deletion path while a second production caller depends on it, and that caller is nowhere in the plan.** `mark_work_done` has exactly two production callers: `agent/session_executor.py:2781` and `bridge/telegram_bridge.py:2456`. The plan's Integration points, Task 5, Test Impact, Team Orchestration and Verification table all treat the executor as the only one; `bridge/telegram_bridge.py` appears nowhere in the plan. On the executor path the removal is safe, and for a stronger reason than the plan gives: the cleanup block runs only under `not task.error and not _is_non_clean_runner_exit(agent_session) and not chat_state.defer_reaction` (`session_executor.py:2764-2772`), i.e. after `complete_transcript` finalized the row at `:2589`, and `check_revival` only ever enumerates candidate branches from sessions with `status` in `("pending", "running")` (`session_revival.py:74-81`), so a terminal session's branch is never a revival candidate at all. On the bridge path the removal is **not** safe. `bridge/telegram_bridge.py:2450-2456` calls `mark_work_done(Path(working_dir_str), revival_info["branch"])` under the comment "Mark the stale work as dormant so it doesn't re-trigger", with no `safe_delete_branch` anywhere in that module. The branch handed in came from a pending-or-running session by construction, so the terminal-sibling filter at `session_revival.py:86-104` does **not** cover it; the only thing that stops it re-triggering is the `git branch -d` at `branch_manager.py:443-451`, because the branch-existence check at `:108-124` is what drops it from the candidate list. Archive, commit and return-to-main do not remove a branch from `git branch --list`. Task 5's stated mitigation ("Verify that `safe_delete_branch` at `:2787` covers it") is scoped to the executor and has no counterpart on this path. Net effect: a merged-but-stuck lane re-fires its revival prompt every `REVIVAL_COOLDOWN_SECONDS` (86400, `session_revival.py:23`) indefinitely, and that cooldown is per-chat, so each false revival also suppresses genuine ones for 24h. | **addressed (revision 3)** — shape (a) chosen and written into new Task 5a, Integration points, Team Orchestration, Test Impact, and a positive Verification row. | Two shapes, both acceptable; pick one and write it into Task 5 plus Integration points plus Test Impact. (a) Add `safe_delete_branch(working_dir_str, revival_info["branch"], predicate=merged_via_ancestor, force=False)` immediately after the `mark_work_done` call in `bridge/telegram_bridge.py:2456`; `agent/session_revival.py:10-15` already imports `safe_delete_branch` and `merged_via_tree`, so the dependency exists. Note that `working_dir_str` there resolves from `project.get("working_directory")` and is the **shared project checkout**, not a `.worktrees/{slug}/` path, so `refresh_lane_branch` and `verify_worktree_branch` do not apply and must not be assumed. (b) Move dormancy out of git entirely: record the dismissed branch in a dormant set that `check_revival` filters on alongside the terminal-branches filter at `session_revival.py:86-104`, which removes the bridge path's dependency on deletion altogether. Either way, add `bridge/telegram_bridge.py` to Integration points, add a Test Impact row for the revival-dormancy path, and add a Verification row — a grep on the literal will not catch this because `bridge/telegram_bridge.py` contains neither `"branch", "-d"` nor `--abbrev-ref`. |
| BLOCKER | Driver (measured against `origin/main` today) | **A Verification row's stated baseline is wrong, and the task meant to satisfy it only fixes half the sites, so the row cannot pass on correct code.** The row "Prefix literal has one home" expects `grep -c 'f"session/{slug}"' agent/session_executor.py` to reach `match count == 0` and states "**RED on the branch today, measured `1`** at `:1428`"; the baseline paragraph beneath the table repeats "matches **1** (`:1428`)" and presents both as re-verified against `ba24bfd0c`. The actual count is **2**: `agent/session_executor.py:1407` (the `is_synthetic_slug` arm) and `:1428` (the stageless-eng arm). Both are live code, not comments. Task 4's bullet names only `:1428` ("`:1428` — replace the inline `f"session/{slug}"` with `lane_branch_name(slug)`"), so a builder following the task literally leaves `:1407` in place and the anti-criterion row fails on a correct build. This is the exact failure the paragraph beneath the table claims to prevent ("so the anti-criteria are known to be meaningful rather than vacuously true"), and it also makes `lane_branch_name`'s "applied here and nowhere else" docstring still false after Task 4 — the stated purpose of that task bullet. | **addressed (revision 3)** — both statements corrected to `2` (re-measured today), Task 4's bullet now routes both sites as a class, and the Key Elements restatement of the docstring claim is scoped to the executor with #3417 named. | Re-measure and correct both statements to `2`, then extend Task 4's bullet to route **both** `:1407` and `:1428` through `lane_branch_name(slug)`. `:1407` sits in the `if is_synthetic_slug:` arm and `:1428` in the stageless-eng-with-worktree arm; they are mutually exclusive branches of the same resolution block, both assigning `resolved_branch` before `branch_name = resolved_branch` at `:1430`. Treat this as a class, not two sites, per the repo's replicated-defect rule: the closing evidence is the clean `grep -c` on the whole file, which is what the row already asserts. Separately, `agent/session_revival.py:29` spells the prefix a third time in `_session_branch_name`; the row is file-scoped to the executor so it is not in the row's way, but the docstring claim should not be restated as repo-wide until that is settled. |
| CONCERN | Driver (supervisor-surfaced) | **The re-measure-after-rebase instruction exists but does not live where it will be read.** Every "measured N today" figure in the Verification table is pinned to `ba24bfd0c`, and Prerequisites states the lane lands third, behind #3091 and #2652. The instruction to re-measure after rebase appears once, in the Prerequisites merge-order paragraph. It is absent from the Verification table itself, from the baseline-measurements paragraph beneath it (which is where the pinned figures actually live), and from Task 8 (`validate-all`, "Run every row of the Verification table"). A RED-today anti-criterion can go GREEN under someone else's merged change; the validator running Task 8 has no instruction telling them to re-take the baselines. Blocker 2 shows the exposure is not hypothetical — a pinned figure is already wrong before any rebase. | **addressed (revision 3)** — added to the baseline-measurements paragraph beneath the table and as the first bullet of Task 8; Prerequisites keeps its merge-order copy. | Add the re-measure instruction to the baseline-measurements paragraph directly beneath the Verification table (where the pinned figures are stated) and as an explicit bullet in Task 8: "before running the table, re-take every `measured N today` figure against the rebased `main` and update the plan if any moved; a figure that has moved to the expected value means the row is now vacuous and must be re-anchored, not ticked." Keep the Prerequisites paragraph as well — it is the merge-order home — but it is not the instruction's operative location. |
| CONCERN | Scope & Value | **The `skipped_checked_out` pre-check is not required by any acceptance criterion, and the plan says so itself.** spike-1 already proved git refuses to delete a branch a live worktree has checked out, for both `-d` and `-D`, and spike-1's own Impact paragraph records that the incident never exercised this path — the damage was the inverse. The plan nonetheless makes the pre-check a mandatory Task 5 bullet, adds a `skipped_checked_out: bool` key to `safe_delete_branch`'s return dict, requires that key be present-and-`False` on every existing path, and adds a scan-failure fail-safe test — while its own Verification row is labelled "defense in depth, not AC 2". | **deferred — outside the revision-3 mandate.** The cap override ruling narrowed this round to the two blockers plus the re-measurement placement; scope-reduction calls on `skipped_checked_out` are a separate decision and are recorded here unresolved rather than silently dropped. | The dict-shape change is additive so downstream callers at `agent/session_executor.py:2785-2810` do not break either way; the cost is the mandatory scan-failure fail-safe test in the Failure Path Test Strategy for a scenario spike-1 shows cannot arise through the normal delete path. Either demote the whole item to builder's discretion (drop the Task 5 bullet and the Failure Path row, keep the Verification row as optional), or state in the Task 5 bullet why a named decision is worth the cost over git's own refusal — the triage-legibility argument is in Key Elements but is not in the task the builder reads. |
| CONCERN | Scope & Value | **The `commit_sha` persistence fix is an independent latent bug riding along in this PR.** `session.commit_sha` is assigned at `agent/agent_session_queue.py:624` and omitted from `update_fields` at `:625`, so the SHA half of the checkpoint never persists. It is real, but nothing in this plan's invariant ("record == live `HEAD`") or in any of the seven ACs reads `commit_sha`. It is justified on "a plan that promotes this function to sole source of truth cannot leave half of it unwritten" — a values argument, not a functional dependency — and it brings its own `update_fields` change, a `restore_branch_state` early-return re-verification, an ORM round-trip test and a Verification row into a PR whose actual defect is one unguarded `git branch -d`. | **deferred — outside the revision-3 mandate**, same ruling as the row above. The `commit_sha` fix stays in the plan; whether it should ride along is an open scope call, not a defect. | The fix is a one-line `update_fields` change at `agent/agent_session_queue.py:625` (add `"commit_sha"`), which meets this repo's hotfix-on-main threshold and can land independently of this lane. If the decider keeps it bundled, move it out of Task 5's dependency chain so a `commit_sha` regression cannot block review of the branch-deletion fix, and say in the task that it is a deliberate ride-along rather than part of the invariant. |
| CONCERN | History & Consistency | **The `executor-builder` role does not own the file holding the plan's centrepiece fix.** Team Orchestration lists that role's files as `agent/session_executor.py`, `agent/agent_session_queue.py::checkpoint_branch_state` and `agent/worktree_manager.py::safe_delete_branch`. `agent/branch_manager.py` is absent, yet Task 5 — assigned to `executor-builder` — instructs it to delete the whole `if switched and branch_name.startswith("session/")` block at `agent/branch_manager.py:443-451`, which revision 2 established as the change that actually fixes the incident. The role definition was written before revision 2 made that file load-bearing and was not reconciled. | **addressed (revision 3)** — `executor-builder` now owns `agent/branch_manager.py::mark_work_done` and `bridge/telegram_bridge.py:2450-2456` explicitly. | Add `agent/branch_manager.py::mark_work_done` to the `executor-builder` role's owned-files list in Team Orchestration. If blocker 1 is resolved by shape (a), add `bridge/telegram_bridge.py`'s revival block to the same role in the same edit, since it is the same function's contract. |
| CONCERN | History & Consistency | **A test file that Verification runs and Test Impact upgraded to UPDATE is owned by no task.** Revision 2 upgraded `tests/unit/test_branch_manager.py` from REVIEW to UPDATE and requires "a case proving no deletion occurs, which is the unit-level half of AC 2". The Verification table runs it in the "Guard tests green (AC 2, AC 5)" row. But no task's `Validates:` names it: Task 5 validates `test_safe_delete_branch.py` and `test_worktree_manager_cleanup.py`; Task 6 validates `test_session_isolation_bypass.py`, `test_worktree_manager_venv_provisioning.py`, `test_safe_delete_branch.py` and `test_lane_branch_identity.py`. The case Test Impact demands has no task instructed to write it, so AC 2's unit-level half is unowned. | **addressed (revision 3)** — `tests/unit/test_branch_manager.py` is now in Task 5a's `Validates` list, alongside `test_safe_delete_branch.py` and `test_session_revival.py`. | Add `tests/unit/test_branch_manager.py` to Task 5's `Validates:` list — Task 5 is the task that removes the deletion block, so it is the natural owner of the case proving deletion no longer occurs. Existing cases in that file asserting `mark_work_done` deletes `session/*`, or asserting the `Deleted local branch` log, invert rather than disappear. |
| NIT | Driver (structural) | The plan refers to "Task 5a" six times (in Why Previous Fixes Failed, Test Impact, two Task 5 bullets, a Verification row, and the round-1 Critique Results table), but the Step by Step Tasks section has no `### 5a.` heading — the headings run 1, 2, 3, 4, 4b, 5, 6, 7, 8, and the removal is a bullet inside Task 5. A builder told to execute Task 5a finds no such task. Note that 4b, by contrast, is a real heading, so the `Na` convention is established and the omission reads as an oversight rather than shorthand. | **addressed (revision 3)** — Task 5a is now a real `### 5a.` heading with its own Task ID (`build-remove-unguarded-delete`), and Task 5 depends on it. | n/a |

---

## Open Questions

1. **Should the lane's branch record live on `AgentSession.branch_name` or on `PipelineLedger` next to the slug?** This plan chooses `AgentSession.branch_name` because it exists, is already written, and needs no Popoto migration — and the fix is urgent, since 23% of live lanes are one turn away from this failure. But the *lane* owns the slug on `PipelineLedger` (PR #2792), and branch identity is arguably lane-scoped too. The cost of choosing wrong is Race 3: two sessions sharing one worktree can hold disagreeing records. Today's behavior is preserved either way, so this is "take the free fix now, move it later" vs. "do it once, properly, with a migration". **I recommend the former** and would take a ruling rather than assume.

2. **Is the Race-2 recovery (a nonexistent expected branch clears the record and falls back to the seed, with a WARNING, instead of raising) acceptable?** I argue it is not a weakening of #1377: a branch that does not exist carries no risk of running on the *wrong* branch, which is the only thing that guard protects against. But #1377 exists because of a production incident, and I would rather hear "yes, that reading is right" than discover later that the raise was load-bearing for a reason not written down.

   **This question now gates a task.** An earlier draft scheduled the Race-2 recovery unconditionally inside Task 4 while simultaneously asking for a ruling here and forbidding guard-loosening in Rabbit Holes — the plan implementing behaviour it said needed a decision. It is split out as **Task 4b**, which does not start without a recorded answer. **No answer is a valid outcome:** the rest of the plan ships without it and the Race-2 window becomes a follow-up issue. Nothing else in the plan depends on 4b.

3. **Should #3413 (the `branch=main` checkpoint anomaly, filed during this planning pass) block this work, or follow it?** This plan promotes `AgentSession.branch_name` to source of truth while #3413 describes a way that field can be written from the wrong directory's `HEAD`. Sequencing #3413 first is the conservative call; shipping this first fixes 23% of lanes sooner. I lean toward shipping this first and treating #3413 as a fast-follow, because the failure #3413 describes predates this change and is not made worse by it — but that is a judgement about acceptable exposure, not a technical fact.
