---
status: Planning
type: chore
appetite: Small
owner: Valor
created: 2026-02-16
tracking: https://github.com/tomcounsell/ai/issues/120
---

# Rename Commands to `do-*` Convention

## Problem

The slash commands `make-plan` and `build` don't follow a consistent naming convention. We're moving to a `do-*` prefix pattern (`do-plan`, `do-build`) and adding new commands (`do-docs`, `do-test`) that fit the same family.

**Current behavior:**
- `/make-plan` creates plans
- `/build` executes plans
- No dedicated `/do-docs` or `/do-test` commands

**Desired outcome:**
- `/do-plan` creates plans (replaces `/make-plan`)
- `/do-build` executes plans (replaces `/build`)
- `/do-docs` runs documentation updates (replaces `/update-docs`)
- `/do-test` runs test suite
- Consistent `do-*` naming across all action commands

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Command renames**: Rename `.claude/commands/make-plan.md` → `do-plan.md`, `build.md` → `do-build.md`
- **Skill renames**: Rename `.claude/skills/make-plan/` → `do-plan/`, `.claude/skills/build/` → `do-build/`
- **New commands**: Add `do-docs.md` and `do-test.md` commands
- **Reference updates**: Update all references in CLAUDE.md, docs, and other commands

### Flow

User types `/do-plan slug` → Plan created → User types `/do-build docs/plans/slug.md` → Build executes

### Technical Approach

- Rename files/directories for commands and skills
- Find-and-replace all references across the codebase
- Add new `do-docs` command (thin wrapper pointing to `update-docs` skill)
- Add new `do-test` command (runs pytest with appropriate flags)
- Update `.claude/agents/plan-maker.md` if it references old names

## Rabbit Holes

- Don't try to keep backward compatibility aliases — just rename cleanly
- Don't redesign the command/skill system itself during this rename

## Risks

### Risk 1: Missed references
**Impact:** Old command names referenced in docs or code won't work
**Mitigation:** Grep exhaustively for `make-plan`, `/build`, `update-docs` across all files

## No-Gos (Out of Scope)

- Not changing command behavior, only names
- Not restructuring the skills directory layout
- Not adding new functionality to existing commands

## Update System

No update system changes required — this is a rename of local development commands only.

## Agent Integration

No agent integration required — slash commands are invoked by the human user in Claude Code, not by the agent.

## Documentation

- [ ] Update all references in `CLAUDE.md`
- [ ] Update references in `docs/features/` files
- [ ] Update `.claude/README.md` if it references old names
- [ ] Update `docs/CONSOLIDATED_DOCUMENTATION.md`

## Success Criteria

- [ ] `/do-plan` works (old `/make-plan` removed)
- [ ] `/do-build` works (old `/build` removed)
- [ ] `/do-docs` works
- [ ] `/do-test` works
- [ ] No remaining references to old command names in docs
- [ ] All renamed skills have correct `name:` in SKILL.md frontmatter

## Team Orchestration

### Team Members

- **Builder (rename)**
  - Name: renamer
  - Role: Rename files, update references
  - Agent Type: builder
  - Resume: true

- **Validator (rename)**
  - Name: rename-validator
  - Role: Verify all references updated
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Rename command files
- **Task ID**: rename-commands
- **Depends On**: none
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: true
- `git mv .claude/commands/make-plan.md .claude/commands/do-plan.md`
- `git mv .claude/commands/build.md .claude/commands/do-build.md`
- Copy/adapt `.claude/commands/update-docs.md` → `.claude/commands/do-docs.md`
- Create `.claude/commands/do-test.md`

### 2. Rename skill directories
- **Task ID**: rename-skills
- **Depends On**: none
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: true
- `git mv .claude/skills/make-plan .claude/skills/do-plan`
- `git mv .claude/skills/build .claude/skills/do-build`
- Update `name:` field in each renamed SKILL.md
- Create `.claude/skills/do-docs/` and `.claude/skills/do-test/` if needed

### 3. Update all references
- **Task ID**: update-refs
- **Depends On**: rename-commands, rename-skills
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: false
- Update CLAUDE.md (all `/make-plan` → `/do-plan`, `/build` → `/do-build`)
- Update all docs/ files referencing old names
- Update `.claude/agents/plan-maker.md`
- Update any cross-references in other commands

### 4. Final validation
- **Task ID**: validate-all
- **Depends On**: update-refs
- **Assigned To**: rename-validator
- **Agent Type**: validator
- **Parallel**: false
- Grep for any remaining `make-plan`, `/build` references (excluding git history)
- Verify new command files exist and have correct frontmatter
- Verify skill directories exist with correct SKILL.md names

## Validation Commands

- `grep -r "make-plan" .claude/ CLAUDE.md docs/ --include="*.md" | grep -v do-plan` - No old make-plan refs
- `grep -r '"/build"' .claude/ CLAUDE.md docs/ --include="*.md" | grep -v do-build` - No old /build refs
- `ls .claude/commands/do-plan.md .claude/commands/do-build.md .claude/commands/do-docs.md .claude/commands/do-test.md` - All new commands exist
- `ls .claude/skills/do-plan/SKILL.md .claude/skills/do-build/SKILL.md` - Renamed skills exist

## Open Questions

1. Should `update-docs` command/skill be renamed to `do-docs`, or should `do-docs` be a new command that delegates to `update-docs`? (I assumed rename for consistency)
2. What should `/do-test` do exactly — just `pytest tests/`? Or something more specific?
3. Should we also rename `update` → `do-update` and `review` → `do-review` to fully commit to the `do-*` pattern?
