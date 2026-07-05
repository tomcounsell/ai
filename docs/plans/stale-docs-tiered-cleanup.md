---
status: Ready
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1900
last_comment_id:
revision_applied: true
---

# Stale docs: four-tier cleanup + fix the do-merge plan-migration leak

## Problem

`docs/` weighs 26MB, and most of it is artifacts nobody will read again. The root cause of the largest pile — 212 plan files (13MB) sitting in `docs/plans/` root — is a leak in the merge pipeline: the step that is supposed to migrate a plan out of the root after its PR merges runs on some paths and silently skips others.

**Current behavior:**

- `docs/plans/` root holds 212 plan files (13MB); `docs/plans/completed/` holds 138 (5.4MB). All six of the oldest sampled root plans reference CLOSED issues. Migration to `completed/` demonstrably works on the primary path but skips on others, so ~130 pre-June plans remain in the root.
- Three filenames exist in BOTH `docs/research/` and `docs/guides/` with diverged content (md5 mismatch confirmed for all three): `agent-sdk-replacement-requirements.md`, `claude-code-feature-swot.md`, `ruflo-deep-dive.md`. The docs cascade already pays the duplication tax (commit 5d8ed46e updated both SWOT copies in one pass).
- `docs/presentations/` carries 5.1MB of April–May strategy decks plus a newer `skill-fleet-renovation/` folder. Point-in-time audit outputs sit among evergreen guides; `CHANGELOG.md` (abandoned after one entry) and `GEMINI.md` (an April fork of CLAUDE.md that drifts) sit at repo root.
- `docs/media/anthropic-skills-guide.pdf` (552K, zero inbound links) is downloaded vendor material that, per the repo's own KB convention, belongs in the work-vault, indexed.

**Desired outcome:** Every root file in `docs/plans/` corresponds to open, in-flight work. Each doc topic has exactly one canonical file. One-off decks and vendor material live in the knowledge base, indexed. The merge pipeline migrates plans on every merge path, so the root never regrows a stale backlog.

**Two distinct value props (do not conflate them):**
- **Correctness (Tier 0 + Tier 1):** every root file in `docs/plans/` maps to open work, and the pipeline keeps it that way. This moves files *within* `docs/` (root → `completed/`) and reclaims **zero bytes** — the 13MB plans pile does not shrink, it gets organized. The value is a trustworthy root and a leak that stays fixed.
- **Size (Tier 3 + Tier 4):** deleting one-off decks/reports and relocating vendor/business material to the vault is what actually shrinks `docs/` — roughly **5MB**. The "26MB / 13MB" figures motivate the cleanup but are **not** the reclaim target; only Tier 3/4 reduce working-tree size.

## Freshness Check

**Baseline commit:** 5a5efb96 (`git rev-parse HEAD` at plan time)
**Issue filed at:** 2026-07-04T15:55:24Z
**Disposition:** Minor drift

**Claims re-verified against current main:**
- `docs/plans/` root: issue said 214 files; now **212** (a few migrated since filing). `completed/` still 138. `docs/plans` 13MB, `completed/` 5.4MB. — holds with minor drift.
- Three research/guides duplicate pairs: all three still exist in both dirs with mismatched md5s. — holds exactly.
- `docs/presentations/`: issue said ~3.7MB; now **5.1MB** — a new `skill-fleet-renovation/` folder was added by the merge of PR #1894 (commit 5a5efb96, the only commit on main since the issue was filed). This is additive drift, not a root-cause change. The new folder is itself a one-off deck and folds into Tier 3.
- `CHANGELOG.md`, `GEMINI.md`, `docs/media/anthropic-skills-guide.pdf`, and all three `openhuman-vs-hermes.{md,html,pdf}` forms: all present as described.
- `~/work-vault/AI Valor Engels System/README.md`: confirmed **missing**. Vault root `~/work-vault/_index.md` exists (the linking point).
- The delete-vs-migrate contradiction (`PR_AND_CLEANUP.md` vs `docs/sdlc/do-merge.md`): confirmed present, and **the picture is worse than the issue stated** — see Prior Art / Why Previous Fixes Failed. A third behavior (the script deletes rather than moves) is now in play.

**Cited sibling issues/PRs re-checked:**
- #1304 (plan-doc auto-commits to main) — CLOSED. Established plans live on `main` throughout the lifecycle.
- #1394 (PM pipeline governance: plan-doc lifecycle) — CLOSED. Its plan (`docs/plans/sdlc-1394.md`, still in root) targeted the exact `rm -f` vs migrate contradiction. That fix has since regressed via the command→skill migration.
- #1543 (docs-auditor floods tracker) — CLOSED. Cautionary tale: doc reconcilers need dedup and dry-run discipline.
- PR #1894 (skill-fleet renovation) — MERGED (5a5efb96). Added `docs/presentations/skill-fleet-renovation/`.

