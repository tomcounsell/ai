---
status: Planning
type: chore
appetite: Small
owner: Valor Engels
created: 2026-03-11
tracking: https://github.com/tomcounsell/ai/issues/364
last_comment_id:
---

# Auto-Fix Lint Instead of Blocking Agents

## Problem

During do-build and do-patch, agents frequently get trapped in lint-related churn loops:

1. Agent makes code changes and commits
2. Pre-commit hook or manual lint check runs `ruff check` and fails
3. Agent sees the error, starts "fixing" lint issues
4. Lint fixes sometimes break or revert the actual code changes
5. Agent commits the lint fix, which triggers another check
6. Repeat -- the agent loses focus on the actual feature/patch work

**Current behavior:**
- No git pre-commit hook exists (only `.sample` files in `.git/hooks/`)
- The `format_file.py` PostToolUse hook runs `ruff check --fix` and `black --quiet` on individual files after Write/Edit, but only in builder subagents
- The main agent has no auto-format hook on Write/Edit
- Skill files (do-build, do-patch, builder) instruct agents to run `ruff check` and `black --check` manually, which surfaces fixable errors as blocking failures
- Agents then enter fix-lint-fix churn loops instead of focusing on feature work

**Desired outcome:**
- Fixable lint issues are silently auto-fixed before commits, never surfaced to agents
- Only genuinely unfixable issues (ambiguous imports, type errors) appear as failures
- Agents spend zero iterations on lint, focusing entirely on feature/patch work
- Intermediate WIP commits use `--no-verify` to avoid lint interruptions mid-thought

## Prior Art

No prior issues found related to auto-fixing lint in pre-commit hooks.

The existing `format_file.py` hook is a partial solution -- it auto-fixes individual files in builder subagents via PostToolUse but does not cover the main agent or the commit-time check. The issue is that agents still run manual lint checks and get trapped in fix loops.

## Data Flow

1. **Entry point**: Agent edits a Python file via Write/Edit tool
2. **PostToolUse hook** (builder only): `format_file.py` runs `ruff check --fix` + `black --quiet` on that single file
3. **Agent runs manual lint**: Skill instructions tell agent to run `ruff check .` and `black --check .` -- these surface fixable issues as errors
4. **Agent enters fix loop**: Tries to manually fix reported lint errors, sometimes breaking code
5. **Agent commits**: No pre-commit hook exists, so no auto-fix at commit time
6. **Result**: Wasted tokens, polluted git history, potential regressions

After this change:

1. **Entry point**: Agent edits a Python file via Write/Edit tool
2. **PostToolUse hook** (all agents): `format_file.py` runs `ruff check --fix` + `ruff format` on that single file (replaces black)
3. **Agent commits (final)**: Git pre-commit hook runs `ruff format . && ruff check --fix . && git add -u`, silently fixing everything fixable
4. **Only unfixable issues**: Surface as hook failure -- agent addresses only genuine problems
5. **Intermediate commits**: Use `--no-verify` per skill instructions, skipping lint entirely mid-task
6. **Result**: Zero lint churn, agents focus on features

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Git pre-commit hook**: Auto-fix script that runs `ruff format`, `ruff check --fix`, and `git add -u` before commits, only failing on unfixable issues
- **Skill instruction updates**: Add "Lint Discipline" sections to do-build and do-patch skills telling agents to use `--no-verify` for intermediate commits and format once at the end
- **format_file.py update**: Replace `black` with `ruff format` for consistency (ruff handles both linting and formatting)
- **builder.md update**: Align TDD workflow instructions to reference `ruff format` instead of `black`

### Flow

**Agent edits file** -> PostToolUse auto-fixes that file -> **Agent does intermediate commit** (`--no-verify`) -> **Agent finishes feature** -> Final commit triggers pre-commit hook -> Hook auto-fixes all files -> Only unfixable issues block -> **Clean commit**

### Technical Approach

- Create `.githooks/pre-commit` as a tracked script (not in `.git/hooks/` which is gitignored)
- Configure git to use `.githooks/` via `core.hooksPath` (set in update script and setup docs)
- The hook script: `ruff format . && ruff check --fix . && git add -u` -- if ruff exits non-zero after `--fix`, there are unfixable issues and the hook fails
- Update `format_file.py` to use `ruff format` instead of `black`
- Add "Lint Discipline" section to `do-build/SKILL.md` and `do-patch/SKILL.md`
- Update `builder.md` to replace `black` references with `ruff format`

## Failure Path Test Strategy

### Exception Handling Coverage
- No exception handlers in scope -- the pre-commit hook is a bash script with straightforward exit codes

### Empty/Invalid Input Handling
- [ ] Verify the pre-commit hook handles repos with no Python files gracefully (ruff exits 0)
- [ ] Verify the hook handles staged non-Python files without error

### Error State Rendering
- [ ] Verify unfixable lint errors are clearly reported to the agent (not swallowed)
- [ ] Verify the hook failure message includes enough context for the agent to understand the issue

## Rabbit Holes

- Do NOT adopt the `pre-commit` Python framework -- it adds complexity for a simple shell script
- Do NOT build lint-tracking state, counters, or dashboards -- the fix is structural, not behavioral
- Do NOT create a separate lint subagent or lint-fixing skill -- auto-fix handles it
- Do NOT try to make the hook work with `black` -- ruff format replaces it entirely, simplifying the toolchain

