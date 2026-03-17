# Sub-Skill: Code Review

Judgment work: analyze the PR diff for correctness, security, and quality.

## Context Variables

- `$SDLC_PR_NUMBER` — PR number (fallback: extract from git or coaching message)
- `$SDLC_SLUG` — work item slug for finding the plan document
- `$SDLC_PLAN_PATH` — direct path to plan document (fallback: derive from slug)
- `$SDLC_ISSUE_NUMBER` — tracking issue number

## Prerequisites

PR branch must already be checked out (via checkout sub-skill).

## Steps

### 1. Gather PR Context

```bash
PR_NUMBER="${SDLC_PR_NUMBER}"
gh pr view $PR_NUMBER --json title,body,headRefName,baseRefName,files,additions,deletions
gh pr diff $PR_NUMBER
gh pr diff $PR_NUMBER --name-only
```

### 2. Load Plan Context

```bash
PLAN_PATH="${SDLC_PLAN_PATH:-}"
if [ -z "$PLAN_PATH" ] && [ -n "${SDLC_SLUG:-}" ]; then
  PLAN_PATH="docs/plans/${SDLC_SLUG}.md"
fi
```

Read the plan document (if it exists) and extract:
- Acceptance criteria / success conditions
- No-Gos (things explicitly excluded)
- Architectural decisions

If `$SDLC_ISSUE_NUMBER` is set, also fetch the issue:
```bash
gh issue view $SDLC_ISSUE_NUMBER
```

### 3. Analyze the Diff

For each changed file, evaluate:

- **Correctness**: Does the code do what the plan/PR description says?
- **Security**: No secrets, injection vulnerabilities, or unsafe patterns
- **Error handling**: Appropriate error handling at system boundaries
- **Tests**: Are new features covered by tests? Do existing tests pass?
- **Code quality**: Follows project patterns, no unnecessary complexity
- **Documentation**: Are docs updated for user-facing changes?

Check for common issues:
- Leftover debug code (`print()`, `console.log()`, `TODO`)
- Missing error handling for external calls
- Hardcoded values that should be configurable
- Breaking changes without migration path

### 4. Plan Validation (if plan exists)

For each requirement/acceptance criterion in the plan:
1. Locate the corresponding implementation in the PR diff
2. Verify behavior matches the plan specification
3. Check that edge cases mentioned in the plan are handled
4. Verify any "No-Gos" from the plan are respected

### 5. Run Verification Checks (if plan has ## Verification table)

```bash
python -c "
from agent.verification_parser import parse_verification_table, run_checks, format_results
from pathlib import Path
plan = Path('${SDLC_PLAN_PATH}').read_text()
checks = parse_verification_table(plan)
if checks:
    results = run_checks(checks)
    print(format_results(results))
else:
    print('No verification table in plan.')
"
```

### 6. Classify Findings

**Severity Guidelines:**

- **blocker**: Must fix before merge (breaks functionality, security issue, data loss risk)
- **tech_debt**: Fix before merge, patched by `/do-patch` (code quality, missing edge case tests)
- **nit**: Fix before merge unless purely subjective (style, naming, docs wording)

For each finding, use this format:
```
**File:** `path/to/file.py:42` (verified: read this file)
**Code:** `the_actual_code_on_that_line()`
**Issue:** [clear description of the problem]
**Severity:** blocker | tech_debt | nit
**Fix:** [suggested fix]
```

### 7. Verify All Findings

Before reporting, verify every blocker and tech_debt finding:
1. Confirm the file exists (you must have read it)
2. Confirm the code exists at or near the cited line
3. Confirm your description of the problem is accurate

Drop any finding that fails verification. A false blocker is worse than a missed issue.

## Completion

Return the list of classified findings and the verification results (if any).