**Commits on main since issue filed (touching referenced files):** only `5a5efb96` (PR #1894), which added a presentations subfolder. No change to the migration mechanism or the plans root beyond what is described above.

**Active plans in `docs/plans/` overlapping this area:** `sdlc-1394.md` and `sdlc-1304.md` are prior (closed, shipped) plans on the same merge-migration mechanism, now stale in the root — they are themselves Tier 1 cleanup targets. No *active* competing plan.

## Prior Art

- **#1304 / `docs/plans/sdlc-1304.md`**: "Plan-doc auto-commits to main." Established that plan docs live on `main` throughout the lifecycle (why migration must run on `main` after-merge). Diagnosed that `do-merge.md:542` ran `scripts/migrate_completed_plan.py` AND `rm -f`'d the working-tree plan regardless of migration outcome — so a migration failure silently destroyed the plan. Shipped.
- **#1394 / `docs/plans/sdlc-1394.md`**: "PM pipeline governance: watchdog resilience and plan-doc lifecycle." Directly targeted the `rm -f` vs migrate contradiction: remove the unconditional `rm -f`, gate deletion on `migrate_completed_plan.py` exit code, leave the plan in place as a visible warning on failure. Shipped — but against a version of `/do-merge` that lived at `.claude/commands/do-merge.md` and *called the script deterministically*.
- **#1543**: docs-auditor duplicate-issue flood. Relevance: the Tier 1 reconciliation must be dry-run-first, dedup evidence, and never mass-file issues.
- **#1357 / `agent/worktree_manager.py::cleanup_after_merge()`** (CLOSED): added a busy-session guard — when a live AgentSession still references the worktree, `remove_worktree()` returns `("blocked", session_id)`, `cleanup_after_merge()` surfaces `blocked_by_session`, and `scripts/post_merge_cleanup.py` **exits 2**. This is one more reason enforcement candidate B (`cleanup_after_merge()` / `post_merge_cleanup.py`) is rejected: even setting aside its slug-keying and lack of issue-state knowledge, folding migration in *after* this guard would let a blocked worktree short-circuit the function and skip migration — recreating the exact leak this plan fixes. The chosen sites (the deterministic `/do-merge --issue` call and the path-independent `merged-branch-cleanup` reflection) sidestep the guard entirely — neither runs inside `cleanup_after_merge()`.
- **`scripts/migrate_completed_plan.py`** (292 lines): exists, works, has `--dry-run`. **Critically, its `delete_plan()` calls `plan_path.unlink()` — it DELETES the plan, it does not move it to `completed/`.** Its own `--help` says "On success: Deletes the plan file."
- **`reflections/housekeeping/merged_branch_cleanup.py::run()`** (registered callable `run_branch_plan_cleanup`, reflection `merged-branch-cleanup` in `config/reflections.yaml`, currently `enabled: false`). **This is the decisive prior art: a standing daily reflection that ALREADY sweeps `docs/plans/` root, extracts every issue ref from each plan (both `#N` and `github.com/.../issues/N` forms), batch-queries `gh` for issue state (in batches of 10), and classifies each plan — including an explicit `closed_issue` verdict** (lines 166–175). It is **report-only**: on the `closed_issue` verdict it appends a finding string ("Plan with closed issue(s): …") and does nothing else. It is disabled with the comment "calls gh issue list per plan file, slow on auth issues." A net-new `reconcile_leaked_plans()` reflection would duplicate this file's sweep/extract/classify verbatim — a functionally-overlapping sibling, which the NO LEGACY CODE TOLERANCE principle forbids. **The chosen design extends this existing reflection** (add the evidence-gated `git mv` on its `closed_issue` branch and re-enable it) rather than shipping a parallel one. See Data Flow and Technical Approach.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1304 | Kept plans on `main`; documented the `rm -f`-after-migrate hazard | Documented but did not remove the destructive path; left the delete-vs-move ambiguity unresolved |
| #1394 | Gated plan deletion on `migrate_completed_plan.py` exit code inside `.claude/commands/do-merge.md` | The fix lived in the **command file**, which was later replaced by the skill at `docs/sdlc/do-merge.md`. The command→skill migration dropped the deterministic script call and replaced it with a manual prose instruction ("move the plan from `docs/plans/{slug}.md` to `docs/plans/completed/{slug}.md`"). Migration now depends on an agent remembering to `git mv` by hand — which fails on manual `gh pr merge`, forked `/do-sdlc` runs, and merges on a machine that doesn't hold the plan. |
| `merged-branch-cleanup` reflection | Built the sweep-and-classify machinery (reads every root plan, extracts issue refs, batch-queries `gh`, produces a `closed_issue` verdict) as a standing daily reflection | Stopped one step short of enforcement: the `closed_issue` branch only *reports* a finding string — it never `git mv`s the plan. Then it was `enabled: false`'d for being slow on `gh` auth issues, so even the report stopped running. The correct fix is to finish this file (add the guarded `git mv` on its existing `closed_issue` branch) and re-enable it, not to build a second sweeper beside it. |

**Root cause pattern (leading hypothesis, to be confirmed in build):** The migration invariant was never anchored at a deterministic, path-independent site. It moved from a script call (in a command that has since been deleted) to hand-written prose in a skill addendum. There are now **three conflicting specifications** of the step:
1. `scripts/migrate_completed_plan.py` — **deletes** the plan (`unlink()`), relying on git history as the archive.
2. `docs/sdlc/do-merge.md:57` — **moves** the plan to `docs/plans/completed/`.
3. `.claude/skills-global/do-build/PR_AND_CLEANUP.md:146` says the plan is **deleted** by do-merge; line 167 of the same file says do-merge **migrates** it.

The existence of 138 files in `completed/` proves *some* path moves rather than deletes (likely agents hand-following the do-merge.md prose), while the script deletes — the two mechanisms disagree, which is why the behavior is path-dependent. Note also that `do-merge.md` references an after-merge worker function `_handle_merge_completion()` that **does not exist anywhere in the codebase** — so the issue's Solution-Sketch option (b) ("`_handle_merge_completion()` already runs after-merge memory extraction and could own migration") rests on a false premise and must not be taken at face value.

## Data Flow

**The leak (Tier 0), traced end-to-end:**

1. **Entry point:** a PR merges. Paths: (a) `/do-merge` skill runs `gh pr merge`; (b) a human/agent runs `gh pr merge` manually (gated only by the merge-guard hook + `data/merge_authorized_{PR}`); (c) a forked `/do-sdlc` run merges; (d) a PR-review-initiated merge.
2. **Merge-guard hook** (`.claude/hooks/validators/validate_merge_guard.py`): intercepts every `gh pr merge` and blocks unless `data/merge_authorized_{PR}` exists. This is the one choke point **every** merge path passes through — the natural anchor for a path-independent invariant, but it fires *before* the merge, not after.
3. **Migration step:** on path (a), `docs/sdlc/do-merge.md`'s "Plan Migration" prose asks the agent to `git mv` the plan on `main`. On paths (b)–(d), nothing runs the migration at all. `scripts/migrate_completed_plan.py` (which deletes) is no longer wired into any skill or hook.
4. **Output:** plan is inconsistently deleted, moved to `completed/`, or left in root. The root accumulates stale plans.

**Enforcement-site candidates and the RESOLVED decision (Tier 0 design):**
- **(A) merge-guard hook** — covers every *Claude-Code-mediated* `gh pr merge`, but runs pre-merge; it can *verify/authorize* but an after-merge action needs a follow-through step. Also fires no hook at all for a human typing `gh pr merge` in a raw terminal. **Rejected as a standalone site.**
- **(B) `post_merge_cleanup.py` / `cleanup_after_merge()`** — already runs after-merge, but its `main()` takes **only a slug** (`cleanup_after_merge(REPO_ROOT, slug)`); it has **no `gh` call and no tracking-issue knowledge**, so it cannot evidence-gate on issue state without bolting on a second, redundant network path. Worse, it is **slug-keyed**: it locates work by `docs/plans/<slug>.md`, but a plan's branch slug routinely differs from its plan-doc filename (this very plan ships on branches `session/stale-docs-tier0` and `session/stale-docs-tiers1234`, while the doc is `stale-docs-tiered-cleanup.md`), so a slug-keyed migration would look for the wrong path and silently no-op. **Rejected** — folding evidence-gated migration here would duplicate the migration logic against a mismatched key.
- **(C) EXTEND the existing `merged-branch-cleanup` reflection** — `reflections/housekeeping/merged_branch_cleanup.py::run()` already sweeps `docs/plans/` root, extracts each plan's issue refs, batch-queries `gh`, and classifies a `closed_issue` verdict. It stops at reporting. **Finish it:** on the `closed_issue` branch, call the shared migration primitive to `git mv` the plan into `completed/` (evidence-gated on the plan's own `tracking:` frontmatter issue being CLOSED — path-independent, never a branch slug), then re-enable the reflection. This is the ONLY *path-independent* site — it covers every merge path on its daily cycle, including the raw-terminal `gh pr merge` (path b) that fires no hook and calls no cleanup script. **CHOSEN as the path-independent backstop.** Extending the existing sweeper (not shipping a parallel one) honors NO LEGACY CODE TOLERANCE.
- **(D) deterministic merge-site call in `/do-merge`** — the `/do-merge` skill (`docs/sdlc/do-merge.md`) knows the exact issue it just closed. **Restore a deterministic call:** after the merge, invoke the shared migration primitive scoped to that issue (`migrate_completed_plan.py --issue <N>`), which migrates the one plan whose `tracking:` frontmatter matches the just-closed issue. This is issue-keyed, not slug-keyed, so the slug≠filename mismatch does not bite. It gives the primary path **synchronous, same-transaction** migration instead of waiting up to a day for the reflection cycle.

**Decision (two coordinated sites, one shared primitive):** the enforcement is NOT a single reconciler. Issue #1900 explicitly warns that a reconciler-alone design "institutionalizes the leak" (the root would routinely hold day-old closed-issue plans between reflection cycles). So:
- **(D) is the deterministic primary path:** `/do-merge` calls the shared migration primitive on the merged issue, synchronously.
- **(C) is the path-independent backstop:** the extended `merged-branch-cleanup` reflection catches every path `/do-merge` does not cover (raw-terminal `gh pr merge`, forked `/do-sdlc`, cross-machine merges) within one daily cycle.
- Both call **one** guarded migration primitive in `scripts/migrate_completed_plan.py` — no duplicated `git mv` logic, no parallel reflection. See Technical Approach for the concrete wiring.

## Appetite

**Size:** Large

**Team:** Solo dev (lead), builder(s), validator, documentarian

**Interactions:**
- PM check-ins: 2-3 (Tier 0 enforcement-site choice; GEMINI.md/CHANGELOG.md keep-or-delete decisions; completed/ prune decision)
- Review rounds: 2+ (code review for the Tier 0 mechanism + its test; evidence-report review for Tier 1)

Large because it spans a merge-pipeline invariant (with a regression test), a scripted evidence-gated migration over ~130 files, a docs reorganization touching multiple directories, and vault writes outside the repo diff that require executed-command proof.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `gh` authenticated | `gh auth status` | Query issue/PR state for Tier 1 evidence gating |
| work-vault present | `test -d "$HOME/work-vault/AI Valor Engels System"` | Tier 4 moves + README creation |
| `valor-ingest` available | `test -x "$HOME/src/ai/.venv/bin/valor-ingest"` | Tier 4 sidecar generation |

## Solution

### Key Elements

