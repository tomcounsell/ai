---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-07-29
tracking: https://github.com/tomcounsell/ai/issues/2436
last_comment_id:
revision_applied: true
revision_applied_at: 2026-07-29T07:34:50Z
---

# Skills-audit rule 19: exempt the git-ignored `references/metadata.json` sync cache from the husk predicate

## Problem

The `skills-audit` reflection has FAILed on 2 consecutive runs for skill
`do-skills-audit` (rule 19), auto-filing issue #2436:

> Husk directory: no SKILL.md (contains: references/metadata.json) — delete or restore

**Current behavior:**
`.claude/skills-global/do-skills-audit/` is a husk left behind when the skill was
renamed `do-skills-audit` → `audit-skills` (#2339). It has no `SKILL.md`. Its only
contents are `references/metadata.json` (a git-ignored best-practices sync cache)
and `scripts/__pycache__/*.pyc` (orphaned bytecode). Because `metadata.json` is not
in the audit's artifact-exemption list (`__pycache__`, `.DS_Store`), rule 19 treats
it as "real orphaned content" and FAILs, and the `--fix` auto-pruner
(`_is_empty_husk`) refuses to remove it. The husk therefore persists and re-FAILs
every run.

The husk is **entirely git-ignored** (`metadata.json` matches `*.json` at
`.gitignore:404`; `.pyc` under `__pycache__`). `#2339` could only `git rm` tracked
files, so these artifacts survived. A plain `rm -rf` clears it locally but produces
no committable diff and does not stop recurrence on any other machine that ran the
old skill before its rename.

**Desired outcome:**
The audit recognizes `references/metadata.json` as the generated sync cache it is —
not skill content — so a husk whose only leftovers are that cache plus build
artifacts is (a) no longer flagged as a rule 19 FAIL and (b) auto-pruned under
`--fix`. The reflection stops re-filing this issue on the next run on every machine.

**What the code change does and does NOT do (remediation path, verified):**
The `skills-audit` reflection invokes the audit **detection-only** — it runs
`audit_skills.py --no-sync --json` (`reflections/audits/skills_audit.py:269`) and
**never passes `--fix`**. Nothing in `scripts/update/` runs the audit with `--fix`
either (verified: `scripts/update/hardlinks.py` only carries the `RENAMED_REMOVALS`
user-level sweep, no audit invocation). Therefore:
- **Delivered by the code change alone:** widening the predicate makes rule 19
  report the `do-skills-audit` husk as an empty husk (PASS, not FAIL). On the next
  daily reflection run, the FAIL disappears and the streak/dedup bookkeeping stops
  re-filing — on *every* machine, automatically. This is the durable fix that kills
  the recurring-issue signal.
- **NOT delivered by the code change alone:** actual deletion of the on-disk husk
  directory. The husk stays on disk (now harmless and unflagged) until `--fix` is
  run. Because it is git-ignored, it cannot be removed via a committed `git rm`.
  The true removal paths are: (a) a manual `audit-skills --fix` run on a machine
  (prunes the repo-level `skills-global/do-skills-audit/` husk there), or (b) a plain
  `rm -rf .claude/skills-global/do-skills-audit/`. `RENAMED_REMOVALS` in
  `hardlinks.py` only sweeps the *user-level* `~/.claude/skills/do-skills-audit/`
  copy, never the repo-level source husk. This plan removes the husk on *this*
  machine as an explicit post-merge step (Task 3); other machines simply stop
  FAILing and carry a dormant, unflagged directory until someone runs `--fix` there.

## Freshness Check

**Baseline commit:** 060e2f791113d1fd28f6f78c8a4080a03d0f9790
**Issue filed at:** 2026-07-28T11:29:55Z
**Disposition:** Unchanged

**File:line references re-verified:**
- `.claude/skills-global/do-skills-audit/` — husk still on disk, no `SKILL.md`; contains `references/metadata.json` + `scripts/__pycache__/*.pyc`. Confirmed.
- `.claude/skills-global/audit-skills/SKILL.md` — renamed target present and live. Confirmed.
- `scripts/update/hardlinks.py:95` — `("skills", "do-skills-audit")` in `RENAMED_REMOVALS`; consumed only by `_cleanup_renamed()` (line 462) which sweeps the **user-level** `~/.claude/skills/do-skills-audit/`, never the repo-level source husk. Confirmed.
- `.claude/skills-global/audit-skills/scripts/audit_skills.py:798` `rule_19_husk_directories`, `:828` `_is_empty_husk`, `:839` `prune_husk_directories` — artifact-exemption set is `{__pycache__, .DS_Store}` only. Confirmed.
- `.claude/skills-global/audit-skills/scripts/sync_best_practices.py:42` — writes `references/metadata.json`. Confirmed the exact husk-relative path.

**Cited sibling issues/PRs re-checked:**
- #2339 (merged) — the rename that created the husk; could only remove tracked files.
- PR #1909 (merged) — "Auto-prune empty rule-19 husk directories in skills audit `--fix`". Established the exact `_is_empty_husk` predicate this plan extends. This husk is skipped by it because `metadata.json` is not exempted.

**Commits on main since issue was filed (touching referenced files):** none.

**Active plans in `docs/plans/` overlapping this area:** none.

**Notes:** No drift. The first-pass investigation's "git rm -r the husk" remediation is factually impossible (the husk is untracked/git-ignored) and would leave no committable artifact; this plan supersedes it with the durable predicate fix.

## Prior Art

- **PR #1909**: *Auto-prune empty rule-19 husk directories in skills audit `--fix`* — added `_is_empty_husk` + `prune_husk_directories` so genuinely-empty husks (only `__pycache__`/`.DS_Store`) are removed under `--fix`. Succeeded, but its "empty" predicate does not exempt the `references/metadata.json` sync cache, which is exactly why this husk slips through. This plan extends #1909's predicate.
- **#2339**: the `do-skills-audit` → `audit-skills` rename + skills-global prune. Removed tracked files; git-ignored artifacts (`metadata.json`, `.pyc`) survived as the husk.

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| #2339 rename/prune | `git rm` of the old `do-skills-audit` tree | Only tracked files can be `git rm`'d. The git-ignored `references/metadata.json` and `__pycache__` were never in the index, so they remained on disk as a husk. |
| PR #1909 `--fix` pruner | Auto-remove husks that are empty except build artifacts | Its artifact set is `{__pycache__, .DS_Store}` only. `references/metadata.json` (a git-ignored generated cache) is counted as real content, so the pruner skips this husk and rule 19 keeps FAILing. |

**Root cause pattern:** The audit's notion of "husk build artifacts" is too narrow. `references/metadata.json` is a git-ignored, machine-generated best-practices sync cache (`fetched_at`, `sync_ttl_days`) — never authored skill content. Treating it as content is what makes a dead directory look "non-empty".

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Shared artifact predicate**: introduce a single helper that decides whether a file inside a husk is a disposable build/generated artifact — `__pycache__/*`, `.DS_Store`, and the sync cache `references/metadata.json`.
- **Detection (`rule_19_husk_directories`)**: use the helper when computing `contents`, so a husk whose only files are artifacts is reported as `(empty)` and does not FAIL. (A husk that also holds a real orphaned file still FAILs, unchanged.)
- **Prune predicate (`_is_empty_husk`)**: use the same helper so `--fix` auto-prunes the now-recognized-empty husk. Detection and prune stay in lockstep (the module already documents that invariant).

### Flow

Audit run → scan `.claude/skills-global/` → `do-skills-audit` has no `SKILL.md` → its only files are `references/metadata.json` + `__pycache__/*.pyc` → all recognized as artifacts → **rule 19 reports it as an empty husk (PASS, not FAIL)**. Two consumers diverge here:
- **Reflection (detection-only, `--no-sync --json`):** the FAIL vanishes, so the streak counter never re-arms and the reflection stops re-filing this issue — automatically, on every machine's next daily run. The husk directory itself is left in place, dormant and unflagged.
- **Manual `--fix` run:** `prune_husk_directories` now also removes the husk (the same widened predicate gates it). This is the only path that actually deletes the directory; it is not exercised by the reflection or by `/update`.

### Technical Approach

- Add a module-level predicate in `audit_skills.py`, e.g. `_is_husk_artifact(file_path: Path, husk_root: Path) -> bool`, returning `True` when:
  - `"__pycache__" in file_path.parts`, or
  - `file_path.name == ".DS_Store"`, or
  - `file_path.relative_to(husk_root) == Path("references/metadata.json")` (anchored to the exact husk-relative path `sync_best_practices.py` writes, so we don't over-broadly exempt every `metadata.json` anywhere).
- Refactor `rule_19_husk_directories`'s `contents` comprehension (lines 810-814) and `_is_empty_husk` (lines 831-836) to filter through the shared helper. Keep behavior identical for every husk that holds any non-artifact file.
- No change to `hardlinks.py` `RENAMED_REMOVALS` — its user-level sweep is orthogonal and correct; the durable repo-level cleanup belongs in the audit's own `--fix`, matching prior art #1909.

## Failure Path Test Strategy

### Exception Handling Coverage
- `prune_husk_directories` already wraps `shutil.rmtree` in `try/except OSError: continue` (line 870) — unchanged by this work. No new exception handlers are introduced.
- No `except Exception: pass` blocks in scope.

### Empty/Invalid Input Handling
- Both touched functions already guard `if not skills_dir.is_dir(): return`. The helper operates on `Path` objects; `relative_to` is only called on files under the husk root, so it cannot raise `ValueError`.
- Add a test that a husk containing ONLY `references/metadata.json` (no `.pyc`) is treated as empty — the minimal git-ignored-cache case.

### Error State Rendering
- Rule 19 output is a `Finding` list surfaced by the reflection. Test asserts the exact disposition flip: FAIL → absent for the artifact-only husk, and still-FAIL for a husk with a real file.

## Test Impact

- [ ] `tests/unit/test_skills_audit.py` — UPDATE/ADD: add cases for the new predicate. Existing rule-19 and prune tests must still pass unchanged (a husk with a real orphaned file still FAILs and is not pruned).

## Rabbit Holes

- **Do not** rework `hardlinks.py` to sweep repo-level source husks — that duplicates the audit `--fix` mechanism and widens blast radius for a one-line-of-intent fix.
- **Do not** broaden the exemption to "any git-ignored file" (shelling out to `git check-ignore` per file) — slow, and it would mask genuinely-orphaned ignored content. Anchor to the one known sync-cache path.
- **Do not** touch `sync_best_practices.py` — it correctly writes the cache into the live `audit-skills/references/`; the stale copy is only in the dead husk.

## Risks

### Risk 1: Over-broad exemption masks a real husk
**Impact:** A directory that is actually mid-migration but happens to contain `references/metadata.json` could be silently pruned.
**Mitigation:** Anchor the exemption to the exact husk-relative path `references/metadata.json` only. Any other real file in the husk still makes it non-empty → still FAILs → still preserved for human decision.

### Risk 2: Detection/prune predicates drift apart
**Impact:** Rule 19 stops flagging but `--fix` still refuses to prune (or vice-versa), reintroducing the split that caused this bug.
**Mitigation:** Route both through the single shared helper; assert both behaviors in the same test.

## Race Conditions

No race conditions identified — the audit is a synchronous, single-process scan. `prune_husk_directories` already re-checks `_is_empty_husk` immediately before `rmtree` (TOCTOU guard, line 864), and that guard automatically inherits the widened predicate.

## No-Gos (Out of Scope)

- [DESTRUCTIVE] Bulk deletion of unrelated git-ignored artifacts across the repo — out of scope; this fix only widens the husk-emptiness predicate for the audit.
- Nothing else deferred — the durable fix and the local husk removal are both in scope for this plan.

## Update System

The fix lives in a synced global skill (`.claude/skills-global/audit-skills/`), propagated to every machine by the existing `/update` hardlink sync — no new `/update` step required. Once merged, the reflection (detection-only) stops FAILing and re-filing on each machine's next daily audit run automatically. Actual pruning of a lingering `do-skills-audit` husk directory only happens when someone runs `audit-skills --fix` (or `rm -rf`) on that machine — neither the reflection nor `/update` runs `--fix`, so the dormant directory is harmless but not auto-deleted fleet-wide. No new dependencies or config.

## Agent Integration

No agent integration required — this is an internal change to the skills-audit reflection's husk logic. No new CLI entry point, no bridge wiring.

## Documentation

- [ ] Update the module docstring in `.claude/skills-global/audit-skills/scripts/audit_skills.py` (lines 9-11) to note that the git-ignored `references/metadata.json` sync cache is also treated as a build artifact for husk-emptiness.
- [ ] Update the `rule_19_husk_directories` / `_is_empty_husk` docstrings (lines 800, 829) to reference the shared `_is_husk_artifact` helper as the single source of the artifact-exemption set.

**Conscious `docs/features/` exemption (recorded per repo convention):** No `docs/features/` page is created or updated for this work, and this is a deliberate exemption, not an oversight. The repo's `## Documentation` convention prefers a `docs/features/` checkbox path for new capabilities; this change is a one-file behavior refinement to an existing audit rule (`rule_19_husk_directories` / `_is_empty_husk` widen their artifact-exemption set by one anchored path), introducing no new capability, surface, or config that a feature doc would describe. No documentation changes to `docs/features/` are needed; the two docstring updates above are the authoritative, co-located reference for the widened husk predicate. (The validator `.claude/hooks/validators/validate_documentation_section.py` is satisfied by the two checkbox tasks above; this note additionally records the conscious feature-doc exemption the critique asked for.)

## Success Criteria

- [ ] Rule 19 no longer FAILs on a husk whose only files are `references/metadata.json` and/or `__pycache__`/`.DS_Store`.
- [ ] `--fix` (`prune_husk_directories`) removes such a husk.
- [ ] A husk containing a genuine non-artifact orphaned file still FAILs and is NOT pruned (regression guard).
- [ ] After merge + local `audit-skills --fix` (or `rm -rf .claude/skills-global/do-skills-audit/`), `.claude/skills-global/do-skills-audit/` is gone on this machine and the audit passes.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation (docstring) updated (`/do-docs`).

## Team Orchestration

Small solo change — one builder, one validator.

### Team Members

- **Builder (audit-predicate)**
  - Name: audit-builder
  - Role: Add the shared husk-artifact helper and route detection + prune through it; add tests.
  - Agent Type: builder
  - Resume: true

- **Validator (audit-predicate)**
  - Name: audit-validator
  - Role: Verify FAIL→pass flip for the artifact-only husk, still-FAIL for real-content husk, and `--fix` prune behavior.
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add shared husk-artifact predicate and route both call sites through it
- **Task ID**: build-predicate
- **Depends On**: none
- **Validates**: tests/unit/test_skills_audit.py
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_is_husk_artifact(file_path, husk_root)` to `audit_skills.py` exempting `__pycache__/*`, `.DS_Store`, and husk-relative `references/metadata.json`.
- Refactor `rule_19_husk_directories` `contents` filter (lines 810-814) and `_is_empty_husk` (lines 831-836) to use it.
- Update the module docstring (lines 9-11) to mention the sync-cache exemption.
- Add unit tests: artifact-only husk → no FAIL + pruned; husk with a real file → still FAIL + not pruned; husk with only `references/metadata.json` → treated empty.

### 2. Validation
- **Task ID**: validate-predicate
- **Depends On**: build-predicate
- **Assigned To**: audit-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_skills_audit.py -q`.
- Run the audit against a temp fixture husk to confirm the disposition flip.
- Confirm `python -m ruff check .` and `ruff format --check .` are clean.

### 3. Local husk removal + audit re-run (post-merge)
- **Task ID**: local-cleanup
- **Depends On**: validate-predicate
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: false
- After merge, run the skills audit with `--fix` on this machine (or `rm -rf .claude/skills-global/do-skills-audit/`) and confirm the husk is gone and rule 19 passes.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Audit tests pass | `pytest tests/unit/test_skills_audit.py -q` | exit code 0 |
| Lint clean | `python -m ruff check .claude/skills-global/audit-skills/scripts/audit_skills.py` | exit code 0 |
| Format clean | `python -m ruff format --check .claude/skills-global/audit-skills/scripts/audit_skills.py` | exit code 0 |
| Predicate present | `grep -c "_is_husk_artifact" .claude/skills-global/audit-skills/scripts/audit_skills.py` | output > 0 |
| metadata.json exempted | `grep -c "references/metadata.json" .claude/skills-global/audit-skills/scripts/audit_skills.py` | output > 0 |

## Critique Results

Critique verdict: **READY TO BUILD (WITH CONCERNS)** — 0 blockers, 2 tech-debt. Both CONCERNs resolved in this revision pass.

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | reviewer | Widening the husk predicate silences rule 19 everywhere but the reflection is detection-only, so "self-heals on every machine" overstates what the code delivers. | Corrected Desired Outcome, Flow, and Update System | Verified `reflections/audits/skills_audit.py:269` runs `--no-sync --json` (never `--fix`) and nothing in `scripts/update/` runs `--fix`. Plan now states: code change stops FAIL/re-filing fleet-wide automatically; actual husk deletion requires a manual `audit-skills --fix` or `rm -rf` (Task 3 removes it on this machine; `RENAMED_REMOVALS` only sweeps the user-level copy). |
| CONCERN | reviewer | `## Documentation` section justified docstring-only without a conscious `docs/features/` exemption note. | Added explicit conscious-exemption note in Documentation | The validator is already satisfied by the two docstring checkboxes; the note now explicitly records that no `docs/features/` page is warranted for a one-file audit-rule behavior refinement with no new capability. |

---

## Resolved Questions

1. **Durable predicate fix vs. a mere local `rm -rf`?** Resolved: the durable fix is preferred and is the plan of record. A plain `rm -rf` clears the husk only on this machine and leaves no committable artifact (the husk is git-ignored), so the reflection would keep FAILing and re-filing on any other machine that carries it. Widening the audit's husk-artifact predicate is what stops the recurring FAIL fleet-wide. Note (from critique concern 1): the predicate fix stops the FAIL/re-filing everywhere automatically, but does not by itself delete the on-disk directory anywhere — that still needs a manual `--fix`/`rm -rf` (Task 3 handles this machine).
