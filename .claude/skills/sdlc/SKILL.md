---
name: sdlc
description: "The single entry point for all issue-referenced work. Detects where an issue stands in the pipeline and picks up from there. Runs Plan → Build → Test → Review → Docs → Ship with quality gates."
context: fork
---

# AI Developer Workflow (SDLC)

Autonomous software development lifecycle: **Plan → Build → Test → Review → Docs → Ship**

This is the **single entry point** for all development work referenced by issue number. It runs to completion without human intervention. Each phase validates before proceeding. Failures loop back automatically.

## Step 0: Assess Current State

Before doing anything, determine where this issue stands. Run these checks:

```bash
# 1. Get the issue details
gh issue view {number}

# 2. Check if a plan already exists
ls docs/plans/*.md  # look for a plan that references this issue

# 3. Check if a feature branch exists
git branch -a | grep session/

# 4. Check if a PR already exists
gh pr list --search "issue {number}" --state open
```

Based on the results, **pick up from the right phase**:

| State | Action |
|-------|--------|
| No plan exists | Start from Plan — invoke `/do-plan` |
| Plan exists, no branch/PR | Start from Build — invoke `/do-build` |
| Branch exists, tests failing | Invoke `/do-patch` to fix failures, then `/do-test` |
| Branch exists, tests passing, no PR | Invoke `/do-pr-review` to open and review PR |
| PR exists, review blockers | Invoke `/do-patch` to fix blockers, re-test, re-review |
| PR approved, docs not updated | Invoke `/do-docs` to cascade doc updates |
| PR approved, docs done | Report ready for human merge |

Do NOT restart from scratch if prior phases are already complete.

## Core Principle

Don't just write code. Execute a complete development cycle with built-in quality gates. The system should not stop until:
1. Code is written
2. Tests pass
3. Quality checks pass
4. Documentation is updated (/do-docs)
5. Work is committed and pushed

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
│                     5. DOCS PHASE (/do-docs)                 │                            │
│  - Run /do-docs cascade                                      │
│  - Update all docs referencing changed area                  │
│  - Create new docs if feature is undocumented                │
│  - Verify docs match actual implementation                   │
└─────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌─────────────────────────────────────────────────────────────┐
│                     6. SHIP PHASE                            │
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

### 5. DOCS Phase

Cascade documentation updates using `/do-docs`:

```markdown
## Docs Checklist
- [ ] Run /do-docs cascade for the changes made
- [ ] All docs referencing changed areas are updated
- [ ] New features have corresponding docs created
- [ ] Feature index (docs/features/README.md) updated if needed
- [ ] No stale references to old patterns remain
```

The `/do-docs` skill handles this automatically — it finds every document referencing the changed area and makes targeted surgical updates so docs match the actual implementation.

If `/do-docs` reports zero affected documents, that's fine — proceed to Ship.

### 6. SHIP Phase

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
            docs()   # /do-docs cascade
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
| Docs | Docs match implementation | Update stale references |
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

DOCS (/do-docs):
- Updated docs/tools-reference.md with rate limit parameters
- No other docs referenced rate limiting

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
**Phases**: Plan → Build → Test → Review → Docs → Ship
**Completion Criteria**: All quality gates pass, code is pushed
**Failure Handling**: Automatic loop-back with analysis
