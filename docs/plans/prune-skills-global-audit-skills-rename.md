---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-24
tracking: https://github.com/tomcounsell/ai/issues/2337
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-24T11:13:52Z
---

# Prune stale skills from skills-global; make audit-skills rename and skillify merge durable

## Problem

`.claude/skills-global/` is the canonical source for the skill fleet: every directory there is hardlink-synced to `~/.claude/skills/` on every machine by the `/update` pipeline (`scripts/update/hardlinks.py`). On 2026-07-24 the operator ran a usage audit and made keep/remove/rename/merge decisions at the *user* level (`~/.claude/skills/`), and the next `/update` run promptly reverted most of them — because user-level is the wrong layer. The repo is the source of truth.

**Current behavior:**
- `skills-global/` contains 50 skills including 7 confirmed-dead ones (`analyze`, `claude-standards`, `deepen`, `observability`, `do-oop-audit`, `pthread`, `tdd`), a stale-named `do-skills-audit`, and a redundant `skillify`.
- The operator renamed `do-skills-audit` → `audit-skills` and merged `skillify` into `new-skill` at user level; the sync reverted both. One fragment survived by accident: the edit to `.claude/skills-global/new-skill/WORKFLOW_TEMPLATE.md` wrote *through* a still-intact hardlink into the repo working tree, where it now sits **modified-uncommitted** referencing a `SESSION_CAPTURE.md` that does not exist in the repo (verified: `git status` shows `M .claude/skills-global/new-skill/WORKFLOW_TEMPLATE.md`).

**Desired outcome:**
- `skills-global/` contains only the skills the fleet keeps.
- `audit-skills` is the canonical name everywhere it is *actively* referenced (dir, frontmatter, script self-checks, live reflection wiring, active docs, tests).
- `new-skill` owns the session-capture flow (`SESSION_CAPTURE.md`), and the dangling `WORKFLOW_TEMPLATE.md` edit lands as part of that merge.
- `RENAMED_REMOVALS` sweeps every stale user-level copy off every machine on the next `/update`.
- The working tree is clean.

## Freshness Check

**Baseline commit:** `f5867c876bbb700b16f7b8e6274147ae145b0e79`
**Issue filed at:** 2026-07-24T10:32:15Z
**Disposition:** Unchanged

The issue was filed today. `git log --since="2026-07-24T10:32:15Z"` returns no commits on main since filing. The dangling working-tree edit the issue predicts is present exactly as described (`git status` → `M .claude/skills-global/new-skill/WORKFLOW_TEMPLATE.md`).

**File:line / claim re-verification against current HEAD:**
- `.claude/skills-global/` directory listing — confirmed: all 7 prune targets, `do-skills-audit`, and `skillify` present; the 8 explicit-keep skills present.
- `scripts/update/hardlinks.py:14` `RENAMED_REMOVALS` list — confirmed present; entry format is `(kind, old_name)` tuples; `("commands", "pthread.md")` already present (the old command), so pruning the `pthread` *skill* needs a new `("skills", "pthread")` entry.
- `.claude/skills-global/do-skills-audit/scripts/audit_skills.py` self-references — confirmed: `INFRA_SKILLS` frozenset (line 87) includes `"do-skills-audit"`; `is_auditor = dir_name == "do-skills-audit"` (line 1033); path-depth docstring (line 50); rule-inventory docstring (line 564); `FORK_SKILLS` frozenset (line 100) includes `"pthread"`.
- `reflections/audits/skills_audit.py:55-59` — confirmed: resolves the audit script by the literal path `.claude/skills-global/do-skills-audit/scripts/audit_skills.py`. **This is live reflection wiring — renaming the dir without updating this path breaks the skills-audit reflection.**
- Verify-candidate wiring re-checked (see Solution). Both resolve to KEEP.

**Cited sibling issues/PRs re-checked:** #1883 (open) is the broad skills-architecture audit; this issue executes a narrow operator-confirmed subset. #2065/#2189, #2079/#2214 established the `RENAMED_REMOVALS` orphan-sweep mechanism and its inode-guard invariants — this change respects them.