- **Tier 0 — deterministic merge-site call + path-independent backstop, one shared primitive.** One guarded migration primitive in `scripts/migrate_completed_plan.py` (`git mv` a plan into `completed/`, evidence-gated on the plan's own `tracking:` frontmatter issue being CLOSED). It is called from two coordinated sites: (1) **`/do-merge` calls it synchronously** on the issue it just closed (`--issue <N>`), so the primary path migrates in-transaction; (2) the **existing `merged-branch-cleanup` reflection is extended** to call it on its `closed_issue` verdict branch and re-enabled, covering every path `/do-merge` misses (raw-terminal `gh pr merge`, forked `/do-sdlc`, cross-machine) within one daily cycle. Extending the existing sweeper (which already reads, extracts refs, and classifies `closed_issue`) rather than shipping a parallel reconciler honors NO LEGACY CODE TOLERANCE. The reflection is capped (N=10/run) + apply-gated + alertable for unattended safety. Ships with a regression test that fails if a merged issue's plan remains in root, plus a static assertion that `merged-branch-cleanup` is registered, `enabled: true`, and its `closed_issue` branch calls the migration primitive. Resolve the three-way delete/move contradiction to ONE documented behavior and cascade it.
- **Tier 1 — evidence-gated bulk backfill (uncapped, supervised).** The ~130-plan historical backlog cannot clear in one pass under the reflection's daily cap of 10 — so Tier 1 is a **separate supervised sweep** (`migrate_completed_plan.py --sweep --apply`, no per-run cap) reusing the **same** guarded migration primitive as Tier 0, run dry-run-first with a mandatory human review of the report before the destructive pass. For each root plan it extracts the tracking issue, queries `gh` for state, and migrates only when the issue is CLOSED / PR MERGED. Unresolvable plans go to a human-review list in the PR, never moved blind. Emits a machine-readable report as the proof artifact. The daily reflection's cap is an unattended safety valve; the one-time backfill is uncapped precisely because a human reviews the dry-run report first.
- **Tier 2 — de-duplication.** Merge each diverged research/guides pair into one canonical file, delete the other, fix the (minimal) inbound links, and record the guides-vs-research placement rule in `docs/README.md`.
- **Tier 3 — remove one-off decks and point-in-time reports.** Delete outright what has no lasting value; route business-valuable decks to Tier 4. Surface GEMINI.md and CHANGELOG.md as explicit keep-or-delete decisions.
- **Tier 4 — vendor/business docs to the knowledge base, indexed.** Move the PDF and the strategy decks to the vault, `valor-ingest` each binary, create the missing `~/work-vault/AI Valor Engels System/README.md` file index, and link it from vault root `_index.md`. Prove with executed commands (listing + README content) pasted into the PR.

### Flow

PR merges via `/do-merge` → `/do-merge` calls the shared migration primitive on the just-closed issue (`--issue <N>`), migrating that plan to `completed/` synchronously (Tier 0 primary path) → for any merge path that bypasses `/do-merge` (raw-terminal `gh pr merge`, forked `/do-sdlc`, cross-machine), the extended `merged-branch-cleanup` reflection catches the plan on its next daily cycle, gated on the plan's own tracking frontmatter (Tier 0 backstop) → Tier 1 (a separate uncapped, dry-run-first sweep using the same primitive) clears the historical backlog with per-file evidence → Tier 2/3 collapse duplicates and delete one-offs → Tier 4 relocates business/vendor docs to the indexed vault → docs cascade confirms zero dangling inbound links.

### Technical Approach

- **Decide the canonical behavior first** (delete vs move-to-`completed/`). Recommendation to confirm with PM: **move to `completed/`** (git history alone is a poor archive for grep/context, and 138 files already live there), and change `scripts/migrate_completed_plan.py`'s `delete_plan()` to a `git mv` into `completed/`. Then make ONE mechanism authoritative and delete the contradictory prose/behaviors (no-legacy-code tolerance).

- **One shared migration primitive, two calling sites.** The authoritative migration logic lives in a single guarded function in `scripts/migrate_completed_plan.py` — call it `migrate_plan_to_completed(plan_path, *, apply: bool) -> str`. It returns an action verdict (`migrated` | `skipped-open` | `already-migrated` | `dirty-tree-skip` | `rebase-conflict-skip`) and performs the guarded `git mv` (existence-guard, rebase-retry push, rebase-conflict abort+skip, clean-tree/HEAD precondition — see Race Conditions). A thin `--issue <N>` CLI resolves the one root plan whose `tracking:` frontmatter matches issue N and calls the primitive; a `--sweep` CLI iterates all root plans (used by the reflection with a cap and by the Tier 1 backfill uncapped). **No `git mv` logic is duplicated anywhere** — the reflection, the merge-site call, and the Tier 1 backfill all funnel through this one function.

- **Site D — deterministic merge-site call in `/do-merge` (restores primary-path coverage, C2).** Issue #1900 warns that a reconciler-alone design "institutionalizes the leak" (the root routinely holds day-old closed-issue plans between reflection cycles). So the primary `/do-merge` path migrates **synchronously**: after the merge, `docs/sdlc/do-merge.md`'s Plan Migration section instructs the deterministic call `python scripts/migrate_completed_plan.py --issue <closed-issue-number> --apply` on `main`. It is **issue-keyed, not slug-keyed**, so the slug≠plan-filename mismatch (candidate B's fatal flaw) does not bite: the primitive resolves the plan by reading `tracking:` frontmatter, not by a filename guess. This is the load-bearing deterministic anchor.

- **Site C — extend the existing `merged-branch-cleanup` reflection as the path-independent backstop (resolves the duplicate-reflection BLOCKER).** `reflections/housekeeping/merged_branch_cleanup.py::run()` already sweeps `docs/plans/` root, extracts each plan's issue refs, batch-queries `gh`, and produces a `closed_issue` verdict — it just reports instead of acting. **Extend that existing `closed_issue` branch** to call `migrate_plan_to_completed(plan_file, apply=<config>)` (tightening the gate to the plan's own `tracking:` frontmatter issue via `extract_tracking_issue()`, so a plan that merely references a closed sibling issue in prose is not swept), then re-enable the reflection (`enabled: true`). Do **not** create a net-new `plan-migration-reconciler` reflection — that would duplicate this file's sweep/extract/classify verbatim (NO LEGACY CODE TOLERANCE). Update the disabled-reason comment; the slowness concern is bounded by the existing batch-of-10 `gh` querying plus the clean-tree precondition's report-only fallback. This reflection covers path (b) — the bare-terminal `gh pr merge` that fires no hook — and every path `/do-merge` misses, within one daily cycle.
  - **Unattended-safety posture (the reflection runs daily with no human in the loop).** (i) **Apply-gated (dry-run default):** the migration branch runs in report-only mode unless explicitly armed. Arming is a **two-part decision** the plan makes deliberately in a discrete DAG task, only after PR 1's regression test proves the evidence gate: flip the reflection entry to `enabled: true` AND set its apply mode on. Until armed it classifies-and-reports exactly as today. (ii) **Per-run migration cap:** the sweep migrates at most **N=10** plans per invocation (configurable) and logs the remainder as deferred, so a mis-evaluated gate can never move the whole root in one unattended run. The cap is a parameter of the sweep, distinct from the uncapped Tier 1 backfill. (iii) **Alerting:** every migration and skip-reason is logged; a run that migrates ≥1 plan or hits any `git mv` / push failure emits a summary line the reflection scheduler surfaces (the same channel other reflections use), so silent mass-moves are impossible.
  - **Why NOT fold into `post_merge_cleanup.py` (rejected candidate B).** Its `main()` takes only a slug and has no issue-state knowledge, and the branch slug ≠ plan-doc filename, so a slug-keyed call would no-op on the wrong path (see Data Flow). Site D already gives the primary path deterministic, issue-keyed coverage; adding a slug-keyed call in `post_merge_cleanup.py` would be redundant and wrong-keyed.
  - Do **not** rely on `_handle_merge_completion()` — it does not exist (confirmed: zero Python definitions; one stale reference at `docs/sdlc/do-merge.md:61`, removed by the Tier 0 doc cascade).

- **Ship a regression test** that constructs a merged-issue scenario and asserts the plan is not left in `docs/plans/` root after migration (Tier 0 acceptance), plus static assertions that the enforcement wiring is present: `merged-branch-cleanup` is registered in `config/reflections.yaml` with `enabled: true`, its `closed_issue` branch calls `migrate_plan_to_completed`, and `/do-merge` carries the deterministic `--issue` call.

- **Tier 1 backfill** reuses `migrate_plan_to_completed()` via `--sweep` (uncapped, supervised, dry-run-first). It imports/uses ONLY the sweep primitive and `extract_tracking_issue()`; it must NOT call `main()`, `validate_feature_doc()`, or `validate_feature_index()`: that path refuses any plan lacking an indexed `docs/features/*.md` doc, and most of the ~130 historical plans never had one, so reusing it would silently refuse the entire backlog. Tier 1 gates purely on issue/PR state, not on feature-doc presence. Report mode prints `(plan, evidence, action)` rows. Because it is uncapped, it clears the full backlog in one supervised pass — the daily reflection's N=10 cap is a safety valve for the *unattended* case only.

