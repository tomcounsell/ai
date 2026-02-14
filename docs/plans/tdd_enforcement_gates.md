---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-02-14
tracking: https://github.com/tomcounsell/ai/issues/101
---

# TDD Enforcement Gates for Build Workflow

## Problem

Builder agents can (and do) write implementation first and tests after — or skip tests entirely for "simple" changes. There's no structural enforcement of test-driven development.

**Current behavior:**
- Builder agent prompt says "Run tests" but doesn't enforce writing tests *first*
- The SDLC workflow is Build → Test → Fix loop — tests are an afterthought, not a starting point
- Validator checks that tests pass but not that tests *exist* for new code
- No mechanism prevents the agent from rationalizing "this is too simple to test"

**Desired outcome:**
- Builder agents write tests before implementation (RED-GREEN-REFACTOR)
- Validators verify that new/modified code has corresponding tests
- Common rationalizations for skipping tests are explicitly countered in the prompt
- The build workflow structurally enforces TDD, not just philosophically

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0 (prompt engineering, no alignment needed)
- Review rounds: 1

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Builder prompt TDD section**: RED-GREEN-REFACTOR instructions with hard gate language
- **Rationalization table**: Explicit counters to common excuses for skipping tests
- **Validator TDD check**: Verify test files exist/changed for implementation files changed
- **Definition of Done update**: Add "Tests written first (TDD)" as a requirement

### Flow

**Builder receives task** → [Write failing test] → [Verify it fails] → [Write minimal implementation] → [Verify it passes] → [Refactor] → **Mark complete**

**Validator receives task** → [Check git diff for test files] → [Verify test files changed alongside implementation files] → [Report pass/fail]

### Technical Approach

Three changes, all prompt-level:

1. **Builder agent prompt** (`.claude/agents/builder.md`): Replace the current "Build → Test → Fix" SDLC section with a TDD-first workflow: RED (write failing test) → GREEN (minimal implementation) → REFACTOR. Add hard gate language adapted from superpowers: "Code written before a test? Delete it. Start over."

2. **Rationalization table** in builder prompt: Add a "Common Rationalizations" section with explicit counters. Built from superpowers' table plus our own agent log observations.

3. **Validator agent prompt** (`.claude/agents/validator.md`): Add a TDD verification check — did test files get created/modified alongside implementation files? Check via `git diff --name-only` comparing test files vs implementation files.

### Rationalization Table (Draft)

| Rationalization | Reality |
|---|---|
| "It's too simple to test" | Simple code still breaks. Write the test. |
| "I'll write tests after" | You won't. Write them now. |
| "This is just a refactor" | Refactors break things. Tests prove they don't. |
| "The test would just duplicate the code" | Then your abstraction is wrong. |
| "This is just config/boilerplate" | Config errors cause production outages. Test the behavior. |
| "I'm running out of iterations" | Commit [WIP] with tests. Don't ship untested code. |
| "The existing code doesn't have tests" | That's why we're fixing it. Add tests for what you touch. |
| "I can't test this without mocking everything" | If it needs that many mocks, the design is wrong. Simplify first. |

### Exceptions (Explicit, Not Implicit)

TDD does not apply to:
- Documentation-only changes (markdown, comments)
- Configuration files (pyproject.toml, .env.example)
- Plan documents
- Agent/skill prompt files (the prompts themselves)
- Pure deletion of dead code (no new behavior)

Everything else: write the test first.

## Rabbit Holes

- **Coverage thresholds**: Don't try to enforce a specific coverage percentage. TDD naturally produces good coverage. Adding a threshold gate adds friction without value.
- **Testing the test order via git history**: Don't try to verify from git commits that tests were written chronologically before implementation. Too fragile. Trust the process, verify the artifacts.
- **Mocking policy**: Don't add a comprehensive mocking policy here. The rationalization table handles the worst case ("I can't test without mocking everything"). A full mock policy is a separate concern.

## Risks

### Risk 1: Builder agents ignore the TDD instructions
**Impact:** Tests still written after implementation, or skipped entirely
**Mitigation:** The validator independently checks for test files. Even if the builder ignores TDD order, the validator catches missing tests. Over time, review session logs and strengthen rationalizations.

### Risk 2: TDD slows down simple tasks
**Impact:** Builder takes more iterations on trivial changes
**Mitigation:** The exceptions list covers truly trivial cases. For everything else, TDD is faster in aggregate because it catches bugs earlier.

## No-Gos (Out of Scope)

- Coverage threshold enforcement
- Test framework changes or additions
- Pre-commit hooks (too heavy for this appetite)
- Changes to the build orchestrator or job queue
- Git history analysis for test ordering

## Update System

No update system changes required — this is purely prompt engineering on agent definitions.

## Agent Integration

No agent integration required — changes are to agent prompt files (`.claude/agents/builder.md` and `.claude/agents/validator.md`), which are loaded natively by Claude Code.

## Documentation

- [ ] Update `docs/features/README.md` index if a feature doc is created
- [ ] Inline documentation: the rationalization table and TDD process serve as self-documenting

## Success Criteria

- [ ] Builder agent prompt includes RED-GREEN-REFACTOR workflow with hard gate
- [ ] Rationalization table present in builder prompt
- [ ] Validator checks for test file changes alongside implementation changes
- [ ] Definition of Done includes "Tests written first"
- [ ] Exceptions list explicitly defined
- [ ] All existing tests pass after prompt changes

## Team Orchestration

### Team Members

- **Builder (prompts)**
  - Name: prompt-engineer
  - Role: Update builder and validator agent prompts with TDD enforcement
  - Agent Type: builder
  - Resume: true

- **Validator (verification)**
  - Name: prompt-validator
  - Role: Verify prompt changes are correct and complete
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update builder agent prompt with TDD workflow
- **Task ID**: build-tdd-prompt
- **Depends On**: none
- **Assigned To**: prompt-engineer
- **Agent Type**: builder
- **Parallel**: true
- Read current `.claude/agents/builder.md`
- Replace "SDLC Workflow (Build → Test Loop)" section with TDD-first workflow
- Add RED-GREEN-REFACTOR cycle with hard gate language
- Add rationalization table
- Add exceptions list
- Update Definition of Done to include "Tests written first"
- Preserve existing code quality hooks and safety net sections

### 2. Update validator agent prompt with TDD check
- **Task ID**: build-validator-prompt
- **Depends On**: none
- **Assigned To**: prompt-engineer
- **Agent Type**: builder
- **Parallel**: true
- Read current `.claude/agents/validator.md`
- Add TDD verification check to validation checklist
- Check: "Were test files created/modified for each implementation file changed?"
- Use `git diff --name-only` to compare test files vs implementation files
- Add exception awareness (don't flag docs-only or config-only changes)

### 3. Validate prompt changes
- **Task ID**: validate-prompts
- **Depends On**: build-tdd-prompt, build-validator-prompt
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify builder prompt contains RED-GREEN-REFACTOR workflow
- Verify rationalization table is present and complete
- Verify exceptions list is explicit
- Verify validator prompt includes TDD check
- Run existing tests to ensure nothing broke
- `pytest tests/ -v && ruff check . && black --check .`

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-prompts
- **Assigned To**: prompt-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met

## Validation Commands

- `cat .claude/agents/builder.md` - Verify TDD workflow and rationalization table present
- `cat .claude/agents/validator.md` - Verify TDD check in validation checklist
- `pytest tests/ -v` - Ensure existing tests still pass
- `ruff check .` - Linting
- `black --check .` - Formatting
