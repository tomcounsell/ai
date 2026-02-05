---
status: Complete
type: feature
appetite: Small: 1-2 days
owner: Valor
created: 2026-02-05
tracking: https://github.com/tomcounsell/ai/issues/57
---

# Plan Prerequisites Section with Validation

## Problem

Plans currently have no way to declare or validate prerequisites before execution begins. This leads to silent failures where a builder discovers halfway through that a required API key, dependency, or service isn't available. Acceptance criteria get marked "complete" with workarounds like "skipped when keys missing" instead of actual validation.

**Current behavior:**
Plans have no Prerequisites section. The `/build` skill starts executing immediately without verifying the environment can support the work. Missing requirements are discovered mid-build, wasting time and producing incomplete results.

**Desired outcome:**
Every plan includes a `## Prerequisites` section with check commands. The `/build` skill runs `scripts/check_prerequisites.py` as step 0 before any builder tasks. If prerequisites fail, execution stops with a clear report of what's missing.

## Appetite

**Time budget:** Small: 1-2 days

**Team size:** Solo

Three changes: template update, new script, build skill integration. All are small and well-scoped.

## Solution

### Key Elements

- **Template section**: Add `## Prerequisites` to the make-plan SKILL.md template between `## Appetite` and `## Solution`
- **Prerequisite checker script**: `scripts/check_prerequisites.py` that parses a plan's Prerequisites table and runs each check command
- **Build skill integration**: `/build` runs the checker as step 0, aborting if any check fails

### Flow

**Plan creation flow:**
Author writes plan → includes `## Prerequisites` table with requirements and check commands

**Build execution flow:**
`/build docs/plans/foo.md` → run `check_prerequisites.py docs/plans/foo.md` → all checks pass → proceed to step 1 → (or) any check fails → stop and report what's missing

### Technical Approach

**1. Template addition in `.claude/skills/make-plan/SKILL.md`**

Add after `## Appetite` section:

```markdown
## Prerequisites

[Environment requirements that must be satisfied before building. Each requirement has a programmatic check command.]

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `EXAMPLE_API_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env').get('EXAMPLE_API_KEY')"` | Example service |

Run all checks: `python scripts/check_prerequisites.py docs/plans/{slug}.md`
```

**2. Prerequisite checker: `scripts/check_prerequisites.py`**

```python
#!/usr/bin/env python3
"""Validate plan prerequisites before build execution."""
# Reads a plan markdown file
# Parses the ## Prerequisites section for the table
# Extracts check commands from the "Check Command" column
# Runs each command via subprocess
# Reports pass/fail for each, exits 0 if all pass, 1 if any fail
```

The script:
- Accepts a plan path as argument
- Parses the markdown to find `## Prerequisites`
- Extracts rows from the markdown table
- Runs each check command via `subprocess.run(cmd, shell=True)`
- Prints a clear report: `PASS: requirement` or `FAIL: requirement — error`
- Exits 0 if all pass, 1 if any fail
- If no Prerequisites section exists, prints a note and exits 0 (backward compatible)

**3. Build skill integration in `.claude/commands/build.md`**

Add as step 0 in the Instructions section:

> Before creating the feature branch, run prerequisite validation:
> `python scripts/check_prerequisites.py {PLAN_PATH}`
> If any check fails, report the failures and stop. Do not proceed to task execution.

**4. Validator update in `.claude/hooks/validators/validate_file_contains.py`**

Add `## Prerequisites` to the required sections check in the SKILL.md hooks config.

## Rabbit Holes

- Don't build a complex prerequisite resolution system (auto-install missing deps, etc.) — just check and report
- Don't add prerequisite caching or state tracking — run fresh every time
- Don't require Prerequisites for plans that genuinely have none — the checker handles missing sections gracefully

## Risks

### Risk 1: Markdown table parsing fragility
**Impact:** If the table format varies, the parser may miss check commands
**Mitigation:** Use a simple regex-based parser that handles standard markdown table syntax. The template enforces a consistent format. If parsing fails, report the error clearly rather than silently passing.

