---
description: "Autonomous software development lifecycle that executes Plan → Build → Test → Review → Ship phases with quality gates. Use when the user wants end-to-end autonomous development or says 'ship this feature'."
---

# AI Developer Workflow (SDLC)

Autonomous software development lifecycle: **Plan → Build → Test → Review → Ship**

This workflow runs to completion without human intervention. Each phase validates before proceeding. Failures loop back automatically.

## Core Principle

Don't just write code. Execute a complete development cycle with built-in quality gates. The system should not stop until:
1. Code is written
2. Tests pass
3. Quality checks pass
4. Work is committed and pushed

## The Workflow

```
┌─────────────────────────────────────────────────────────────┐
│                        USER REQUEST                          │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     1. PLAN PHASE                            │
│  - Understand requirements                                   │
│  - Identify files to modify                                  │
│  - Design approach                                           │
│  - Create spec/checklist                                     │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     2. BUILD PHASE                           │
│  - Implement changes                                         │
│  - Follow existing patterns                                  │
│  - Write/update tests                                        │
│  - Update documentation                                      │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     3. TEST PHASE                            │
│  - Run unit tests                                            │
│  - Run integration tests                                     │
│  - Run linting (ruff, black, mypy)                          │
│  - Validate functionality                                    │
└─────────────────────────────────────────────────────────────┘
                              │
                    ┌────────┴────────┐
                    │                 │
               Tests Pass        Tests Fail
                    │                 │
                    ▼                 ▼
┌──────────────────────┐   ┌──────────────────────┐
│   4. REVIEW PHASE    │   │   LOOP BACK TO BUILD │
│  - Self-review code  │   │  - Analyze failures  │
│  - Check for issues  │   │  - Fix issues        │
│  - Validate complete │   │  - Re-run tests      │
└──────────────────────┘   └──────────────────────┘
                    │
                    ▼
┌─────────────────────────────────────────────────────────────┐
│                     5. SHIP PHASE                            │
│  - Stage changes (git add)                                   │
│  - Commit with clear message                                 │
│  - Push to remote                                            │
│  - Report completion                                         │
└─────────────────────────────────────────────────────────────┘
```

## Phase Details

### 1. PLAN Phase

Before writing any code:

```markdown
## Planning Checklist
- [ ] Understand the goal and success criteria
- [ ] Identify all files that need modification
- [ ] Check existing patterns in the codebase
- [ ] Identify dependencies and impacts
- [ ] Design the approach
- [ ] List specific changes to make
```

Output: Clear spec of what will be done

### 2. BUILD Phase

Implementation with quality built in:

```markdown
## Build Checklist
- [ ] Follow existing code patterns
- [ ] Keep changes minimal and focused
- [ ] Write tests alongside code
- [ ] Handle error cases
- [ ] Update documentation if needed
- [ ] No TODO comments left behind
```

### 3. TEST Phase

Automated validation (must all pass):

```bash
# Run in sequence, stop on first failure
pytest tests/ -v
ruff check .
black --check .
mypy . --ignore-missing-imports
```

If ANY test fails:
1. Analyze the failure
2. Fix the issue
3. Return to BUILD phase
4. Re-run TEST phase
5. Repeat until all pass

### 4. REVIEW Phase

Self-review before shipping:

```markdown
## Review Checklist
- [ ] Changes match the original request
- [ ] No unintended side effects
- [ ] Tests cover the new functionality
- [ ] Code is clean and maintainable
- [ ] No security issues introduced
- [ ] No debugging code left in
```

### 5. SHIP Phase

Commit and push:

```bash
git add -A
git commit -m "Descriptive commit message

- What was done
- Why it was done
- Any notable decisions"
git push
```

## Validation Loop (Ralph Wiggum Pattern)

The system MUST NOT stop until the full cycle completes:

```python
def execute_sdlc(task):
    spec = plan(task)

    max_iterations = 5
    iteration = 0

    while iteration < max_iterations:
        build(spec)
        test_results = test()

        if test_results.all_pass:
            review()
            ship()
            return SUCCESS
        else:
            # Analyze failures, update spec, try again
            spec = analyze_and_fix(test_results, spec)
            iteration += 1

    # Only escalate after multiple failed attempts
    escalate_to_human("Failed after {iteration} attempts")
```

## When to Use This Workflow

**Use SDLC for:**
- Feature implementations
- Bug fixes
- Refactoring tasks
- Any code change that should be tested

**Skip SDLC for:**
- Pure research/exploration
- Documentation-only changes
- Configuration changes
- Quick fixes explicitly requested without tests

## Integration with Thread Types

The SDLC workflow can use different thread types:

- **Base Thread**: Simple changes, single pass
- **C-Thread**: Large changes, phase checkpoints
- **P-Thread**: Parallel test execution across modules
- **L-Thread**: Complex features requiring extended work

## Quality Gates

Each phase has a quality gate. The system cannot proceed unless the gate passes:

| Phase | Gate | Failure Action |
|-------|------|----------------|
| Plan | Clear spec exists | Refine understanding |
| Build | Code compiles/loads | Fix syntax/imports |
| Test | All tests pass | Loop back to Build |
| Review | Self-review passes | Address issues |
| Ship | Commit succeeds | Resolve conflicts |

## Metrics

Track SDLC efficiency:

```python
sdlc_metrics = {
    "iterations_to_success": N,  # Lower is better
    "test_failures_fixed": count,
    "total_duration": time,
    "lines_changed": diff_stats,
    "test_coverage_delta": coverage_change
}
```

## Example: Autonomous Feature Implementation

```markdown
Input: "Add rate limiting to the API endpoints"

PLAN:
- Identify: routes.py, middleware.py
- Pattern: Use existing decorator pattern
- Approach: Token bucket algorithm
- Tests: test_rate_limiting.py

BUILD:
- Created RateLimiter class
- Added @rate_limit decorator
- Applied to all public endpoints
- Wrote 5 test cases

TEST (Iteration 1):
- pytest: 3 failures (edge cases)
- ruff: pass
- black: pass
- mypy: 1 error (type hint)

BUILD (Fix):
- Fixed edge cases
- Added type hints

TEST (Iteration 2):
- All pass

REVIEW:
- Changes match request
- Tests comprehensive
- No security issues

SHIP:
- Committed: "Add rate limiting to API endpoints"
- Pushed to origin

COMPLETE
```

## Key Insight

*"Agents should verify their own work. This creates closed-loop systems where agents self-correct."*

The SDLC workflow embodies this principle. The system doesn't ask "is this good?" - it runs tests, checks quality, and only ships when everything passes.

---

**Workflow Type**: AI Developer Workflow (ADW)
**Phases**: Plan → Build → Test → Review → Ship
**Completion Criteria**: All quality gates pass, code is pushed
**Failure Handling**: Automatic loop-back with analysis
