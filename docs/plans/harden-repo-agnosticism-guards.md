---
status: Planning
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2079
last_comment_id: 4977658840
---

# Harden repo-agnosticism guards

## Problem

This repo is the canonical source for **global skills** (`.claude/skills-global/`) that `/update` hardlinks to `~/.claude/skills/` on every machine. Global skill bodies must stay repo-agnostic — behavior coupled to this repo's infra defers to the skill-context seam. Two guards protect that invariant: `rule_13_coupling_signals` in `.claude/skills-global/do-skills-audit/scripts/audit_skills.py` and the sync exclusions in `scripts/update/hardlinks.py`. A 3-agent documentation audit (2026-07-14) found four gaps where a regression can ship undetected.

**Current behavior:**

1. **`COUPLING_SIGNALS` misses whole signal classes.** The set (`audit_skills.py:123`) is `sdlc-tool`, `python -m tools.`, `reflections.`, `valor-`, `config/identity.json`. It does not catch Bucket-C skill invocations (`/sdlc`, `/setup`, `/prime`, `/do-deploy` — project-only skills that don't exist off this repo) or internal infra tokens (`sdk_client.py`, `SDLC_TARGET_REPO`). Five real leaks shipped through a passing `rule_13` and were hand-fixed in `61b55ce7`; nothing stops recurrence.
2. **`rule_13` only scans `SKILL.md`.** Sub-files (`CRITICS.md`, `CHECKS.md`, templates, sub-skills) are hardlinked to every machine too, but are never scanned. Clean today — an unguarded surface, not a live leak.
3. **`PROJECT_ONLY_SKILLS` is dead code that reads as protection.** The set (`hardlinks.py:90`) lists only `{telegram, reading-sms-messages, checking-system-logs}` and is checked inside `_sync_skills` (`:339`), whose source dir is always `.claude/skills-global/` — so the filter can never match. The other ~12 project-only skills are excluded from sync only *structurally* (wrong source dir). A future scan-root widening leaks them silently; no test catches it.
4. **Skill moves are an unenforced two-place edit.** Moving a skill between `.claude/skills/` and `.claude/skills-global/` requires a matching `RENAMED_REMOVALS` entry (`hardlinks.py:14`) to remove stale hardlinks on every machine. Nothing automated asserts this; the audit could only verify by hand via `git log --follow`.

**Desired outcome:**

Each of the four gaps has an automated guard (audit rule or unit test), so the next `/do-skills-audit` run — or the test suite — catches a regression instead of a manual multi-agent audit. All new guard logic must itself stay repo-agnostic, since `audit_skills.py` ships to every machine.

## Freshness Check

**Baseline commit:** `8a0594765bbde4b0dd847c8f5eb7176af79b6626`
**Issue filed at:** 2026-07-14T04:53:43Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `audit_skills.py:123` — `COUPLING_SIGNALS` tuple (5 tokens) — still holds, exact line.
- `audit_skills.py:417` — `rule_13_coupling_signals(skill_name, body)` — still holds; takes only `SKILL.md` body, confirming the single-file scan (Gap 2).
- `hardlinks.py:14` — `RENAMED_REMOVALS` — still holds; list has grown (new entries below).
- `hardlinks.py:80` (issue) → **`hardlinks.py:90`** — `PROJECT_ONLY_SKILLS` definition drifted +10 lines; contents unchanged (3 names).
- `hardlinks.py:323` (issue) → **`hardlinks.py:339`** — the `if skill_dir.name in PROJECT_ONLY_SKILLS:` call site inside `_sync_skills`; `_sync_skills` is only ever called with `.claude/skills-global/` as `src_dir` (`sync_claude_dirs:346`), confirming the dead-code diagnosis (Gap 3).

**Cited sibling issues/PRs re-checked:**
- #1783 / PR #1806 — closed/merged 2026-06-26; established the probe convention and `rule_13`. This issue hardens those guards. Confirmed.
- #2078 (hotfix 80fdfc26) and `61b55ce7` (the 5-leak hand-fix) — both landed; the 5 fixes are in the tree. Verified all 5 skills (`audit-models`, `claude-standards`, `mermaid-render`, `do-issue`, `do-deploy-example`) live under `.claude/skills-global/` and currently pass `rule_13`.

**Commits on main since issue was filed (touching referenced files):**
- `cf2d190d3` (#2096) — added `RENAMED_REMOVALS` entry `("skills", "do-xref-audit")` / `("skills", "do-xref")`. Fresh in-tree example of a valid Gap-4 entry (per the issue comment). Must PASS the Gap-4 guard.
- `7b52b005c` (#2065) — added four more `RENAMED_REMOVALS` orphan-sweep entries (`audit-next-tool`, `do-design-review`, `get-telegram-messages`, `searching-message-history`). More valid in-tree fixtures for Gap 4.
- Neither commit changed `audit_skills.py`; the four gaps are all still present and confirmed against source this session.

**Active plans in `docs/plans/` overlapping this area:** none touching `audit_skills.py` or `hardlinks.py`.

**Notes:** The issue's "900 PASS baseline" (AC5) is now **943 PASS / 5 WARN / 0 FAIL** (63 skills) — skills were added since filing. The absolute number is not load-bearing; the invariant is **0 FAIL preserved on the current tree**. The 5 pre-existing WARNs are unrelated (fleet-description budget, rule 14) and out of scope.

## Prior Art

- **Issue #1783 / PR #1806**: "Generalize all global skills to be repo-agnostic" — established the probe-sentence convention (`PROBE_SUFFIX`) and `rule_13_coupling_signals`. This issue hardens the guards that #1783 introduced; the design must not regress #1783's Bucket-A clean skills (`mermaid-render`, `reclassify`, `do-discover-paths`) into false positives.
- **Commit `61b55ce7`**: hand-fix of the 5 leaks (`audit-models`, `claude-standards`, `mermaid-render` → `/sdlc`/`/setup`; `do-issue` → `/sdlc`; `do-deploy-example` → `sdk_client.py`/`SDLC_TARGET_REPO`). This is the *symptom*; the plan is the *cure*. The reverted diff is the natural AC1 test fixture.
- No prior attempt tried to build these specific four guards — greenfield hardening on top of #1783's foundation.

## Data Flow

The two guards run at two different moments, both **before** a skill reaches another machine:

1. **Author edits a skill body** under `.claude/skills-global/{skill}/` (SKILL.md or a sub-`.md`).
2. **`/do-skills-audit` (or `pytest`)** runs `audit_skills.py` → `audit_skill()` dispatches per-skill rules → `rule_13` (and the new sibling rule) inspect the body for coupling signals; global root only (`dir_label != "project"`). FAIL when `report.summary["fail"] > 0` → non-zero exit.
3. **`/update` runs `sync_claude_dirs()`** → `_sync_skills(.claude/skills-global, ~/.claude/skills)` hardlinks each global skill + sub-files; `_cleanup_renamed()` removes `RENAMED_REMOVALS` orphans.
4. **Output:** a skill lands in `~/.claude/skills/` on every machine — or the audit/test red-states before it can, catching the regression at author time.

Gaps 1–2 harden step 2 (audit coverage). Gaps 3–4 harden step 3 (sync correctness) via tests that assert the invariants the sync relies on.

## Appetite

**Size:** Medium

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (confirm the Gap-1 escape-hatch design and the Gap-3 delete-vs-wire decision)
- Review rounds: 1

Four small, mostly independent guards in two files plus their tests. The coding is modest; the care is in false-positive avoidance (Gap 1) and not red-stating the current clean tree.

## Prerequisites

No prerequisites — this work has no external dependencies. All logic is local Python operating on the repo's own files and git history.

## Solution

### Key Elements

- **Bucket-C coupling rule (Gap 1)**: a new audit rule (sibling to `rule_13`, e.g. `rule_21_bucket_c_coupling`) that flags a global skill body invoking a *project-only* skill as a slash-command (e.g. `` `/sdlc` ``) or referencing a curated internal-infra token (`sdk_client.py`, `SDLC_TARGET_REPO`), unless the reference carries a conditional/probe escape hatch. Project-only skill names are **derived at runtime** from the `.claude/skills/` directory listing — not hardcoded — to stay repo-agnostic.
- **Sub-file scan (Gap 2)**: the coupling checks scan every `*.md` under each global skill dir, not just `SKILL.md`. A sub-file signal counts as covered if the skill's `SKILL.md` carries the probe. Non-`.md` files (especially `.py`) are excluded so `audit_skills.py`'s own literal token list is never self-flagged.
- **Dead-code removal + invariant test (Gap 3)**: delete `PROJECT_ONLY_SKILLS` (no-legacy) and add a filesystem-derived unit test asserting no `.claude/skills/` directory name ever appears among `sync_claude_dirs` destinations — independent of any hand-maintained set.
- **RENAMED_REMOVALS completeness test (Gap 4)**: a unit test that walks git history for deleted `SKILL.md` files under each skill root and asserts every vanished skill name appears in `RENAMED_REMOVALS`, treating the `do-xref-audit` (#2096) and #2065 sweep entries as valid in-tree fixtures.

### Flow

Author edits a global skill body → runs `/do-skills-audit` (or the unit tests) → a coupling signal without an escape hatch red-states the audit → author adds conditional wording, a probe, or genericizes → audit passes → `/update` syncs cleanly. Independently: author moves a skill between the two skill dirs → the RENAMED_REMOVALS completeness test fails until the matching entry is added.

### Technical Approach

**Gap 1 — new rule, not an extension of `COUPLING_SIGNALS` (decision, with rationale).** Folding the new tokens into the existing `COUPLING_SIGNALS` tuple would reuse `rule_13`'s blanket escape hatch: *any* `PROBE_SUFFIX` anywhere in `SKILL.md` = PASS. But two of the five leaked skills (`audit-models`, `do-issue`) **already carry the probe** (verified this session). Their reverted fixtures would then still PASS via the probe, violating AC1 ("all 5 reverted would FAIL"). Therefore the Bucket-C rule needs a **distinct, reference-scoped escape hatch**:
- **Signal A — Bucket-C skill invocation**: a slash-invocation `` `/{name}` `` (backtick- or boundary-anchored, leading `/`) where `{name}` is a directory under `.claude/skills/` (derived live) **and not** itself a global skill. Anchor on the leading `/` and a trailing word boundary so `/setup` does not match `/setups` and `/sdlc` does not match inside `/do-sdlc`.
- **Signal B — curated infra tokens**: `sdk_client.py`, `SDLC_TARGET_REPO` (extendable). These are repo-specific filenames/env-vars consistent with the existing curated tokens (`valor-`, `sdlc-tool`); hardcoding is acceptable and harmless in foreign repos (they simply never appear).
- **Escape hatch (reference-scoped)**: a match is *covered* when the same line (or same sentence) carries conditional framing — `in this repo`, `this repo's`, or the canonical `PROBE_SUFFIX`. This is line/sentence-proximity, not a doc-wide free pass, so a stray "in this repo" elsewhere cannot excuse an unrelated bare `/sdlc`. This makes the corrected `do-issue` ("...router (in this repo: `/sdlc`)") PASS while its reverted form ("invoked by `/sdlc`") FAILs — satisfying AC1 for the probe-carrying skills.

**Gap 2 — sub-file scan.** In `audit_skill()`, gather the concatenated text of every `*.md` sub-file (excluding `SKILL.md`, already the `body`) and feed both `rule_13` and the new rule the union of (SKILL.md body + sub-file text) for signal detection, while probe/conditional coverage is read from `SKILL.md`. Restrict strictly to `*.md`: `.py`/`.pyc`/scripts are excluded so `audit_skills.py`'s literal `COUPLING_SIGNALS = ("sdlc-tool", ...)` data is never counted as a leak. Reuse the existing `rglob` pattern from `_sync_skills`/`rule_18` for consistency.

**Gap 3 — delete + test.** Remove the `PROJECT_ONLY_SKILLS` set and its dead call site in `_sync_skills`. Add `tests/unit/test_update_hardlinks.py::test_no_project_only_skill_is_a_sync_destination`: build the real destination set from `sync_claude_dirs` (via a dry-run/introspection of the source roots) and assert `set(names under .claude/skills/) ∩ set(sync destination names) == ∅`. The invariant is derived from the live filesystem, so it holds even if someone later widens `_sync_skills`'s scan root. (Optional defense-in-depth — Open Question 2 — a live-derived guard *inside* `_sync_skills`.)

**Gap 4 — git-history completeness test.** Add `tests/unit/test_update_hardlinks.py::test_renamed_removals_covers_deleted_skills`: for each root (`.claude/skills-global/`, `.claude/skills/`), run `git log --diff-filter=D --name-only -- '<root>/*/SKILL.md'`, extract each deleted skill's directory name, and assert it appears in `RENAMED_REMOVALS` as `("skills", name)` **or** currently exists on disk in the *other* root (a move that was correctly recorded, or a rename already covered). Guard against shallow-clone/CI history gaps (Risk 2). The `do-xref-audit` (#2096) and #2065 entries are the required in-tree fixtures that must PASS.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `audit_skills.py` rules never raise on empty/garbage input (existing contract — `rule_13` is "deterministic on empty/garbage input, never raises"). The new rule must preserve this: assert `rule_21(name, "")` and `rule_21(name, None)` return a PASS Finding, not an exception.
- [ ] The Gap-4 git walk wraps `subprocess.run` and treats git-unavailable / non-zero exit as a **skip** (like `_git_tracked_files` returning `None`), not a hard test failure — assert the skip path.
- [ ] No new `except Exception: pass` blocks introduced; any caught error emits an observable Finding or test skip.

### Empty/Invalid Input Handling
- [ ] New rule on a skill with no sub-files, an empty sub-file, and a whitespace-only body → PASS, no crash.
- [ ] Gap-3 test when `.claude/skills/` is absent (foreign-repo shape) → invariant vacuously holds, no crash.
- [ ] Gap-4 test when `git log` returns empty (no deletions) → PASS.

### Error State Rendering
- [ ] A genuine Gap-1 violation renders as a `FAIL` Finding whose message names the matched signal and the skill (mirrors `rule_13`'s message), so `format_human`/`format_json` surface it and `main()` exits non-zero.
- [ ] The reverted-5-fixtures test asserts the FAIL is *observable* in the report, not merely a return value.

## Test Impact

- [ ] `tests/unit/test_skills_audit.py` — UPDATE: add cases for the new Bucket-C rule (5 reverted fixtures FAIL; corrected forms PASS; `do-sdlc`/`do-deploy-example`-style legitimate mentions with conditional/probe cover PASS) and for the sub-file scan (planted `sdlc-tool` in a `CHECKS.md` fixture without SKILL.md probe → FAIL; with probe → PASS). Existing assertions unchanged.
- [ ] `tests/unit/test_update_hardlinks.py` — UPDATE: remove any assertion referencing `PROJECT_ONLY_SKILLS` (Gap 3 deletes it); add the two new invariant tests (no-project-only-destination; RENAMED_REMOVALS completeness). Verify no existing test imports `PROJECT_ONLY_SKILLS` before deleting — `grep -rn PROJECT_ONLY_SKILLS tests/`.
- [ ] `tests/unit/test_skills_audit_reflection.py` — UPDATE only if it asserts an exact rule count or the FAIL/PASS total; the new rule adds a per-skill Finding, shifting counts. Confirm and adjust if needed.
- [ ] Full audit baseline — VERIFY: `audit_skills.py --json --no-sync` still reports `0 FAIL` on the current tree after the new rule lands (AC5).

## Rabbit Holes

- **Perfect natural-language conditional detection.** Do not build an NLP parser for "is this reference conditional." A small allowlist of markers (`in this repo`, `this repo's`, `PROBE_SUFFIX`) checked at line/sentence scope is sufficient. Anything fancier is scope creep.
- **`do-deploy-example` self-reference.** This template legitimately says `/do-deploy` and describes copying to `.claude/skills/do-deploy/`. `do-deploy` *is* a project-only skill name, so Signal A could false-positive it. Do not special-case it in code — it already carries conditional/template framing; if the audit flags it, add conditional wording to the offending line rather than an exclusion list. The AC5 "audit clean on current tree" gate will surface any such hit during build.
- **Scanning non-`.md` sub-files.** Tempting to scan scripts too, but that would flag `audit_skills.py`'s own token literals and any example snippets. Stay `*.md`-only.
- **Reimplementing `git log --follow` per skill for Gap 4.** A single `--diff-filter=D` name-only walk over the two globs is enough; per-file `--follow` is O(skills) subprocesses and slower with no added correctness.
- **Widening `COUPLING_SIGNALS` to catch everything.** The issue and #1783 both warn that over-broad signals false-positive Bucket-A clean skills. Keep signals executable/invocation-scoped.

## Risks

### Risk 1: New Bucket-C rule false-positives a currently-clean global skill
**Impact:** Audit red-states on the current tree (breaks AC5), or blocks a legitimate skill body that discusses the pipeline (`do-sdlc`, `do-deploy-example`).
**Mitigation:** Reference-scoped escape hatch (conditional wording OR probe on the same line/sentence). Build step MUST run the full audit (`--no-sync`) and confirm `0 FAIL` on the current tree before opening the PR. Any hit is either a real residual leak (fix the line) or a hatch gap (widen the marker set) — resolved during build, asserted by AC5.

### Risk 2: Gap-4 git-history test is fragile under shallow clones / CI
**Impact:** `git log --diff-filter=D` returns incomplete history in a shallow CI checkout, causing false failures or false passes.
**Mitigation:** Treat git-unavailable or a suspiciously-empty history as a **skip** (return `None`, mirror `_git_tracked_files`). Anchor assertions to *present* deletions only; never assert "history must contain N deletions." Document the local-run expectation. If CI shallowness proves unworkable, fall back to the cheaper invariant (Open Question 3).

### Risk 3: Deleting `PROJECT_ONLY_SKILLS` breaks an unseen importer
**Impact:** An import elsewhere (a reflection, a script, a test) referencing the symbol raises `ImportError`.
**Mitigation:** `grep -rn 'PROJECT_ONLY_SKILLS' .` across the repo before deletion; the plan's Verification table includes a `match count == 0` anti-criterion asserting no references remain post-change.

## Race Conditions

No race conditions identified — all logic is synchronous, single-threaded, and operates on read-only filesystem/git state. The audit and the sync do not run concurrently on shared mutable state within the scope of this change.

## No-Gos (Out of Scope)

- `[SEPARATE-SLUG N/A]` Widening the fleet-description budget warning (rule 14) — the issue's Recon "Dropped" bucket explicitly excludes it as unrelated to repo-agnosticism. Not filed as its own issue; genuinely nothing to do here, listed only to record the audit's own scoping decision. *(No anti-criterion required — advisory scope note, not a forbidden code outcome.)*
- Nothing else deferred — every relevant guard for the four gaps is in scope for this plan.

## Update System

No update-script changes required — this feature is purely internal to the audit script and the sync module's tests. The sync behavior itself is unchanged (Gap 3 removes dead code that never executed a real branch; sync destinations are identical before and after). The new guards run at author/CI time via `/do-skills-audit` and `pytest`, both already wired.

## Agent Integration

No agent integration required — `audit_skills.py` is already reachable via the `/do-skills-audit` skill, and the new unit tests run under the existing `pytest` surface. No new CLI entry point, MCP tool, or bridge import is needed; nothing new must be exposed to the Telegram agent.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/skill-context-convention.md` — document the new Bucket-C coupling rule (derived project-only skill names + curated infra tokens) and the sub-file scan, alongside the existing `rule_13`/`PROBE_SUFFIX` description.
- [ ] Update the `.claude/skills-global/do-skills-audit/SKILL.md` rule inventory (if it enumerates rules) to include the new rule.

### Inline Documentation
- [ ] Docstring on the new rule mirroring `rule_13`'s (what it guards, why FAIL not WARN, empty-input determinism, escape-hatch semantics).
- [ ] Comment at the `RENAMED_REMOVALS` completeness test and the Gap-3 invariant test explaining the derived-from-filesystem rationale.
- [ ] Comment in `hardlinks.py` where `PROJECT_ONLY_SKILLS` was removed is unnecessary (no-legacy — leave no tombstone); instead ensure the `_sync_skills` docstring still explains the structural (source-dir) exclusion.

No external documentation-site changes — these are internal developer guards.

## Success Criteria

- [ ] The new Bucket-C rule FAILs a global `SKILL.md` referencing `` `/sdlc` `` or `` `/setup` `` without conditional/probe cover; the 5 fixed files from `61b55ce7`, reverted in a test fixture, all FAIL (AC1).
- [ ] Sub-files under global skill dirs are scanned: a planted `sdlc-tool` reference in a `CHECKS.md` without SKILL.md probe cover is flagged; with probe cover it passes (AC2).
- [ ] A unit test asserts no `.claude/skills/` directory name appears among `sync_claude_dirs` destinations, independent of any hand-maintained set; `PROJECT_ONLY_SKILLS` is deleted (AC3).
- [ ] A unit test fails when a skill dir disappears from a skill root without a corresponding `RENAMED_REMOVALS` entry; the `do-xref-audit` (#2096) and #2065 sweep entries PASS as in-tree fixtures (AC4).
- [ ] `audit_skills.py --json --no-sync` reports `0 FAIL` on the current tree after the new rule lands (AC5 — 943 PASS baseline preserved modulo the added rule).
- [ ] No references to `PROJECT_ONLY_SKILLS` remain anywhere in the repo.
- [ ] Tests pass (`/do-test`).
- [ ] Documentation updated (`/do-docs`).

## Team Orchestration

### Team Members

- **Builder (audit-rule)**
  - Name: `audit-builder`
  - Role: Implement the Gap-1 Bucket-C rule and the Gap-2 sub-file scan in `audit_skills.py` + their tests in `test_skills_audit.py`.
  - Agent Type: builder
  - Domain: untrusted-input / text-parsing (false-positive avoidance)
  - Resume: true

- **Builder (sync-invariants)**
  - Name: `sync-builder`
  - Role: Gap-3 (delete `PROJECT_ONLY_SKILLS` + invariant test) and Gap-4 (RENAMED_REMOVALS completeness test) in `hardlinks.py` + `test_update_hardlinks.py`.
  - Agent Type: builder
  - Resume: true

- **Validator (guards)**
  - Name: `guards-validator`
  - Role: Verify AC1–AC5, run the full audit clean, confirm no `PROJECT_ONLY_SKILLS` references remain.
  - Agent Type: validator
  - Resume: true

### Available Agent Types

Tier 1 core agents (`builder`, `validator`, `documentarian`) suffice; no service agents needed.

## Step by Step Tasks

### 1. Bucket-C coupling rule + sub-file scan (Gap 1 + Gap 2)
- **Task ID**: build-audit-rule
- **Depends On**: none
- **Validates**: `tests/unit/test_skills_audit.py`
- **Assigned To**: audit-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `rule_21_bucket_c_coupling(skill_name, body, project_only_names)` (or equivalent) to `audit_skills.py`: derive `project_only_names` from `.claude/skills/` dir listing at call time; match Signal A (boundary-anchored `` `/{name}` `` slash-invocations) and Signal B (curated `sdk_client.py`, `SDLC_TARGET_REPO`); apply the reference-scoped conditional/probe escape hatch. FAIL severity, empty-input safe, never raises.
- Wire it into `audit_skill()` alongside `coupling`, global-root only (skip when `dir_label == "project"`).
- Extend the coupling scan (both `rule_13` and the new rule) to consume the union of `SKILL.md` body + every `*.md` sub-file's text; probe/conditional coverage read from `SKILL.md`. Exclude non-`.md` files explicitly.
- Add tests: 5 reverted `61b55ce7` fixtures FAIL; corrected forms PASS; conditional/probe-covered mentions PASS; planted `sdlc-tool` in a `CHECKS.md` fixture FAIL without probe / PASS with probe; empty/None/whitespace bodies PASS.

### 2. Sync invariants (Gap 3 + Gap 4)
- **Task ID**: build-sync-invariants
- **Depends On**: none
- **Validates**: `tests/unit/test_update_hardlinks.py`
- **Assigned To**: sync-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `PROJECT_ONLY_SKILLS` and its dead call site in `_sync_skills`; keep the source-dir structural exclusion and its docstring explanation. First `grep -rn PROJECT_ONLY_SKILLS` repo-wide and remove any references.
- Add `test_no_project_only_skill_is_a_sync_destination`: derive destination names from `sync_claude_dirs` and assert disjoint from `.claude/skills/` dir names.
- Add `test_renamed_removals_covers_deleted_skills`: git `--diff-filter=D` walk over both skill roots; assert each vanished skill name is in `RENAMED_REMOVALS` or present in the other root; treat git-unavailable/empty history as skip. Confirm `do-xref-audit` and the #2065 entries PASS.

### 3. Validation
- **Task ID**: validate-guards
- **Depends On**: build-audit-rule, build-sync-invariants
- **Assigned To**: guards-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_skills_audit.py tests/unit/test_update_hardlinks.py`.
- Run `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py --json --no-sync` and confirm `0 FAIL`.
- Confirm `grep -rn PROJECT_ONLY_SKILLS .` returns nothing.
- Verify each AC1–AC5 explicitly; report pass/fail.

### 4. Documentation
- **Task ID**: document-guards
- **Depends On**: validate-guards
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/skill-context-convention.md` with the new rule and sub-file scan.
- Update the do-skills-audit rule inventory if one exists.

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-guards
- **Assigned To**: guards-validator
- **Agent Type**: validator
- **Parallel**: false
- Re-run tests + audit + full ruff.
- Confirm all Success Criteria including docs.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Guard tests pass | `pytest tests/unit/test_skills_audit.py tests/unit/test_update_hardlinks.py -q` | exit code 0 |
| Audit clean on current tree | `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py --json --no-sync \| python3 -c "import json,sys; assert json.load(sys.stdin)['summary']['fail']==0"` | exit code 0 |
| No PROJECT_ONLY_SKILLS references | `grep -rn "PROJECT_ONLY_SKILLS" scripts/ tests/ .claude/` | match count == 0 |
| New rule present | `grep -c "def rule_21" .claude/skills-global/do-skills-audit/scripts/audit_skills.py` | output > 0 |
| RENAMED_REMOVALS test present | `grep -c "renamed_removals_covers_deleted" tests/unit/test_update_hardlinks.py` | output > 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Gap-1 rule granularity.** The plan recommends a **new rule** (e.g. `rule_21`) with a reference-scoped conditional/probe escape hatch, *not* an extension of `COUPLING_SIGNALS` — because two of the five leaked skills (`audit-models`, `do-issue`) carry the probe and would otherwise still PASS when reverted, breaking AC1. Confirm this split is acceptable, or accept that AC1's "all 5 FAIL" is relaxed for probe-carrying skills.
2. **Gap-3 defense-in-depth.** Delete `PROJECT_ONLY_SKILLS` and rely on the structural (source-dir) exclusion + the new invariant test (recommended, no-legacy). Or additionally make `_sync_skills` compute a live exclusion set from `.claude/skills/` as a belt-and-suspenders guard against a future scan-root widening? The test alone catches the regression; the live guard prevents it. Preference?
3. **Gap-4 mechanism if CI history is shallow.** If the `git --diff-filter=D` walk proves unreliable in CI, fall back to a cheaper invariant — e.g. assert that every `("skills", name)` in `RENAMED_REMOVALS` is *not* currently present in **both** skill roots (a stale entry would mean the skill is back), plus a lighter "moved skills are recorded" check. Acceptable fallback, or is the git walk worth keeping local-only (skip in CI)?
