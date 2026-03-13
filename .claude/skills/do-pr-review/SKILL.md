---
name: do-pr-review
description: "Use when reviewing a pull request. Analyzes code changes, validates against plan requirements, and captures visual proof via screenshots. Triggered by 'review this PR', 'check the pull request', 'do a PR review', or a PR URL."
context: fork
---

# PR Review

Review a pull request by analyzing its changes against the plan, checking code quality, validating tests, and capturing screenshots of UI changes.

## Cross-Repo Resolution

For cross-project work, the `GH_REPO` environment variable is automatically set by `sdk_client.py`. The `gh` CLI natively respects this env var, so all `gh` commands automatically target the correct repository. No `--repo` flags or manual parsing needed.

## When to Use

- After `/do-build` creates a PR
- When a PR needs thorough review before merge
- To validate UI changes visually with screenshots
- To generate structured review reports with issue severity classification

## Variables

- `pr_number` (required): The PR number to review (e.g., `42` or `#42`)

## Session Progress Tracking

Extract the session ID from the conversation context. The bridge injects `SESSION_ID: {id}` into enriched messages. Look for this pattern and store it:

```bash
# Extract SESSION_ID from context
# Look for a line like "SESSION_ID: abc123" in the message you received
# Store in variable: SESSION_ID="abc123"

# Mark REVIEW stage as in_progress at the start
python -m tools.session_progress --session-id "$SESSION_ID" --stage REVIEW --status in_progress 2>/dev/null || true
```

After posting the review (Step 6):

```bash
# On approval (no blockers):
python -m tools.session_progress --session-id "$SESSION_ID" --stage REVIEW --status completed 2>/dev/null || true

# Note: If blockers found, leave as in_progress - the SDLC dispatcher will invoke /do-patch
# and then re-run review, which will complete the stage after fixes
```

## Goal Alignment

Every PR review must be grounded in the original intent. Before reviewing code, find and read the plan and tracking issue to understand *what was supposed to be built and why*.

**How to get plan context** (in priority order):
1. Check PR body for `Closes #N` — fetch the issue via `gh issue view N`
2. Extract slug from branch name (e.g., `session/{slug}`) and read `docs/plans/{slug}.md`
3. Look for plan docs in `docs/plans/` that reference the issue number
4. If no plan exists (e.g., hotfix), review against the PR description alone

When plan context is available, the review should validate that implementation matches the plan's:
- Acceptance criteria and success conditions
- No-Gos (things explicitly excluded)
- Architectural decisions and patterns

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

**Find and read the associated plan and issue:**
- Check PR body for `Closes #N` — run `gh issue view N` to get the tracking issue context
- Extract slug from the head branch name and read `docs/plans/{slug}.md`
- The plan contains acceptance criteria, no-gos, and requirements to validate against
- Keep the plan summary in mind throughout the entire review

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

# Ensure clean git state before switching branches (aborts in-progress merges/rebases)
python -c "from agent.worktree_manager import ensure_clean_git_state; from pathlib import Path; ensure_clean_git_state(Path('.'))"

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
5. If the plan has an Agent Integration section, verify integration points exist in the codebase (e.g., grep for expected tool calls, imports, or MCP references)

### 4.5. Verification Checks (if plan has ## Verification table)

If the plan document has a `## Verification` section with a machine-readable table, run each check automatically on the PR branch:

```bash
python -c "
from agent.verification_parser import parse_verification_table, run_checks, format_results
from pathlib import Path
plan = Path('{PLAN_PATH}').read_text()
checks = parse_verification_table(plan)
if checks:
    results = run_checks(checks)
    print(format_results(results))
else:
    print('No verification table in plan.')
"
```

Include the verification results in the review comment under a "Verification Results" section. If any check fails, classify it as a **blocker**.

### 5. Issue Identification & Classification

**Severity Guidelines:**

- **blocker**: Must fix before merge
  - Breaks core functionality
  - Security vulnerability
  - Data loss risk
  - Missing tests for critical paths
  - Crashes or severe errors

- **tech_debt**: Fix before merge (patched automatically by `/do-patch`)
  - Code quality issues
  - Missing tests for edge cases
  - Performance improvements
  - Refactoring opportunities
  - These are NOT optional — the SDLC pipeline will invoke `/do-patch` to fix them

- **nit**: Fix before merge unless purely subjective (patched automatically by `/do-patch`)
  - Style/formatting
  - Minor naming improvements
  - Documentation wording
  - Future enhancements
  - Only skip nits that are genuinely subjective (e.g., naming preference) — requires human approval

**For each issue found, use this format:**

```
**File:** `path/to/file.py:42` (verified: read this file)
**Code:** `the_actual_code_on_that_line()`
**Issue:** [clear description of the problem]
**Severity:** blocker | tech_debt | nit
**Fix:** [suggested fix]
```

The `Code:` field MUST be a verbatim quote from the file, not paraphrased. The `File:` path MUST be a file you read with the Read tool during this review. If you cannot produce both of these, do not include the finding.

