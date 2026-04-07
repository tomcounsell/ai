# Fix PR Review Skill Not Posting Reviews

**Issue:** #302
**Slug:** `fix_pr_review_posting`
**Branch:** `session/fix_pr_review_posting`

## Problem

When `/do-pr-review` is invoked, the forked sub-agent completes successfully but doesn't post a review or comment to the GitHub PR. The failure is silent — no error is surfaced.

Root causes identified:

1. **Self-authored PR rejection**: `gh pr review --approve` fails with "Cannot approve your own pull request" when the PR author matches the GitHub token owner. The error is not handled — the review body is lost.
2. **No fallback for approval failure**: The skill instructs agents to use `gh pr review --approve` but has no fallback path for self-authored PRs.
3. **No verification step**: The skill doesn't verify the review was actually posted before reporting success.

## Solution

### Change 1: Add self-authored PR detection and fallback to comment

In `SKILL.md` Step 6 ("Post Review"), add logic to handle the self-authored case:

- Before attempting `gh pr review --approve`, check if the PR author matches the current user
- If self-authored, use `gh pr comment` instead of `gh pr review --approve`
- For `--request-changes`, use `gh pr comment` as fallback too (same restriction applies)

### Change 2: Add post-review verification

After posting, verify the review/comment exists:

```bash
# Check for reviews
gh api repos/{owner}/{repo}/pulls/{pr_number}/reviews --jq length

# If 0 reviews, check for comments
gh api repos/{owner}/{repo}/issues/{pr_number}/comments --jq length
```

If neither exists, retry posting as a comment.

### Change 3: Update SDLC dispatcher verification

The SDLC dispatcher (Step 3, REVIEW stage) already checks review count. Update it to also accept PR comments as valid review evidence for self-authored PRs.

## Success Criteria

- [ ] Self-authored PRs get reviews posted as comments instead of failing silently
- [ ] Non-self-authored PRs still use `gh pr review` normally
- [ ] Post-review verification confirms the review was actually posted
- [ ] SDLC dispatcher accepts comments as valid review evidence
- [ ] Existing review workflow for non-self-authored PRs is unaffected

## No-Gos

- Do NOT change the review analysis logic (Step 2-5) — only the posting mechanism
- Do NOT remove `context: fork` — the fork is fine, the issue is in the skill instructions
- Do NOT add new dependencies or tools

## Documentation

- [ ] Update `docs/features/README.md` if a new feature doc is warranted (likely not — this is a bug fix)
- [ ] No new documentation files needed — the fix is self-contained in the skill

## Update System

No update system changes required — this is a skill-only change that propagates via hardlinks.

## Agent Integration

No agent integration changes required — the fix modifies skill instructions that the agent already follows. The `gh` CLI is already available.

## Implementation Notes

The fix is entirely within `.claude/skills/do-pr-review/SKILL.md` and `.claude/skills/sdlc/SKILL.md`. No Python code changes needed — this is a prompt/instruction fix.

Key `gh` commands to use:
- `gh api user --jq .login` — get current authenticated user
- `gh pr view {number} --json author --jq .author.login` — get PR author
- `gh pr comment {number} --body "..."` — post as comment (always works)
- `gh pr review {number} --approve --body "..."` — post as review (fails for self-authored)
