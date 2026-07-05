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

`docs/` weighs 26MB, and most of it is artifacts nobody will read again. The root cause of the largest pile — 211 plan files (13MB) sitting in `docs/plans/` root — is a leak in the merge pipeline: the step that is supposed to migrate a plan out of the root after its PR merges runs on some paths and silently skips others.

**Current behavior:**

- `docs/plans/` root holds 211 plan files (13MB); `docs/plans/completed/` holds 138 (5.4MB). All six of the oldest sampled root plans reference CLOSED issues. Migration to `completed/` demonstrably works on the primary path but skips on others, so ~130 pre-June plans remain in the root.
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
- `docs/plans/` root: issue said 214 files; now **211** (a few migrated since filing). `completed/` still 138. `docs/plans` 13MB, `completed/` 5.4MB. — holds with minor drift.
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
- **#1357 / `agent/worktree_manager.py::cleanup_after_merge()`** (CLOSED): added a busy-session guard — when a live AgentSession still references the worktree, `remove_worktree()` returns `("blocked", session_id)`, `cleanup_after_merge()` surfaces `blocked_by_session`, and `scripts/post_merge_cleanup.py` **exits 2**. This directly constrains enforcement candidate B (`cleanup_after_merge()`): if plan migration is folded in *after* this guard, a blocked worktree short-circuits the function and migration is skipped — recreating the exact leak this plan fixes. Any migration added to `cleanup_after_merge()` MUST run **before** (or fully independent of) the busy-session guard's early-surface, never after it.
- **`scripts/migrate_completed_plan.py`** (292 lines): exists, works, has `--dry-run`. **Critically, its `delete_plan()` calls `plan_path.unlink()` — it DELETES the plan, it does not move it to `completed/`.** Its own `--help` says "On success: Deletes the plan file."

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #1304 | Kept plans on `main`; documented the `rm -f`-after-migrate hazard | Documented but did not remove the destructive path; left the delete-vs-move ambiguity unresolved |
| #1394 | Gated plan deletion on `migrate_completed_plan.py` exit code inside `.claude/commands/do-merge.md` | The fix lived in the **command file**, which was later replaced by the skill at `docs/sdlc/do-merge.md`. The command→skill migration dropped the deterministic script call and replaced it with a manual prose instruction ("move the plan from `docs/plans/{slug}.md` to `docs/plans/completed/{slug}.md`"). Migration now depends on an agent remembering to `git mv` by hand — which fails on manual `gh pr merge`, forked `/do-sdlc` runs, and merges on a machine that doesn't hold the plan. |

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
- **(A) merge-guard hook** — covers every *Claude-Code-mediated* `gh pr merge`, but runs pre-merge; it can *verify/authorize* but an after-merge action needs a follow-through step. Also fires no hook at all for a human typing `gh pr merge` in a raw terminal. **Rejected as sole mechanism.**
- **(B) `post_merge_cleanup.py` / `cleanup_after_merge()`** — already runs after-merge with the slug in hand; only invoked on paths that call it. **Caveat (#1357):** `cleanup_after_merge()` carries a busy-session guard that early-surfaces `blocked_by_session` and makes `post_merge_cleanup.py` exit 2; migration folded in after that guard would inherit the early-return and skip. **CHOSEN as Layer 1 (primary)** — migration runs at the top of `main()`, before the #1357 guard.
- **(C) a reconciler reflection** — sweeps root plans whose issue is CLOSED. It is the ONLY path-independent site: it covers the raw-terminal `gh pr merge` (path b) that fires no hook and calls no cleanup script. **CHOSEN as Layer 2 (backstop).** It does not merely institutionalize the leak — paired with Layer 1, Layer 1 handles the common path immediately and Layer 2 guarantees eventual convergence for every path within one cycle.

**Decision:** ship both B (primary) and C (backstop), both delegating to the single authoritative `scripts/migrate_completed_plan.py`. See Technical Approach for the concrete wiring.

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

- **Tier 0 — path-independent migration invariant (two enforcement layers, one authoritative module).** Layer 1 migrates a merged PR's plan out of `docs/plans/` root at the merge site (`post_merge_cleanup.py`) for the common Claude-Code-mediated paths; Layer 2 is a standing reconciler reflection that catches the raw-terminal `gh pr merge` and anything Layer 1 misses. Both delegate to `scripts/migrate_completed_plan.py`. Ships with a regression test that fails if a merged PR's plan remains in root, plus a static-invocation assertion. Resolve the three-way delete/move contradiction to ONE documented behavior and cascade it.
- **Tier 1 — evidence-gated leaked-plan migration.** A dry-run-first script that, for each root plan, extracts issue/PR references, queries `gh` for state, and migrates only when the issue is CLOSED / PR MERGED. Unresolvable plans go to a human-review list in the PR, never moved blind. Emits a machine-readable report as the proof artifact.
- **Tier 2 — de-duplication.** Merge each diverged research/guides pair into one canonical file, delete the other, fix the (minimal) inbound links, and record the guides-vs-research placement rule in `docs/README.md`.
- **Tier 3 — remove one-off decks and point-in-time reports.** Delete outright what has no lasting value; route business-valuable decks to Tier 4. Surface GEMINI.md and CHANGELOG.md as explicit keep-or-delete decisions.
- **Tier 4 — vendor/business docs to the knowledge base, indexed.** Move the PDF and the strategy decks to the vault, `valor-ingest` each binary, create the missing `~/work-vault/AI Valor Engels System/README.md` file index, and link it from vault root `_index.md`. Prove with executed commands (listing + README content) pasted into the PR.

### Flow

PR merges → merge-site invariant runs (Tier 0) → plan leaves root deterministically → Tier 1 script sweeps the historical backlog with per-file evidence → Tier 2/3 collapse duplicates and delete one-offs → Tier 4 relocates business/vendor docs to the indexed vault → docs cascade confirms zero dangling inbound links.

### Technical Approach

- **Decide the canonical behavior first** (delete vs move-to-`completed/`). Recommendation to confirm with PM: **move to `completed/`** (git history alone is a poor archive for grep/context, and 138 files already live there), and change `scripts/migrate_completed_plan.py`'s `delete_plan()` to a `git mv` into `completed/`. Then make ONE mechanism authoritative and delete the contradictory prose/behaviors (no-legacy-code tolerance).
- **Anchor the invariant path-independently — two-layer enforcement (DECIDED, resolves Open Question #2).** No single hook can cover every path: a human typing a bare `gh pr merge` in a raw terminal fires **no** Claude Code hook and calls **no** cleanup script, and the merge-guard hook fires *before* the merge, so it can authorize but not carry out an after-merge action. The mechanism is therefore two layers, both invoking the ONE authoritative module (`scripts/migrate_completed_plan.py`):
  - **Layer 1 — primary merge-site migration (immediate, common path).** Fold the migration call into `scripts/post_merge_cleanup.py::main()`, which the `/do-merge` skill already runs after-merge with the slug in hand. Cover paths (a) `/do-merge`, (c) forked `/do-sdlc`, (d) PR-review merges. The migration call runs at the **top** of `main()`, BEFORE `cleanup_after_merge()` and its #1357 `blocked_by_session` early-return (line 73, `return 2`) — so a busy worktree never skips migration. This file is the static-invocation enforcement target: `grep -q "migrate_completed_plan" scripts/post_merge_cleanup.py`.
  - **Layer 2 — backstop reconciler reflection (path-independent, catches the raw-terminal case).** Add a `plan-migration-reconciler` entry to the reflections registry (`config/reflections.yaml`, `execution_type: function`, daily) whose callable is a **new** `reconcile_leaked_plans()` function in `scripts/migrate_completed_plan.py`. It sweeps `docs/plans/` root for plans whose tracking issue is CLOSED / PR MERGED and `git mv`s them into `completed/` with the rebase-retry push (see Race Conditions). This is the ONLY site that covers path (b) — the bare-terminal `gh pr merge` that fires no hook — and it self-heals anything Layer 1 misses within one cycle. `reconcile_leaked_plans()` is the shared evidence-gated sweep: **Tier 1's one-time historical sweep is a manual (dry-run-first) invocation of this same function**, so both the standing backstop and the one-time cleanup run identical authoritative code.
  - Do **not** rely on `_handle_merge_completion()` — it does not exist. Do **not** fold migration in *after* the #1357 busy-session guard.
- **Ship a regression test** that constructs a merged-PR scenario and asserts the plan is not left in `docs/plans/` root (Tier 0 acceptance).
- **Tier 1 script** imports ONLY `extract_tracking_issue()` from `migrate_completed_plan.py` for reference extraction (`from scripts.migrate_completed_plan import extract_tracking_issue`). It must NOT call `main()`, `validate_feature_doc()`, or `validate_feature_index()`: that path refuses any plan lacking an indexed `docs/features/*.md` doc, and most of the ~130 historical plans never had one, so reusing it would silently refuse the entire backlog. Tier 1 gates purely on issue/PR state, not on feature-doc presence. It uses `git mv` for history preservation; report mode prints `(plan, evidence, action)` rows.
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

- [ ] `tests/unit/test_migrate_completed_plan.py` — UPDATE: if the canonical behavior changes from delete→move, its assertions around `delete_plan()`/`unlink()` must flip to assert a `git mv` into `completed/`, and add the failure-preserves-plan case. If PM chooses to keep delete-semantics, add tests asserting the single authoritative behavior instead.
- [ ] New test file (CREATE): `tests/unit/test_plan_migration_invariant.py` (or integration equivalent) — the Tier 0 regression test asserting a merged PR's plan does not remain in `docs/plans/` root, AND a static-invocation assertion `assert "migrate_completed_plan" in Path("scripts/post_merge_cleanup.py").read_text()` (guards against prose-only regression, per Risk 1). Also assert Layer 1 runs migration BEFORE the #1357 `blocked_by_session` early-return (a busy-worktree fixture still migrates).
- [ ] New test coverage (CREATE) for `reconcile_leaked_plans()` (the shared Layer 2 / Tier 1 function): closed-issue plan → migrated, open-issue plan → skipped, no-reference → human-review list, already-migrated (source absent) → idempotent success, and the `plan-migration-reconciler` entry is present and well-formed in `config/reflections.yaml`.
- [ ] New test (CREATE) for the Tier 1 sweep script covering: closed-issue → migrate, open-issue → skip, no-reference → human-review list, `git mv` failure → plan preserved, and **a fixture plan with no `## Documentation` / indexed feature doc still migrates on closed-issue evidence** (guards against accidentally routing Tier 1 through `main()`/`validate_feature_doc()`).

No other existing tests reference the plans-root layout or the moved docs (grep of `tests/` for the duplicated filenames and presentation paths returned no production-test hits).

## Rabbit Holes

- **Rewriting git history to reclaim the 13MB.** `git mv` and deletion shrink the working tree, not the packed history. The acceptance criterion is `du -sh docs` (working tree), not repo size. Do not `filter-branch`/`filter-repo`.
- **Building a general-purpose docs-lifecycle framework.** Tier 0 needs ONE deterministic migration site plus a test, not a plugin architecture for doc reconcilers.
- **Auto-filing issues for every unresolvable plan (the #1543 trap).** Unresolvable plans go to a static human-review list in the PR body, not a flood of tracker issues.
- **Perfecting `completed/` as an archive.** Whether to prune `completed/` is an Open Question, not scope creep — leave it as-is unless PM says otherwise.
- **Reconstructing the exact history of every one of ~130 leaked plans.** The Tier 1 script gates on current issue/PR state; it does not need per-plan forensic archaeology.

## Risks

### Risk 1: Tier 0 root cause misidentified, leak regrows
**Impact:** Cleanup is repeated in three months.
**Mitigation:** Name the confirmed skipping paths in the PR; ship the regression test that fails if a merged PR's plan stays in root; prefer the merge-guard choke point (covers every path) over a per-skill fix. **Additionally, guard against prose-degradation (exactly how #1394's fix silently regressed when a deterministic script call was replaced by prose during a command→skill refactor): add a static-invocation assertion** — the Tier 0 test must assert `"migrate_completed_plan" in Path(<chosen_enforcement_file>).read_text()` (or an equivalent AST call-site check), so a future doc-only "fix" that drops the call fails CI immediately rather than silently reopening the leak. The regression test therefore covers both migration *logic* AND that the enforcement site still statically *invokes* it.

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

1. **Concurrent cross-machine merges racing on `git push` to `main`.** Two machines can each merge a PR and try to push the migration commit near-simultaneously; the second push is rejected non-fast-forward. **Mitigation:** wrap the after-merge migration commit in a rebase-retry loop — `git pull --rebase origin main && git push`, retried a bounded number of times on non-fast-forward rejection — so a losing push replays on top of the winner instead of aborting the migration.
2. **`git mv` is not idempotent.** The plan previously asserted migrating an already-migrated plan is a no-op; that is false. If the source path is already gone (a prior migration moved it) `git mv` exits non-zero, which would look like a migration failure and could re-trigger error handling. **Mitigation:** guard the move with an existence check — `if not plan_path.exists(): return success` when the source is absent and the destination under `completed/` is present, treat it as already-migrated (idempotent success), not an error.

All other operations are synchronous, single-process file-system and `git`/`gh` CLI invocations with no shared mutable state.

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Rewriting git history (`filter-branch`/`filter-repo`/`gc --aggressive`) to reclaim packed space. The acceptance target is working-tree size; history stays intact. An anti-criterion in Verification asserts no history-rewrite tooling appears in the change.
- [DESTRUCTIVE] Deleting `docs/plans/completed/` or any plan whose tracking issue is still OPEN. Tier 1 moves only closed/merged-evidence plans; the archive prune is an Open Question, defaulting to "keep."
- [EXTERNAL] Confirming whether any teammate still relies on `GEMINI.md` — needs a human answer before deletion; surfaced as an explicit recorded decision in the PR, not assumed.

## Update System

The Tier 0 mechanism has two enforcement sites, both propagating via the normal repo sync with **no new launchd job**:

- **Layer 1** edits `scripts/post_merge_cleanup.py` and `scripts/migrate_completed_plan.py` — plain repo files, synced by `/update` with no special step.
- **Layer 2** adds one `plan-migration-reconciler` entry to the reflections registry. The registry resolves in order `REFLECTIONS_YAML env → ~/Desktop/Valor/reflections.yaml → config/reflections.yaml` (`agent/reflection_scheduler.py`), and `install_worker.sh` copies the active registry to `config/reflections.yaml` at install time. Add the entry to **`config/reflections.yaml`** (the in-repo source of truth) AND propagate it to the vault copy `~/Desktop/Valor/reflections.yaml` on worker machines that use it, or the entry is invisible there. The already-installed `com.valor.reflection-worker` subprocess (`python -m reflections`) runs it on its next cycle — **no new launchd plist**; reload the worker (`./scripts/install_reflection_worker.sh`) to pick it up immediately. Add a task to `scripts/update/` docs only if a machine needs the vault-copy propagation automated; the in-repo fallback already covers dev machines.

Vault writes (Tier 4) are one-time and outside the update system.

## Agent Integration

No agent integration required. No new MCP server, `.mcp.json` entry, or bridge import is introduced. `valor-ingest` (Tier 4) is an existing CLI already on the agent's Bash surface. The merge-guard hook and `migrate_completed_plan.py` are internal pipeline machinery the agent already invokes via `gh pr merge` / `/do-merge`; the change re-anchors an existing invariant rather than adding a new agent-reachable capability.

## Documentation

### Feature Documentation (PR 1)
- [ ] Create `docs/features/plan-migration-invariant.md` describing the two-layer after-merge plan-migration behavior (Layer 1 merge-site in `post_merge_cleanup.py`, Layer 2 `plan-migration-reconciler` reflection), which merge paths each layer covers, the evidence-gate on issue state, and the regression + static-invocation tests that guard it.
- [ ] Add entry to `docs/features/README.md` index table.

### Repo Docs Cascade
- [ ] (PR 1) Resolve `docs/sdlc/do-merge.md` "Plan Migration" section and `.claude/skills-global/do-build/PR_AND_CLEANUP.md` Steps 8 to ONE documented behavior; remove the `_handle_merge_completion()` reference (function does not exist) or replace it with the real two-layer mechanism.
- [ ] (PR 2) Record the `docs/guides/` (evergreen how-to) vs `docs/research/` (dated investigation) placement rule in `docs/README.md` (Tier 2).
- [ ] (PR 1 and PR 2) Run `/do-docs` cascade so no inbound link points at a moved/deleted doc.

### Knowledge Base (PR 2)
- [ ] Create `~/work-vault/AI Valor Engels System/README.md` indexing the files **directly inside that one directory** — one line per file/subfolder with a short description, following the KB-section convention (`docs/conventions/knowledge-base-section.md`). "Complete" means every current entry in `AI Valor Engels System/` is listed; it is **not** a recursive index of the whole vault, and it is **not** the vault-root `_index.md` (which only links to it). Link it from `~/work-vault/_index.md`.

## Success Criteria

- [ ] Tier 0: the skipping merge paths are named in the PR; two enforcement layers ship over one authoritative module — Layer 1 (`post_merge_cleanup.py`) for `/do-merge` and Claude-Code-mediated merges, Layer 2 (`plan-migration-reconciler` reflection) covering the raw-terminal `gh pr merge` that fires no hook; a test fails if a merged PR's plan remains in `docs/plans/` root, and a static-invocation assertion confirms `post_merge_cleanup.py` still calls `migrate_completed_plan`.
- [ ] Tier 0: the delete-vs-migrate contradiction across `migrate_completed_plan.py`, `PR_AND_CLEANUP.md`, and `docs/sdlc/do-merge.md` is resolved to one documented behavior and cascaded by `/do-docs`.
- [ ] Tier 1: after merge, zero files in `docs/plans/` root reference a CLOSED issue or MERGED PR (re-run the tier-1 script in report mode to verify); the evidence report is attached to the PR.
- [ ] Tier 2: exactly one copy of each of the three duplicated docs exists — a **filename** search excluding plan docs returns a single path (`git ls-files -- 'docs/**/<name>.md' ':(exclude)docs/plans/**' | wc -l` == 1; a content grep is unreliable here because the canonical docs do not contain their own slug string, so only plan-doc references would match); the guides-vs-research rule is recorded in `docs/README.md`.
- [ ] Tier 3: `docs/presentations/`, the listed one-off reports, and resolved stragglers are gone; `du -sh docs` drops by at least 5MB; GEMINI.md and CHANGELOG.md each have an explicit recorded decision in the PR.
- [ ] Size reclaim recorded explicitly: capture `du -sh docs` **before** and **after** the Tier 3/4 sweep and paste both totals in the PR (correctness Tiers 0/1 reclaim zero bytes by design — only Tier 3/4 shrink the tree).
- [ ] Tier 4: moved files exist in the vault, each binary has a `valor-ingest` `.md` sidecar, `~/work-vault/AI Valor Engels System/README.md` exists with a complete index, and vault root `_index.md` links it; PR includes the proof artifact (listing + README content).
- [ ] No inbound link in the repo points at a moved or deleted doc (docs cascade run and clean).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it never builds directly. Tier 0 (mechanism + test) is the critical path and should be built and reviewed first, since it prevents regrowth and is dogfooded by this plan's own merge. Tiers 1–4 can proceed in parallel once Tier 0's canonical behavior is decided.

### Delivery: two PRs

Tier 0 (the pipeline-invariant bugfix + regression test) and Tiers 1–4 (the one-time docs janitorial sweep) share **no code, test, or review surface**. The split is enforced in the task DAG (see Step by Step Tasks: tasks carry an explicit **PR:** tag, and every PR-2 task depends on the sentinel `pr1-merged` gate, not merely on `build-tier0-mechanism`). Ship as two PRs on **separate branches**:

1. **PR 1 — Tier 0 alone** (`type: bug`). Small, ships fast, stops the leak. Contains `migrate_completed_plan.py` changes (including the shared `reconcile_leaked_plans()` function), the Layer 1 wiring in `post_merge_cleanup.py`, the Layer 2 `plan-migration-reconciler` reflection entry, the regression + static-invocation tests, the feature doc, and the doc-contradiction resolution.
2. **PR 2 — Tiers 1–4 one-time sweep** (`type: chore`). Branched from `main` **after PR 1 merges** (so it inherits the fixed pipeline and the `reconcile_leaked_plans()` function it calls). Contains the Tier 1 dry-run-first invocation + report, the Tier 2 de-dup, Tier 3 deletions, and Tier 4 vault relocation + indexes.

**Plan-doc migration timing (avoids the git resurrection / rename conflict).** Both PRs carry `docs/plans/stale-docs-tiered-cleanup.md` in root. The Tier 0 invariant is **evidence-gated on the tracking issue being CLOSED**, and issue #1900 stays OPEN until PR 2 merges with `Closes #1900`. Therefore:
- Merging **PR 1** does NOT migrate this plan doc (issue #1900 still open → the evidence gate correctly skips it). The file stays in root on `main`, identical to what PR 2's branch carries — no divergence, no resurrection, no rename conflict.
- The backstop reconciler running between the two merges also skips this plan (issue still open).
- Only **PR 2's** merge (which closes #1900) makes this plan eligible; PR 2's own Layer 1 merge-site step migrates `stale-docs-tiered-cleanup.md` → `completed/`. Because PR 2 is the last to merge, nothing resurrects it.

The invariant is therefore dogfooded by its own evidence-gate logic (correctly declining to migrate an open-issue plan), and demonstrated by the regression test (red→green) — **not** by moving this plan's doc mid-flight at PR 1's merge.

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

The DAG is partitioned into two PRs. Every **PR 2** task depends on the `pr1-merged` sentinel gate (PR 1 merged to `main`), not merely on `build-tier0-mechanism` — Tiers 1–4 need Tier 0 *landed and merged*, so they call the shipped `reconcile_leaked_plans()` and branch from a `main` that already carries the fixed pipeline.

#### PR 1 — Tier 0 (branch `session/stale-docs-tier0`)

##### 1. Ship the Tier 0 path-independent invariant
- **Task ID**: build-tier0-mechanism
- **PR**: 1
- **Depends On**: none
- **Validates**: tests/unit/test_plan_migration_invariant.py (create), tests/unit/test_migrate_completed_plan.py (update)
- **Assigned To**: tier0-builder
- **Agent Type**: builder
- **Parallel**: false
- Confirm which merge paths skip migration (manual `gh pr merge`, forked `/do-sdlc`, cross-machine, PR-review merges).
- Decide delete-vs-move with PM; make `migrate_completed_plan.py` the single authoritative mechanism (recommend `git mv` into `completed/`).
- Wire **Layer 1**: call migration at the top of `scripts/post_merge_cleanup.py::main()`, BEFORE `cleanup_after_merge()` and the #1357 `blocked_by_session` early-return. Do NOT use the nonexistent `_handle_merge_completion()`.
- Add **Layer 2**: a `reconcile_leaked_plans()` function in `scripts/migrate_completed_plan.py` (evidence-gated sweep + rebase-retry push) and a `plan-migration-reconciler` entry in `config/reflections.yaml`.
- Write the regression test that fails when a merged PR's plan stays in root, and add the static-invocation assertion that `scripts/post_merge_cleanup.py` still calls `migrate_completed_plan` (guards against a future prose-only "fix" silently dropping the call, per #1394).

##### 2. Validate Tier 0
- **Task ID**: validate-tier0
- **PR**: 1
- **Depends On**: build-tier0-mechanism
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the test fails on a deliberately-leaked plan (red-state) and passes after the fix.

##### 3. Tier 0 feature doc + contradiction resolution
- **Task ID**: document-tier0
- **PR**: 1
- **Depends On**: build-tier0-mechanism
- **Assigned To**: docs-cascade
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/plan-migration-invariant.md` + `docs/features/README.md` entry describing both enforcement layers.
- Resolve the `do-merge.md` / `PR_AND_CLEANUP.md` delete-vs-move contradiction wording; remove the `_handle_merge_completion()` reference. Run `/do-docs` for the Tier 0 surface.

##### GATE: PR 1 merged
- **Task ID**: pr1-merged
- **PR**: 1→2 boundary
- **Depends On**: validate-tier0, document-tier0
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
- Invoke the shipped `reconcile_leaked_plans()` in dry-run/report mode first; review the `(plan, evidence, action)` report; then run the destructive pass. Route unresolvable plans to a human-review list in the PR body.

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
| Tier 0: primary merge-site statically invokes migration | `grep -q "migrate_completed_plan" scripts/post_merge_cleanup.py; echo $?` | output contains 0 |
| Tier 0: backstop reconciler is registered | `grep -q "plan-migration-reconciler" config/reflections.yaml; echo $?` | output contains 0 |
| Tier 0: backstop callable exists in authoritative module | `grep -q "def reconcile_leaked_plans" scripts/migrate_completed_plan.py; echo $?` | output contains 0 |
| No history-rewrite tooling | `git grep -rn "filter-branch\|filter-repo" -- scripts/ .claude/` | match count == 0 |

## Critique Results

<!-- Round 1 (/do-plan-critique war room, 2026-07-05): NEEDS REVISION (1 blocker, 6 concerns) — addressed below. -->
<!-- Round 2 (re-critique, 2026-07-05): NEEDS REVISION (2 blockers, 1 concern, 1 nit) — addressed in "Round 2 resolution" below. -->

**Round 2 resolution (this revision — 2 blockers + 1 concern + 1 nit addressed):**

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

**RESOLVED this revision (was Open Question #2 — enforcement site):** Two-layer enforcement over one authoritative module — Layer 1 merge-site migration in `scripts/post_merge_cleanup.py` (runs before the #1357 guard) for Claude-Code-mediated merges, plus Layer 2 `plan-migration-reconciler` reflection as the path-independent backstop for the raw-terminal `gh pr merge` that fires no hook. The merge-guard hook is rejected as a sole mechanism (pre-merge, and absent entirely for raw-terminal merges). See Technical Approach and the Data Flow decision block.
