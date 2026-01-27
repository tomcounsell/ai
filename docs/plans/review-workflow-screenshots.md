# Plan: Review Workflow with Screenshots

## Overview

Add a `/review` command that validates implementations against specifications and captures visual proof via screenshots.

## Source Inspiration

From `indydan/tac-6/.claude/commands/review.md` and the `adw_review.py` workflow.

## Problem Statement

Currently, Valor lacks:
- Formal review process for completed work
- Visual validation of UI changes
- Structured issue severity classification
- Screenshot capture for documentation

This means work gets shipped without visual verification and issues aren't categorized by impact.

## Proposed Solution

Create a `/review` command that:
1. Finds the spec file for current work
2. Compares implementation against requirements
3. Captures screenshots of critical UI paths
4. Categorizes any issues found
5. Outputs a structured review report

### Review Process Flow

```
1. Check git branch context
2. Find spec file (specs/*.md matching branch)
3. Read spec requirements
4. Run git diff to see changes
5. If UI changes: capture screenshots via agent-browser
6. Compare implementation vs spec
7. Categorize issues:
   - blocker: Must fix before release
   - tech_debt: Fix later, doesn't block release
   - skippable: Nice to have, non-critical
8. Output JSON review report
```

### New Files to Create

```
.claude/commands/
  review.md           # Review workflow command
  prepare_app.md      # App setup for review (start servers, etc.)

agents/
  {workflow_id}/
    review/
      review_img/     # Screenshots directory
      report.json     # Review output
```

### Review Command Template

```markdown
# Review

Follow instructions to review work against specification.

## Variables
workflow_id: $1
spec_file: $2

## Instructions

1. Check current git branch
2. Run `git diff origin/main` to see changes
3. Find spec file in specs/*.md
4. Read spec requirements
5. If UI work:
   - Navigate to application
   - Capture 1-5 screenshots of critical paths
   - Store in agents/{workflow_id}/review/review_img/
6. Compare implementation vs spec
7. Identify issues with severity

## Issue Severity Guidelines

- `blocker`: Prevents release, harms user experience
- `tech_debt`: Should fix but doesn't block release
- `skippable`: Non-critical improvement

## Output Format

{
  "success": boolean,
  "review_summary": "2-4 sentence summary",
  "review_issues": [
    {
      "issue_number": number,
      "screenshot_path": "/path/to/screenshot.png",
      "description": "issue description",
      "resolution": "how to fix",
      "severity": "blocker|tech_debt|skippable"
    }
  ],
  "screenshots": [
    "/path/to/screenshot1.png",
    "/path/to/screenshot2.png"
  ]
}
```

### Integration with agent-browser

Use existing `agent-browser` skill for screenshots:
- Navigate to URLs
- Capture full page or element screenshots
- Store with descriptive names: `01_login_form.png`, `02_dashboard.png`

### Screenshot Naming Convention

```
{nn}_{descriptive_name}.png

Examples:
01_main_dashboard.png
02_user_profile.png
03_error_state.png
```

## Implementation Steps

1. Create `.claude/commands/review.md` with review logic
2. Create `.claude/commands/prepare_app.md` for app setup
3. Add screenshot storage to `agents/{id}/review/review_img/`
4. Integrate with agent-browser for captures
5. Add review report schema
6. Update CLAUDE.md with review workflow documentation
7. Optional: Add cloud upload for screenshots (R2/S3)

## Benefits

- Visual proof that UI features work
- Structured issue tracking with severity
- Documentation-ready screenshots
- Clear blocker vs tech-debt distinction
- Better QA before shipping

## Estimated Effort

Medium - Requires agent-browser integration

## Dependencies

- agent-browser skill (already available)
- Spec files from planning phase (from issue-classification-commands plan)

## Risks

- Screenshots might not capture all states
- App must be running for UI validation
- Screenshot storage could grow large
- Headless browser limitations

## Future Enhancements

- Cloud upload to R2/S3 for screenshot sharing
- Automated visual regression comparison
- Integration with PR comments
- Screenshot diffing against baseline