- **Tier 4 is outside the repo diff.** Acceptance is executed-command proof (directory listing + README content), per the issue's executable-criteria mandate.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The Tier 1 script's `gh` calls must handle timeouts / missing references without aborting the whole run — a plan with an unparseable reference is routed to the human-review list, logged, and the sweep continues. Test asserts a malformed plan does not crash the batch.
- [ ] `migrate_completed_plan.py` `git mv` failure (destination exists, dirty tree) must leave the plan in place and surface a non-zero exit — test asserts the plan is NOT lost on failure (the #1394 invariant, re-anchored).

### Empty/Invalid Input Handling
- [ ] Tier 1 script on a plan with no extractable issue/PR reference → routed to human-review list, not moved. Test covers the empty-reference case.
- [ ] Tier 0 invariant when a PR has no associated plan file (hotfix with no plan) → no-op, no error. Test covers the no-plan case.
- [ ] Tier 0 invariant when the plan is already migrated (source absent under root, destination present under `completed/`) → idempotent success, not a `git mv` error. Test covers the already-migrated case (the existence-guard from Race Conditions).

### Error State Rendering
- [ ] Tier 1 report clearly distinguishes `migrated`, `skipped (issue open)`, and `needs-human-review` rows; the report is the user-visible output and must render all three states, not just successes.

## Test Impact

- [ ] `tests/unit/test_migrate_completed_plan.py` — UPDATE: canonical behavior changes from delete→move, so its assertions around `delete_plan()`/`unlink()` must flip to assert a `git mv` into `completed/` via `migrate_plan_to_completed()`, and add the failure-preserves-plan case.
- [ ] `reflections/housekeeping/merged_branch_cleanup.py` tests (CREATE or UPDATE existing reflection test) — the extended `closed_issue` branch now migrates rather than only reporting: closed-tracking-issue plan → migrated, open-tracking-issue plan → skipped, plan that only references a closed *sibling* issue in prose (tracking issue open) → NOT swept (gate reads `tracking:` frontmatter only), per-run cap honored (≥ N+1 eligible → only N migrated, remainder deferred+logged), apply-off (report-only) mode moves nothing, dirty/non-main tree → report-only fallback moves nothing.
- [ ] New test file (CREATE): `tests/unit/test_plan_migration_invariant.py` (or integration equivalent) — the Tier 0 regression test asserting that after a merge whose tracking issue is CLOSED the plan is moved out of `docs/plans/` root, AND static assertions that the enforcement is wired: `merged-branch-cleanup` is registered in `config/reflections.yaml` with `enabled: true`, its `closed_issue` branch calls `migrate_plan_to_completed`, and `docs/sdlc/do-merge.md` carries the deterministic `migrate_completed_plan.py --issue` call (guards against a future prose-only "fix" or a re-disable dropping the enforcement, per Risk 1).
- [ ] New test coverage (CREATE) for the shared `migrate_plan_to_completed()` primitive and the `--sweep`/`--issue` CLIs: closed-issue → migrated, open-issue → skipped, already-migrated (source absent) → idempotent `already-migrated`, `git mv` failure → plan preserved + non-zero, dirty-tree/non-main HEAD → `dirty-tree-skip` (report-only), and **a fixture plan with no `## Documentation` / indexed feature doc still migrates on closed-issue evidence** (guards against accidentally routing through `main()`/`validate_feature_doc()`).

No other existing tests reference the plans-root layout or the moved docs (grep of `tests/` for the duplicated filenames and presentation paths returned no production-test hits).

## Rabbit Holes

- **Rewriting git history to reclaim the 13MB.** `git mv` and deletion shrink the working tree, not the packed history. The acceptance criterion is `du -sh docs` (working tree), not repo size. Do not `filter-branch`/`filter-repo`.
- **Building a general-purpose docs-lifecycle framework.** Tier 0 needs one shared migration primitive called from two existing sites (the `/do-merge` call and the already-present `merged-branch-cleanup` reflection) plus a test — not a plugin architecture for doc reconcilers, and not a net-new reflection beside the existing one.
- **Auto-filing issues for every unresolvable plan (the #1543 trap).** Unresolvable plans go to a static human-review list in the PR body, not a flood of tracker issues.
- **Perfecting `completed/` as an archive.** Whether to prune `completed/` is an Open Question, not scope creep — leave it as-is unless PM says otherwise.
- **Reconstructing the exact history of every one of ~130 leaked plans.** The Tier 1 script gates on current issue/PR state; it does not need per-plan forensic archaeology.

## Risks

### Risk 1: Tier 0 root cause misidentified, leak regrows
**Impact:** Cleanup is repeated in three months.
**Mitigation:** Name the confirmed skipping paths in the PR; ship the regression test that fails if a merged issue's plan stays in root; anchor enforcement in two coordinated code-level sites (the deterministic `/do-merge --issue` call plus the extended path-independent reflection) rather than per-skill prose. **Additionally, guard against prose-degradation (exactly how #1394's fix silently regressed when a deterministic script call was replaced by prose during a command→skill refactor): the enforcement is now deterministic code and registry wiring, not prose** — the Tier 0 test asserts (a) `merged-branch-cleanup` is registered in `config/reflections.yaml` with `enabled: true`; (b) that reflection's `closed_issue` branch calls `migrate_plan_to_completed`; and (c) `docs/sdlc/do-merge.md` carries the deterministic `migrate_completed_plan.py --issue` call. Deleting or disabling any of the three fails CI immediately rather than silently reopening the leak. The regression test therefore covers both migration *logic* AND that the enforcement wiring is present.

### Risk 2: Tier 1 script migrates a plan whose issue is closed but work is still in flight
**Impact:** An active plan disappears from the root.
**Mitigation:** Dry-run-first with a mandatory human review of the report before the destructive pass; migrate (move, not delete) so it is recoverable; plans with open issues are never touched.

### Risk 3: Tier 4 vault writes are invisible to the PR diff
**Impact:** Reviewer cannot confirm the moves happened.
**Mitigation:** Executed-command proof (directory listing + pasted README content) in the PR, per the issue's executable-criteria mandate. `valor-ingest` sidecar existence is asserted by command output.

### Risk 4: Deleting a doc that still has an inbound link
**Impact:** Broken reference in a live doc or skill.
**Mitigation:** Run `/do-docs` cascade; the Verification table includes an anti-criterion asserting no inbound link points at a moved/deleted path. Inbound-link recon already shows the duplicated docs have only one low-stakes reference (a completed plan) and the PDF has zero.

## Race Conditions

Two hazards exist, both on the Tier 0 after-merge path that commits the migration to `main`:

1. **Concurrent cross-machine merges racing on `git push` to `main`.** Two machines (or the daily reflection and a live `/do-merge` call) can each try to push a migration commit near-simultaneously; the second push is rejected non-fast-forward. **Mitigation:** wrap the migration primitive's commit in a rebase-retry loop — `git pull --rebase origin main && git push`, retried a bounded number of times on non-fast-forward rejection — so a losing push replays on top of the winner instead of aborting the migration. **Rebase-conflict handling (distinct from non-fast-forward rejection):** because the primitive only ever `git mv`s a plan file from root to `completed/`, a rebase that hits an actual textual conflict means another process already touched or moved that same plan. On any `git pull --rebase` conflict the primitive MUST `git rebase --abort`, leave the working tree clean, skip that plan for this run (re-evaluated next cycle, where it will read as already-migrated and no-op), and log the skip with reason `rebase-conflict` — it never resolves conflicts unattended and never leaves a half-rebased tree.
2. **`git mv` is not idempotent.** The plan previously asserted migrating an already-migrated plan is a no-op; that is false. If the source path is already gone (a prior migration moved it) `git mv` exits non-zero, which would look like a migration failure and could re-trigger error handling. **Mitigation:** guard the move with an existence check — `if not plan_path.exists(): return "already-migrated"` when the source is absent and the destination under `completed/` is present, treat it as idempotent success, not an error.

3. **Dirty / non-`main` working tree in a shared session checkout.** The reflection (and any merge-site call) runs inside the worker's live checkout, which other sessions share and may have on a feature branch or with uncommitted edits. A `git mv` + `git pull --rebase` + `git push` sequence launched against a dirty or non-`main` HEAD would either fail confusingly, sweep another session's uncommitted work into a migration commit, or push a feature branch's state. **Mitigation — clean-tree/HEAD precondition (C4):** before any `git mv`, the migration primitive asserts `HEAD == main` AND the working tree is clean (`git status --porcelain` empty). If either check fails it takes the **report-only fallback** for that run — it logs which plans it *would* have migrated and moves nothing, exiting success. Migration only ever commits from a clean `main` checkout; it never mutates a tree it does not own.

All other operations are synchronous, single-process file-system and `git`/`gh` CLI invocations with no shared mutable state.

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Rewriting git history (`filter-branch`/`filter-repo`/`gc --aggressive`) to reclaim packed space. The acceptance target is working-tree size; history stays intact. An anti-criterion in Verification asserts no history-rewrite tooling appears in the change.
- [DESTRUCTIVE] Deleting `docs/plans/completed/` or any plan whose tracking issue is still OPEN. Tier 1 moves only closed/merged-evidence plans; the archive prune is an Open Question, defaulting to "keep."
- [EXTERNAL] Confirming whether any teammate still relies on `GEMINI.md` — needs a human answer before deletion; surfaced as an explicit recorded decision in the PR, not assumed.

## Update System

The Tier 0 mechanism edits existing files and re-enables an already-registered reflection, propagating via the normal repo sync with **no new launchd job and no new reflection entry**:

- The migration primitive edits `scripts/migrate_completed_plan.py` (changes `delete_plan()` to a move; adds `migrate_plan_to_completed()` + the `--issue`/`--sweep` CLIs) — a plain repo file, synced by `/update` with no special step.
- The path-independent backstop edits `reflections/housekeeping/merged_branch_cleanup.py` (extends the existing `closed_issue` branch to migrate) and **flips the already-present `merged-branch-cleanup` entry to `enabled: true`** in the reflections registry — no *new* entry is added. The registry resolves in order `REFLECTIONS_YAML env → ~/Desktop/Valor/reflections.yaml → config/reflections.yaml` (`agent/reflection_scheduler.py`), and `install_worker.sh` copies the active registry to `config/reflections.yaml` at install time. Flip `enabled: true` in **`config/reflections.yaml`** (the in-repo source of truth) AND propagate the flip to the vault copy `~/Desktop/Valor/reflections.yaml` on worker machines that use it, or the change is invisible there. The already-installed `com.valor.reflection-worker` subprocess (`python -m reflections`) runs the (now-enabled) reflection on its next cycle — **no new launchd plist**; reload the worker (`./scripts/install_reflection_worker.sh`) to pick it up immediately. Add a task to `scripts/update/` docs only if a machine needs the vault-copy propagation automated; the in-repo fallback already covers dev machines.
- The deterministic merge-site call edits `docs/sdlc/do-merge.md` (Plan Migration section) — a skill addendum synced by `/update` with no special step.

Vault writes (Tier 4) are one-time and outside the update system.

## Agent Integration

No agent integration required. No new MCP server, `.mcp.json` entry, or bridge import is introduced. `valor-ingest` (Tier 4) is an existing CLI already on the agent's Bash surface. The merge-guard hook and `migrate_completed_plan.py` are internal pipeline machinery the agent already invokes via `gh pr merge` / `/do-merge`; the change re-anchors an existing invariant rather than adding a new agent-reachable capability.

## Documentation

### Feature Documentation (PR 1)
- [ ] Create `docs/features/plan-migration-invariant.md` describing the after-merge plan-migration behavior handled by the two coordinated sites over one shared primitive: the deterministic `/do-merge --issue` call (primary path, synchronous) and the extended `merged-branch-cleanup` reflection (path-independent backstop). Cover how the primitive reads each plan's tracking frontmatter (never a branch slug), the evidence-gate on issue state, the unattended-safety posture (apply-gating, per-run cap, alerting, rebase-conflict skip, clean-tree fallback), which merge paths each site covers, and the regression + wiring-presence tests that guard both.
- [ ] Add entry to `docs/features/README.md` index table.

### Repo Docs Cascade (PR 1) — resolve the three-way delete/move/phantom contradiction to ONE behavior
- [ ] Rewrite `docs/sdlc/do-merge.md` "Plan Migration" section (currently instructs a manual hand `git mv` on `main`): replace the hand `git mv` with the **deterministic command** `python scripts/migrate_completed_plan.py --issue <closed-issue-number> --apply` run on `main` after the merge, and note that the `merged-branch-cleanup` reflection is the path-independent backstop for merges that bypass `/do-merge`. Replacing the "agent must remember to `git mv`" prose with a deterministic script call is the fix for the leak (it is exactly the #1394 regression re-anchored).
- [ ] In the SAME file, fix the separate `_handle_merge_completion()` phantom reference at `docs/sdlc/do-merge.md:61` (the "Memory Extraction" section — a DIFFERENT section from Plan Migration): the function does not exist in any Python file (grep-confirmed). Reword to describe the actual after-merge learning-extraction path without naming a nonexistent function.
- [ ] Correct `.claude/skills-global/do-build/PR_AND_CLEANUP.md` Step 8 ("The plan will be deleted by do-merge after the PR is successfully merged"): the plan is **moved to `completed/`** — by the deterministic `/do-merge --issue` call once the tracking issue closes, or by the `merged-branch-cleanup` reflection on a bypassing path — not deleted. Align it with the single canonical move-to-`completed/` behavior.
- [ ] (PR 2) Record the `docs/guides/` (evergreen how-to) vs `docs/research/` (dated investigation) placement rule in `docs/README.md` (Tier 2).
- [ ] (PR 1 and PR 2) Run `/do-docs` cascade so no inbound link points at a moved/deleted doc.

### Knowledge Base (PR 2)
- [ ] Create `~/work-vault/AI Valor Engels System/README.md` as a **depth-1 index of exactly one directory**: `~/work-vault/AI Valor Engels System/`. Scope, stated without ambiguity:
  - **List:** every immediate child of that directory — one table/list row per file AND per subfolder at depth 1 (e.g. a row for `foo.md`, a row for the `bar/` subfolder as a single line), each with a one-line description.
  - **Do NOT:** recurse into subfolders (list `bar/` as one row, not its contents); index any other vault directory; or duplicate the vault-root `_index.md` (which is a whole-vault map, not this file).
  - **Include the Tier 4 arrivals:** the strategy decks and the vendor PDF moved here in this same PR are themselves immediate children, so their rows (and any `.md` sidecars `valor-ingest` produced) must appear in this index.
  - **"Complete" =** `ls -1 "~/work-vault/AI Valor Engels System/"` and the README's listed rows are the same set. Follow the KB-section convention (`docs/conventions/knowledge-base-section.md`). Finally, add one link to this README from `~/work-vault/_index.md`.

## Success Criteria

- [ ] Tier 0: the skipping merge paths are named in the PR; enforcement is two coordinated sites over one shared primitive — the deterministic `/do-merge --issue` call (primary path) and the re-enabled, extended `merged-branch-cleanup` reflection (path-independent backstop covering every path `/do-merge` misses, including the raw-terminal `gh pr merge` that fires no hook); a test fails if a plan whose tracking issue is CLOSED remains in `docs/plans/` root after migration, and static assertions confirm `merged-branch-cleanup` is registered with `enabled: true`, its `closed_issue` branch calls `migrate_plan_to_completed`, and `docs/sdlc/do-merge.md` carries the deterministic `migrate_completed_plan.py --issue` call.
- [ ] Tier 0: the delete-vs-migrate contradiction across `migrate_completed_plan.py`, `PR_AND_CLEANUP.md`, and `docs/sdlc/do-merge.md` is resolved to one documented behavior (move to `completed/`) and cascaded by `/do-docs`; no net-new reflection is added (the existing `merged-branch-cleanup` is extended, not duplicated).
- [ ] Tier 1: after merge, zero files in `docs/plans/` root reference a CLOSED issue or MERGED PR (re-run the tier-1 script in report mode to verify); the evidence report is attached to the PR.
- [ ] Tier 2: exactly one copy of each of the three duplicated docs exists — a **filename** search excluding plan docs returns a single path (`git ls-files -- 'docs/**/<name>.md' ':(exclude)docs/plans/**' | wc -l` == 1; a content grep is unreliable here because the canonical docs do not contain their own slug string, so only plan-doc references would match); the guides-vs-research rule is recorded in `docs/README.md`.
- [ ] Tier 3: `docs/presentations/`, the listed one-off reports, and resolved stragglers are gone; `du -sh docs` drops by at least 5MB; GEMINI.md and CHANGELOG.md each have an explicit recorded decision in the PR.
- [ ] Size reclaim recorded explicitly: capture `du -sh docs` **before** and **after** the Tier 3/4 sweep and paste both totals in the PR (correctness Tiers 0/1 reclaim zero bytes by design — only Tier 3/4 shrink the tree).
- [ ] Tier 4: moved files exist in the vault, each binary has a `valor-ingest` `.md` sidecar, `~/work-vault/AI Valor Engels System/README.md` exists with a complete index, and vault root `_index.md` links it; PR includes the proof artifact (listing + README content).
- [ ] No inbound link in the repo points at a moved or deleted doc (docs cascade run and clean).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it never builds directly. Tier 0 (mechanism + test) is the critical path and should be built and reviewed first, since it prevents regrowth and is dogfooded by its own evidence-gate reading this plan's tracking issue (see Plan-doc migration timing below). Tiers 1–4 can proceed in parallel once Tier 0's canonical behavior is decided.

### Delivery: two PRs

Tier 0 (the pipeline-invariant bugfix + regression test) and Tiers 1–4 (the one-time docs janitorial sweep) share **no code, test, or review surface**. The split is enforced in the task DAG (see Step by Step Tasks: tasks carry an explicit **PR:** tag, and every PR-2 task depends on the sentinel `pr1-merged` gate, not merely on `build-tier0-mechanism`). Ship as two PRs on **separate branches**:

1. **PR 1 — Tier 0 alone** (`type: bug`). Small, ships fast, stops the leak. Contains the `migrate_completed_plan.py` changes (the canonical move-to-`completed/` behavior + the shared `migrate_plan_to_completed()` primitive and `--issue`/`--sweep` CLIs with cap/apply-gating/rebase-conflict/clean-tree handling), the extended `merged-branch-cleanup` reflection + its `enabled: true` flip, the deterministic `/do-merge --issue` call, the regression + wiring-presence tests, the feature doc, and the doc-contradiction resolution.
2. **PR 2 — Tiers 1–4 one-time sweep** (`type: chore`). Branched from `main` **after PR 1 merges** (so it inherits the shipped `migrate_plan_to_completed()` primitive and `--sweep` CLI it calls). Contains the Tier 1 uncapped dry-run-first sweep + report, the Tier 2 de-dup, Tier 3 deletions, and Tier 4 vault relocation + indexes.

**Plan-doc migration timing (avoids the git resurrection / rename conflict).** Both PRs carry `docs/plans/stale-docs-tiered-cleanup.md` in root. Migration is **evidence-gated on the tracking issue being CLOSED** and reads this plan's own `tracking:` frontmatter (`.../issues/1900`, path-independent — it never depends on either branch slug). Issue #1900 stays OPEN until PR 2 merges with `Closes #1900`. Therefore:
- While #1900 is open, **every** migration attempt — PR 1's merge (the deterministic `/do-merge --issue` runs against PR 1's own closing issue, not #1900), the daily reflection cycle between the two merges, and any manual Tier 1 dry-run — reads #1900 as OPEN and correctly declines to move this plan. The file stays in root on `main`, identical to what PR 2's branch carries — no divergence, no resurrection, no rename conflict.
- Only after **PR 2's merge closes #1900** does this plan become eligible. PR 2's own `/do-merge --issue 1900` call (or the next reflection cycle) then `git mv`s `stale-docs-tiered-cleanup.md` → `completed/`. Because PR 2 is the last to merge and migration reads live issue state (not a slug), nothing resurrects it.

The invariant is dogfooded by its own evidence-gate logic reading this plan's tracking frontmatter (correctly declining to migrate an open-issue plan until #1900 closes), and demonstrated by the regression test (red→green). Convergence is deterministic on the primary path (PR 2's `/do-merge --issue 1900`) with the reflection as backstop — the design needs no per-merge, slug-keyed wiring.

### Team Members

- **Builder (tier0-invariant)**
  - Name: tier0-builder
  - Role: Confirm skipping paths; ship the single authoritative migration mechanism + regression test; resolve the doc contradiction
  - Agent Type: builder
  - Domain: async/pipeline (paste relevant DOMAIN_FRAMING rules)
  - Resume: true

- **Builder (tier1-sweep)**
  - Name: tier1-builder
  - Role: Dry-run-first evidence-gated leaked-plan migration script + report
  - Agent Type: builder
  - Resume: true

- **Builder (tier234-docs)**
  - Name: docs-builder
  - Role: De-dup (Tier 2), delete one-offs (Tier 3), vault relocation + index (Tier 4)
  - Agent Type: builder
  - Resume: true

- **Documentarian (cascade)**
  - Name: docs-cascade
  - Role: Resolve the contradiction wording, record placement rule, run `/do-docs`, create feature doc + vault README
  - Agent Type: documentarian
  - Resume: true

- **Validator (all)**
  - Name: cleanup-validator
  - Role: Verify every acceptance criterion, including executed Tier 4 proof and the Tier 0 regression test
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Standard Tier 1 agents (builder, validator, documentarian). No specialist pool.

## Step by Step Tasks

The DAG is partitioned into two PRs. Every **PR 2** task depends on the `pr1-merged` sentinel gate (PR 1 merged to `main`), not merely on `build-tier0-mechanism` — Tiers 1–4 need Tier 0 *landed and merged*, so they call the shipped `migrate_plan_to_completed()` primitive (`--sweep`) and branch from a `main` that already carries the fixed pipeline.

#### PR 1 — Tier 0 (branch `session/stale-docs-tier0`)

##### 1. Ship the Tier 0 path-independent invariant
- **Task ID**: build-tier0-mechanism
- **PR**: 1
- **Depends On**: none
- **Validates**: tests/unit/test_plan_migration_invariant.py (create), tests/unit/test_migrate_completed_plan.py (update)
- **Assigned To**: tier0-builder
- **Agent Type**: builder
- **Parallel**: false
- Confirm which merge paths skip migration (manual `gh pr merge`, forked `/do-sdlc`, cross-machine, PR-review merges) and record them in the PR.
- Decide delete-vs-move with PM; make `migrate_completed_plan.py` the single authoritative mechanism (recommend `git mv` into `completed/`).
- Add the shared migration primitive `migrate_plan_to_completed(plan_path, *, apply)` in `scripts/migrate_completed_plan.py` (guarded `git mv`: existence-guard, rebase-retry push, rebase-conflict abort+skip+log, clean-tree/HEAD==main precondition → report-only fallback, returns an action verdict). Add the `--issue <N>` CLI (resolve the one plan whose `tracking:` matches issue N) and the `--sweep` CLI (iterate all root plans, capped when armed).
- **Extend the existing `merged-branch-cleanup` reflection** (`reflections/housekeeping/merged_branch_cleanup.py::run()`): on its existing `closed_issue` verdict branch, call `migrate_plan_to_completed()` (gate tightened to the plan's own `tracking:` frontmatter via `extract_tracking_issue()`), with the per-run cap (N=10) and apply-gating. Do **not** create a net-new reflection (NO LEGACY CODE TOLERANCE — it duplicates this file's sweep/classify). Leave the reflection **report-only in this task** (apply off); arming is the discrete task below. Update the disabled-reason comment.
- **Restore the deterministic merge-site call:** edit `docs/sdlc/do-merge.md`'s Plan Migration section to invoke `python scripts/migrate_completed_plan.py --issue <closed-issue-number> --apply` on `main` after the merge (issue-keyed, so slug≠filename does not bite).
- Do NOT wire migration into `scripts/post_merge_cleanup.py` (its `main()` is slug-keyed with no issue-state knowledge — see Data Flow rejection of candidate B). Do NOT reference the nonexistent `_handle_merge_completion()`.
- Write the regression test that fails when a plan whose tracking issue is CLOSED stays in root after migration, and add the static assertions that `merged-branch-cleanup` is registered with `enabled: true`, its `closed_issue` branch calls `migrate_plan_to_completed`, and `docs/sdlc/do-merge.md` carries the deterministic `--issue` call (guards against a future prose-only "fix" or a re-disable silently dropping enforcement, per #1394).

##### 2. Validate Tier 0
- **Task ID**: validate-tier0
- **PR**: 1
- **Depends On**: build-tier0-mechanism
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the test fails on a deliberately-leaked plan (red-state) and passes after the fix, with the reflection still report-only (apply off).

##### 3. Arm the backstop reflection
- **Task ID**: arm-reflection
- **PR**: 1
- **Depends On**: validate-tier0
- **Assigned To**: tier0-builder
- **Agent Type**: builder
- **Parallel**: false
- Only after the Tier 0 regression test proves the evidence gate (validate-tier0 green): flip `merged-branch-cleanup` to `enabled: true` in `config/reflections.yaml` AND turn its apply mode on (armed-but-capped, N=10). This is the deliberate two-part arming decision — kept as a discrete task so arming never rides silently inside the mechanism build. Note the vault-copy propagation (`~/Desktop/Valor/reflections.yaml`) requirement from Update System.

##### 4. Tier 0 feature doc + contradiction resolution
- **Task ID**: document-tier0
- **PR**: 1
- **Depends On**: build-tier0-mechanism
- **Assigned To**: docs-cascade
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/plan-migration-invariant.md` + `docs/features/README.md` entry describing both enforcement sites (deterministic `/do-merge --issue` call + extended `merged-branch-cleanup` reflection backstop) over the one shared primitive.
- Resolve the `do-merge.md` / `PR_AND_CLEANUP.md` delete-vs-move contradiction wording; replace the hand `git mv` with the deterministic `--issue` call; remove the `_handle_merge_completion()` reference. Run `/do-docs` for the Tier 0 surface.

##### GATE: PR 1 merged
- **Task ID**: pr1-merged
- **PR**: 1→2 boundary
- **Depends On**: validate-tier0, arm-reflection, document-tier0
- **Assigned To**: (lead — human/PM confirms PR 1 merged and #1900 still OPEN)
- Sentinel: PR 1 is merged to `main`; issue #1900 remains open (PR 1 does not close it). PR 2 branches from `main` at this point.

#### PR 2 — Tiers 1–4 one-time sweep (branch `session/stale-docs-tiers1234`, cut after `pr1-merged`)

##### 4. Tier 1 evidence-gated sweep
- **Task ID**: build-tier1-sweep
- **PR**: 2
- **Depends On**: pr1-merged
- **Assigned To**: tier1-builder
- **Agent Type**: builder
- **Parallel**: true
- Invoke the shipped `migrate_completed_plan.py --sweep` (uncapped, reusing `migrate_plan_to_completed()`) in dry-run/report mode first; review the `(plan, evidence, action)` report; then run the destructive pass (`--sweep --apply`). The uncapped one-time backfill clears the full ~130-plan backlog in one supervised pass (the daily reflection's N=10 cap is the unattended safety valve, not a limit on this human-reviewed run). Route unresolvable plans to a human-review list in the PR body.

##### 5. Tier 2/3/4 docs reorganization
- **Task ID**: build-docs-reorg
- **PR**: 2
- **Depends On**: pr1-merged
- **Assigned To**: docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Tier 2: merge diverged pairs to one canonical file each; fix inbound links.
- Tier 3: delete one-off decks/reports; surface GEMINI.md/CHANGELOG.md decisions.
- Tier 4: `git mv`/move business+vendor docs to vault; `valor-ingest` each binary; capture executed-command proof.

##### 6. Tier 2/3/4 documentation cascade + indexes
- **Task ID**: document-tiers234
- **PR**: 2
- **Depends On**: build-docs-reorg
- **Assigned To**: docs-cascade
- **Agent Type**: documentarian
- **Parallel**: false
- Record the guides-vs-research placement rule in `docs/README.md`; create the vault README and link from `_index.md`; run `/do-docs` so no inbound link points at a moved/deleted doc.

##### 7. Final validation
- **Task ID**: validate-all
- **PR**: 2
- **Depends On**: build-tier1-sweep, build-docs-reorg, document-tiers234
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify every Success Criterion, including `du -sh docs` before/after delta, the filename-based single-copy `git ls-files` checks, executed Tier 4 proof, and a clean docs cascade.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Tier 2: swot single copy | `git ls-files -- 'docs/**/claude-code-feature-swot.md' ':(exclude)docs/plans/**' \| wc -l` | output is 1 |
| Tier 2: sdk-req single copy | `git ls-files -- 'docs/**/agent-sdk-replacement-requirements.md' ':(exclude)docs/plans/**' \| wc -l` | output is 1 |
| Tier 2: ruflo single copy | `git ls-files -- 'docs/**/ruflo-deep-dive.md' ':(exclude)docs/plans/**' \| wc -l` | output is 1 |
| Tier 3: presentations gone | `test -d docs/presentations; echo $?` | output contains 1 |
| Tier 3: GEMINI decided (gone) | `test -e GEMINI.md; echo $?` | output contains 1 |
| Tier 4: vendor PDF out of repo | `test -e docs/media/anthropic-skills-guide.pdf; echo $?` | output contains 1 |
| Tier 4: vault README exists | `test -f "$HOME/work-vault/AI Valor Engels System/README.md"; echo $?` | output contains 0 |
| Tier 0: migration invariant test present | `ls tests/unit/test_plan_migration_invariant.py tests/integration/test_plan_migration_invariant.py 2>/dev/null \| wc -l` | output > 0 |
| Tier 0: no phantom handler reference | `grep -rn "_handle_merge_completion" docs/sdlc/do-merge.md .claude/skills-global/do-build/PR_AND_CLEANUP.md` | match count == 0 |
| Tier 0: backstop reflection enabled | `python -c "import yaml; r=[x for x in yaml.safe_load(open('config/reflections.yaml'))['reflections'] if x['name']=='merged-branch-cleanup'][0]; print(r['enabled'])"` | output is `True` |
| Tier 0: migration primitive exists | `grep -q "def migrate_plan_to_completed" scripts/migrate_completed_plan.py; echo $?` | output contains 0 |
| Tier 0: reflection calls the primitive | `grep -q "migrate_plan_to_completed" reflections/housekeeping/merged_branch_cleanup.py; echo $?` | output contains 0 |
| Tier 0: do-merge carries deterministic call | `grep -q "migrate_completed_plan.py --issue" docs/sdlc/do-merge.md; echo $?` | output contains 0 |
| No history-rewrite tooling | `git grep -rn "filter-branch\|filter-repo" -- scripts/ .claude/` | match count == 0 |

## Critique Results

<!-- Round 1 (/do-plan-critique war room, 2026-07-05): NEEDS REVISION (1 blocker, 6 concerns) — addressed below. -->
<!-- Round 2 (re-critique, 2026-07-05): NEEDS REVISION (2 blockers, 1 concern, 1 nit) — addressed in "Round 2 resolution" below. -->
<!-- Round 3 (re-critique, 2026-07-05): NEEDS REVISION (1 blocker, 4 concerns) — addressed in "Round 3 resolution" below. -->
<!-- Round 4 (re-critique, 2026-07-05): NEEDS REVISION (1 blocker, 4 concerns, 1 trivial drift) — addressed in "Round 4 resolution" below. -->

**Round 4 resolution (this revision — 1 blocker + 4 concerns + trivial drift; adopted the critics' recommendations directly):**

- **BLOCKER (net-new reconciler duplicates the existing `merged-branch-cleanup` reflection — NO LEGACY CODE TOLERANCE):** Confirmed by reading `reflections/housekeeping/merged_branch_cleanup.py::run()` — it already sweeps `docs/plans/` root, extracts every issue ref, batch-queries `gh` (batches of 10), and classifies a `closed_issue` verdict (lines 166–175), stopping at a report-only finding string; it is registered as `merged-branch-cleanup` (callable `run_branch_plan_cleanup`) and currently `enabled: false`. **Fix: dropped the net-new `reconcile_leaked_plans()` + `plan-migration-reconciler` reflection entirely and instead EXTEND this existing reflection** — its `closed_issue` branch now calls the shared `migrate_plan_to_completed()` primitive (gate tightened to the plan's own `tracking:` frontmatter) and the reflection is re-enabled. Added the file to Prior Art and to the Why-Previous-Fixes-Failed table. Cascaded through Data Flow (candidate C rewritten to "extend existing"), Key Elements, Flow, Technical Approach, Test Impact, Risk 1, Update System (flip `enabled: true`, no new entry), Documentation, Success Criteria, Verification, Step 1, Team Orchestration, and Open Questions.
- **C1 (registry-presence guard must assert `enabled: true`):** The static assertions now check `merged-branch-cleanup` is registered **with `enabled: true`** (Verification uses a yaml-parse check that prints `True`), that its `closed_issue` branch calls `migrate_plan_to_completed`, and that `/do-merge` carries the deterministic `--issue` call. Updated Test Impact, Risk 1, and Success Criteria to match.
- **C2 (restore merge-site coverage — issue #1900 said reconciler-alone "institutionalizes the leak"):** Added Site D — `/do-merge` calls `migrate_completed_plan.py --issue <closed-issue-number> --apply` deterministically after each merge (issue-keyed, so slug≠filename does not bite), migrating the primary path synchronously. The extended reflection stays as the path-independent backstop for merges that bypass `/do-merge`. Documented in Data Flow, Technical Approach (Site D), the do-merge.md cascade bullet, and Success Criteria.
- **C3 (cap/arming semantics + Tier-1 bulk under a shared cap):** The primitive takes explicit `apply` and cap params; the reflection is armed (enabled + apply on, N=10) only in a **discrete DAG task** (`arm-reflection`, depends on `validate-tier0`) so arming never rides silently inside the build. Reconciled the numbers: the daily reflection's N=10 cap is an unattended safety valve, while the **Tier-1 backfill is a separate uncapped supervised sweep** (`--sweep --apply`, dry-run-first) that clears the full ~130-plan backlog in one pass. Updated Key Elements (Tier 1), Technical Approach, Step 1, `arm-reflection` task, and the Tier 1 sweep task.
- **C4 (reconciler push assumes clean main checkout in the shared session tree):** Added Race Conditions hazard #3 — a clean-tree + `HEAD == main` precondition before any `git mv`/push; on failure the primitive takes a report-only fallback (logs what it would migrate, moves nothing, exits success). Added the `dirty-tree-skip` verdict to the primitive and a test case for it.
- **Trivial drift (211 → 212):** Corrected the root-plan count in Problem and Freshness Check to 212 (verified `ls -1 docs/plans/*.md | wc -l` == 212).

**Round 3 resolution (1 blocker + 4 concerns addressed; adopted the critique's simplification):**

- **BLOCKER (Layer 1 callable unspecified, contradicts the two-PR dogfood; slug ≠ filename):** Adopted the escape hatch the critique offered. `post_merge_cleanup.py::main()` takes only a slug, calls `cleanup_after_merge()`, and has **no `gh` call or issue-state knowledge** (verified by reading the file), so it cannot evidence-gate; and both branch slugs (`stale-docs-tier0`, `stale-docs-tiers1234`) differ from the plan filename `stale-docs-tiered-cleanup`, so a slug-keyed Layer 1 no-ops on both merges. **Fix: made the path-independent reconciler the SOLE load-bearing enforcement and dropped Layer 1 entirely.** The reconciler reads each plan's own `tracking:` frontmatter (never a slug), so the dogfood story now actually holds: every reconciler run declines to migrate this plan while #1900 is open, and migrates it once PR 2 closes #1900. This dissolves the blocker and shrinks PR 1 (no `post_merge_cleanup.py` edit, no #1357-ordering test). Cascaded through Data Flow (candidate B now explicitly rejected), Technical Approach, Key Elements, Solution Flow, Test Impact, Risk 1, Update System, Success Criteria, Verification (removed the `post_merge_cleanup.py` grep row), Step 1, Team Orchestration, and Open Questions.
- **CONCERN 1 (do-merge.md prose half-fix):** Finished the wording correction and made it precise. The Repo Docs Cascade now names three distinct edits: (a) rewrite `do-merge.md` "Plan Migration" section to defer to the reconciler (deleting the manual `git mv` instruction that IS the leak); (b) fix the separate `_handle_merge_completion()` phantom at `do-merge.md:61` in the "Memory Extraction" section (grep-confirmed: zero Python definitions, one stale doc reference); (c) correct `PR_AND_CLEANUP.md` Step 8's "deleted by do-merge" to "moved to `completed/` by the reconciler."
- **CONCERN 2 (rebase-conflict handling):** Race Conditions #1 now distinguishes non-fast-forward rejection (rebase-retry) from an actual textual rebase conflict: on conflict the reconciler `git rebase --abort`s, leaves a clean tree, skips that plan for the run (re-evaluated next cycle), and logs reason `rebase-conflict` — never resolves conflicts unattended.
- **CONCERN 3 (unattended reconciler safety):** Technical Approach adds an explicit unattended-safety posture — dry-run default (destructive pass requires arming), a per-run migration cap (N=10, remainder deferred+logged), and alerting (summary line via the reflection scheduler on any migration or failure). Test Impact adds cap-honored and dry-run-moves-nothing cases.
- **NIT (Tier 4 vault README scope):** Rewrote the Knowledge Base bullet as an unambiguous depth-1 index of exactly one directory: list every immediate child (file OR subfolder as a single row), do not recurse or index other vault dirs, include the Tier 4 arrivals, and define "complete" as `ls -1` parity.

**Round 2 resolution (2 blockers + 1 concern + 1 nit addressed):**

- **BLOCKER 1 (self-defeating Tier 2 single-copy grep):** The `git grep -l <name> -- docs/ | grep -v completed` rows counted the wrong thing — the canonical `docs/guides|research/*.md` files do NOT contain their own slug string, so a content grep matches only plan-doc *references*, never the doc files. Replaced all three Verification rows and the Tier 2 Success Criterion with a **filename** search: `git ls-files -- 'docs/**/<name>.md' ':(exclude)docs/plans/**' | wc -l` (== 1 after dedup; empirically returns 2 today, 1 after removing one duplicate). Immune to the plan doc's own references.
- **BLOCKER 2 (undefined enforcement site):** MADE the decision (was Open Question #2). No single hook covers a human's raw-terminal `gh pr merge` (fires no Claude Code hook, calls no cleanup script), and the merge-guard hook is pre-merge. Chose **two layers over one authoritative module** (`scripts/migrate_completed_plan.py`): Layer 1 = merge-site migration in `scripts/post_merge_cleanup.py::main()`, before the #1357 `blocked_by_session` early-return; Layer 2 = a `plan-migration-reconciler` reflection (`config/reflections.yaml`, callable `reconcile_leaked_plans()`) as the path-independent backstop. The `<chosen_enforcement_file>` placeholder in the Verification table is replaced by the real path `scripts/post_merge_cleanup.py`, plus two new rows asserting the reconciler is registered and its callable exists. Documented in Technical Approach, the Data Flow decision block, Update System, and Open Questions.
- **CONCERN (two-PR split prose-only, shared plan-doc conflict):** The split is now enforced in the task DAG — every PR-2 task depends on a `pr1-merged` sentinel gate, tasks carry a **PR:** tag, and PR 2 branches from `main` after PR 1 merges. The git resurrection/rename risk is resolved by the evidence gate: issue #1900 stays OPEN until PR 2 closes it, so PR 1's merge does not migrate this plan doc (gate correctly skips an open-issue plan); only PR 2's merge (last to land) migrates it. Removed the false "dogfood at PR 1 merge" framing.
- **NIT (Tier 4 vault README scope):** Clarified in the Documentation → Knowledge Base bullet — the README indexes only the files directly inside `~/work-vault/AI Valor Engels System/` (one line each, per the KB-section convention), not a recursive whole-vault index and not the vault-root `_index.md`.

**Round 1 resolution (all findings addressed):**

- **BLOCKER (self-defeating grep):** Verification row rescoped to `grep -rn "_handle_merge_completion" docs/sdlc/do-merge.md .claude/skills-global/do-build/PR_AND_CLEANUP.md` (no longer matches this plan's own copy under `docs/plans/`); added a companion static-invocation Verification row.
- **C1 (feature-doc gate):** Technical Approach now mandates `from scripts.migrate_completed_plan import extract_tracking_issue` ONLY — never `main()`/`validate_feature_doc()`/`validate_feature_index()`; Test Impact adds a no-`## Documentation` fixture that still migrates on closed-issue evidence.
- **C2 (race conditions):** Race Conditions section rewritten with a `git pull --rebase origin main && git push` retry loop for concurrent cross-machine merges and an existence-guard (`if not plan_path.exists(): return success`) for `git mv` idempotency; failure-path test adds the already-migrated case.
- **C3 (prose-degradation guard):** Risk 1, the Tier 0 test, and Step 1 now require a static-invocation assertion that the enforcement file still contains a `migrate_completed_plan` call; added a Verification row for it.
- **C4 (split PRs):** Team Orchestration adds a "Delivery: two PRs" subsection — PR 1 = Tier 0 alone, PR 2 = Tiers 1–4 gated on PR 1's merged issue.
- **C5 (value framing):** Problem separates correctness (Tier 0/1, zero bytes) from size (Tier 3/4, ~5MB); Success Criteria adds an explicit before/after `du -sh docs` total.
- **C6 (#1357):** Prior Art cites #1357's busy-session exit-2 guard in `cleanup_after_merge()`; Technical Approach and enforcement-candidate B now require any folded-in migration to run BEFORE that guard's early-surface.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | History & Consistency + Structural | Verification row `grep -rn "_handle_merge_completion" docs/ .claude/` == 0 can never pass: this plan file itself contains the string and stays under `docs/` after migrating to `docs/plans/completed/`. Self-defeating acceptance criterion. | Verification table | Replace with `grep -rn "_handle_merge_completion" docs/sdlc/do-merge.md .claude/skills-global/do-build/PR_AND_CLEANUP.md` (expect 0), or `git grep -n "_handle_merge_completion" -- docs/ .claude/ ':(exclude)docs/plans/'`. |
| CONCERN | Risk & Robustness | Tier 1's ~130-plan sweep must NOT route through `migrate_completed_plan.py`'s `main()`, which refuses any plan lacking an indexed `docs/features/*.md` doc — most historical plans never had one, so reuse would silently refuse the whole backlog. | Tier 1 / Technical Approach | Import ONLY `from scripts.migrate_completed_plan import extract_tracking_issue`; never call `main()`/`validate_feature_doc()`/`validate_feature_index()`. Add a Tier 1 test: a fixture plan with no `## Documentation` section still migrates on closed-issue evidence. |
| CONCERN | Risk & Robustness | Race Conditions "none identified" ignores concurrent cross-machine merges racing on `git push` to `main`; and `git mv` idempotency is asserted but false (an already-moved source path fails non-zero, not a no-op). | Race Conditions | Wrap the after-merge commit in a `git pull --rebase origin main && git push` retry loop on non-fast-forward; guard the move with `if not plan_path.exists(): return success` (source-absent + dest-present = already migrated). |
| CONCERN | Risk & Robustness | Tier 0 regression test exercises migration *logic*, not whether the enforcement site still *invokes* it — exactly how #1394's fix silently degraded (deterministic call replaced by prose during a command→skill refactor). | Risk 1 / Tier 0 test | Add a static-invocation assertion: `assert "migrate_completed_plan" in Path(<enforcement_file>).read_text()` (or an AST call-site check) targeting the chosen enforcement file, so a future doc-only "fix" fails CI immediately. |
| CONCERN | Scope & Value | Tier 0 (pipeline-invariant bugfix + regression test) is bundled with Tiers 1-4 (docs janitorial sweep) that share no code; the task graph already gates Tiers 1-4 on `build-tier0-mechanism`, proving they need only Tier 0 *landed*, not co-reviewed. | Solution / Team Orchestration | Split into two PRs: (1) Tier 0 alone — small, ships fast, stops the leak; (2) Tiers 1-4 as a one-time sweep whose Prerequisites row references Tier 0's merged issue. No shared code/test/review surface is lost. |
| CONCERN | Scope & Value | The "26MB / 13MB" headline motivates the whole plan, but only Tier 3/4 shrink `docs/` (~5MB); Tier 0/1 move files within `docs/` (root→completed/) and reclaim zero bytes — a value framing mismatch. | Problem / Success Criteria | Separate the two value props: Tier 0/1 = correctness (every root file maps to open work); Tier 3/4 = the ~5MB size reclaim. Add an explicit before/after `du -sh docs` total to Success Criteria, not just Tier 3's isolated delta. |
| CONCERN | History & Consistency | Prior Art omits #1357, which added a busy-session guard (`exit 2`) to `cleanup_after_merge()` — the very function named as enforcement candidate B; folding migration in after that guard inherits its early-return, creating a new "migration skipped" path (the exact leak the plan fixes). | Prior Art / Technical Approach | Cite #1357; require that any migration added to `cleanup_after_merge()` run BEFORE (or independent of) the busy-session guard, never after it. |

---

## Open Questions

1. **Canonical migration behavior:** delete the plan (git history as archive) or move to `docs/plans/completed/`? Recommendation: move — but this contradicts `migrate_completed_plan.py`'s current `unlink()`. Confirm before build changes the script.
2. **Prune `docs/plans/completed/`?** It holds 138 files (5.4MB). Keep as archive (default) or prune, given git history already preserves content?
3. **GEMINI.md and CHANGELOG.md:** delete both, or is either still in use? (GEMINI.md is [EXTERNAL] — needs a human answer.)

**RESOLVED (enforcement site — was Open Question #2):** Enforcement is **two coordinated sites over one shared primitive** (`migrate_plan_to_completed()` in `scripts/migrate_completed_plan.py`, gating on each plan's own `tracking:` frontmatter, never a branch slug): (D) a **deterministic `/do-merge --issue <N>` call** that migrates synchronously on the primary path (so the root does not hold day-old closed-issue plans between cycles — the issue's "reconciler-alone institutionalizes the leak" objection), plus (C) the **extended, re-enabled `merged-branch-cleanup` reflection** as the path-independent backstop covering every path `/do-merge` misses (raw-terminal `gh pr merge`, forked `/do-sdlc`, cross-machine). A net-new reflection is NOT added — the existing `merged-branch-cleanup` (which already sweeps/extracts/classifies `closed_issue`) is extended, per NO LEGACY CODE TOLERANCE. Candidate B (`post_merge_cleanup.py`) is rejected: its `main()` is slug-keyed with no issue-state knowledge, and the branch slug ≠ plan-doc filename mismatch would make a slug-keyed call no-op. The merge-guard hook is rejected (pre-merge, absent for raw-terminal merges). See Technical Approach and the Data Flow decision block.
