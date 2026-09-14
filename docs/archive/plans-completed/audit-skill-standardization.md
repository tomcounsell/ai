---
status: Done
type: chore
appetite: Medium
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/639
last_comment_id:
---

# Standardize Audit Skill Naming and Quality

## Problem

**Current behavior:**
Two audit skills (`do-design-review` and `do-xref`) violate the naming convention defined in `new-audit-skill` SKILL.md section 6 and BEST_PRACTICES.md section 5. The convention requires general-purpose audits to use `do-{subject}-audit` and repo-specific audits to use `audit-{subject}`. These two skills follow neither pattern.

Additionally, audit skills have inconsistent quality: some are exemplary (`do-docs-audit`, `do-oop-audit`) while others lack trigger synonyms in their descriptions (`audit-models`, `do-skills-audit`), have implicit disposition sections (`do-design-review`), or have stale references in the meta-skill (`new-audit-skill` references `audit-next-tool` which was renamed to `audit-tools`).

**Desired outcome:**
- All 8 audit skills follow the two-tier naming convention
- All audit skills pass the 7-point structural checklist from `new-audit-skill` section 8
- The `new-audit-skill` meta-skill is updated with discoveries from the audit
- All references to renamed skills are updated across the codebase

## Prior Art

- **[Issue #437](https://github.com/tomcounsell/ai/issues/437)**: Create OOP/data modeling audit skill -- established `do-oop-audit` as the exemplar pattern. Closed 2026-03-24.
- **[PR #167](https://github.com/tomcounsell/ai/pull/167)**: Fix 12 skill audit warnings across 9 skills -- prior quality pass using `do-skills-audit`. Merged 2026-02-24.
- **[PR #157](https://github.com/tomcounsell/ai/pull/157)**: Add /do-skills-audit with 12 validation rules -- created the script-backed audit pattern. Merged 2026-02-24.
- **[PR #405](https://github.com/tomcounsell/ai/pull/405)**: Fix do-docs-audit: separate fixing from reporting -- established the disposition pattern. Merged 2026-03-14.

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: Skill directory names change (`do-design-review` -> `do-design-audit`, `do-xref` -> `do-xref-audit`). Users invoking `/do-design-review` or `/do-xref` will need to use the new names.
- **Coupling**: No change -- skill directories are self-contained
- **Data ownership**: No change
- **Reversibility**: High -- directory renames are trivially reversible. All changes are to documentation and skill definitions, not runtime code.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (scope fully defined by issue recon)
- Review rounds: 1 (final validation)

## Prerequisites

No prerequisites -- this work modifies only skill definitions, documentation, and one Python audit script.

## Solution

### Key Elements

- **Part 1: Atomic renames** -- Rename `do-design-review` to `do-design-audit` and `do-xref` to `do-xref-audit` (directories, frontmatter, descriptions, all references)
- **Part 2: Structural audit** -- Run each of the 8 audit skills through the 7-point checklist and fix findings
- **Part 3: Meta-skill update** -- Update `new-audit-skill` reference table and add guidance discovered during the audit

### Flow

**Part 1** (renames + reference updates) -> **Part 2** (structural fixes per skill) -> **Part 3** (meta-skill improvements)

Part 1 must be atomic to avoid broken skill invocations. Parts 2 and 3 can each be separate commits.

### Technical Approach

#### Part 1: Renames

Rename directories and update all references in a single commit:

- `mv .claude/skills/do-design-review .claude/skills/do-design-audit`
- `mv .claude/skills/do-xref .claude/skills/do-xref-audit`
- Update frontmatter `name:` in each renamed SKILL.md
- Update `do-skills-audit/scripts/audit_skills.py` FORK_SKILLS set: `do-design-review` -> `do-design-audit`
- Update `docs/features/do-design-review.md` -- rename file to `docs/features/do-design-audit.md`, update all internal references
- Update `docs/features/README.md` index entry
- Update `docs/features/skills-dependency-map.md` references
- Update `do-xref` self-references in its own SKILL.md

#### Part 2: Structural Audit (7-Point Checklist)

Run each skill through the checklist from `new-audit-skill` section 8:

1. Frontmatter has `name`, `description` (trigger-oriented, includes synonyms), `allowed-tools`
2. "What this skill does" has numbered steps: scan -> check -> report -> act
3. Each check has name, description (with why), severity, and verification method
4. Output format section shows 2-3 concrete examples with realistic data
5. Disposition section clearly states what happens after findings
6. SKILL.md is under 500 lines (use sub-files for detailed reference material)
7. Description includes trigger synonyms beyond just "audit" (check, validate, review, scan)

Known findings from issue recon:
- `audit-models`: Add trigger synonyms ("check model health", "validate Redis models", "scan for data model issues")
- `do-skills-audit`: Add trigger synonyms ("check skills quality", "validate skill structure", "lint SKILL.md files"), make disposition explicit
- `do-design-audit` (post-rename): Add explicit disposition section ("After the Audit"), add "audit" trigger synonyms alongside existing "review" triggers
- All skills: Verify checks have name + description (with why) + severity + verification method

#### Part 3: Meta-Skill Updates

- Update reference table in `new-audit-skill/SKILL.md`: `audit-next-tool` -> `audit-tools` (3 occurrences)
- Update `new-audit-skill/BEST_PRACTICES.md`: `audit-next-tool` -> `audit-tools` (5 occurrences)
- Update description in `new-audit-skill/SKILL.md` frontmatter: reference `audit-tools` instead of `audit-next-tool`
- Add guidance on when `disable-model-invocation` should be set (observed: some audits set it, some do not, no documented rationale)
- Consider adding the count of audit skills to the reference table (currently says "4 existing", there are now 8)

## Failure Path Test Strategy

### Exception Handling Coverage
No exception handlers in scope -- this work modifies skill definitions (Markdown) and one Python script (audit_skills.py) where the only change is a string in a frozenset.

### Empty/Invalid Input Handling
Not applicable -- no new functions or runtime code.

### Error State Rendering
Not applicable -- no user-visible runtime output changes.

## Test Impact

No existing tests affected -- this work modifies skill SKILL.md files (Markdown documentation), one Python script constant (FORK_SKILLS set in `audit_skills.py`), and feature documentation files. No test files reference `do-design-review` or `do-xref` by name. The `audit_skills.py` script is tested by running `/do-skills-audit` which will be validated as part of this work.

## Rabbit Holes

- Refactoring audit skill internals beyond what the structural checklist requires -- this plan standardizes naming and structure, not audit logic
- Adding new checks to existing audit skills -- out of scope, each skill's check design is intentional
- Automating the 7-point checklist as a script -- tempting but the checks require semantic judgment (e.g., "does the description include good trigger synonyms?"), making it a poor automation candidate
- Creating a unified audit runner that dispatches all audits -- separate project

## Risks

### Risk 1: Broken skill invocations during rename
**Impact:** Users invoking `/do-design-review` or `/do-xref` get "skill not found" errors
**Mitigation:** Atomic commit -- directory rename and all reference updates in one commit. No intermediate state where old name is gone but references still point to it.

### Risk 2: Missing references to renamed skills
**Impact:** Stale references in docs or skills cause confusion
**Mitigation:** Grep-based sweep for both old names before committing. The issue recon confirmed `do-xref` has no external references; `do-design-review` has 4 files to update.

## Race Conditions

No race conditions identified -- all operations are file renames and text edits with no concurrency or shared mutable state.

## No-Gos (Out of Scope)

- Rewriting audit skill check logic or adding new checks
- Creating new audit skills
- Changing audit skill disposition behavior (auto-fix vs report-only)
- Modifying the `do-skills-audit/scripts/audit_skills.py` validation rules beyond updating FORK_SKILLS
- Automating the structural checklist as a new script

## Update System

No update system changes required -- this work modifies only skill definitions and documentation, which are synced via normal git pull during updates.

## Agent Integration

No agent integration required -- audit skills are invoked via Claude Code slash commands, not through MCP servers or bridge integration. The renamed skills will be auto-discovered by Claude Code from their directory names and frontmatter.

## Documentation

- [ ] Rename `docs/features/do-design-review.md` to `docs/features/do-design-audit.md` and update content
- [ ] Update `docs/features/README.md` index entry for the renamed skill
- [ ] Update `docs/features/skills-dependency-map.md` references to renamed skills

## Success Criteria

- [ ] `do-design-review` renamed to `do-design-audit` (directory + frontmatter + description)
- [ ] `do-xref` renamed to `do-xref-audit` (directory + frontmatter + description)
- [ ] All references to old names updated (docs, scripts, other skills)
- [ ] All 8 audit skills pass the 7-point structural checklist from `new-audit-skill` section 8
- [ ] `audit-models` and `do-skills-audit` descriptions include action verb trigger synonyms
- [ ] `new-audit-skill` meta-skill reference table updated (`audit-next-tool` -> `audit-tools`)
- [ ] `new-audit-skill` meta-skill improved with additional guidance discovered during audit
- [ ] `do-skills-audit/scripts/audit_skills.py` FORK_SKILLS updated with new name
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (renames)**
  - Name: rename-builder
  - Role: Atomic rename of skill directories and all references
  - Agent Type: builder
  - Resume: true

- **Builder (structural-fixes)**
  - Name: structural-builder
  - Role: Run 7-point checklist against all 8 audit skills and fix findings
  - Agent Type: builder
  - Resume: true

- **Builder (meta-skill)**
  - Name: meta-skill-builder
  - Role: Update new-audit-skill references and add discovered guidance
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify all 8 skills pass checklist and no stale references remain
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Atomic Renames
- **Task ID**: build-renames
- **Depends On**: none
- **Validates**: grep for old names returns zero matches outside git history
- **Assigned To**: rename-builder
- **Agent Type**: builder
- **Parallel**: true
- Rename `.claude/skills/do-design-review` directory to `.claude/skills/do-design-audit`
- Rename `.claude/skills/do-xref` directory to `.claude/skills/do-xref-audit`
- Update frontmatter `name:` field in both renamed SKILL.md files
- Update `do-skills-audit/scripts/audit_skills.py` FORK_SKILLS: `do-design-review` -> `do-design-audit`
- Rename `docs/features/do-design-review.md` to `docs/features/do-design-audit.md`, update all internal references
- Update `docs/features/README.md` index entry
- Update `docs/features/skills-dependency-map.md` references (2 occurrences)
- Update self-references in `do-xref-audit/SKILL.md` (change `/do-xref` to `/do-xref-audit`)
- Commit atomically

### 2. Structural Audit and Fixes
- **Task ID**: build-structural
- **Depends On**: build-renames
- **Validates**: manual checklist pass for all 8 skills
- **Assigned To**: structural-builder
- **Agent Type**: builder
- **Parallel**: false
- Run 7-point structural checklist against each of the 8 audit skills
- Fix `audit-models` description: add trigger synonyms ("check model health", "validate Redis models", "scan for data model issues")
- Fix `do-skills-audit` description: add trigger synonyms ("check skills quality", "validate skill structure", "lint SKILL.md files")
- Fix `do-skills-audit`: make disposition section explicit if missing
- Fix `do-design-audit` (post-rename): add explicit "After the Audit" disposition section
- Fix `do-design-audit` description: add "audit" trigger synonyms alongside existing "review" triggers
- For each skill, verify checks have name + description (with why) + severity + verification method
- Commit fixes

### 3. Meta-Skill Updates
- **Task ID**: build-meta-skill
- **Depends On**: build-structural
- **Validates**: grep for `audit-next-tool` returns zero matches in new-audit-skill/
- **Assigned To**: meta-skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `new-audit-skill/SKILL.md` reference table: `audit-next-tool` -> `audit-tools` (3 occurrences in SKILL.md)
- Update `new-audit-skill/BEST_PRACTICES.md`: `audit-next-tool` -> `audit-tools` (5 occurrences)
- Update `new-audit-skill/SKILL.md` description frontmatter: `audit-next-tool` -> `audit-tools`
- Update count of existing audit skills in reference table (currently says 4, should list all 8)
- Add guidance on when `disable-model-invocation` should be set
- Commit meta-skill updates

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-meta-skill
- **Assigned To**: structural-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Verify `docs/features/do-design-audit.md` content is correct post-rename
- Verify `docs/features/README.md` entry is updated
- Verify `docs/features/skills-dependency-map.md` references are updated

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Grep for `do-design-review` across entire repo -- must return zero matches (outside git history and plan doc)
- Grep for `do-xref[^-]` across entire repo -- must return zero matches (outside git history and plan doc)
- Grep for `audit-next-tool` in `new-audit-skill/` -- must return zero matches
- Verify all 8 skill directories exist with correct names
- Run `python .claude/skills/do-skills-audit/scripts/audit_skills.py` and verify no FAIL findings related to renamed skills
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale design-review refs | `grep -r 'do-design-review' .claude/ docs/ --include='*.md' --include='*.py'` | exit code 1 |
| No stale xref refs | `grep -r '"do-xref"' .claude/ docs/ --include='*.md' --include='*.py'` | exit code 1 |
| No stale audit-next-tool refs | `grep -r 'audit-next-tool' .claude/skills/new-audit-skill/` | exit code 1 |
| Renamed dirs exist | `test -d .claude/skills/do-design-audit && test -d .claude/skills/do-xref-audit` | exit code 0 |
| Old dirs gone | `test ! -d .claude/skills/do-design-review && test ! -d .claude/skills/do-xref` | exit code 0 |
| Skills audit clean | `python .claude/skills/do-skills-audit/scripts/audit_skills.py --json` | exit code 0 |
| Renamed doc exists | `test -f docs/features/do-design-audit.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| CONCERN | [agent-type] | [The concern raised] | [How/whether it was addressed] |

---

## Open Questions

No open questions -- the issue recon fully scoped this work and all references have been confirmed via grep.
