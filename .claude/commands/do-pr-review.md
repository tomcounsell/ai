---
description: "Review PRs by validating implementations against specifications and capturing visual proof via screenshots using agent-browser"
argument-hint: <PR-number or workflow_id>
---

# PR Review

Validate implementations against specifications and capture visual proof via screenshots.

## When to Use

- After completing feature implementation
- Before creating a pull request
- When validating UI changes visually
- To generate structured review reports with issue severity classification

## Variables

- `workflow_id` (optional): Unique identifier for this review session (defaults to branch name or timestamp)
- `spec_file` (optional): Path to specification file (auto-detected from specs/*.md if not provided)

## Instructions

Follow this review process to validate work against specifications:

### 1. Context Gathering

**Check current git branch:**
```bash
git branch --show-current
```

**Get changes since main:**
```bash
git diff origin/main --stat
git diff origin/main
```

**Identify workflow ID:**
- If provided as argument, use it
- Otherwise, derive from branch name (e.g., `feature/review-workflow` → `review-workflow`)
- Fallback to timestamp if on main: `review-$(date +%Y%m%d-%H%M%S)`

### 2. Spec File Discovery

**Find matching spec file:**
```bash
# Look for spec files matching branch/workflow name
ls specs/*.md 2>/dev/null | grep -i "{workflow_id}" | head -1

# If not found, list all specs for user to choose
ls specs/*.md 2>/dev/null
```

If no specs exist, ask user for requirements/acceptance criteria.

**Read spec requirements:**
- Parse the spec file
- Extract acceptance criteria, requirements, and expected behavior
- Note any UI-specific requirements

### 3. Screenshot Capture (if UI changes detected)

**Prepare screenshot directory:**
```bash
mkdir -p generated_images/{workflow_id}
```

**Determine if screenshots needed:**
- Check git diff for UI-related files: `*.html`, `*.jsx`, `*.tsx`, `*.vue`, `*.css`, `*.scss`
- Check spec for UI requirements
- Ask user if uncertain

**If screenshots needed:**

1. Use `/prepare_app` command to ensure app is running
2. Use `agent-browser` to navigate and capture:

```bash
# Open the application
agent-browser open http://localhost:8000

# Get interactive snapshot
agent-browser snapshot -i

# Navigate critical paths and capture screenshots
# Name format: {workflow_id}_{nn}_{descriptive_name}.png
agent-browser screenshot generated_images/{workflow_id}/01_main_view.png

# Continue for each critical UI path (1-5 screenshots typical)
```

**Screenshot naming convention:**
- `01_main_dashboard.png` - Primary view
- `02_feature_in_action.png` - Core functionality
- `03_edge_case.png` - Edge case or error state
- `04_responsive_mobile.png` - Mobile view if applicable
- `05_final_state.png` - End state after user flow

### 4. Implementation Analysis

**Compare implementation vs spec:**

For each requirement in the spec:
1. Locate corresponding implementation in code
2. Verify behavior matches specification
3. Check for edge cases handled
4. Validate error handling
5. Review code quality and patterns

**Check for:**
- ✅ All acceptance criteria met
- ✅ Edge cases handled
- ✅ Error states implemented
- ✅ Tests written (if spec requires)
- ✅ Documentation updated
- ✅ No regressions introduced

### 5. Issue Identification & Classification

**Severity Guidelines:**

- **blocker**: Must fix before release
  - Breaks core functionality
  - Security vulnerability
  - Data loss risk
  - Prevents users from completing critical tasks
  - Crashes or severe errors

- **tech_debt**: Should fix but doesn't block release
  - Code quality issues
  - Missing tests
  - Performance improvements
  - Refactoring opportunities
  - Minor bugs in edge cases

- **skippable**: Nice to have, non-critical
  - UI polish
  - Minor text changes
  - Non-essential features
  - Future enhancements
  - Cosmetic improvements

**For each issue found:**
1. Assign issue number (sequential: 1, 2, 3...)
2. Write clear description
3. Suggest resolution steps
4. Link to screenshot if visual issue
5. Classify severity

### 6. Generate Review Report

**Create report.json:**
```json
{
  "workflow_id": "review-workflow",
  "spec_file": "specs/review-workflow.md",
  "branch": "feature/review-workflow",
  "timestamp": "2026-02-04T15:00:00Z",
  "success": true,
  "review_summary": "Implementation meets all core requirements. Minor tech debt identified around error handling. UI screenshots captured showing proper functionality across critical paths.",
  "review_issues": [
    {
      "issue_number": 1,
      "screenshot_path": "generated_images/review-workflow/02_error_state.png",
      "description": "Error message not displayed when user submits invalid input",
      "resolution": "Add error state rendering in FormComponent.tsx line 45",
      "severity": "blocker"
    },
    {
      "issue_number": 2,
      "screenshot_path": null,
      "description": "Missing unit tests for validation logic",
      "resolution": "Add tests for validateInput() function",
      "severity": "tech_debt"
    }
  ],
  "screenshots": [
    "generated_images/review-workflow/01_main_dashboard.png",
    "generated_images/review-workflow/02_error_state.png",
    "generated_images/review-workflow/03_success_flow.png"
  ],
  "metrics": {
    "total_issues": 2,
    "blockers": 1,
    "tech_debt": 1,
    "skippable": 0,
    "screenshots_captured": 3,
    "acceptance_criteria_met": "4/5"
  }
}
```

**Save report:**
```bash
# Save to agents/{workflow_id}/review/report.json
cat > agents/{workflow_id}/review/report.json << 'EOF'
{...json content...}
EOF
```

### 7. Output Summary

**Present review summary to user:**

```
🔍 Review Complete: {workflow_id}

📋 Spec: {spec_file}
🌿 Branch: {branch_name}

✅ Success: {success}

📊 Summary:
{review_summary}

🚨 Issues Found: {total_issues}
  - Blockers: {blockers}
  - Tech Debt: {tech_debt}
  - Skippable: {skippable}

📸 Screenshots: {screenshot_count} captured
  → generated_images/{workflow_id}/

📝 Full report: agents/{workflow_id}/review/report.json
```

**If blockers found:**
- List each blocker with issue number and description
- Recommend fixing before merge

**If only tech_debt/skippable:**
- Note that work can proceed
- Create tech debt issues if needed

## Integration Notes

**Works with:**
- `/prepare_app` - Ensures app is running before screenshots
- `agent-browser` - Handles all browser automation and screenshot capture
- Git workflow - Uses branch context for workflow identification
- Spec files - Located in `specs/*.md`

**Screenshot storage:**
- Saved to `generated_images/{workflow_id}/` directory
- Automatically detected and sent via Telegram bridge (same as valor-image-gen)
- Organized by workflow ID for easy tracking
- Bridge uses RELATIVE_PATH_PATTERN to auto-detect generated_images/ files

**Report schema:**
- Machine-readable JSON
- Human-readable summary
- Severity-classified issues
- Screenshot references

## Example Usage

```bash
# Auto-detect workflow from branch
/do-pr-review

# Specify workflow ID
/do-pr-review my-feature

# Specify both workflow and spec
/do-pr-review my-feature specs/my-feature.md
```

## Output Artifacts

1. **Review report**: `agents/{workflow_id}/review/report.json`
2. **Screenshots**: `generated_images/{workflow_id}/*.png`
   - Auto-sent via Telegram when paths mentioned in response
   - Same detection pattern as valor-image-gen output
3. **Console summary**: Immediate feedback on review status

## Best Practices

1. **Run review before creating PR**: Catch issues early
2. **Capture key UI paths**: Focus on critical user flows, not every pixel
3. **Be specific in issue descriptions**: Make resolution clear
4. **Classify severity honestly**: Don't downgrade blockers to ship faster
5. **Keep screenshots focused**: 1-5 screenshots typical, avoid excessive captures
6. **Document edge cases**: If spec doesn't cover it, note in tech_debt

## Notes

- App must be running for UI screenshot capture
- Spec files are optional but recommended for thorough review
- Screenshots stored locally; future enhancement for cloud upload
- Review reports are versioned by timestamp for historical tracking
