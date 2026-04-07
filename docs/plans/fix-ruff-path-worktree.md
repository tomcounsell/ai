---
status: Ready
type: bug
appetite: Small
owner: Valor Engels
created: 2026-03-06
tracking: https://github.com/valorengels/ai/issues/266
---

# Fix ruff not in PATH inside git worktrees

## Problem

Running `ruff check` or `ruff format` inside a git worktree (`.worktrees/{slug}/`) fails with `command not found: ruff`. The `ruff` binary exists at `.venv/bin/ruff` but is not on PATH when running inside worktree directories. This breaks the SDLC pipeline since quality checks need to run in the worktree context.

**Current behavior:**
Any bare `ruff` invocation from a worktree or from environments where `.venv/bin` is not on PATH fails. The workaround used in recent plans is `python -m ruff`, but the majority of the codebase still references bare `ruff`.

**Desired outcome:**
All ruff invocations work reliably regardless of whether they run from the main repo or a worktree. Use `python -m ruff` everywhere for maximum portability.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

Mechanical find-and-replace across hooks, skills, agents, and docs. No architectural decisions needed.

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Hook detection patterns**: Update regex patterns in `post_tool_use.py` to also match `python -m ruff` invocations
- **Hook hint messages**: Update suggested commands in `validate_sdlc_on_stop.py` and `sdlc_reminder.py` to use `python -m ruff`
- **Skill instructions**: Update `do-test`, `do-patch`, `do-build`, and agent definitions to use `python -m ruff`
- **Builder auto-fix hook**: Update the builder agent's PostToolUse hook command from `ruff check --fix` to `python -m ruff check --fix`

### Flow

**Agent runs quality checks** -> uses `python -m ruff` -> works in both main repo and worktrees -> hook detects the command via updated regex -> SDLC state records quality gate as satisfied

### Technical Approach

- Replace all bare `ruff check` and `ruff format` invocations with `python -m ruff check` and `python -m ruff format`
- Update detection regexes in `post_tool_use.py` to match both `ruff` and `python -m ruff` patterns (backward compatible)
- Update the user-level hooks in `.claude/hooks/sdlc/` that are deployed via the update system
- Update CLAUDE.md quick commands table

## Rabbit Holes

- **Adding ruff to system PATH**: Tempting but fragile -- depends on shell profile sourcing, which varies across contexts (SSH, launchd, subshells). Using `python -m ruff` is strictly more portable.
- **Virtualenv activation in worktrees**: Trying to auto-activate `.venv` in worktrees adds complexity. The worktrees share the same Python installation; `python -m ruff` works without activation.
- **Black references**: Several files still reference `black`. Cleaning those up is a separate concern -- this plan focuses exclusively on ruff.

## Risks

### Risk 1: Detection regex breaks backward compatibility
**Impact:** Existing sessions that run bare `ruff` would stop being detected as having run quality checks.
**Mitigation:** Update regexes to match BOTH bare `ruff` AND `python -m ruff`. Old behavior still works.

### Risk 2: User-level hooks out of sync
**Impact:** The hooks in `~/.claude/hooks/sdlc/` are deployed separately from the repo. If the update system doesn't propagate changes, the deployed hooks show stale hint messages.
**Mitigation:** The update system copies hooks from the repo to `~/.claude/hooks/sdlc/`. As long as the repo files are updated, the next `/update` run propagates them.

## No-Gos (Out of Scope)

- Replacing `black` references with `ruff format` (separate cleanup)
- Adding `.venv/bin` to system PATH or modifying shell profiles
- Changing how worktrees are created or managed
- Modifying the `scripts/update/verify.py` tool check (it already uses `check_venv_tool`, not bare CLI)

## Update System

The update system deploys hooks from `.claude/hooks/sdlc/` to `~/.claude/hooks/sdlc/`. The files in that directory (`sdlc_reminder.py`, `validate_sdlc_on_stop.py`) will be modified by this plan. The next `/update` run will propagate the changes automatically. No update script changes needed.

## Agent Integration

No agent integration required -- this is a hook and skill instruction change. The agent already runs bash commands; the only change is which command string it uses for ruff. The detection hooks that track quality command execution are updated to recognize the new invocation pattern.

## Documentation

