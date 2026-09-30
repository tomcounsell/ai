# Plan Prerequisites Validation

## Overview

Plans can declare environment prerequisites in a `## Prerequisites` markdown table. Before `/do-build` executes any tasks, it runs `scripts/check_prerequisites.py` to verify all requirements are met.

## How It Works

1. Plan author adds a `## Prerequisites` section with a markdown table listing requirements, check commands, and purposes
2. When `/do-build` is invoked, step 0 runs `python scripts/check_prerequisites.py <plan-path>`
3. The script parses the table, runs each check command, and reports pass/fail
4. If any check fails, the build stops with a clear report of what's missing
5. Plans without a Prerequisites section pass automatically (backward compatible)

## Prerequisites Table Format

```markdown
## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `STRIPE_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('STRIPE_API_KEY')"` | Stripe payments |
| `redis-server` | `which redis-server` | Queue backend |
```

## Check Command Guidelines

- Commands should be **read-only assertions** that test for presence, not modify state
- Use `python -c "assert ..."` for environment variable checks
- Use `which <tool>` for CLI tool availability
- Use `python -c "import <module>"` for Python package checks
- Each command runs with a 30-second timeout

## Files

| File | Purpose |
|------|---------|
| `scripts/check_prerequisites.py` | Prerequisite checker script |
| `.claude/skills/do-plan/SKILL.md` | Plan template with Prerequisites section |
| `.claude/commands/do-build.md` | Build skill with step 0 check |
