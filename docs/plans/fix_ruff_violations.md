---
status: Ready
type: chore
appetite: Small
owner: Valor
created: 2026-03-05
tracking: https://github.com/tomcounsell/ai/issues/255
---

# Fix Pre-existing Ruff Lint and Format Violations

## Problem

The SDLC gate now enforces `ruff format` and `ruff check`, but the codebase was historically formatted with `black`. This creates a mismatch: 141 files fail `ruff format --check` and 8 lint errors exist across 2 files.

**Current behavior:**
Any PR touching these files triggers format/lint failures in the SDLC quality gate, even though the violations are pre-existing.

**Desired outcome:**
`ruff check .` and `ruff format --check .` both pass with zero violations on the main branch.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

This is a mechanical formatting pass with a handful of manual lint fixes. No design decisions needed.

## Prerequisites

No prerequisites — ruff is already installed in `.venv`.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `ruff` available | `.venv/bin/ruff --version` | Linting and formatting |

## Solution

### Key Elements

- **Auto-format**: Run `ruff format .` to reformat all 141 files to ruff's style
- **Auto-fix lint**: Run `ruff check --fix .` to fix the 5 auto-fixable f-string violations
- **Manual lint fixes**: Fix the 3 remaining errors by hand

### Technical Approach

**Step 1 — Auto-fix (handles ~98% of violations):**
```bash
ruff format .
ruff check --fix .
```

**Step 2 — Manual fixes (3 remaining errors):**

1. `config/settings.py:17` — Change `class LogLevel(str, Enum)` to `class LogLevel(StrEnum)` and update import
2. `scripts/analyze_error_log.py:157` — Remove unused `tb_text` assignment
3. `scripts/analyze_error_log.py:352` — Break long line (E501, 143 > 100 chars)

**Step 3 — Verify clean:**
```bash
ruff check . && ruff format --check .
```

## Rabbit Holes

- Do NOT change ruff configuration or add new rules — fix existing violations only
- Do NOT refactor code while formatting — pure style changes only
- Do NOT update `.pre-commit-config.yaml` or add new hooks

## Risks

### Risk 1: Large diff obscures meaningful changes in git blame
**Impact:** `git blame` on 141 files will point to this commit instead of the original author
**Mitigation:** Use `git blame --ignore-rev` with this commit's SHA. Add the SHA to `.git-blame-ignore-revs` if the file exists.

## No-Gos (Out of Scope)

- Adding new ruff rules or changing `pyproject.toml` lint config
- Refactoring any logic alongside formatting
- Fixing warnings or style issues not flagged by current ruff config

## Update System

No update system changes required — this is a formatting-only change with no new dependencies or config files.

## Agent Integration

No agent integration required — this is a codebase hygiene change that does not affect tools, MCP servers, or bridge behavior.

## Documentation

- [ ] No feature documentation needed — this is a formatting chore
- [ ] Verify `CLAUDE.md` quality gate commands still reference `ruff format` (already correct)

## Success Criteria

- [ ] `ruff check .` exits 0 (zero lint errors)
- [ ] `ruff format --check .` exits 0 (zero format violations)
- [ ] No functional code changes — only style/lint fixes
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (ruff-fixer)**
  - Name: ruff-fixer
  - Role: Apply ruff formatting and fix lint errors
  - Agent Type: builder
  - Resume: true

- **Validator (lint-checker)**
  - Name: lint-checker
  - Role: Verify zero violations remain
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Apply ruff auto-formatting and auto-fixes
- **Task ID**: build-ruff-format
- **Depends On**: none
- **Assigned To**: ruff-fixer
- **Agent Type**: builder
- **Parallel**: false
- Run `ruff format .` to reformat all files
- Run `ruff check --fix .` to auto-fix lint errors
- Manually fix `config/settings.py` LogLevel to use StrEnum
- Manually remove unused `tb_text` variable in `scripts/analyze_error_log.py`
- Manually fix line-too-long in `scripts/analyze_error_log.py:352`

### 2. Validate zero violations
- **Task ID**: validate-ruff-clean
- **Depends On**: build-ruff-format
- **Assigned To**: lint-checker
- **Agent Type**: validator
- **Parallel**: false
- Run `ruff check .` and verify exit code 0
- Run `ruff format --check .` and verify exit code 0
- Confirm no functional code changes (only style)

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-ruff-clean
- **Assigned To**: lint-checker
- **Agent Type**: validator
- **Parallel**: false
- Run test suite to confirm nothing is broken
- Verify all success criteria met

## Validation Commands

- `.venv/bin/ruff check .` - Zero lint errors
- `.venv/bin/ruff format --check .` - Zero format violations
- `pytest tests/ -x -q` - Tests still pass
