# Lint Auto-Fix

Automatic lint and format fixing via git pre-commit hook and PostToolUse hook, eliminating agent lint churn loops.

## Problem

During do-build and do-patch, agents frequently got trapped in lint-related churn loops:

1. Agent makes code changes and commits
2. Manual lint check runs `ruff check` and fails
3. Agent tries to "fix" lint issues manually
4. Fixes sometimes break or revert actual code changes
5. Repeat -- agent loses focus on feature/patch work

This wasted tokens, polluted git history, and caused potential regressions.

## Solution

Two layers of automatic lint fixing ensure agents never need to think about lint:

### Layer 1: PostToolUse Hook (`format_file.py`)

After every `Write` or `Edit` tool call, the `format_file.py` hook automatically runs:

1. `ruff check --fix --quiet` on the changed file (auto-fix lint issues)
2. `ruff format --quiet` on the changed file (auto-format code)

This keeps individual files clean as agents work. The hook runs in builder subagents via the agent definition in `.claude/agents/builder.md`.

### Layer 2: Git Pre-Commit Hook (`.githooks/pre-commit`)

On every commit (unless `--no-verify` is used), the pre-commit hook:

1. Gets the list of staged Python files
2. Runs `ruff format --quiet` on all staged files
3. Runs `ruff check --fix --quiet` on all staged files
4. Re-stages the auto-fixed files with `git add`
5. Runs `ruff check --quiet` to detect any remaining unfixable issues
6. Only blocks the commit if genuinely unfixable issues remain
7. Then runs the existing secret scan (`scan_secrets.py`)

### Lint Discipline in Skill Files

The `do-build` and `do-patch` skill files include a "Lint Discipline" section that instructs agents:

- Use `--no-verify` for intermediate WIP commits (skip lint during mid-task work)
- Let the pre-commit hook run on final commits (auto-fixes everything fixable)
- Never run manual lint checks as separate steps

## Configuration

The pre-commit hook requires git to be configured to use the `.githooks/` directory:

```bash
git config core.hooksPath .githooks
```

This is automatically set by the update system (`scripts/update/git.py`).

## Files

| File | Purpose |
|------|---------|
| `.githooks/pre-commit` | Git pre-commit hook with auto-fix and secret scan |
| `.claude/hooks/format_file.py` | PostToolUse hook for per-file auto-fix |
| `.claude/agents/builder.md` | Builder agent definition (references ruff format) |
| `.claude/skills/do-build/SKILL.md` | Build skill with Lint Discipline section |
| `.claude/skills/do-patch/SKILL.md` | Patch skill with Lint Discipline section |

## Design Decisions

- **Ruff replaces Black**: Ruff handles both linting (`ruff check`) and formatting (`ruff format`), simplifying the toolchain to a single tool.
- **No `pre-commit` framework**: A simple shell script is sufficient. The `pre-commit` Python framework adds unnecessary complexity.
- **Only staged files**: The hook only processes staged Python files, not the entire repo, keeping it fast.
- **Auto-fix before check**: Running `--fix` before the final check means only genuinely unfixable issues block commits.
- **`--no-verify` for WIP**: Intermediate commits skip the hook entirely, avoiding unnecessary overhead during active development.