## Risks

### Risk 1: Agents ignore `--no-verify` instruction
**Impact:** Agents still trigger the pre-commit hook on every intermediate commit, adding minor overhead (but no churn since the hook auto-fixes)
**Mitigation:** The hook auto-fixes silently, so even if triggered frequently, it does not create churn loops. The instruction is an optimization, not a requirement.

### Risk 2: `ruff format` output differs from `black` output
**Impact:** Large diff on first run as all files reformat
**Mitigation:** Run `ruff format .` once on main before the feature branch to establish the new baseline. Ruff format is designed to be black-compatible by default.

## Race Conditions

No race conditions identified -- all operations are synchronous and single-threaded (git hooks run sequentially before commit).

## No-Gos (Out of Scope)

- Do NOT disable linting entirely -- auto-fix it, do not skip it
- Do NOT add complex lint-tracking state or counters
- Do NOT create a separate lint subagent
- Do NOT let agents spend more than one round on lint fixes -- format once, move on
- Do NOT adopt the `pre-commit` Python framework (overkill for this use case)

## Update System

The git hooks path change needs to propagate to all machines:
- Add `git config core.hooksPath .githooks` to `scripts/remote-update.sh`
- The `.githooks/pre-commit` script is tracked in the repo, so `git pull` delivers it
- No new dependencies required (ruff is already installed)

## Agent Integration

No agent integration required -- this modifies skill instructions and git hooks only. No new tools or MCP servers.

## Documentation

- [ ] Create `docs/features/lint-auto-fix.md` describing the hook behavior, skill instructions, and rationale
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add inline comments in the pre-commit hook script explaining auto-fix behavior

## Success Criteria

- [ ] `.githooks/pre-commit` exists and auto-fixes lint issues before commits
- [ ] `git config core.hooksPath` is set to `.githooks` (or setup instructions updated)
- [ ] `format_file.py` uses `ruff format` instead of `black`
- [ ] `builder.md` references `ruff format` instead of `black`
- [ ] `do-build/SKILL.md` contains "Lint Discipline" section with `--no-verify` guidance
- [ ] `do-patch/SKILL.md` contains "Lint Discipline" section with `--no-verify` guidance
- [ ] Agent can commit code with fixable lint issues -- hook auto-fixes silently
- [ ] Unfixable lint issues still block the commit with a clear error message
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (hooks-and-skills)**
  - Name: hooks-builder
  - Role: Create pre-commit hook, update format_file.py, update skill files and builder.md
  - Agent Type: builder
  - Resume: true

- **Validator (hooks-and-skills)**
  - Name: hooks-validator
  - Role: Verify hook auto-fixes, skill instructions are correct, black references removed
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create pre-commit hook and update format/skill files
- **Task ID**: build-hooks-and-skills
- **Depends On**: none
- **Assigned To**: hooks-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `.githooks/pre-commit` with auto-fix behavior: `ruff format .`, `ruff check --fix .`, `git add -u`, fail only on unfixable issues
- Make the hook executable (`chmod +x`)
- Update `.claude/hooks/format_file.py`: replace `black --quiet` with `ruff format --quiet`
- Update `.claude/agents/builder.md`: replace all `black` references with `ruff format`, update TDD workflow lint commands
- Add "Lint Discipline" section to `.claude/skills/do-build/SKILL.md` (before Critical Rules): instruct agents to use `--no-verify` for intermediate WIP commits and run `ruff format . && ruff check --fix .` once before final commit
- Add "Lint Discipline" section to `.claude/skills/do-patch/SKILL.md` (before Critical Rules): same instruction
- Add `git config core.hooksPath .githooks` to `scripts/remote-update.sh` if it exists

### 2. Validate all changes
- **Task ID**: validate-hooks-and-skills
- **Depends On**: build-hooks-and-skills
- **Assigned To**: hooks-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `.githooks/pre-commit` exists and is executable
- Verify `format_file.py` no longer references `black`
- Verify `builder.md` no longer references `black` (except in historical context)
- Verify `do-build/SKILL.md` contains "Lint Discipline" section
- Verify `do-patch/SKILL.md` contains "Lint Discipline" section
- Run `ruff check .` and `ruff format --check .` to verify clean state
- Run `pytest tests/ -x -q` to verify no test regressions

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-hooks-and-skills
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/lint-auto-fix.md` describing the hook behavior and skill instructions
- Add entry to `docs/features/README.md` index table

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: hooks-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Hook exists | `test -x .githooks/pre-commit` | exit code 0 |
| No black refs in format_file | `grep -c 'black' .claude/hooks/format_file.py` | exit code 1 |
| No black refs in builder | `grep -c 'black --check' .claude/agents/builder.md` | exit code 1 |
| Lint discipline in do-build | `grep -c 'Lint Discipline' .claude/skills/do-build/SKILL.md` | output > 0 |
| Lint discipline in do-patch | `grep -c 'Lint Discipline' .claude/skills/do-patch/SKILL.md` | output > 0 |

---

## Resolved Questions

1. **ruff format vs black baseline**: Yes, run `ruff format .` on main as a preparatory commit. Consistency is more important than the specific tool choice.

2. **core.hooksPath propagation**: Use per-project git config (`git config core.hooksPath .githooks` in each repo), not a global setting.

3. **black removal scope**: Yes, remove black in favor of ruff across all Python repos.
