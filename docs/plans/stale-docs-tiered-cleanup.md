---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-07-05
tracking: https://github.com/tomcounsell/ai/issues/1900
last_comment_id:
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

**Enforcement-site candidates (the Tier 0 design decision):**
- **(A) merge-guard hook** — covers every path (all merges go through `gh pr merge`), but runs pre-merge; it can *verify/authorize* but a after-merge action needs a follow-through step.
- **(B) `post_merge_cleanup.py` / `cleanup_after_merge()`** — already runs after-merge with the slug in hand; a natural place to also migrate the plan, but only invoked on paths that call it.
- **(C) a reconciler reflection** — sweeps root plans whose issue is CLOSED. Institutionalizes the leak rather than preventing it; acceptable only as a backstop, not the primary fix.

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

- **Tier 0 — merge-site migration invariant.** A single deterministic mechanism that migrates a merged PR's plan out of `docs/plans/` root on **every** merge path, plus a regression test that fails if a merged PR's plan remains in root. Resolve the three-way delete/move contradiction to ONE documented behavior and cascade it.
- **Tier 1 — evidence-gated leaked-plan migration.** A dry-run-first script that, for each root plan, extracts issue/PR references, queries `gh` for state, and migrates only when the issue is CLOSED / PR MERGED. Unresolvable plans go to a human-review list in the PR, never moved blind. Emits a machine-readable report as the proof artifact.
- **Tier 2 — de-duplication.** Merge each diverged research/guides pair into one canonical file, delete the other, fix the (minimal) inbound links, and record the guides-vs-research placement rule in `docs/README.md`.
- **Tier 3 — remove one-off decks and point-in-time reports.** Delete outright what has no lasting value; route business-valuable decks to Tier 4. Surface GEMINI.md and CHANGELOG.md as explicit keep-or-delete decisions.
- **Tier 4 — vendor/business docs to the knowledge base, indexed.** Move the PDF and the strategy decks to the vault, `valor-ingest` each binary, create the missing `~/work-vault/AI Valor Engels System/README.md` file index, and link it from vault root `_index.md`. Prove with executed commands (listing + README content) pasted into the PR.

### Flow

PR merges → merge-site invariant runs (Tier 0) → plan leaves root deterministically → Tier 1 script sweeps the historical backlog with per-file evidence → Tier 2/3 collapse duplicates and delete one-offs → Tier 4 relocates business/vendor docs to the indexed vault → docs cascade confirms zero dangling inbound links.

### Technical Approach

