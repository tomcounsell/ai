---
description: "Review pull requests by analyzing code changes, validating against plan requirements, and capturing visual proof via screenshots"
argument-hint: <PR-number>
---

# PR Review

Review a pull request by analyzing its changes against the plan, checking code quality, validating tests, and capturing screenshots of UI changes.

## When to Use

- After `/do-build` creates a PR
- When a PR needs thorough review before merge
- To validate UI changes visually with screenshots
- To generate structured review reports with issue severity classification

## Variables

- `pr_number` (required): The PR number to review (e.g., `42` or `#42`)

## Instructions

Follow this review process to validate a pull request:

### 1. PR Context Gathering

**Fetch PR details:**
```bash
gh pr view {pr_number} --json title,body,headRefName,baseRefName,files,additions,deletions
```

**Get the full diff:**
```bash
gh pr diff {pr_number}
```

**Get changed files:**
```bash
gh pr diff {pr_number} --name-only
```

**Find the associated plan (if any):**
- Check PR body for `Closes #N` to find the tracking issue
- Look for plan docs in `docs/plans/` that reference that issue
- The plan contains acceptance criteria and requirements to validate against

### 2. Code Review

**Analyze the diff for:**

- **Correctness**: Does the code do what the plan/PR description says?
- **Security**: No secrets, injection vulnerabilities, or unsafe patterns
- **Error handling**: Appropriate error handling at system boundaries
- **Tests**: Are new features covered by tests? Do existing tests still pass?
- **Code quality**: Follows project patterns, no unnecessary complexity
- **Documentation**: Are docs updated for user-facing changes?

**Check for common issues:**
- Leftover debug code (`print()`, `console.log()`, `TODO`)
- Missing error handling for external calls
- Hardcoded values that should be configurable
- Breaking changes without migration path

### 3. Screenshot Capture (if UI changes detected)

**Determine if screenshots needed:**
- Check diff for UI-related files: `*.html`, `*.jsx`, `*.tsx`, `*.vue`, `*.css`, `*.scss`, `*.py` (templates)
- If no UI files changed, skip this step

**If screenshots needed:**

```bash
# Prepare screenshot directory
mkdir -p generated_images/pr-{pr_number}

# Checkout the PR branch locally
gh pr checkout {pr_number}

# Use /prepare_app to ensure app is running
# Then capture with agent-browser:
agent-browser open http://localhost:8000
agent-browser snapshot -i
agent-browser screenshot generated_images/pr-{pr_number}/01_main_view.png
```

**Screenshot naming convention:**
- `01_main_view.png` - Primary affected view
- `02_feature_demo.png` - New feature in action
- `03_edge_case.png` - Edge case or error state

### 4. Plan Validation (if plan exists)

If a plan document was found in step 1:

For each requirement/acceptance criterion in the plan:
1. Locate the corresponding implementation in the PR diff
2. Verify behavior matches the plan specification
3. Check that edge cases mentioned in the plan are handled
4. Verify any "No-Gos" from the plan are respected

### 5. Issue Identification & Classification

**Severity Guidelines:**

- **blocker**: Must fix before merge
  - Breaks core functionality
  - Security vulnerability
  - Data loss risk
  - Missing tests for critical paths
  - Crashes or severe errors

- **tech_debt**: Should fix but doesn't block merge
  - Code quality issues
  - Missing tests for edge cases
  - Performance improvements
  - Refactoring opportunities

- **nit**: Nice to have, non-critical
  - Style/formatting
  - Minor naming improvements
  - Documentation wording
  - Future enhancements

**For each issue found:**
1. Reference the file and line number
2. Write clear description of the problem
3. Suggest a fix
4. Classify severity

### 6. Post Review

**If blockers found:**
```bash
gh pr review {pr_number} --request-changes --body "$(cat <<'EOF'
## Review: Changes Requested

[summary of blockers]

### Blockers
- [ ] [blocker 1 with file:line reference]
- [ ] [blocker 2 with file:line reference]

### Tech Debt (non-blocking)
- [tech debt items]

### Screenshots
[screenshot references if captured]
EOF
)"
```

**If no blockers:**
```bash
gh pr review {pr_number} --approve --body "$(cat <<'EOF'
## Review: Approved

[summary of review]

### Verified
- [x] Code correctness
- [x] Test coverage
- [x] Security (no vulnerabilities found)
- [x] Plan requirements met

### Tech Debt (optional follow-ups)
- [any non-blocking items]

### Screenshots
[screenshot references if captured]
EOF
)"
```

### 7. Output Summary

**Present review summary:**

```
Review Complete: PR #{pr_number}

Branch: {head_branch} -> {base_branch}
Plan: {plan_file or "none"}

Result: {Approved | Changes Requested}

Issues Found: {total}
  - Blockers: {count}
  - Tech Debt: {count}
  - Nits: {count}

Screenshots: {count} captured
  -> generated_images/pr-{pr_number}/
```

## Integration Notes

**Works with:**
- `/do-build` - Reviews PRs created by the build workflow
- `/prepare_app` - Ensures app is running before screenshots
- `agent-browser` - Handles browser automation and screenshot capture
- `gh` CLI - Fetches PR data and posts reviews

**Screenshot storage:**
- Saved to `generated_images/pr-{pr_number}/` directory
- Auto-detected and sent via Telegram bridge
- Bridge uses RELATIVE_PATH_PATTERN to auto-detect generated_images/ files

## Example Usage

```bash
# Review PR by number
/do-pr-review 42
/do-pr-review #42
```

## Best Practices

1. **Always read the plan first**: The plan is the source of truth for what should have been built
2. **Focus on correctness over style**: Don't nitpick formatting if the code works
3. **Be specific in issue descriptions**: Include file paths and line numbers
4. **Classify severity honestly**: Don't mark blockers as tech debt to speed up merge
5. **Capture key UI paths**: 1-3 screenshots typical, focus on changed functionality