### Risk 2: Check commands may have side effects
**Impact:** A poorly written check command could modify state
**Mitigation:** Document that check commands should be read-only assertions. The convention of `python -c "assert ..."` makes this clear.

## No-Gos (Out of Scope)

- Auto-installing missing prerequisites
- Prerequisite caching or state management
- Retroactively adding Prerequisites sections to existing plans
- Making Prerequisites mandatory for plans that have no environment requirements
- Running prerequisite checks outside of the `/build` workflow

## Update System

No update system changes required. The new script is a standalone Python file in `scripts/`. It will be available on all machines after git pull. No new dependencies beyond Python stdlib (uses `subprocess`, `re`, `sys`).

## Agent Integration

No agent integration required — the prerequisite checker is a developer workflow tool invoked by the `/build` skill, not an agent-facing capability.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/plan-prerequisites.md` describing the prerequisites system
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstring in `scripts/check_prerequisites.py` with usage examples
- [ ] Comments in the make-plan template explaining the Prerequisites section format

## Success Criteria

- [ ] Make-plan SKILL.md template includes `## Prerequisites` section with table format
- [ ] `scripts/check_prerequisites.py` exists and can parse a plan's Prerequisites table
- [ ] `scripts/check_prerequisites.py` exits 0 when all checks pass
- [ ] `scripts/check_prerequisites.py` exits 1 with clear report when checks fail
- [ ] `scripts/check_prerequisites.py` handles plans without Prerequisites section gracefully
- [ ] Build skill (`/build`) runs prerequisite check as step 0
- [ ] Plan validator requires `## Prerequisites` section
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (script)**
  - Name: script-builder
  - Role: Create the prerequisite checker script
  - Agent Type: builder
  - Resume: true

- **Builder (template)**
  - Name: template-builder
  - Role: Update make-plan template, build skill, and validator hooks
  - Agent Type: builder
  - Resume: true

- **Validator (prerequisites)**
  - Name: prereq-validator
  - Role: Verify the full prerequisites workflow
  - Agent Type: validator
  - Resume: true

- **Documentarian (prerequisites)**
  - Name: prereq-docs
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Create prerequisite checker script
- **Task ID**: build-script
- **Depends On**: none
- **Assigned To**: script-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/check_prerequisites.py`
- Parse `## Prerequisites` section from a given plan file
- Extract markdown table rows and check commands
- Run each check command, collect pass/fail results
- Print clear report and exit with appropriate code
- Handle missing Prerequisites section gracefully (exit 0)

### 2. Update make-plan template and build skill
- **Task ID**: build-template
- **Depends On**: none
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `## Prerequisites` section to `.claude/skills/make-plan/SKILL.md` template (between `## Appetite` and `## Solution`)
- Add `## Prerequisites` to the validate_file_contains hook in SKILL.md frontmatter
- Add step 0 prerequisite check to `.claude/commands/build.md`

### 3. Validate prerequisites workflow
- **Task ID**: validate-prereqs
- **Depends On**: build-script, build-template
- **Assigned To**: prereq-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `scripts/check_prerequisites.py` parses test plans correctly
- Verify it exits 0 on passing checks and 1 on failing checks
- Verify make-plan template includes Prerequisites section
- Verify build skill includes step 0 prerequisite check
- Verify validator hooks include Prerequisites
- Run all validation commands

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-prereqs
- **Assigned To**: prereq-docs
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/plan-prerequisites.md`
- Add entry to `docs/features/README.md` index table (if it exists by then, otherwise note for future)

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: prereq-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Validation Commands

- `python scripts/check_prerequisites.py --help 2>&1 || python scripts/check_prerequisites.py 2>&1 | head -5` - verify script runs
- `grep '## Prerequisites' .claude/skills/make-plan/SKILL.md` - verify template section exists
- `grep 'Prerequisites' .claude/skills/make-plan/SKILL.md | head -3` - verify validator hook includes it
- `grep -i 'prerequisit' .claude/commands/build.md` - verify build skill integration
- `python -c "import ast; ast.parse(open('scripts/check_prerequisites.py').read()); print('syntax OK')"` - verify script syntax
