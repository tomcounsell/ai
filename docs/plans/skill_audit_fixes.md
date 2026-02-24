---
status: Ready
type: chore
appetite: Small
owner: Valor Engels
created: 2026-02-24
tracking: https://github.com/tomcounsell/ai/issues/158
---

# Skill Audit Fixes

## Problem

`/do-skills-audit` found **11 WARN findings across 8 skills** plus gaps in the audit infrastructure itself. The warnings indicate drift from the Anthropic SKILL.md spec — missing `argument-hint` fields, undeclared fork contexts, non-standard frontmatter fields, and infrastructure skills that can be auto-triggered by the model.

**Current behavior:**
Running `audit_skills.py --no-sync` reports 11 warnings. The audit also relied on a live network fetch and a JSON blob cache rather than version-controlled reference files.

**Desired outcome:**
Zero warnings from the audit. All skill frontmatter aligned with the Anthropic spec. Audit infrastructure reads from local, committed reference files.

## Already Done (this session)

- ✅ Added `pyyaml` to `uv` project dependencies
- ✅ Stored Anthropic skill docs as local reference files in `~/.claude/skills/do-skills-audit/references/` (version-controlled, 7-day sync TTL)
- ✅ Rewrote `sync_best_practices.py` to write/update local files; audit always reads from local files
- ✅ Updated `new-skill/SKILL.md` to reference the Anthropic docs and documented `agent`, `argument-hint`, `model` fields in the field constraints table

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0 (each fix is a one-line frontmatter edit, verified by re-running the audit)

## Prerequisites

No external dependencies — all work is frontmatter edits to files in `~/.claude/skills/`.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Audit script runs | `.venv/bin/python .claude/skills/do-skills-audit/scripts/audit_skills.py --no-sync` | Baseline 11 warnings before fixes |

## Solution

### Key Elements

- **`argument-hint` additions**: Add the field to 4 skills that use `$ARGUMENTS` without it
- **`context: fork` additions**: Add to 2 fork-pattern skills missing the declaration
- **Unknown field cleanup**: Remove or replace non-standard fields in 3 skills
- **`disable-model-invocation` additions**: Add to 2 infrastructure skills

### Flow

Run audit (11 warnings) → patch each SKILL.md frontmatter → re-run audit (0 warnings) → commit all skills repo changes → push

### Technical Approach

Each fix is a YAML frontmatter edit to the relevant `~/.claude/skills/<name>/SKILL.md`. The skills live in a separate git repo (`~/.claude/skills/`). All changes commit and push there.

**Fixes by skill:**

| Skill | Fix | Field |
|-------|-----|-------|
| `add-feature` | Add | `argument-hint: "<feature-name> or description"` |
| `do-build` | Add | `argument-hint: "<plan-path-or-issue-number>"` |
| `do-patch` | Add | `argument-hint: "<description-of-what-to-patch>"` |
| `do-test` | Add | `argument-hint: "[test-path-or-filter]"` |
| `do-design-review` | Add | `context: fork` |
| `sdlc` | Add | `context: fork` |
| `do-patch` | Remove | `model-invocable` (not in Anthropic spec) |
| `sdlc` | Remove | `model-invocable` (not in Anthropic spec) |
| `frontend-design` | Remove | `icon`, `license` (not in Anthropic spec) |
| `new-skill` | Add | `disable-model-invocation: true` |
| `new-valor-skill` | Add | `disable-model-invocation: true` |

## Rabbit Holes

- **Rewriting skill descriptions**: We're only fixing frontmatter fields, not overhauling skill content
- **Adding `argument-hint` to all skills**: Only the 4 that actually use `$ARGUMENTS` need it
- **Deciding what `model-invocable` should become**: Just remove it — it's non-standard and neither `do-patch` nor `sdlc` need to be hidden from the model

## Risks

### Risk 1: `context: fork` changes behavior
**Impact:** Skills with `context: fork` run in a forked subagent. If `do-design-review` or `sdlc` weren't actually intended to fork, adding the field could change how they execute.
**Mitigation:** Both skills are already documented as fork-pattern (they're in the `FORK_SKILLS` constant in `audit_skills.py`). The field formalizes existing intent.

## No-Gos (Out of Scope)

- Fixing the content or logic inside any skill — frontmatter only
- Adding `argument-hint` to skills that don't use `$ARGUMENTS`
- Changing which skills are classified as fork/infra/background (that's a separate audit question)
- Auto-fix via `--fix` flag — the existing `apply_fixes` only handles trivial cases; these need targeted edits

## Update System

No update system changes required — all edits are to files in `~/.claude/skills/` which are already synced via the `/update` skill's git pull of the skills repo.

## Agent Integration

No agent integration required — this is purely a skill metadata fix with no bridge or MCP changes.

## Documentation

- [ ] No new feature docs needed — the audit warnings are the documentation; zero warnings is the success state
- [ ] `new-skill/SKILL.md` field constraints table already updated (done this session) to include `argument-hint`, `agent`, and `model`

## Success Criteria

- [ ] `audit_skills.py --no-sync` reports **0 WARN, 0 FAIL** across all 26 skills
- [ ] All 11 specific warnings resolved (per findings table above)
- [ ] Changes committed and pushed to the skills repo

## Team Orchestration

Single builder handles all frontmatter edits sequentially (they're all in the same repo), followed by a validator running the audit.

### Team Members

- **Builder (frontmatter-fixer)**
  - Name: frontmatter-fixer
  - Role: Apply all 11 frontmatter fixes across 8 skill SKILL.md files
  - Agent Type: builder
  - Resume: true

- **Validator (audit-verifier)**
  - Name: audit-verifier
  - Role: Run the audit and confirm 0 warnings
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix all frontmatter warnings
- **Task ID**: fix-frontmatter
- **Depends On**: none
- **Assigned To**: frontmatter-fixer
- **Agent Type**: builder
- **Parallel**: false
- Edit `~/.claude/skills/add-feature/SKILL.md` — add `argument-hint`
- Edit `~/.claude/skills/do-build/SKILL.md` — add `argument-hint`
- Edit `~/.claude/skills/do-patch/SKILL.md` — add `argument-hint`, remove `model-invocable`
- Edit `~/.claude/skills/do-test/SKILL.md` — add `argument-hint`
- Edit `~/.claude/skills/do-design-review/SKILL.md` — add `context: fork`
- Edit `~/.claude/skills/sdlc/SKILL.md` — add `context: fork`, remove `model-invocable`
- Edit `~/.claude/skills/frontend-design/SKILL.md` — remove `icon`, `license`
- Edit `~/.claude/skills/new-skill/SKILL.md` — add `disable-model-invocation: true`
- Edit `~/.claude/skills/new-valor-skill/SKILL.md` — add `disable-model-invocation: true`
- Commit all changes: `git -C ~/.claude/skills add -A && git -C ~/.claude/skills commit -m "Fix 11 skill audit warnings: argument-hint, context fork, unknown fields, disable-model-invocation"`
- Push: `git -C ~/.claude/skills push`

### 2. Validate zero warnings
- **Task ID**: validate-audit
- **Depends On**: fix-frontmatter
- **Assigned To**: audit-verifier
- **Agent Type**: validator
- **Parallel**: false
- Run `.venv/bin/python .claude/skills/do-skills-audit/scripts/audit_skills.py --no-sync`
- Confirm summary shows `0 WARN | 0 FAIL`
- Report any remaining warnings

## Validation Commands

- `.venv/bin/python .claude/skills/do-skills-audit/scripts/audit_skills.py --no-sync` — must show 0 WARN, 0 FAIL
