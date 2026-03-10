---
status: Building
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/330
---

# Machine-readable Definition of Done in Plan Documents

## Problem

Plan documents define success criteria as prose checkboxes that are not machine-verifiable. The `/do-build` skill must rely on LLM judgment to determine whether criteria are met.

**Current behavior:**
- Validation Commands section exists in plans but is free-form prose
- No structured format for automated extraction and execution
- Subjective completion, silent skipping of hard-to-check criteria

**Desired outcome:**
A structured `## Verification` table in plan documents with executable checks that `/do-build` and `/do-pr-review` parse and run automatically.

## Prior Art

No prior issues found related to machine-readable verification in plan documents.

- **#331 (Goal Gates)**: Enforces stage transitions with deterministic gates -- this complements that work by making per-plan checks machine-readable too.

## Data Flow

1. **Entry point**: Plan author writes `## Verification` table during `/do-plan`
2. **Hook validation**: `validate_verification_section.py` enforces the table exists with at least one check
3. **Parser**: `agent/verification_parser.py` extracts `VerificationCheck` objects from markdown
4. **Execution in /do-build**: After build completes, parser extracts checks, `run_checks()` executes them
5. **Execution in /do-pr-review**: During review, same parser runs checks on the PR branch
6. **Output**: Structured pass/fail report with check name, command, expected vs actual

## Architectural Impact

- **New file**: `agent/verification_parser.py` -- pure functions, no external dependencies beyond subprocess
- **New file**: `.claude/hooks/validators/validate_verification_section.py` -- hook enforcing section exists
- **Modified**: `.claude/skills/do-plan/PLAN_TEMPLATE.md` -- replaces Validation Commands with Verification table
- **Modified**: `.claude/skills/do-build/SKILL.md` -- adds Step 5.1 for automated verification
- **Modified**: `.claude/skills/do-pr-review/SKILL.md` -- adds Step 4.5 for verification in review
- **Coupling**: Low -- parser is standalone, consumed by skills via prompt instructions
- **Reversibility**: Easy -- removing the step from SKILL.md files reverts to manual validation

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Verification parser** (`agent/verification_parser.py`): Extracts checks from markdown tables, evaluates expectations, runs commands
- **Hook validator** (`validate_verification_section.py`): Enforces the section exists during plan creation
- **Plan template update**: Replaces prose Validation Commands with structured table
- **Build integration**: `/do-build` runs checks automatically after build
- **Review integration**: `/do-pr-review` runs checks and includes results in review

### Flow

**Plan created** --> Hook validates `## Verification` exists --> **Build completes** --> Parser extracts checks --> `run_checks()` executes each --> Structured report --> All pass? --> PR ready

### Technical Approach

- `VerificationCheck` dataclass: `name`, `command`, `expected`
- `parse_verification_table(markdown)` returns list of checks
- `evaluate_expectation(expected, exit_code, output)` handles three formats: `exit code N`, `output > N`, `output contains X`
- `run_checks(checks, cwd)` executes commands via subprocess
- `format_results(results)` produces human-readable report

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `run_checks()` catches subprocess exceptions and returns `CheckResult` with error details
- [ ] Timeout handling returns clear error message

### Empty/Invalid Input Handling
- [ ] `parse_verification_table()` returns empty list when no section found
- [ ] `evaluate_expectation()` returns False for unknown expectation formats
- [ ] Empty tables and malformed rows are handled gracefully

### Error State Rendering
- [ ] Failed checks include command, expected, and actual output in report
- [ ] `format_results()` produces structured pass/fail report

## Rabbit Holes

- Migrating all existing plans to the new format (future work -- existing plans still have Validation Commands)
- Complex expectation DSL (keep to three simple patterns)
- Graph-based pipeline engine from attractor (out of scope)

## Risks

### Risk 1: Breaking existing plan validation
**Impact:** Existing plans without ## Verification section fail hook validation
**Mitigation:** Hook only triggers on new/modified plan files, not existing ones. Auto-detection via git status.

## Race Conditions

No race conditions identified. All operations are synchronous file reads and subprocess calls.

## No-Gos (Out of Scope)

- Migrating existing plans (they keep working with their Validation Commands sections)
- Complex expectation formats beyond the three supported patterns
- Caching or parallelizing check execution
- Automatic remediation of failed checks (that is /do-patch's job)

## Update System

No update system changes required -- this feature is purely internal to the SDLC pipeline.

## Agent Integration

No agent integration required -- this is infrastructure consumed by `/do-build` and `/do-pr-review` skill prompts. The parser is invoked via inline Python in the skill instructions, not via MCP tools.

## Documentation

- [ ] Create `docs/features/machine-readable-dod.md` describing the verification table format
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Code comments and docstrings in `agent/verification_parser.py`

## Success Criteria

- [x] Plan template includes structured `## Verification` table format
- [x] Hook validator enforces the section exists with at least one check
- [x] `/do-build` automatically extracts and runs verification checks from the plan
- [x] Failed checks produce structured failure output (check name + command + actual vs expected)
- [x] `/do-pr-review` re-runs verification checks and includes results in review comment
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_verification_parser.py tests/unit/test_validate_verification_section.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check agent/verification_parser.py .claude/hooks/validators/validate_verification_section.py` | exit code 0 |
| Format clean | `python -m ruff format --check agent/verification_parser.py` | exit code 0 |
| Parser importable | `python -c "from agent.verification_parser import parse_verification_table, run_checks, format_results; print('ok')"` | exit code 0 |
| Template has table | `python -c "from agent.verification_parser import parse_verification_table; t = open('.claude/skills/do-plan/PLAN_TEMPLATE.md').read(); checks = parse_verification_table(t); assert len(checks) >= 3, f'Expected >=3 checks, got {len(checks)}'; print('ok')"` | exit code 0 |
| Feature doc exists | `test -f docs/features/machine-readable-dod.md` | exit code 0 |

## Team Orchestration

### Team Members

- **Builder (parser)**
  - Name: parser-builder
  - Role: Implement verification parser and hook validator
  - Agent Type: builder
  - Resume: true

- **Builder (integration)**
  - Name: integration-builder
  - Role: Update skill files and plan template
  - Agent Type: builder
  - Resume: true

- **Documentarian (docs)**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build Parser and Hook Validator
- **Task ID**: build-parser
- **Depends On**: none
- **Assigned To**: parser-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `agent/verification_parser.py` with VerificationCheck, parse_verification_table, evaluate_expectation, run_checks, format_results
- Create `.claude/hooks/validators/validate_verification_section.py`
- Write unit tests for both

### 2. Update Plan Template and Skills
- **Task ID**: build-integration
- **Depends On**: build-parser
- **Assigned To**: integration-builder
- **Agent Type**: builder
- **Parallel**: false
- Replace Validation Commands with Verification table in PLAN_TEMPLATE.md
- Add Step 5.1 to /do-build SKILL.md
- Add Step 4.5 to /do-pr-review SKILL.md

### 3. Documentation
- **Task ID**: document-feature
- **Depends On**: build-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create docs/features/machine-readable-dod.md
- Add entry to docs/features/README.md

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: parser-builder
- **Agent Type**: validator
- **Parallel**: false
- Run all verification checks from the plan
- Verify all success criteria met

---

## Open Questions

None -- the scope is well-defined and all technical decisions are made.