### 5.5. Verify Findings (mandatory)

Before posting, verify every blocker and tech_debt finding:

1. **Confirm the file exists** — you must have read it with the Read tool during this review
2. **Confirm the code exists** — the function, class, or pattern you're citing must appear in the file at or near the line you reference
3. **Confirm the behavior** — re-read the relevant code to make sure your description of the problem is accurate

**If a finding fails verification** (file doesn't exist, function not found, behavior described doesn't match actual code):
- **Drop it entirely.** Do not include unverified findings in the review.
- A false blocker is worse than a missed real issue — it wastes time and erodes trust.

This step exists because of issue #181: a prior review hallucinated two "blocker" findings citing functions and files that did not exist.

### 6. Post Review

**First, detect if this is a self-authored PR:**
```bash
PR_AUTHOR=$(gh pr view {pr_number} --json author --jq .author.login)
CURRENT_USER=$(gh api user --jq .login)
SELF_AUTHORED=$( [ "$PR_AUTHOR" = "$CURRENT_USER" ] && echo "true" || echo "false" )
```

Self-authored PRs cannot use `gh pr review --approve` or `--request-changes` (GitHub rejects these). Use `gh pr comment` as fallback.

**If blockers found:**
```bash
REVIEW_BODY="$(cat <<'EOF'
## Review: Changes Requested

[summary of blockers]

### Blockers
- [ ] **`file.py:42`** — `actual_code()` — [description of issue]
- [ ] **`file.py:87`** — `actual_code()` — [description of issue]

### Tech Debt (non-blocking)
- **`file.py:15`** — `code()` — [description]

### Screenshots
[screenshot references if captured]
EOF
)"

if [ "$SELF_AUTHORED" = "true" ]; then
  gh pr comment {pr_number} --body "$REVIEW_BODY"
else
  gh pr review {pr_number} --request-changes --body "$REVIEW_BODY"
fi
```

**If no blockers:**
```bash
REVIEW_BODY="$(cat <<'EOF'
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

if [ "$SELF_AUTHORED" = "true" ]; then
  gh pr comment {pr_number} --body "$REVIEW_BODY"
else
  gh pr review {pr_number} --approve --body "$REVIEW_BODY"
fi
```

### 6.5. Verify Review Was Posted

**Always verify the review or comment exists after posting:**
```bash
# Check for formal reviews
REVIEW_COUNT=$(gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews --jq length)

# Check for comments (used for self-authored PRs)
COMMENT_COUNT=$(gh api repos/{owner}/{repo}/issues/{pr_number}/comments --jq '[.[] | select(.body | startswith("## Review:"))] | length')

if [ "$REVIEW_COUNT" -eq 0 ] && [ "$COMMENT_COUNT" -eq 0 ]; then
  echo "WARNING: Review was not posted. Retrying as comment..."
  gh pr comment {pr_number} --body "$REVIEW_BODY"
fi
```

**After verification, fetch the review URL:**
```bash
# Try formal review URL first
REVIEW_URL=$(gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews --jq '.[-1].html_url // empty')

# Fall back to comment URL
if [ -z "$REVIEW_URL" ]; then
  REVIEW_URL=$(gh api repos/{owner}/{repo}/issues/{pr_number}/comments --jq '.[-1].html_url // empty')
fi
```
Save this URL as `{review_url}` for the output summary.

### 7. Output Summary

**Present review summary:**

| | |
|---|---|
| **Branch** | `{head_branch}` -> `{base_branch}` |
| **Plan** | `{plan_file}` or "none" |
| **Result** | {Approved \| Changes Requested} |
| **Review** | [{review_url}]({review_url}) |

**Issues Found: {total}**
- **Blockers: {count}**
- **Tech Debt: {count}**
- **Nits: {count}**

**Screenshots: {count}** captured -> `generated_images/pr-{pr_number}/`

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

## Hard Rules

1. **Reviews MUST be posted on GitHub.** A review that only exists in agent output is NOT a review. Use `gh pr review` to post, or `gh pr comment` for self-authored PRs. Step 6.5 verifies posting succeeded. The SDLC dispatcher checks for both reviews and comments before advancing.
2. **Tech debt and nits get patched.** After review, `/do-patch` fixes all tech debt and non-subjective nits before proceeding to docs/merge. Only purely subjective nits may be skipped — and that requires human approval.
3. **Never approve and skip issues.** If you found tech debt or nits, they appear in the review body. The pipeline will patch them. Don't omit findings to make the review look clean.

## Best Practices

1. **Always read the plan first**: The plan is the source of truth for what should have been built
2. **Focus on correctness over style**: Don't nitpick formatting if the code works
3. **Quote actual code in every finding**: Include the verbatim code snippet, not a paraphrase — this makes hallucinated findings self-evident
4. **Verify before posting**: Every blocker must cite a file you read and code you saw (Step 5.5)
5. **Classify severity honestly**: Don't mark blockers as tech debt to speed up merge
6. **Capture key UI paths**: 1-3 screenshots typical, focus on changed functionality