- **Decide the canonical behavior first** (delete vs move-to-`completed/`). Recommendation to confirm with PM: **move to `completed/`** (git history alone is a poor archive for grep/context, and 138 files already live there), and change `scripts/migrate_completed_plan.py`'s `delete_plan()` to a `git mv` into `completed/`. Then make ONE mechanism authoritative and delete the contradictory prose/behaviors (no-legacy-code tolerance).
- **Anchor the invariant path-independently.** Confirm the root cause in build (which paths skip migration), then place enforcement so it covers manual `gh pr merge` too. Leading candidate: have the merge-guard hook (the universal choke point) verify/record the plan slug and drive the migration through a after-merge follow-through (or fold migration into `post_merge_cleanup.py` and make the merge-guard require it). Do **not** rely on `_handle_merge_completion()` — it does not exist.
- **Ship a regression test** that constructs a merged-PR scenario and asserts the plan is not left in `docs/plans/` root (Tier 0 acceptance).
- **Tier 1 script** reuses `extract_tracking_issue()` from `migrate_completed_plan.py` for reference extraction; `git mv` for history preservation; report mode prints `(plan, evidence, action)` rows.
- **Tier 4 is outside the repo diff.** Acceptance is executed-command proof (directory listing + README content), per the issue's executable-criteria mandate.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] The Tier 1 script's `gh` calls must handle timeouts / missing references without aborting the whole run — a plan with an unparseable reference is routed to the human-review list, logged, and the sweep continues. Test asserts a malformed plan does not crash the batch.
- [ ] `migrate_completed_plan.py` `git mv` failure (destination exists, dirty tree) must leave the plan in place and surface a non-zero exit — test asserts the plan is NOT lost on failure (the #1394 invariant, re-anchored).

### Empty/Invalid Input Handling
- [ ] Tier 1 script on a plan with no extractable issue/PR reference → routed to human-review list, not moved. Test covers the empty-reference case.
- [ ] Tier 0 invariant when a PR has no associated plan file (hotfix with no plan) → no-op, no error. Test covers the no-plan case.

### Error State Rendering
- [ ] Tier 1 report clearly distinguishes `migrated`, `skipped (issue open)`, and `needs-human-review` rows; the report is the user-visible output and must render all three states, not just successes.

## Test Impact

- [ ] `tests/unit/test_migrate_completed_plan.py` — UPDATE: if the canonical behavior changes from delete→move, its assertions around `delete_plan()`/`unlink()` must flip to assert a `git mv` into `completed/`, and add the failure-preserves-plan case. If PM chooses to keep delete-semantics, add tests asserting the single authoritative behavior instead.
- [ ] New test file (CREATE): `tests/unit/test_plan_migration_invariant.py` (or integration equivalent) — the Tier 0 regression test asserting a merged PR's plan does not remain in `docs/plans/` root, covering the manual-`gh pr merge` path.
- [ ] New test (CREATE) for the Tier 1 sweep script covering: closed-issue → migrate, open-issue → skip, no-reference → human-review list, `git mv` failure → plan preserved.

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
**Mitigation:** Name the confirmed skipping paths in the PR; ship the regression test that fails if a merged PR's plan stays in root; prefer the merge-guard choke point (covers every path) over a per-skill fix.

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

No race conditions identified. All operations are synchronous, single-process, file-system and `git`/`gh` CLI invocations. The Tier 0 invariant runs after-merge on `main` and is idempotent (migrating an already-migrated plan is a no-op).

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Rewriting git history (`filter-branch`/`filter-repo`/`gc --aggressive`) to reclaim packed space. The acceptance target is working-tree size; history stays intact. An anti-criterion in Verification asserts no history-rewrite tooling appears in the change.
- [DESTRUCTIVE] Deleting `docs/plans/completed/` or any plan whose tracking issue is still OPEN. Tier 1 moves only closed/merged-evidence plans; the archive prune is an Open Question, defaulting to "keep."
- [EXTERNAL] Confirming whether any teammate still relies on `GEMINI.md` — needs a human answer before deletion; surfaced as an explicit recorded decision in the PR, not assumed.

## Update System

The Tier 0 enforcement mechanism ships inside the repo (hook, script, and/or skill-addendum edits) and propagates via the normal repo sync — `/update` needs no special step **if** enforcement lives in a hook or an existing script. **Conditional:** if the chosen mechanism is a new standing reflection (option C backstop) requiring a launchd job, add its install to the reflection-worker wiring and note it here; the plan's recommendation is a merge-site invariant precisely to avoid a new scheduled job. Vault writes (Tier 4) are one-time and outside the update system. Confirm at build time which enforcement site was chosen and update this section to the concrete answer.

## Agent Integration

No agent integration required. No new MCP server, `.mcp.json` entry, or bridge import is introduced. `valor-ingest` (Tier 4) is an existing CLI already on the agent's Bash surface. The merge-guard hook and `migrate_completed_plan.py` are internal pipeline machinery the agent already invokes via `gh pr merge` / `/do-merge`; the change re-anchors an existing invariant rather than adding a new agent-reachable capability.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/plan-migration-invariant.md` describing the single authoritative after-merge plan-migration behavior, which merge paths it covers, and the regression test that guards it.
- [ ] Add entry to `docs/features/README.md` index table (also required so this very plan can migrate cleanly at its own merge — the Tier 0 mechanism is dogfooded on `stale-docs-tiered-cleanup.md`).

### Repo Docs Cascade (Tier 0 contradiction resolution)
- [ ] Resolve `docs/sdlc/do-merge.md` "Plan Migration" section and `.claude/skills-global/do-build/PR_AND_CLEANUP.md` Steps 8 to ONE documented behavior; remove the `_handle_merge_completion()` reference (function does not exist) or replace it with the real after-merge mechanism.
- [ ] Record the `docs/guides/` (evergreen how-to) vs `docs/research/` (dated investigation) placement rule in `docs/README.md` (Tier 2).
- [ ] Run `/do-docs` cascade so no inbound link points at a moved/deleted doc.

### Knowledge Base
- [ ] Create `~/work-vault/AI Valor Engels System/README.md` with a complete file index (Tier 4); link it from `~/work-vault/_index.md`.

## Success Criteria

- [ ] Tier 0: the skipping merge paths are named in the PR; one authoritative enforcement mechanism ships covering manual `gh pr merge` as well as `/do-merge`; a test fails if a merged PR's plan remains in `docs/plans/` root.
- [ ] Tier 0: the delete-vs-migrate contradiction across `migrate_completed_plan.py`, `PR_AND_CLEANUP.md`, and `docs/sdlc/do-merge.md` is resolved to one documented behavior and cascaded by `/do-docs`.
- [ ] Tier 1: after merge, zero files in `docs/plans/` root reference a CLOSED issue or MERGED PR (re-run the tier-1 script in report mode to verify); the evidence report is attached to the PR.
- [ ] Tier 2: exactly one copy of each of the three duplicated docs exists (`git grep <filename>` returns a single path under `docs/`); the guides-vs-research rule is recorded in `docs/README.md`.
- [ ] Tier 3: `docs/presentations/`, the listed one-off reports, and resolved stragglers are gone; `du -sh docs` drops by at least 5MB; GEMINI.md and CHANGELOG.md each have an explicit recorded decision in the PR.
- [ ] Tier 4: moved files exist in the vault, each binary has a `valor-ingest` `.md` sidecar, `~/work-vault/AI Valor Engels System/README.md` exists with a complete index, and vault root `_index.md` links it; PR includes the proof artifact (listing + README content).
- [ ] No inbound link in the repo points at a moved or deleted doc (docs cascade run and clean).
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

The lead agent orchestrates; it never builds directly. Tier 0 (mechanism + test) is the critical path and should be built and reviewed first, since it prevents regrowth and is dogfooded by this plan's own merge. Tiers 1–4 can proceed in parallel once Tier 0's canonical behavior is decided.

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

### 1. Confirm Tier 0 root cause and decide canonical behavior
- **Task ID**: build-tier0-mechanism
- **Depends On**: none
- **Validates**: tests/unit/test_plan_migration_invariant.py (create), tests/unit/test_migrate_completed_plan.py (update)
- **Assigned To**: tier0-builder
- **Agent Type**: builder
- **Parallel**: false
- Confirm which merge paths skip migration (manual `gh pr merge`, forked `/do-sdlc`, cross-machine, PR-review merges).
- Decide delete-vs-move with PM; make `migrate_completed_plan.py` the single authoritative mechanism (recommend `git mv` into `completed/`).
- Anchor the invariant path-independently (merge-guard choke point and/or `post_merge_cleanup.py`); do NOT use the nonexistent `_handle_merge_completion()`.
- Write the regression test that fails when a merged PR's plan stays in root.

### 2. Validate Tier 0
- **Task ID**: validate-tier0
- **Depends On**: build-tier0-mechanism
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm the test fails on a deliberately-leaked plan (red-state) and passes after the fix.

### 3. Tier 1 evidence-gated sweep
- **Task ID**: build-tier1-sweep
- **Depends On**: build-tier0-mechanism
- **Assigned To**: tier1-builder
- **Agent Type**: builder
- **Parallel**: true
- Dry-run-first script: extract references, query `gh`, `git mv` closed/merged plans, route unresolvable to a human-review list, emit `(plan, evidence, action)` report.

### 4. Tier 2/3/4 docs reorganization
- **Task ID**: build-docs-reorg
- **Depends On**: build-tier0-mechanism
- **Assigned To**: docs-builder
- **Agent Type**: builder
- **Parallel**: true
- Tier 2: merge diverged pairs to one canonical file each; fix inbound links.
- Tier 3: delete one-off decks/reports; surface GEMINI.md/CHANGELOG.md decisions.
- Tier 4: `git mv`/move business+vendor docs to vault; `valor-ingest` each binary; capture executed-command proof.

### 5. Documentation cascade + indexes
- **Task ID**: document-feature
- **Depends On**: build-tier0-mechanism, build-docs-reorg
- **Assigned To**: docs-cascade
- **Agent Type**: documentarian
- **Parallel**: false
- Resolve the do-merge/PR_AND_CLEANUP contradiction wording; record guides-vs-research rule in `docs/README.md`; create `docs/features/plan-migration-invariant.md` + README entry; create vault README and link from `_index.md`; run `/do-docs`.

### 6. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-tier0, build-tier1-sweep, build-docs-reorg, document-feature
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify every Success Criterion, including `du -sh docs` delta, single-copy `git grep`, executed Tier 4 proof, and a clean docs cascade.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Tier 2: swot single copy | `git grep -l claude-code-feature-swot -- docs/ \| grep -v completed \| wc -l` | output contains 1 |
| Tier 2: sdk-req single copy | `git grep -l agent-sdk-replacement-requirements -- docs/ \| grep -v completed \| wc -l` | output contains 1 |
| Tier 2: ruflo single copy | `git grep -l ruflo-deep-dive -- docs/ \| grep -v completed \| wc -l` | output contains 1 |
| Tier 3: presentations gone | `test -d docs/presentations; echo $?` | output contains 1 |
| Tier 3: GEMINI decided (gone) | `test -e GEMINI.md; echo $?` | output contains 1 |
| Tier 4: vendor PDF out of repo | `test -e docs/media/anthropic-skills-guide.pdf; echo $?` | output contains 1 |
| Tier 4: vault README exists | `test -f "$HOME/work-vault/AI Valor Engels System/README.md"; echo $?` | output contains 0 |
| Tier 0: migration invariant test present | `ls tests/unit/test_plan_migration_invariant.py tests/integration/test_plan_migration_invariant.py 2>/dev/null \| wc -l` | output > 0 |
| Tier 0: no phantom handler reference | `grep -rn "_handle_merge_completion" docs/ .claude/` | match count == 0 |
| No history-rewrite tooling | `git grep -rn "filter-branch\|filter-repo" -- scripts/ .claude/` | match count == 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Canonical migration behavior:** delete the plan (git history as archive) or move to `docs/plans/completed/`? Recommendation: move — but this contradicts `migrate_completed_plan.py`'s current `unlink()`. Confirm before build changes the script.
2. **Enforcement site:** merge-guard hook (covers every path, but pre-merge), `post_merge_cleanup.py` (after-merge, but only on paths that call it), or a reconciler backstop? Prefer a merge-site invariant; is a reconciler acceptable as belt-and-suspenders?
3. **Prune `docs/plans/completed/`?** It holds 138 files (5.4MB). Keep as archive (default) or prune, given git history already preserves content?
4. **GEMINI.md and CHANGELOG.md:** delete both, or is either still in use? (GEMINI.md is [EXTERNAL] — needs a human answer.)
