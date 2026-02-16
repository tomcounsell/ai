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
- Update `scripts/update/symlinks.py` to prune stale hardlinks from `~/.claude/`
- Update setup command to remove old hardlinks during fresh setup

### Command Descriptions

These commands will rarely be invoked by exact name. Users will say things like "make a plan", "execute the plan", "update docs", or "run tests". Each command's `description` frontmatter must make this mapping unambiguous:

- **do-plan**: "Create or update feature plan documents. Use when the user says 'make a plan', 'plan this', 'flesh out the idea', or anything about planning work."
- **do-build**: "Execute a plan document using team orchestration. Use when the user says 'build this', 'execute the plan', 'implement the plan', or anything about running/shipping a plan."
- **do-docs**: "Cascade documentation updates after code changes. Use when the user says 'update docs', 'sync the docs', or anything about documentation."
- **do-test**: "Run the test suite. Use when the user says 'run tests', 'test this', or anything about testing."

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

The update system (`scripts/update/symlinks.py`) syncs `.claude/{skills,commands}` to `~/.claude/` via hardlinks. Currently it only **creates** links — it never removes stale ones. After this rename, old hardlinks (`~/.claude/commands/make-plan.md`, `~/.claude/commands/build.md`, `~/.claude/skills/make-plan/`, `~/.claude/skills/build/`) will linger on every deployed machine.

Changes required:
- **`scripts/update/symlinks.py`**: Add a cleanup pass that removes hardlinks in `~/.claude/{skills,commands}` that no longer have a corresponding source in the project's `.claude/` directory. Track removals in `SymlinkSyncResult`.
- **`scripts/update/run.py`**: Log cleaned-up stale links alongside created ones.
- **Setup command** (`.claude/commands/setup.md`): No changes needed — setup runs `/update` which will handle cleanup automatically.

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
- [ ] All command/skill descriptions include natural language triggers so "make a plan" invokes `do-plan`, "execute the plan" invokes `do-build`, etc.
- [ ] `scripts/update/symlinks.py` removes stale hardlinks from `~/.claude/` that no longer exist in the project
- [ ] Running `/update` on a machine with old hardlinks cleans them up automatically

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

### 1. Rename command files and update descriptions
- **Task ID**: rename-commands
- **Depends On**: none
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: true
- `git mv .claude/commands/make-plan.md .claude/commands/do-plan.md`
- `git mv .claude/commands/build.md .claude/commands/do-build.md`
- Copy/adapt `.claude/commands/update-docs.md` → `.claude/commands/do-docs.md`
- Create `.claude/commands/do-test.md`
- Update each command's `description` frontmatter to include natural language triggers (see "Command Descriptions" in Technical Approach)

### 2. Rename skill directories
- **Task ID**: rename-skills
- **Depends On**: none
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: true
- `git mv .claude/skills/make-plan .claude/skills/do-plan`
- `git mv .claude/skills/build .claude/skills/do-build`
- Update `name:` field in each renamed SKILL.md
- Update each SKILL.md `description` to include natural language triggers matching the command descriptions
- Create `.claude/skills/do-docs/` and `.claude/skills/do-test/` if needed

### 3. Add stale hardlink cleanup to update system
- **Task ID**: update-symlinks
- **Depends On**: rename-commands, rename-skills
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: false
- In `scripts/update/symlinks.py`: add `_cleanup_stale_commands()` and `_cleanup_stale_skills()` that remove entries in `~/.claude/{commands,skills}` with no corresponding source in the project `.claude/` dir
- Add `removed: int` counter to `SymlinkSyncResult` and track "removed" actions in `LinkAction`
- In `scripts/update/run.py`: log removed stale links alongside created ones

### 4. Update all references
- **Task ID**: update-refs
- **Depends On**: rename-commands, rename-skills
- **Assigned To**: renamer
- **Agent Type**: builder
- **Parallel**: false
- Update CLAUDE.md (all `/make-plan` → `/do-plan`, `/build` → `/do-build`, `/update-docs` → `/do-docs`)
- Update all docs/ files referencing old names
- Update `.claude/agents/plan-maker.md`
- Update any cross-references in other commands
- Update `scripts/calendar_prompt_hook.sh` if it references old command names

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: update-refs, update-symlinks
- **Assigned To**: rename-validator
- **Agent Type**: validator
- **Parallel**: false
- Grep for any remaining `make-plan`, `/build` references (excluding git history)
- Verify new command files exist and have correct frontmatter with natural language descriptions
- Verify skill directories exist with correct SKILL.md names
- Verify `scripts/update/symlinks.py` has cleanup logic
- Run `python -c "from scripts.update.symlinks import SymlinkSyncResult; assert hasattr(SymlinkSyncResult, 'removed')"` to verify new field

## Validation Commands

- `grep -r "make-plan" .claude/ CLAUDE.md docs/ --include="*.md" | grep -v do-plan` - No old make-plan refs
- `grep -r '"/build"' .claude/ CLAUDE.md docs/ --include="*.md" | grep -v do-build` - No old /build refs
- `ls .claude/commands/do-plan.md .claude/commands/do-build.md .claude/commands/do-docs.md .claude/commands/do-test.md` - All new commands exist
- `ls .claude/skills/do-plan/SKILL.md .claude/skills/do-build/SKILL.md` - Renamed skills exist

## Open Questions

1. Should `update-docs` command/skill be renamed to `do-docs`, or should `do-docs` be a new command that delegates to `update-docs`? (I assumed rename for consistency)
2. What should `/do-test` do exactly — just `pytest tests/`? Or something more specific?
3. Should we also rename `update` → `do-update` and `review` → `do-review` to fully commit to the `do-*` pattern?
