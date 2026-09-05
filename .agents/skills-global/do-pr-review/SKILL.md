---
name: do-pr-review
description: "Review a pull request against its intent and current diff, with verified actionable findings and an evidence-based verdict."
---

# Do Pr Review

Resolve the exact repo/PR and read its state, description, linked issue/plan, comments, prior reviews, and changed files. Use an isolated review worktree or read the git objects so the user's checkout is not disrupted. Read full relevant files and trace behavior beyond the diff.
Review correctness, regressions, missing integration, concurrency, security boundaries, error handling, maintainability, test adequacy, and documentation. Reproduce material claims where practical and distinguish bugs from preferences. Reuse valid prior evidence only when the head, body, and relevant external conditions are unchanged. Inspect UI changes in a browser when available and disclose missing visual verification.
Report findings first, ordered by consequence, with exact locations, triggering conditions, and concrete effects. No verified findings is a valid result. Keep an approval judgment separate from CI status and mergeability. Conflicts are a merge blocker, not proof of a code defect.
Return the review in this task. Post to GitHub only when requested or already authorized by the workflow; do not impersonate an independent reviewer or bypass self-review restrictions.
For an active Valor lane, read `docs/sdlc/do-pr-review.md`, resolve gating head SHAs through `tools/pr_head_resolver.py`, and use its atomic finalize/self-check contract. Tie any recorded approval to the exact current head and verified artifacts. A stale approval never clears a later patch.
