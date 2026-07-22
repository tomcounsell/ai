---
status: Ready
type: chore
appetite: Medium
owner: Valor Engels
created: 2026-07-22
tracking: https://github.com/tomcounsell/ai/issues/2079
last_comment_id: 4977658840
revision_applied: true
revision_applied_at: 2026-07-22T12:51:49Z
---

# Harden repo-agnosticism guards

## Problem

This repo is the canonical source for **global skills** (`.claude/skills-global/`) that `/update` hardlinks to `~/.claude/skills/` on every machine. Global skill bodies must stay repo-agnostic — behavior coupled to this repo's infra defers to the skill-context seam. Two guards protect that invariant: `rule_13_coupling_signals` in `.claude/skills-global/do-skills-audit/scripts/audit_skills.py` and the sync exclusions in `scripts/update/hardlinks.py`. A 3-agent documentation audit (2026-07-14) found four gaps where a regression can ship undetected.

**Current behavior:**

1. **`COUPLING_SIGNALS` misses whole signal classes.** The set (`audit_skills.py:123`) is `sdlc-tool`, `python -m tools.`, `reflections.`, `valor-`, `config/identity.json`. It does not catch Bucket-C skill invocations (`/sdlc`, `/setup`, `/prime`, `/do-deploy` — project-only skills that don't exist off this repo) or internal infra tokens (`sdk_client.py`, `SDLC_TARGET_REPO`). Five real leaks shipped through a passing `rule_13` and were hand-fixed in `61b55ce7`; nothing stops recurrence.
2. **`rule_13` only scans `SKILL.md`.** Sub-files (`CRITICS.md`, `CHECKS.md`, templates, sub-skills) are hardlinked to every machine too, but are never scanned. **Verified this session (not assumed):** two sub-files already contain coupling tokens — `.claude/skills-global/audit-hooks/BEST_PRACTICES.md:118` (`python -m tools.my_tool "$@"`) and `.claude/skills-global/audit-tools/CHECKS.md:171` (`valor-{cli-name} --help`). Both are *generic placeholder examples* (`my_tool`, `{cli-name}`), and both parent `SKILL.md`s carry the probe sentence (`audit-hooks/SKILL.md:14`, `audit-tools/SKILL.md:15`). So under the Gap-2 design (probe/conditional coverage read from the skill's `SKILL.md`) they are *covered*, not leaks — but they prove the surface is unguarded: a future non-placeholder coupling reference in a sub-file whose SKILL.md lacks the probe would ship undetected today.
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

- **Issue #1783 / PR #1806**: "Generalize all global skills to be repo-agnostic" — established the probe-sentence convention (`PROBE_SUFFIX`) and `rule_13_coupling_signals`. This issue hardens the guards that #1783 introduced; the design must not regress #1783's Bucket-A clean skills (e.g. `reclassify`, `do-discover-paths`) into false positives. **Note:** `mermaid-render` is *not* a clean-skill fixture here — it was one of the five skills that leaked `/setup` past `rule_13` and was hand-fixed in `61b55ce7` (see below). It is an AC1 *reverted-to-FAIL* fixture, not a must-stay-PASS clean skill.
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
- **Signal A — Bucket-C skill invocation**: a slash-invocation whose **full slash-token** equals a directory name under `.claude/skills/` (derived live) that is **not** itself a global skill. Capture the whole token with `(?<![\w-])/([a-z0-9][a-z0-9-]*)` and flag **only on exact set-membership of the captured group** — never on a substring/prefix match. **This is the B1 fix.** A plain `\b` word boundary treats a hyphen as a boundary, so `\b`-anchoring `/do-deploy` false-matches inside the legitimate *global* skill body `do-deploy-example` (the token `/do-deploy-example`), red-stating a clean skill and breaking AC5. Full-token capture makes `/do-deploy-example` capture `do-deploy-example` (a global skill, **not** in the project-only set → no flag) while a bare `/do-deploy` captures exactly `do-deploy` (in the set → flag). The leading negative lookbehind `(?<![\w-])` guards the front edge and the greedy `[a-z0-9-]*` consumes the trailing hyphenated remainder, so **both edges are hyphen-safe**; `/setup` never matches `/setups`, and `/sdlc` never matches inside `/do-sdlc`.
- **Signal B — curated infra tokens**: `sdk_client.py`, `SDLC_TARGET_REPO` (extendable). These are repo-specific filenames/env-vars consistent with the existing curated tokens (`valor-`, `sdlc-tool`); hardcoding is acceptable and harmless in foreign repos (they simply never appear).
- **Scan surface (locked after the C1 sweep — see "Signal A/B current-tree budget" below)**: Signal A/B run over the **frontmatter-stripped `body`** (matching `rule_13`, which already receives `body`, so a `/sdlc` in a `description:` frontmatter line is out of scope) and **skip fenced code blocks** (```` ``` ````-delimited). A coupling token inside a code fence is a *usage demonstration* (e.g. do-sdlc's `SDLC_TARGET_REPO=$(git rev-parse …)` bash example) that cannot carry same-line prose framing, not a prose behavioral-coupling claim. Fence-skip is **AC1-safe**: verified against the `61b55ce7` diff, all five reverted fixtures leak in *prose* (audit-models:36, claude-standards:46, mermaid-render:1709, do-issue:82, do-deploy-example:56/:59); do-deploy-example's single fenced leak (:68) is redundant with its two prose leaks, so every reverted fixture still FAILs. `rule_13`'s existing executable signals keep scanning fences unchanged — only the *new* Signal A/B skip them.
- **Escape hatch (reference-scoped, same-line only)**: a match is *covered* when the **same physical line** as the matched signal carries conditional framing — `in this repo`, `this repo's`, or the canonical `PROBE_SUFFIX`. Same-line is a deterministic, unambiguous unit (split on `\n`); "same sentence" is rejected because sentence segmentation over Markdown (code fences, list items, abbreviations, `:` in `(in this repo: /sdlc)`) is non-deterministic and would make coverage depend on a fuzzy tokenizer. Same-line proximity is not a doc-wide free pass, so a stray "in this repo" on another line cannot excuse an unrelated bare `/sdlc`. This makes the corrected `do-issue` ("...router (in this repo: `/sdlc`)" — signal and conditional on one line) PASS while its reverted form ("invoked by `/sdlc`") FAILs — satisfying AC1 for the probe-carrying skills. (The `PROBE_SUFFIX` in `SKILL.md` is also honored as whole-file cover for `rule_13`'s existing contract; the same-line rule is the *additional* Bucket-C constraint.)

**Signal A/B current-tree budget (C1 — grep sweep run this revision, before locking the rule).** The critique required an actual survey of the other global skills for bare project-only slash-refs rather than an assumed-clean baseline. Ran the exact proposed regex + Signal-B token match over every `*.md` under `.claude/skills-global/`, after frontmatter-strip and fence-skip, case-insensitive escape-hatch markers. **Result: 8 uncovered prose hits across 3 skills** that the new rule will flag on the current tree, plus 0 uncovered Signal-B hits (do-sdlc's three `SDLC_TARGET_REPO` occurrences are all inside a bash fence → skipped). These are all *legitimate* references to project-only skills that today lack same-line conditional framing — exactly the under-conditionalization the guard is meant to name. The explicit budget the build must spend to hold AC5 (`0 FAIL`):

| Skill (global) | File:line(s) | Token | Disposition to reach `0 FAIL` |
|---|---|---|---|
| `do-sdlc` | SKILL.md:9, :13, :51, :173 (heading), :175 (table cell) | `/sdlc` | Add same-line conditional framing ("in this repo") on each prose/heading line; for the comparison-table cell, add `(in this repo)` in the `/sdlc` cell. do-sdlc already carries the whole-file `PROBE_SUFFIX`, but the Bucket-C rule is deliberately stricter than `rule_13` (same-line, not whole-file — required so AC1's probe-carrying reverted fixtures FAIL), so these lines still need same-line cover. |
| `do-deploy-example` | SKILL.md:35, :194 | `/do-deploy` | Template self-reference (the Rabbit Hole case). Add conditional/template framing on the offending line — do **not** add a code exclusion. |
| `do-plan` | SCOPING.md:65 | `/update` | Add "in this repo" on the `(via `/update`)` line. |

Excluded by the locked scan surface (no build action needed): do-issue/SKILL.md:3 and do-merge/SKILL.md:3 (`/sdlc` in `description:` frontmatter — not in `body`); do-deploy-example:61 and do-sdlc's `SDLC_TARGET_REPO` bash block (fenced). The build MUST re-run the sweep and confirm `0 FAIL` after adding the framing; any residual hit is either a real leak (fix the line) or a hatch-marker gap (widen the marker set).

**Gap 2 — sub-file scan.** In `audit_skill()`, gather the concatenated text of every `*.md` sub-file (excluding `SKILL.md`, already the `body`) and feed both `rule_13` and the new rule the union of (SKILL.md body + sub-file text) for signal detection, while probe/conditional coverage is read from `SKILL.md`. Restrict strictly to `*.md`: `.py`/`.pyc`/scripts are excluded so `audit_skills.py`'s literal `COUPLING_SIGNALS = ("sdlc-tool", ...)` data is never counted as a leak. Reuse the existing `rglob` pattern from `_sync_skills`/`rule_18` for consistency. **Self-exempt the auditor skill (C2):** skip `do-skills-audit`'s own directory from the sub-file scan entirely. Its `SKILL.md` rule inventory and sub-file docs *describe* the coupling signals — and the Documentation task in this plan will add prose to that inventory naming `/sdlc`, `sdk_client.py`, and `SDLC_TARGET_REPO` to document the new Bucket-C rule — so scanning `do-skills-audit` against its own signals would self-trip a FAIL on the very docs that explain the rule. The exemption is a single `if skill_name == "do-skills-audit": skip` guard (analogous to the existing `dir_label == "project"` skip), and it is forward-safe: the auditor is this repo's own tooling, never a repo-agnostic body shipped for foreign execution semantics.

**Gap 3 — delete + test + migrate the existing test coverage.** Remove the `PROJECT_ONLY_SKILLS` set and its dead call site in `_sync_skills`.

*The real importer is `tests/unit/test_symlinks.py`* (NOT `test_update_hardlinks.py` — verified via `grep -rn PROJECT_ONLY_SKILLS`). That module imports the symbol at `test_symlinks.py:12` and exercises it four ways, all of which must be migrated in the same change or collection ImportErrors:
- `test_project_only_skills_exist` (`:55`) and `test_project_only_skills_is_set` (`:60`) assert on the symbol directly (via `EXPECTED_PROJECT_ONLY`, `:48`). **DELETE** both — they test a symbol that no longer exists.
- `test_sync_skills_skips_project_only` (`:91`) and `test_sync_skills_counts_skipped_project_only` (`:105`) call `_sync_skills` **directly with a `.claude/skills/`-shaped source** and rely on the runtime filter to skip `telegram`/`reading-sms-messages`. Removing the filter changes their expected outcome (both skills would now sync; `result.created` becomes 3, not 1). **DELETE** both — they validate the old runtime mechanism, which is being removed. The invariant they encoded ("project-only skills never land in `~/.claude/skills/`") is *migrated* to the new filesystem-derived test below, which asserts it structurally rather than via a now-deleted filter.
- Remove the `PROJECT_ONLY_SKILLS` import (`:12`) and the `EXPECTED_PROJECT_ONLY` constant (`:48`). Update the stale comment at `test_symlinks.py:209` (it credits the filter for skipping `telegram`; post-change `telegram` is skipped *structurally* because it lives under `skills/`, which is never a sync source).

Add the migrated invariant as `tests/unit/test_symlinks.py::test_no_project_only_skill_is_a_sync_destination` (co-located with the coverage it replaces): build the real destination set from `sync_claude_dirs` (via a dry-run/introspection of the source roots) and assert `set(names under .claude/skills/) ∩ set(sync destination names) == ∅`. The invariant is derived from the live filesystem, so it holds even if someone later widens `_sync_skills`'s scan root. (Optional defense-in-depth — Open Question 2 — a live-derived guard *inside* `_sync_skills`.)

**Gap 4 — two complementary tests: one always-on (CI-safe), one git-history (local enhancement).** The critique flagged that a git-history-only test silently *skips* under CI shallow clones — providing zero protection exactly where regressions merge. So Gap 4 ships **two** tests in `tests/unit/test_update_hardlinks.py` (alongside the existing `test_renamed_removals_contains_issue_2065_orphans`):

1. `test_renamed_removals_entries_are_not_stale` — **always runs, no git dependency, real coverage under shallow CI.** For every `("skills", name)` in `RENAMED_REMOVALS`, assert the named skill is *not currently present in both skill roots at once* (a live skill in both `skills/` and `skills-global/` would mean the removal entry is stale/wrong), and that at least one of {present in exactly one root, absent from both} holds. This is a pure filesystem invariant — it catches the most common regression (a `RENAMED_REMOVALS` entry that contradicts the current tree) with no history walk. This is the promoted Open-Question-3 fallback, now a *primary* always-on guard rather than a conditional one.

2. `test_renamed_removals_covers_deleted_skills` — **git-history completeness, skips cleanly when history is shallow/unavailable.** For each root (`.claude/skills-global/`, `.claude/skills/`), run `git log --diff-filter=D --name-only -- '<root>/*/SKILL.md'`, extract each deleted skill's directory name, and assert it appears in `RENAMED_REMOVALS` as `("skills", name)` **or** currently exists on disk in **any skill root** (either `.claude/skills/` or `.claude/skills-global/`). **This is the C3 fix:** the assertion must accept presence in *any* root, not only "the other root" — a skill deleted from a root and later **re-added to the same root** (a delete-and-re-add, which needs no `RENAMED_REMOVALS` entry because nothing is stale) is live on disk in the root it was deleted from; an "other-root-only" check would false-fail it. To detect shallow clones deterministically rather than mis-passing on truncated history, gate on `git rev-parse --is-shallow-repository` (and git-unavailable / non-zero exit): when shallow or unavailable, `pytest.skip` with an explicit reason — never a silent pass and never a false failure. The `do-xref-audit` (#2096) and #2065 entries are the in-tree fixtures that must PASS.

Because test 1 always runs, CI retains genuine `RENAMED_REMOVALS` protection even when test 2 skips; test 2 adds the stronger "did you forget an entry when you deleted a skill" check for full-history local runs.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `audit_skills.py` rules never raise on empty/garbage input (existing contract — `rule_13` is "deterministic on empty/garbage input, never raises"). The new rule must preserve this: assert `rule_21(name, "")` and `rule_21(name, None)` return a PASS Finding, not an exception.
- [ ] The Gap-4 git walk wraps `subprocess.run` and treats git-unavailable / non-zero exit / **shallow clone** (`git rev-parse --is-shallow-repository` == true) as a **skip** (like `_git_tracked_files` returning `None`), not a hard test failure — assert the skip path. The always-on `test_renamed_removals_entries_are_not_stale` has no git dependency and must never skip.
- [ ] No new `except Exception: pass` blocks introduced; any caught error emits an observable Finding or test skip.

### Empty/Invalid Input Handling
- [ ] New rule on a skill with no sub-files, an empty sub-file, and a whitespace-only body → PASS, no crash.
- [ ] Gap-3 test when `.claude/skills/` is absent (foreign-repo shape) → invariant vacuously holds, no crash.
- [ ] Gap-4 test when `git log` returns empty (no deletions) → PASS.

### Error State Rendering
- [ ] A genuine Gap-1 violation renders as a `FAIL` Finding whose message names the matched signal and the skill (mirrors `rule_13`'s message), so `format_human`/`format_json` surface it and `main()` exits non-zero.
- [ ] The reverted-5-fixtures test asserts the FAIL is *observable* in the report, not merely a return value.

## Test Impact

- [ ] `tests/unit/test_skills_audit.py` — UPDATE: add cases for the new Bucket-C rule (5 reverted fixtures FAIL; corrected forms PASS; `do-sdlc`/`do-deploy-example`-style legitimate mentions with same-line conditional/probe cover PASS) and for the sub-file scan (planted `sdlc-tool` in a `CHECKS.md` fixture without SKILL.md probe → FAIL; with probe → PASS). Existing assertions unchanged.
- [ ] `tests/unit/test_symlinks.py` — **this is the real `PROJECT_ONLY_SKILLS` importer** (verified `grep -rn`: import at `:12`, `EXPECTED_PROJECT_ONLY` at `:48`, assertions `:55-62`; the tuple is NOT imported by `test_update_hardlinks.py`). Gap-3 deletes the symbol, so this module must be migrated in the same commit or it ImportErrors at collection:
  - DELETE `test_project_only_skills_exist` (`:55`) and `test_project_only_skills_is_set` (`:60`) — they assert on the deleted symbol.
  - DELETE `test_sync_skills_skips_project_only` (`:91`) and `test_sync_skills_counts_skipped_project_only` (`:105`) — they call `_sync_skills` with a `skills/`-shaped source and depend on the runtime filter that is being removed (post-change `result.created` would be 3, not 1).
  - REMOVE the `PROJECT_ONLY_SKILLS` import (`:12`) and `EXPECTED_PROJECT_ONLY` (`:48`); update the stale comment at `:209`.
  - ADD `test_no_project_only_skill_is_a_sync_destination` here — the filesystem-derived migration of the deleted invariant.
- [ ] `tests/unit/test_update_hardlinks.py` — UPDATE: add the two Gap-4 tests (`test_renamed_removals_entries_are_not_stale` always-on; `test_renamed_removals_covers_deleted_skills` git-history, shallow-clone-gated). It does NOT import `PROJECT_ONLY_SKILLS`, so no deletion work here.
- [ ] `tests/unit/test_skills_audit_reflection.py` — UPDATE only if it asserts an exact rule count or the FAIL/PASS total; the new rule adds a per-skill Finding, shifting counts. Confirm and adjust if needed.
- [ ] Full audit baseline — VERIFY: `audit_skills.py --json --no-sync` reports `0 FAIL` on the current tree after the new rule + sub-file scan land **AND after the C1 budget is spent**. Two surfaces feed AC5: (a) the two live sub-file coupling tokens (`audit-hooks/BEST_PRACTICES.md:118`, `audit-tools/CHECKS.md:171`) are placeholder examples whose parent `SKILL.md`s carry the probe, so the Gap-2 scan covers them; (b) the **8 new-rule prose hits enumerated in the C1 budget table** (do-sdlc ×5, do-deploy-example ×2, do-plan/SCOPING ×1) must gain same-line conditional framing during build. The build MUST re-run the sweep + full audit and confirm `0 FAIL` after implementing — sub-file scanning *and* the Bucket-C rule are both newly-covered surfaces.

## Rabbit Holes

- **Perfect natural-language conditional detection.** Do not build an NLP parser for "is this reference conditional." A small allowlist of markers (`in this repo`, `this repo's`, `PROBE_SUFFIX`) checked at **same-line** scope is sufficient. Do NOT attempt sentence segmentation — it is non-deterministic over Markdown. Anything fancier is scope creep.
- **`do-deploy-example` self-reference.** This template legitimately says `/do-deploy` and describes copying to `.claude/skills/do-deploy/`. `do-deploy` *is* a project-only skill name, so Signal A could false-positive it. Do not special-case it in code — it already carries conditional/template framing; if the audit flags it, add conditional wording to the offending line rather than an exclusion list. The AC5 "audit clean on current tree" gate will surface any such hit during build.
- **Scanning non-`.md` sub-files.** Tempting to scan scripts too, but that would flag `audit_skills.py`'s own token literals and any example snippets. Stay `*.md`-only.
- **Reimplementing `git log --follow` per skill for Gap 4.** A single `--diff-filter=D` name-only walk over the two globs is enough; per-file `--follow` is O(skills) subprocesses and slower with no added correctness.
- **Widening `COUPLING_SIGNALS` to catch everything.** The issue and #1783 both warn that over-broad signals false-positive Bucket-A clean skills. Keep signals executable/invocation-scoped.

## Risks

### Risk 1: New Bucket-C rule false-positives a currently-clean global skill
**Impact:** Audit red-states on the current tree (breaks AC5), or blocks a legitimate skill body that discusses the pipeline (`do-sdlc`, `do-deploy-example`).
**Mitigation:** Reference-scoped escape hatch (conditional wording OR probe on the **same physical line**) plus the locked scan surface (frontmatter-stripped `body`, fence-skip, `do-skills-audit` self-exempt). The C1 grep sweep (run this revision) **measured** the exposure: exactly 8 current-tree prose hits across 3 skills, all legitimate under-conditionalized references, enumerated with dispositions in the "Signal A/B current-tree budget" table. Build spends that budget (add same-line framing), then MUST run the full audit (`--no-sync`) and confirm `0 FAIL` before opening the PR. Any residual hit is either a real leak (fix the line) or a hatch-marker gap (widen the marker set) — asserted by AC5.

### Risk 2: Gap-4 git-history test is fragile under shallow clones / CI
**Impact:** `git log --diff-filter=D` returns incomplete history in a shallow CI checkout — a history-only test would silently *skip*, leaving CI with zero `RENAMED_REMOVALS` protection (the exact gap the critique flagged).
**Mitigation (resolved, not deferred):** Ship **two** tests (see Gap-4 Technical Approach). `test_renamed_removals_entries_are_not_stale` is a pure-filesystem invariant that **always runs**, so CI retains a real (if narrower) `RENAMED_REMOVALS` check regardless of clone depth. To be precise about what each test buys — they are not equivalent: the always-on test catches only *stale* entries (an entry that names a skill currently live in both roots at once, i.e. a removal that would delete a live hardlink); it does **not** catch the "forgot to add an entry when you deleted a skill" regression, which needs the git-history walk. So when `test_renamed_removals_covers_deleted_skills` skips under a shallow clone, CI loses the completeness half of the coverage — it is not fully protected, only protected against the stale-entry class. That git-history test explicitly detects shallowness via `git rev-parse --is-shallow-repository` (plus git-unavailable) and `pytest.skip`s with a stated reason — never a silent pass, never a false failure. Anchor its assertions to *present* deletions only; never assert "history must contain N deletions." The former Open-Question-3 fallback is now a shipped primary guard, not a contingency.

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
- [ ] A unit test (`test_symlinks.py::test_no_project_only_skill_is_a_sync_destination`) asserts no `.claude/skills/` directory name appears among `sync_claude_dirs` destinations, independent of any hand-maintained set; `PROJECT_ONLY_SKILLS` is deleted and the four `test_symlinks.py` tests that depended on it are removed/migrated (AC3).
- [ ] An **always-on** unit test (`test_renamed_removals_entries_are_not_stale`) fails when a `RENAMED_REMOVALS` entry contradicts the current tree, plus a shallow-clone-gated git-history test (`test_renamed_removals_covers_deleted_skills`) fails when a skill dir disappears from a skill root without a corresponding entry; the `do-xref-audit` (#2096) and #2065 sweep entries PASS as in-tree fixtures (AC4).
- [ ] `audit_skills.py --json --no-sync` reports `0 FAIL` on the current tree after the new rule lands **and the C1 budget (8 prose hits) is spent** (AC5 — 943 PASS baseline preserved modulo the added rule and the same-line framing edits). The rule scans frontmatter-stripped `body`, skips fenced code, and self-exempts `do-skills-audit`.
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
- Add `rule_21_bucket_c_coupling(skill_name, body, project_only_names)` to `audit_skills.py` (rule number `21` — `rule_20` is the current ceiling, so `21` is the natural next; name it definitively rather than hedging). Derive `project_only_names` from the `.claude/skills/` dir listing at call time. Match **Signal A** with full-token capture `(?<![\w-])/([a-z0-9][a-z0-9-]*)` + exact set-membership (the B1 fix — never substring/prefix; `/do-deploy-example` must not match `/do-deploy`) and **Signal B** (curated `sdk_client.py`, `SDLC_TARGET_REPO`). Scan the **frontmatter-stripped `body`** and **skip fenced code blocks** for both signals. Apply the reference-scoped same-line conditional/probe escape hatch. FAIL severity, empty-input safe, never raises.
- Wire it into `audit_skill()` alongside `coupling`, global-root only (skip when `dir_label == "project"`).
- Extend the coupling scan (both `rule_13` and the new rule) to consume the union of `SKILL.md` body + every `*.md` sub-file's text; probe/conditional coverage read from `SKILL.md`. Exclude non-`.md` files explicitly, **and self-exempt `do-skills-audit`** (C2 — its own rule-inventory docs describe the signals and would self-trip once the new rule is documented).
- Spend the C1 budget: add same-line conditional framing to the 8 current-tree prose hits (do-sdlc SKILL.md:9/:13/:51/:173/:175, do-deploy-example SKILL.md:35/:194, do-plan/SCOPING.md:65) so AC5 holds, then re-run the audit to confirm `0 FAIL`.
- Add tests: 5 reverted `61b55ce7` fixtures FAIL; corrected forms PASS; conditional/probe-covered mentions PASS; `/do-deploy-example`-style global-skill self-token does NOT false-match `/do-deploy`; a fenced Signal-A/B token does NOT flag; planted `sdlc-tool` in a `CHECKS.md` fixture FAIL without probe / PASS with probe; `do-skills-audit`'s own docs stay PASS; empty/None/whitespace bodies PASS.

### 2. Sync invariants (Gap 3 + Gap 4)
- **Task ID**: build-sync-invariants
- **Depends On**: none
- **Validates**: `tests/unit/test_symlinks.py`, `tests/unit/test_update_hardlinks.py`
- **Assigned To**: sync-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete `PROJECT_ONLY_SKILLS` and its dead call site in `_sync_skills`; keep the source-dir structural exclusion and its docstring explanation. First `grep -rn PROJECT_ONLY_SKILLS` repo-wide and remove all references.
- Migrate `tests/unit/test_symlinks.py` (the real importer): DELETE `test_project_only_skills_exist`, `test_project_only_skills_is_set`, `test_sync_skills_skips_project_only`, `test_sync_skills_counts_skipped_project_only`; remove the `PROJECT_ONLY_SKILLS` import and `EXPECTED_PROJECT_ONLY`; fix the stale `:209` comment. ADD `test_no_project_only_skill_is_a_sync_destination` (derive destination names from `sync_claude_dirs`; assert disjoint from `.claude/skills/` dir names).
- Add to `tests/unit/test_update_hardlinks.py`: `test_renamed_removals_entries_are_not_stale` (always-on filesystem invariant — no entry names a skill live in both roots) AND `test_renamed_removals_covers_deleted_skills` (git `--diff-filter=D` walk over both skill roots; each vanished skill name in `RENAMED_REMOVALS` **or present in any skill root** — the C3 fix, so a delete-and-re-add within the same root does not false-fail; `pytest.skip` when `git rev-parse --is-shallow-repository` is true or git is unavailable). Confirm `do-xref-audit` and the #2065 entries PASS.

### 3. Validation
- **Task ID**: validate-guards
- **Depends On**: build-audit-rule, build-sync-invariants
- **Assigned To**: guards-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_skills_audit.py tests/unit/test_symlinks.py tests/unit/test_update_hardlinks.py` (N2b — `test_symlinks.py` is the module Gap-3 migrates and must be in the validation run).
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
| Guard tests pass | `pytest tests/unit/test_skills_audit.py tests/unit/test_symlinks.py tests/unit/test_update_hardlinks.py -q` | exit code 0 |
| Audit clean on current tree | `python .claude/skills-global/do-skills-audit/scripts/audit_skills.py --json --no-sync \| python3 -c "import json,sys; assert json.load(sys.stdin)['summary']['fail']==0"` | exit code 0 |
| No PROJECT_ONLY_SKILLS references | `grep -rn "PROJECT_ONLY_SKILLS" scripts/ tests/ .claude/` | match count == 0 |
| New rule present | `grep -c "def rule_21" .claude/skills-global/do-skills-audit/scripts/audit_skills.py` | output > 0 |
| No-project-only-destination test present | `grep -c "test_no_project_only_skill_is_a_sync_destination" tests/unit/test_symlinks.py` | output > 0 |
| RENAMED_REMOVALS always-on test present | `grep -c "renamed_removals_entries_are_not_stale" tests/unit/test_update_hardlinks.py` | output > 0 |
| RENAMED_REMOVALS git-history test present | `grep -c "renamed_removals_covers_deleted" tests/unit/test_update_hardlinks.py` | output > 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| BLOCKER | Critique | Test Impact named the wrong importer (`test_update_hardlinks.py`); real importer is `test_symlinks.py` (import `:12`, `EXPECTED_PROJECT_ONLY` `:48`, asserts `:55-62`). Deleting the symbol ImportErrors that module at collection. | Test Impact + Gap-3 Technical Approach + Step 2 | Verified via `grep -rn`. Four `test_symlinks.py` tests migrated (two assert the symbol, two rely on the runtime filter); invariant migrated to `test_no_project_only_skill_is_a_sync_destination`; stale `:209` comment fixed. |
| CONCERN 1 | Critique | Contradictory `mermaid-render` labeling (listed as both a fixed leak and a clean skill). | Prior Art | Verified in `61b55ce7`: `mermaid-render` was a leaked `/setup` fix. Removed from the Bucket-A clean list; noted as an AC1 reverted-to-FAIL fixture. |
| CONCERN 2 | Critique | Non-deterministic "same sentence" escape hatch. | Gap-1 Technical Approach + Rabbit Holes | Changed to **same physical line** only; documented why sentence segmentation over Markdown is rejected. |
| CONCERN 3 | Critique | Unverified "Clean today" sub-file scan claim. | Problem (Gap 2) + Test Impact | Verified: two sub-files carry tokens (`audit-hooks/BEST_PRACTICES.md:118`, `audit-tools/CHECKS.md:171`) — placeholders whose parent SKILL.md carries the probe, so covered under the Gap-2 design; AC5 preserved. |
| CONCERN 4 | Critique | Gap-4 git-history walk silently skips under CI shallow clones. | Gap-4 Technical Approach + Risk 2 + OQ3 | Added always-on filesystem invariant (`test_renamed_removals_entries_are_not_stale`) as primary CI guard; git-history test now shallow-clone-gated via `git rev-parse --is-shallow-repository`. |
| BLOCKER (B1) | Re-critique | Signal A `\b` word-boundary anchoring false-positives `/do-deploy` inside the legit global body `do-deploy-example` (hyphen is a `\b` boundary), red-stating a clean skill and breaking AC5. | Gap-1 Signal A + Step 1 | Replaced with full-token capture `(?<![\w-])/([a-z0-9][a-z0-9-]*)` + exact set-membership, both edges hyphen-guarded. `/do-deploy-example` captures `do-deploy-example` (global, not flagged); bare `/do-deploy` captures `do-deploy` (flagged). |
| C1 | Re-critique | AC5 rested on an unverified survey of the other global skills for bare `/sdlc`//`/setup` refs. | Gap-1 "Signal A/B current-tree budget" table + Risk 1 + Test Impact + AC5 | Ran the grep sweep this revision (exact proposed regex, frontmatter-strip + fence-skip). Measured budget: 8 uncovered prose hits across 3 skills (do-sdlc ×5, do-deploy-example ×2, do-plan/SCOPING ×1), 0 uncovered Signal-B. Enumerated with dispositions; scan surface locked (body-only, fence-skip). |
| C2 | Re-critique | Gap-2 sub-file scan self-triggers a FAIL on `do-skills-audit`'s own rule-inventory docs (which the Documentation task expands to name `/sdlc`, `sdk_client.py`, `SDLC_TARGET_REPO`). | Gap-2 + Step 1 | Self-exempt `do-skills-audit` from the sub-file scan via a single `skill_name == "do-skills-audit"` skip, analogous to the `dir_label == "project"` skip. |
| C3 | Re-critique | Gap-4 git-history test false-fails on delete-and-re-add within the same root (skill present in the *same* root it was deleted from, not the "other" root). | Gap-4 Technical Approach + Step 2 | Coverage predicate widened from "present in the *other* root" to "present in **any** skill root." |
| N1 | Re-critique | Overstated CI-protection framing ("CI keeps real protection regardless of clone depth"). | Risk 2 | Reworded: the always-on test catches only *stale* entries; the "forgot an entry on delete" class needs the git-history walk, which is lost when it skips under shallow clones. |
| N2 | Re-critique | Verification hardcoded `def rule_21` against an "(or equivalent)" hedge; Step 3 validation omitted `test_symlinks.py`. | Step 1 + Step 3 | Committed to the definitive name `rule_21_bucket_c_coupling` (rule_20 is the ceiling), so the grep is valid; added `test_symlinks.py` to the Step 3 pytest run. |

---

## Open Questions

1. **Gap-1 rule granularity.** The plan recommends a **new rule** (e.g. `rule_21`) with a reference-scoped conditional/probe escape hatch, *not* an extension of `COUPLING_SIGNALS` — because two of the five leaked skills (`audit-models`, `do-issue`) carry the probe and would otherwise still PASS when reverted, breaking AC1. Confirm this split is acceptable, or accept that AC1's "all 5 FAIL" is relaxed for probe-carrying skills.
2. **Gap-3 defense-in-depth.** Delete `PROJECT_ONLY_SKILLS` and rely on the structural (source-dir) exclusion + the new invariant test (recommended, no-legacy). Or additionally make `_sync_skills` compute a live exclusion set from `.claude/skills/` as a belt-and-suspenders guard against a future scan-root widening? The test alone catches the regression; the live guard prevents it. Preference?
3. **Gap-4 mechanism if CI history is shallow.** ~~Fall back to a cheaper invariant if the git walk proves unreliable in CI?~~ **RESOLVED (critique CONCERN 4):** both mechanisms ship. The cheaper always-on invariant (`test_renamed_removals_entries_are_not_stale`) is now a *primary* guard so CI has real coverage regardless of clone depth; the git-history walk (`test_renamed_removals_covers_deleted_skills`) is retained as a shallow-clone-gated (`git rev-parse --is-shallow-repository`) enhancement that `pytest.skip`s cleanly rather than mis-passing. No open decision remains.