- [ ] Update `CLAUDE.md` quick commands table to use `python -m ruff`
- [ ] Update `docs/features/sdlc-enforcement.md` to reflect `python -m ruff` usage
- [ ] Update `docs/features/do-patch-skill.md` ruff reference

## Success Criteria

- [ ] `python -m ruff check .` and `python -m ruff format --check .` used in all hooks and skills
- [ ] Detection regex in `post_tool_use.py` matches both `ruff check` and `python -m ruff check`
- [ ] SDLC reminder messages suggest `python -m ruff` instead of bare `ruff`
- [ ] Quality gate hint messages suggest `python -m ruff` instead of bare `ruff`
- [ ] Builder agent hook uses `python -m ruff check --fix` instead of bare `ruff`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (ruff-fixer)**
  - Name: ruff-fixer
  - Role: Update all ruff invocations and detection patterns
  - Agent Type: builder
  - Resume: true

- **Validator (ruff-validator)**
  - Name: ruff-validator
  - Role: Verify all ruff references are updated and detection works
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update hook detection patterns and hint messages
- **Task ID**: build-hooks
- **Depends On**: none
- **Assigned To**: ruff-fixer
- **Agent Type**: builder
- **Parallel**: true
- Update `post_tool_use.py` quality_patterns regex to also match `python -m ruff`
- Update `.claude/hooks/sdlc_reminder.py` SDLC_REMINDER_MESSAGE to use `python -m ruff`
- Update `.claude/hooks/validators/validate_sdlc_on_stop.py` _QUALITY_RUN_HINTS to use `python -m ruff`
- Update `.claude/hooks/sdlc/sdlc_reminder.py` SDLC_REMINDER_MESSAGE to use `python -m ruff`
- Update `.claude/hooks/sdlc/validate_sdlc_on_stop.py` QUALITY_RUN_HINTS to use `python -m ruff`

### 2. Update skill instructions
- **Task ID**: build-skills
- **Depends On**: none
- **Assigned To**: ruff-fixer
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/skills/do-test/SKILL.md` bare ruff references
- Update `.claude/skills/do-test/PYTHON.md` bare ruff references
- Update `.claude/skills/do-patch/SKILL.md` bare ruff references
- Update `.claude/skills/do-build/SKILL.md` and `PR_AND_CLEANUP.md` if bare ruff
- Update `.claude/skills/new-valor-skill/SKILL.md` bare ruff references

### 3. Update agent definitions
- **Task ID**: build-agents
- **Depends On**: none
- **Assigned To**: ruff-fixer
- **Agent Type**: builder
- **Parallel**: true
- Update `.claude/agents/builder.md` PostToolUse hook command and inline ruff references
- Update `.claude/agents/validator.md` ruff references

### 4. Update top-level config
- **Task ID**: build-config
- **Depends On**: none
- **Assigned To**: ruff-fixer
- **Agent Type**: builder
- **Parallel**: true
- Update `CLAUDE.md` quick commands table

### 5. Update tests
- **Task ID**: build-tests
- **Depends On**: none
- **Assigned To**: ruff-fixer
- **Agent Type**: builder
- **Parallel**: true
- Update `tests/unit/test_post_tool_use_sdlc.py` test cases to include `python -m ruff` patterns
- Update `tests/unit/test_validate_sdlc_on_stop.py` hint message assertions
- Update `tests/unit/test_sdlc_reminder.py` if it asserts on ruff command strings

### 6. Validate all changes
- **Task ID**: validate-all
- **Depends On**: build-hooks, build-skills, build-agents, build-config, build-tests
- **Assigned To**: ruff-validator
- **Agent Type**: validator
- **Parallel**: false
- Grep for remaining bare `ruff check` and `ruff format` in hooks/skills/agents (excluding docs/plans/)
- Run `pytest tests/ -v --tb=short`
- Run `python -m ruff check .`
- Run `python -m ruff format --check .`

## Validation Commands

- `grep -rn "(?<!\-m )ruff\s\+\(check\|format\)" .claude/hooks/ .claude/skills/ .claude/agents/ CLAUDE.md` - Verify no bare ruff invocations remain in active code
- `pytest tests/unit/test_post_tool_use_sdlc.py -v` - Hook detection tests pass
- `pytest tests/unit/test_validate_sdlc_on_stop.py -v` - Stop validator tests pass
- `python -m ruff check .` - Lint passes
- `python -m ruff format --check .` - Format passes