**Active plans in `docs/plans/` overlapping this area:** `docs/plans/skills-architecture-audit.md` (the open #1883 plan) references `do-skills-audit`. It is a separate, comprehensive follow-up; this plan does not modify it (see Open Question 1).

## Prior Art

- **#1883 (open)** — Skills architecture audit. Broad read-only disposition pass over all skills. This issue executes a narrow, operator-confirmed subset; #1883 remains the comprehensive follow-up.
- **#1319 (closed)** — "Add 6 new skills: ontologies, grill-me, deepen, observability, zoom-out, tdd." **Origin of 3 of the 7 prune targets** — `deepen`, `observability`, and `tdd` were added speculatively by this issue alongside `ontologies`/`grill-me`/`zoom-out` (kept). This prune removes the three from that batch that never earned a dispatch. Confirms the prunes are not blind deletions of unknown-provenance skills.
- **#1894 / PR #1894 (merged)** — "Renovate skill fleet: descriptions, progressive disclosure, rot repair (60 skills)." The fleet-wide frontmatter-migration precedent this rename/merge follows; `skillify`'s current `user-invocable: true` + model-invocable description (no `disable-model-invocation`) was normalized here, which is exactly the invocation behavior BLOCKER-2 must preserve after the merge into `new-skill`.
- **#2065 / PR #2189** — Precedent for sweeping orphan skill hardlinks via `RENAMED_REMOVALS`.
- **#2079 / PR #2214** — `RENAMED_REMOVALS` enforcement and sync invariants (the inode-guard guardrails this change respects).
- **#1416 / #1618** — skill-move doc drift; motivates the doc-sweep acceptance criterion.
- **#1783 / PR #1806** — repo-agnostic generalization of global skills; preserved — nothing here re-couples a skill to this repo.

## Research

No relevant external findings — this is a purely internal skill-tree and sync-mechanism change with no external libraries, APIs, or ecosystem patterns involved.

## Data Flow

The sync mechanism this plan operates on:

1. **Source of truth**: `.claude/skills-global/{skill}/` in this repo.
2. **`/update` run** (`scripts/update/hardlinks.py::sync_claude_dirs`): hardlinks each `skills-global/` dir into `~/.claude/skills/`. A user-level file whose inode has diverged from source is replaced; a user-level dir with no matching source (an *orphan*) is deleted **only if** listed in `RENAMED_REMOVALS`, and only when the inode guard confirms it is a genuine orphan (not a foreign same-named skill).
3. **Propagation constraint**: on non-authoring machines, `git pull` gives renamed/deleted files fresh inodes, so the `RENAMED_REMOVALS` inode guard sees genuine orphans and sweeps them. On the *authoring* machine, where the rename happened as a local working-tree change, inodes may be preserved and the guard may keep the old user-level dir — that one machine may need its user-level `do-skills-audit/` / `skillify/` / pruned dirs removed by hand once (documented as an EXTERNAL No-Go).
4. **Live consumer of the renamed skill**: `reflections/audits/skills_audit.py` resolves and invokes `audit_skills.py` by dir-name path every run — the rename must update this resolver in the same commit.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM (scope alignment on doc-sweep boundary), code reviewer (rename completeness + sync-invariant safety)

**Interactions:**
- PM check-ins: 1-2 (resolve the doc-sweep-scope and taxonomy open questions)
- Review rounds: 1 (verify no live reference to the old name survives, sync-invariant tests pass)

This is mechanical rename + delete work. The bottleneck is completeness (catching every *live* reference to the renamed skill, especially the reflection resolver) and drawing the doc-sweep boundary correctly — not coding time.

## Prerequisites

No prerequisites — this work has no external dependencies. All changes are to repo-tracked files; the `/update` propagation is verified after merge, not during build.

## Solution

### Key Elements

- **Rename `do-skills-audit` → `audit-skills`**: directory, frontmatter `name`, all *functional* script self-references (`INFRA_SKILLS`, `is_auditor`, docstrings), the live reflection resolver, active docs, and tests. Add `("skills", "do-skills-audit")` to `RENAMED_REMOVALS`.
- **Merge `skillify` → `new-skill`**: create `new-skill/SESSION_CAPTURE.md` (byte-faithful to `skillify/SKILL.md`'s body apart from the retitle and the `../new-skill/WORKFLOW_TEMPLATE.md` → `WORKFLOW_TEMPLATE.md` link fix), add a routing line + trigger phrases to `new-skill/SKILL.md`, keep the already-in-tree `WORKFLOW_TEMPLATE.md` edit, delete `skillify/`, add `("skills", "skillify")` to `RENAMED_REMOVALS`.
- **Prune 7 dead skills**: delete `analyze`, `claude-standards`, `deepen`, `observability`, `do-oop-audit`, `pthread`, `tdd` dirs; add a `RENAMED_REMOVALS` entry for each; clean up their incidental fleet-side references (see Technical Approach).
- **Keep the 2 verify-candidates** (`do-discover-paths`, `do-investigation-issue`) — both are wired; decision recorded below.

### Verify-then-prune candidates — resolution: KEEP BOTH

The issue flagged `do-discover-paths` and `do-investigation-issue` for verification before pruning. Both have live wiring and are **kept**:

**`do-discover-paths` — KEEP.** Wired into the BYOB test-discovery pipeline and the agent persona:
- `.claude/skill-context/do-discover-paths.md` (dedicated repo-specific context file, 1852 bytes).
- `config/personas/segments/tools.md:51` — the agent persona names it as a skill that works out of the box.
- `scripts/update/mcp_byob.py:99`, `tools/happy_path_schema.py:4` — BYOB discovery stage references it by name.
- `.claude/skill-context/computer-use.md:150` — cross-referenced by computer-use context.
- `agent/hooks/pre_tool_use.py:59` — named as an intentionally-ignored skill (documented exclusion, not absence of wiring).

**`do-investigation-issue` — KEEP.** Wired into the Teammate persona as an issue-filing surface:
- `.claude/commands/roles/prime-teammate-role.md` lines 18, 27, 53 — the Teammate role is instructed to run `/do-investigation-issue` for unverified anomalies.

### Flow

`/update` run → `sync_claude_dirs()` reads `skills-global/` → hardlinks `audit-skills/` fresh, finds no source for `do-skills-audit/` / `skillify/` / 7 pruned dirs → `RENAMED_REMOVALS` inode guard confirms orphans → deletes them from `~/.claude/skills/` → fleet converges: only kept skills remain, `audit-skills/` hardlinked to source.

### Technical Approach

**1. Rename `do-skills-audit` → `audit-skills`.**
- `git mv .claude/skills-global/do-skills-audit .claude/skills-global/audit-skills`.
- Frontmatter `name: audit-skills` in `audit-skills/SKILL.md`.
- **Sweep the auditor's own SKILL.md body (BLOCKER-1 fix).** After the `git mv`, `audit-skills/SKILL.md` still carries four live `do-skills-audit` self-references the frontmatter change does NOT touch: the `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py $ARGUMENTS` invocation (line 31), the `/do-skills-audit` command mention (line 35), the `--skill do-skills-audit` first-check line (line 78), and the husk-cleanup invocation (line 99). Rewrite all four to `audit-skills`. These refs are self-exempt from coupling rules 13/21 (the auditor describes its own signals) but are NOT exempt from the Verification grep — leaving any of them makes the `No active old-name refs` check unsatisfiable, which is precisely what the re-critique caught.
- `audit_skills.py`: replace `"do-skills-audit"` in `INFRA_SKILLS` (line 87) with `"audit-skills"`; change `is_auditor = dir_name == "audit-skills"` (line 1033); update the path-depth docstring (line 50) and rule-inventory docstring (line 564). Confirm `--json --no-sync` still exits 0 and does not flag itself.
- `sync_best_practices.py:8` path comment → `audit-skills`.
- **`reflections/audits/skills_audit.py:47-59` — dual-name resolver (CONCERN-3 fix).** The resolver must resolve `audit-skills/scripts/audit_skills.py` for THIS repo **and** retain a `do-skills-audit` fallback so an un-migrated foreign repo that vendors the skill project-locally under the old name keeps auditing (a single-name switch would silently no-op its skills-audit reflection — no dual-name window). Iterate the cartesian product of names `("audit-skills", "do-skills-audit")` × roots `("skills-global", "skills")`, returning the first path that exists; default the not-found return to the canonical `skills-global/audit-skills/...` path. The one remaining `do-skills-audit` literal here is an intentional forward-compat fallback — mark it with a comment and exclude this file from the Verification grep. **This is still the break-the-reflection-if-missed change for THIS repo.**
- `RENAMED_REMOVALS`: add `("skills", "do-skills-audit")`.

**2. Merge `skillify` → `new-skill`.**
- Create `.claude/skills-global/new-skill/SESSION_CAPTURE.md` from `skillify/SKILL.md`'s body. Retitle from `# Skillify` and fix the sole relative link `../new-skill/WORKFLOW_TEMPLATE.md` → `WORKFLOW_TEMPLATE.md` (now local). Keep the body otherwise byte-faithful (issue constraint).
- `new-skill/SKILL.md`: add a "When to load sub-files" row routing session-capture requests to `SESSION_CAPTURE.md`; add trigger phrases (`skillify`, `capture this as a skill`, `save this workflow`) to the `description`. Keep the merged description within the rule-5 budget (target ≤200 chars, hard cap 1024).
- **Make `new-skill` model-invocable (BLOCKER-2 fix).** `skillify` today ships `user-invocable: true` with NO `disable-model-invocation`, so its triggers fire on a bare utterance ("skillify", "capture this as a skill"). But `new-skill/SKILL.md:6` currently sets `disable-model-invocation: true` and `new-skill` is in `audit_skills.py`'s `INFRA_SKILLS` (rule 6 requires the flag). Left as-is, every merged trigger becomes dead prose and the issue's own acceptance criterion — "saying 'skillify' still resolves via new-skill's description triggers" — is false. Resolve both facts together:
  - Delete the `disable-model-invocation: true` line from `new-skill/SKILL.md` (line 6).
  - Remove `"new-skill"` from `INFRA_SKILLS` in `audit_skills.py` (line 87) so rule 6 does not fail the self-audit on the now-flagless skill. `new-skill`'s description is already fully trigger-oriented, so declassifying it is consistent, not novel.
  - **Reviewer checkpoint:** invocation is per-skill, so this makes ALL of `new-skill`'s triggers (`create a skill`, `new agent`, etc.) model-invocable, not only the skillify ones. This broader surface is an intended, in-scope consequence of the issue's merge-into-new-skill mandate — there is no way to model-invoke a subset of one skill's triggers.
- Keep the already-in-tree `new-skill/WORKFLOW_TEMPLATE.md` edit (it references `SESSION_CAPTURE.md`, which this step creates — resolving the dangling reference).
- `git rm -r .claude/skills-global/skillify`.
- `RENAMED_REMOVALS`: add `("skills", "skillify")`.

**3. Prune 7 dead skills.**
- `git rm -r` each of: `analyze`, `claude-standards`, `deepen`, `observability`, `do-oop-audit`, `pthread`, `tdd`.
- `RENAMED_REMOVALS`: add `("skills", <name>)` for each.
- Incidental fleet-side cleanup (only genuine skill references, not English-word collisions):
  - `audit_skills.py:100` `FORK_SKILLS` — remove `"pthread"` (dead config after prune).
  - `new-audit-skill/SKILL.md:27` — remove the `do-oop-audit` example row.
  - `git rm docs/features/do-oop-audit.md` and remove its index entry from `docs/features/README.md`.
  - Verified non-references (leave as-is): `analyze`/`deepen`/`observability`/`tdd` fleet hits are the English words (logging "observability", the verb "analyze", etc.) or mutual references *within* the pruned set (`observability`↔`deepen`); vendored Anthropic reference docs under `audit-skills/references/` contain the words `analyze`/`tdd` incidentally.

**4. Reference sweep for the rename** — update every *active* (non-frozen) reference to `do-skills-audit`:
- `git mv docs/features/do-skills-audit.md docs/features/audit-skills.md`; update its ~13 internal self-references and the `docs/features/README.md` index row.
- `CLAUDE.md` (1 hit), `docs/features/reflections.md`, `docs/features/skill-context-convention.md`, `docs/features/skills-dependency-map.md`, `docs/features/skills-global.md`.
- `.claude/skill-context/README.md:34`, `.claude/skill-context/new-skill.md:128-131`.
- `.claude/skills/README.md:109` — the Level-2 sub-files table row (`do-skills-audit | scripts/...`) → `audit-skills`. This is the sole edit inside the project-only `.claude/skills/` dir: a one-row doc-accuracy sweep of the architecture README, not a change to any project-only skill body (see No-Gos).
- `.claude/skills-global/new-skill/SKILL.md`, `.claude/skills-global/new-audit-skill/SKILL.md` + `BEST_PRACTICES.md` (8 hits — including moving `skills-audit` from the `do-{subject}-audit` example rows to the `audit-{subject}` rows per the issue, subject to Open Question 2).
- `tests/unit/test_skills_audit.py` (lines 18, 772, 784) and `tests/unit/test_per_project_two_repos_aggregation.py:53` — update path fixtures/assertions to `audit-skills`.
- **Deliberately NOT swept** (frozen or generated — see Open Question 1): `docs/plans/completed/*.md`, `docs/audits/skills-architecture-audit-2026-07.md`, `docs/plans/skills-architecture-audit.md` (open #1883 plan), `site/assets/graph.js` (machine-generated bundle, explicitly excluded from doc sweeps and regenerated by the #2059 pipeline).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] No new exception handlers are introduced by this work. `audit_skills.py`'s existing handlers are untouched by the rename (only string constants change). State in the PR: "No exception handlers added or modified in scope."

### Empty/Invalid Input Handling
- [ ] Not applicable — no new functions receive runtime input. The only executable-logic change is a string constant (`"do-skills-audit"` → `"audit-skills"`) in `INFRA_SKILLS`/`is_auditor` and a path segment in the reflection resolver.
- [ ] Verify `audit_skills.py --json --no-sync` still exits 0 (self-exemption intact) — this is the effective failure-path check that the renamed constants are internally consistent.

### Error State Rendering
- [ ] The skills-audit reflection (`reflections/audits/skills_audit.py`) surfaces a resolver failure as a missing-script error. Add/confirm a test that the resolver returns the `audit-skills` path (not a nonexistent `do-skills-audit` path) so a silent no-op reflection cannot result from the rename.

## Test Impact

- [ ] `tests/unit/test_skills_audit.py` (lines 18, 772, 784) — UPDATE: change the `do-skills-audit` path segment and self-exemption assertion to `audit-skills`. If any case asserts `INFRA_SKILLS` membership, drop `new-skill` from the expected set (intentionally declassified — Technical Approach step 2) and confirm rule 6 stays green for both `audit-skills` (still infra, carries the flag) and `new-skill` (no longer infra, no flag required). Keep this file free of `do-skills-audit` literals so it is NOT excluded from the Verification grep.
- [ ] `tests/unit/test_per_project_two_repos_aggregation.py:53` — UPDATE to prove the dual-name resolver (CONCERN-3 guard): migrate one project's fixture to `.claude/skills-global/audit-skills/scripts` (this-repo, renamed) and keep the other at `.claude/skills/do-skills-audit/scripts` (un-migrated foreign repo). Both projects must still be discovered and aggregated. This file retains the sole test-side `do-skills-audit` literal and is excluded from the Verification grep.
- [ ] `tests/unit/test_update_hardlinks.py` — UPDATE: extend the `RENAMED_REMOVALS` coverage to assert the 9 new entries (`do-skills-audit`, `skillify`, and the 7 pruned skills) are present; confirm `test_no_project_only_skill_is_a_sync_destination` and the sync-invariant group still pass.
- [ ] `reflections/audits/skills_audit.py` resolver — ADD (or update existing) a unit assertion that the resolver returns the `audit-skills/scripts/audit_skills.py` path when present AND falls back to a `do-skills-audit` path for an un-migrated foreign repo (exercises both arms of the dual-name window).

## Rabbit Holes

- **Rewriting historical/completed plan docs and audit records.** `git grep do-skills-audit` hits 14 `docs/plans/completed/*.md` files and `docs/audits/*.md`. These are point-in-time records. Rewriting them is revisionism, produces large churn, and is out of scope (see Open Question 1). Do not sweep them.
- **Hand-editing `site/assets/graph.js`.** It is a 38k-line machine-generated bundle, explicitly excluded from doc sweeps (per `docs/plans/completed/docs-site-living-docs.md`) and regenerated by the separate #2059 pipeline. Leave it.
- **Reclassifying the audit taxonomy.** The `do-{subject}-audit` vs `audit-{subject}` taxonomy in `new-audit-skill/BEST_PRACTICES.md` is a real convention with edge cases. This plan moves one example row per the issue; it does not re-litigate the taxonomy itself.
- **Touching the 8 explicit-keep skills.** `computer-use`, `imagine-agent`, `reclassify`, `cowork`, `email`, `google-workspace`, `do-deploy-example`, plus the 2 verify-candidates — document, do not modify.

## Risks

### Risk 1: Missing the live reflection resolver breaks the skills-audit reflection
**Impact:** `reflections/audits/skills_audit.py` resolves `audit_skills.py` by the literal `do-skills-audit` dir name (lines 55-59). If the rename lands without updating it, the resolver falls through to the `.claude/skills/do-skills-audit/...` fallback, finds nothing, and the skills-audit reflection silently no-ops on every run across every project.
**Mitigation:** The resolver update is a first-class task, not part of the doc sweep. A dedicated test asserts the resolved path ends in `audit-skills/`. The `--json --no-sync` self-check exit-0 gate confirms the audit still runs.

### Risk 2: Authoring machine keeps stale user-level dirs (inode guard preserves them)
**Impact:** On the machine where the rename is a local working-tree change, inodes are preserved, so the `RENAMED_REMOVALS` inode guard may treat the old `~/.claude/skills/do-skills-audit/` (and pruned dirs) as foreign and keep them — leaving a duplicate `do-skills-audit` + `audit-skills` pair on that one machine.
**Mitigation:** Documented as an EXTERNAL No-Go: the operator removes the stale user-level dirs by hand once on the authoring machine. Every other machine `git pull`s fresh inodes and converges automatically. This matches the mechanism established in #2065/#2189.

### Risk 3: Over-broad rename sweep corrupts frozen records
**Impact:** A naive `git grep -l do-skills-audit | xargs sed` would rewrite 14 completed-plan docs, audit records, the open #1883 plan, and the generated `graph.js`.
**Mitigation:** The sweep is explicitly scoped to active references (Technical Approach step 4). The frozen/generated set is enumerated and excluded. Open Question 1 confirms this boundary with the operator.

### Risk 4: Single-name resolver silently stops auditing un-migrated foreign repos
**Impact:** The per-project skills-audit reflection resolves each configured project's OWN `audit_skills.py`. A foreign repo that vendored the skill project-locally under the old `do-skills-audit` name is not touched by this repo's rename; if the resolver only looks for `audit-skills`, that repo's script reads as "missing" and its skills-audit reflection silently no-ops (`skip_if`), with no crash to surface the regression.
**Mitigation:** The resolver keeps a dual-name window (Technical Approach step 1): it tries `audit-skills` then `do-skills-audit` across both roots. Un-migrated foreign repos keep auditing; this repo resolves the new name. Regression-guarded by the two-repo aggregation test using one migrated (`audit-skills`) and one un-migrated (`do-skills-audit`) fixture.

### Risk 5: Declassifying `new-skill` broadens its auto-trigger surface
**Impact:** Removing `disable-model-invocation` and dropping `new-skill` from `INFRA_SKILLS` (required to make the merged skillify triggers fire) makes ALL of `new-skill`'s triggers model-invocable, so casual mentions like "create a skill" could auto-load it where previously only `/new-skill` did.
**Mitigation:** This is the unavoidable, in-scope consequence of the issue's merge-into-new-skill mandate (invocation is per-skill). `new-skill`'s description is already trigger-oriented, so the behavior is consistent with its stated purpose. Flagged as an explicit reviewer checkpoint; the self-audit stays green because rule 6 no longer applies to the declassified skill.

## Race Conditions

No race conditions identified — all changes are static file edits/renames/deletes applied in a single commit. The `/update` sync is idempotent and single-threaded per machine; propagation ordering across machines is handled by the existing inode-guard mechanism, not by this change.

## No-Gos (Out of Scope)

- [EXTERNAL] Removing the stale user-level `~/.claude/skills/do-skills-audit/`, `skillify/`, and pruned-skill dirs on the *authoring* machine — the inode guard may preserve them there because the rename kept their inodes. The operator removes them by hand once; other machines converge via `/update`.
- [EXTERNAL] Reconciling `skillOverrides` in `~/.claude/settings.json` on the worker machine (to restore `computer-use`, `imagine-agent`, `reclassify`, `cowork`, `email`, `google-workspace` for the resident agent). Settings sync is deliberately additive-only; this is a machine-local action, not a repo change.
- [SEPARATE-SLUG #1883] The comprehensive skills-architecture disposition pass over all remaining skills. This plan executes only the operator-confirmed narrow subset.
- Touching project-only *skill bodies* under `.claude/skills/` (`setup`, `prime`, `sdlc`, `do-deploy`) — out of scope; those never sync. **Exception:** the architecture README `.claude/skills/README.md:109` has one stale `do-skills-audit` sub-files-table row that IS swept to `audit-skills` (a doc-accuracy fix, not a skill-body change). No project-only skill body is modified.
- Rewriting frozen historical records (`docs/plans/completed/*.md`, `docs/audits/*.md`) or the generated `site/assets/graph.js` — see Open Question 1.

## Update System

This change *is* an update-system change in the sense that it relies on the `/update` hardlink sync to propagate. Concretely:
- `scripts/update/hardlinks.py::RENAMED_REMOVALS` gains 9 entries (`do-skills-audit`, `skillify`, and the 7 pruned skills). No change to the sync *algorithm* — only its removal list.
- No new dependencies or config files. No migration steps beyond the one-time manual cleanup on the authoring machine (EXTERNAL No-Go above).
- **Non-authoring machine positive convergence check (CONCERN-4 signal).** After merge, on any synced (non-authoring) machine, one `/update` run must (a) leave no `do-skills-audit/`, `skillify/`, or pruned-skill dirs under `~/.claude/skills/`, AND (b) hardlink `audit-skills/` to the repo source. Runnable check (prints `OK` only when both hold — stale dirs gone AND `audit-skills/SKILL.md` shares the repo source's inode):
  ```bash
  MISS=$(ls -d ~/.claude/skills/{do-skills-audit,skillify,analyze,claude-standards,deepen,observability,do-oop-audit,pthread,tdd} 2>/dev/null)
  USER_SKILL=~/.claude/skills/audit-skills/SKILL.md
  REPO_SKILL="$HOME/src/ai/.claude/skills-global/audit-skills/SKILL.md"
  if [ -z "$MISS" ] && [ -f "$USER_SKILL" ] && [ "$(stat -f %i "$USER_SKILL")" = "$(stat -f %i "$REPO_SKILL")" ]; then echo OK; else echo "NOT CONVERGED"; fi
  ```
  `OK` = the fleet machine converged (prune/rename orphans swept, `audit-skills/` hardlinked). `NOT CONVERGED` = investigate before considering the deploy done. This is the positive fleet-side check; the authoring-machine cleanup signal below is its complement.
- **Authoring-machine cleanup verification signal** (post-merge, one-time): after the operator manually removes the stale user-level dirs, this command must print nothing —
  ```bash
  ls -d ~/.claude/skills/{do-skills-audit,skillify,analyze,claude-standards,deepen,observability,do-oop-audit,pthread,tdd} 2>/dev/null
  ```
  Any path it lists is a stale dir the inode guard preserved; remove it by hand. Empty output confirms convergence on the authoring machine.

## Agent Integration

No agent integration required — this is a skill-tree maintenance change. No new CLI entry point in `pyproject.toml [project.scripts]`, no new MCP surface, no bridge import. The renamed `audit-skills` skill and the merged `new-skill` session-capture flow remain reachable exactly as before via the skill dispatch surface (`/audit-skills`, and `skillify`/`capture this as a skill` now resolving through `new-skill`'s description triggers). The one live agent-adjacent consumer — the `skills_audit` reflection — is updated to the new path (Technical Approach step 1).

## Documentation

### Feature Documentation
- [ ] `git mv docs/features/do-skills-audit.md docs/features/audit-skills.md`; update its internal self-references to the new name.
- [ ] Update the `docs/features/README.md` index row for the renamed skill; remove the `do-oop-audit` row (pruned skill; delete `docs/features/do-oop-audit.md`).
- [ ] Sweep `CLAUDE.md`, `docs/features/reflections.md`, `docs/features/skill-context-convention.md`, `docs/features/skills-dependency-map.md`, `docs/features/skills-global.md` for `do-skills-audit` → `audit-skills`.

### External Documentation Site
- [ ] No hand edit to `site/`. `site/assets/graph.js` is machine-generated and excluded from doc sweeps (regenerated by the #2059 pipeline). No docs-build step in scope.

### Inline Documentation
- [ ] Update `audit_skills.py` docstrings (lines 50, 564) that name the skill by its old dir.
- [ ] Update `.claude/skill-context/README.md:34` and `.claude/skill-context/new-skill.md:128-131` path references.
- [ ] Sweep the `.claude/skills/README.md:109` sub-files-table row `do-skills-audit` → `audit-skills` (doc-accuracy only; no skill body touched).

## Success Criteria

- [ ] `git grep -c do-skills-audit` returns zero hits across the *active* reference set (skill bodies — including the auditor's own `audit-skills/SKILL.md` body, now swept — scripts, tests, `docs/features/`, `.claude/skill-context/`, `.claude/skills/README.md`, root `CLAUDE.md`). The only remaining live occurrences are all intentional and all excluded in the Verification grep: the `("skills", "do-skills-audit")` entry in `scripts/update/hardlinks.py`, the dual-name fallback literal in `reflections/audits/skills_audit.py`, the un-migrated-foreign-repo fixture in `tests/unit/test_per_project_two_repos_aggregation.py`, and this plan document itself. (Frozen historical records and generated `graph.js` also excluded per Open Question 1.)
- [ ] `.claude/skills-global/audit-skills/scripts/audit_skills.py --json --no-sync` exits 0 and does not flag itself (self-exemption intact post-rename; `new-skill` declassified out of `INFRA_SKILLS` does not trip rule 6).
- [ ] `reflections/audits/skills_audit.py` resolves `audit-skills/scripts/audit_skills.py` for this repo AND retains a `do-skills-audit` fallback for un-migrated foreign repos (both test-asserted via the two-repo aggregation fixture); the skills-audit reflection is not silently broken on any fleet repo.
- [ ] `.claude/skills-global/new-skill/SESSION_CAPTURE.md` exists; `new-skill/SKILL.md` routes to it and lists the `skillify`/`capture this as a skill`/`save this workflow` triggers; **`new-skill` is model-invocable** (`disable-model-invocation` removed, dropped from `INFRA_SKILLS`) so a bare "skillify" utterance resolves through its description — matching the issue's acceptance criterion; `skills-global/skillify/` is deleted.
- [ ] The 7 pruned dirs are deleted and each has a `RENAMED_REMOVALS` entry; `do-skills-audit` and `skillify` each have a `RENAMED_REMOVALS` entry (9 new entries total).
- [ ] The 2 verify-candidates (`do-discover-paths`, `do-investigation-issue`) are kept, with the keep decision + wiring evidence recorded in the PR body.
- [ ] Working tree is clean (the dangling `new-skill/WORKFLOW_TEMPLATE.md` edit is committed as part of the merge).
- [ ] `test_no_project_only_skill_is_a_sync_destination` and the full sync-invariant test group in `test_update_hardlinks.py` pass.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (rename)**
  - Name: rename-builder
  - Role: Rename `do-skills-audit` → `audit-skills` across dir, script self-checks, reflection resolver, active docs, tests; add `RENAMED_REMOVALS` entry.
  - Agent Type: builder
  - Resume: true

- **Builder (merge + prune)**
  - Name: merge-prune-builder
  - Role: Merge `skillify` into `new-skill` (create `SESSION_CAPTURE.md`, keep the in-tree `WORKFLOW_TEMPLATE.md` edit, wire routing/triggers, delete `skillify`); prune the 7 dead skills + incidental cleanup; add `RENAMED_REMOVALS` entries.
  - Agent Type: builder
  - Resume: true

- **Validator (sync + references)**
  - Name: sync-validator
  - Role: Verify no active `do-skills-audit` reference survives, the reflection resolver + `--json --no-sync` self-check pass, and the sync-invariant test group is green.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Rename/update the feature docs and index; sweep active doc references.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Rename do-skills-audit → audit-skills
- **Task ID**: build-rename
- **Depends On**: none
- **Validates**: tests/unit/test_skills_audit.py, tests/unit/test_per_project_two_repos_aggregation.py, reflections/audits/skills_audit.py resolver assertion
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: false
- `git mv` the dir; set frontmatter `name: audit-skills`.
- **Sweep the auditor's own `audit-skills/SKILL.md` body** — rewrite the 4 live `do-skills-audit` refs (lines 31, 35, 78, 99) to `audit-skills` (BLOCKER-1 fix).
- Update `audit_skills.py` `INFRA_SKILLS`, `is_auditor`, docstrings (lines 50, 87, 564, 1033).
- Update `sync_best_practices.py:8` path comment.
- Update `reflections/audits/skills_audit.py:47-59` resolver to the **dual-name window** — try `audit-skills` then `do-skills-audit` across both roots (CONCERN-3 fix); comment the retained old-name fallback.
- Update `tests/unit/test_skills_audit.py` (fully to `audit-skills`, INFRA_SKILLS expectation) and `tests/unit/test_per_project_two_repos_aggregation.py` (one migrated + one un-migrated fixture) path fixtures.
- Add `("skills", "do-skills-audit")` to `RENAMED_REMOVALS`.
- Confirm `audit_skills.py --json --no-sync` exits 0.

### 2. Merge skillify into new-skill and prune 7 dead skills
- **Task ID**: build-merge-prune
- **Depends On**: build-rename
- **Validates**: tests/unit/test_update_hardlinks.py
- **Assigned To**: merge-prune-builder
- **Agent Type**: builder
- **Parallel**: false
- **Serialization rationale**: both this task and `build-rename` mutate the same two files — they append to the single `RENAMED_REMOVALS` list in `scripts/update/hardlinks.py`, and both edit `INFRA_SKILLS` in `audit_skills.py:87` (`build-rename` swaps `do-skills-audit`→`audit-skills`; this task removes `new-skill`). Running concurrently would race both files; sequencing after `build-rename` makes the 9 `RENAMED_REMOVALS` entries land as clean ordered appends and lets this task edit the already-renamed `INFRA_SKILLS` line deterministically.
- Create `new-skill/SESSION_CAPTURE.md` from `skillify/SKILL.md` body (retitle; fix relative link to local `WORKFLOW_TEMPLATE.md`; otherwise byte-faithful).
- Add routing row to `new-skill/SKILL.md` "When to load sub-files" + trigger phrases to its `description`.
- **Make `new-skill` model-invocable (BLOCKER-2 fix):** delete `disable-model-invocation: true` from `new-skill/SKILL.md:6` and remove `"new-skill"` from `INFRA_SKILLS` in `audit_skills.py:87` so rule 6 stays green. (Confirm `audit_skills.py --json --no-sync` still exits 0.)
- Confirm the in-tree `WORKFLOW_TEMPLATE.md` edit is staged (resolves the dangling reference).
- `git rm -r` `skillify` and the 7 pruned dirs.
- Add `("skills", "skillify")` + 7 pruned entries to `RENAMED_REMOVALS`.
- Incidental cleanup: remove `pthread` from `FORK_SKILLS`; remove `do-oop-audit` row from `new-audit-skill/SKILL.md:27`; `git rm docs/features/do-oop-audit.md`.

### 3. Doc reference sweep (active set only)
- **Task ID**: document-sweep
- **Depends On**: build-rename, build-merge-prune
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- `git mv docs/features/do-skills-audit.md docs/features/audit-skills.md`; update internal refs + `docs/features/README.md` index (and remove the do-oop-audit index row).
- Sweep `CLAUDE.md`, `docs/features/{reflections,skill-context-convention,skills-dependency-map,skills-global}.md`, `.claude/skill-context/{README,new-skill}.md`, `.claude/skills/README.md` (line 109 sub-files-table row only), `new-skill/SKILL.md`, `new-audit-skill/{SKILL,BEST_PRACTICES}.md` (move the taxonomy example row per Open Question 2).
- Do NOT touch `docs/plans/completed/`, `docs/audits/`, the open #1883 plan, or `site/assets/graph.js`.

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: build-rename, build-merge-prune, document-sweep
- **Assigned To**: sync-validator
- **Agent Type**: validator
- **Parallel**: false
- Run the Verification table commands below.
- Confirm every success criterion, including the reflection-resolver assertion and clean working tree.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_skills_audit.py tests/unit/test_update_hardlinks.py tests/unit/test_per_project_two_repos_aggregation.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Audit self-check green | `.claude/skills-global/audit-skills/scripts/audit_skills.py --json --no-sync` | exit code 0 |
| No active old-name refs | `git grep -c do-skills-audit -- ':!docs/plans/completed' ':!docs/audits' ':!docs/plans/skills-architecture-audit.md' ':!docs/plans/prune-skills-global-audit-skills-rename.md' ':!site/assets/graph.js' ':!scripts/update/hardlinks.py' ':!reflections/audits/skills_audit.py' ':!tests/unit/test_per_project_two_repos_aggregation.py'` | exit code 1 (no hits: excluded live occurrences are the `RENAMED_REMOVALS` entry, the intentional dual-name resolver fallback, the un-migrated-foreign-repo test fixture, and this plan doc. The auditor's own `audit-skills/SKILL.md` body and `.claude/skills/README.md` are swept, NOT excluded) |
| Auditor body swept | `grep -c 'do-skills-audit' .claude/skills-global/audit-skills/SKILL.md` | output 0 |
| new-skill model-invocable | `grep -c 'disable-model-invocation' .claude/skills-global/new-skill/SKILL.md` | output 0 |
| new-skill declassified | `grep -c '"new-skill"' .claude/skills-global/audit-skills/scripts/audit_skills.py` | output 0 |
| skillify dir gone | `test -d .claude/skills-global/skillify` | exit code 1 |
| audit-skills dir exists | `test -d .claude/skills-global/audit-skills` | exit code 0 |
| SESSION_CAPTURE created | `test -f .claude/skills-global/new-skill/SESSION_CAPTURE.md` | exit code 0 |
| 7 pruned dirs gone | `for d in analyze claude-standards deepen observability do-oop-audit pthread tdd; do test ! -d ".claude/skills-global/$d"; done` | exit code 0 |
| Reflection resolver updated | `grep -c 'audit-skills' reflections/audits/skills_audit.py` | output > 0 |
| Resolver keeps old-name fallback | `grep -c 'do-skills-audit' reflections/audits/skills_audit.py` | output > 0 (intentional dual-name window) |
| No stale skillify body | `test -f .claude/skills-global/new-skill/WORKFLOW_TEMPLATE.md && ! git diff --name-only \| grep -q WORKFLOW_TEMPLATE` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Verification | The `No active old-name refs` grep (Verification table) expects exit 1 but did not exclude the plan doc itself (38 self-refs) nor sweep `.claude/skills/README.md:109`, so it is unsatisfiable on a correct build. | Grep now excludes `docs/plans/prune-skills-global-audit-skills-rename.md`; `.claude/skills/README.md:109` added to the active doc sweep (Tech Approach step 4, Docs, Step 3). Success Criterion #1 prose and the `.claude/skills/` No-Go reconciled with the command. | Plan doc + `RENAMED_REMOVALS` entry are the only excluded live occurrences; README row is swept, not excluded. |
| Non-blocking | Sync-safety | `RENAMED_REMOVALS` two-writer race: `build-rename` and `build-merge-prune` both append to the same list in `hardlinks.py` while `build-merge-prune` was `Parallel: true`. | `build-merge-prune` now `Depends On: build-rename`, `Parallel: false`, with a serialization rationale. | The 9 entries land as clean ordered appends. |
| Non-blocking | Ops | Authoring-machine manual cleanup (EXTERNAL No-Go) had no verification signal. | Added a post-merge `ls -d ~/.claude/skills/{...}` check to the Update System section that must print nothing after cleanup. | Convergence signal for the one machine the inode guard can't auto-sweep. |
| Non-blocking | Scope | OQ1 (doc-sweep boundary) is the only open question that changes the diff; OQ2/OQ3 are confirmations, not pre-build blockers. | OQ1's active-set boundary is already baked into the plan and Verification; all three OQs resolved to their stated defaults below (Resolved Decisions). | Plan settles for build. |
| BLOCKER (re-critique) | Completeness | The renamed skill's own `SKILL.md` body keeps 4 live `do-skills-audit` refs (lines 31, 35, 78, 99); Task 1 set only frontmatter, so the `No active old-name refs` grep was unsatisfiable. | Technical Approach step 1 + Task 1 now own an explicit body sweep of `audit-skills/SKILL.md`; a `Auditor body swept` Verification row asserts zero old-name hits. | Frontmatter-only rename was incomplete; body is self-exempt from coupling rules but not from the grep. |
| BLOCKER (re-critique) | Consistency | `new-skill/SKILL.md:6` `disable-model-invocation: true` (and `new-skill ∈ INFRA_SKILLS`) contradicted Resolved Decision 3 — merged skillify triggers would be dead prose and "skillify" would resolve to nothing. | Technical Approach step 2 + Task 2 now delete the flag and drop `new-skill` from `INFRA_SKILLS` (rule 6 stays green); Verification rows `new-skill model-invocable`/`new-skill declassified` assert it; Resolved Decision 3 + Risk 5 document the broadened trigger surface. | `skillify` was already model-invocable (`user-invocable: true`, no flag); the merge must preserve that, and the issue AC requires it. |
| CONCERN (re-critique) | Compat | Resolver single-name switch silently breaks un-migrated foreign repos (no dual-name window; test path `test_per_project_two_repos_aggregation.py:53`). | Resolver now a dual-name window (`audit-skills` then `do-skills-audit` × both roots); two-repo test uses one migrated + one un-migrated fixture; Risk 4 added; grep excludes the intentional fallback + fixture. | Per-project reflection resolves each repo's own script; foreign repos lag the rename. |
| CONCERN (re-critique) | Ops | No runnable positive convergence check for non-authoring fleet machines. | Update System gains a runnable `OK`/`NOT CONVERGED` check asserting stale dirs gone AND `audit-skills/` inode-shared with the repo source. | Complements the authoring-machine cleanup signal. |
| CONCERN (re-critique) | Provenance | Prior Art omitted #1319 (origin of `deepen`/`observability`/`tdd`) and #1894 (prior fleet frontmatter migration). | Both added to Prior Art with their relevance. | Grounds the prunes and the invocation-behavior baseline. |

---

## Resolved Decisions

The three questions below were flagged for operator input. Per the critique, only Decision 1 changes the build diff, and its boundary is already encoded in the Technical Approach and Verification. All three are resolved to their stated defaults so the plan can proceed to build; any of them can be reopened during review without re-planning.

1. **Doc-sweep boundary — RESOLVED: active set only.** The issue's literal "zero hits outside `RENAMED_REMOVALS`" would require rewriting 14 `docs/plans/completed/*.md` files, `docs/audits/skills-architecture-audit-2026-07.md`, the *open* #1883 plan, and the machine-generated `site/assets/graph.js` — revisionism on frozen/generated records. The sweep is scoped to the **active** reference set (skill bodies, scripts, tests, `docs/features/`, `.claude/skill-context/`, `.claude/skills/README.md:109`, root `CLAUDE.md`). Frozen historical records and the generated bundle are left untouched and excluded in the Verification grep. This boundary is baked into Technical Approach step 4 and Success Criterion #1.
2. **Taxonomy placement of `audit-skills` — RESOLVED: proceed with the operator's rename; move the example row.** `new-audit-skill/BEST_PRACTICES.md` places `audit-{subject}` in the repo-specific bucket, while the skills-audit tool runs cross-repo (general-purpose). We honor the operator's rename and move the row as instructed, accepting the minor taxonomy inconsistency. A one-line clarifying note in BEST_PRACTICES.md is optional polish, deferred unless review requests it — it does not change the rename diff.
3. **`skillify` description-trigger reachability — RESOLVED: `new-skill` is made model-invocable.** After the merge, `skillify`/`capture this as a skill`/`save this workflow` resolve through `new-skill`'s `description` triggers rather than a standalone `/skillify` command. For those triggers to actually fire on a bare utterance (as the issue's acceptance criterion requires), the merge must **make `new-skill` model-invocable**: delete `disable-model-invocation: true` from `new-skill/SKILL.md` and drop `new-skill` from `INFRA_SKILLS` in `audit_skills.py` so rule 6 stays green (Technical Approach step 2, re-critique BLOCKER-2). Consequence: all of `new-skill`'s triggers become model-invocable, not only the skillify ones — an intended, in-scope effect of merging into `new-skill` (invocation is per-skill), flagged as a reviewer checkpoint. Typing `/skillify` no longer autocompletes to a dedicated command.
