# Sub-Skill: Post Review

Mechanical work: format findings and post the review to GitHub.

## Context Variables

- `$SDLC_PR_NUMBER` — PR number to post review on
- `$SDLC_REPO` — repo in org/name format (fallback: `$GH_REPO`)

## Prerequisites

Code review findings must be available from the code-review sub-skill.

## Steps

### 1. Detect Self-Authored PR

```bash
PR_NUMBER="${SDLC_PR_NUMBER}"
PR_AUTHOR=$(gh pr view $PR_NUMBER --json author --jq .author.login)
CURRENT_USER=$(gh api user --jq .login)
SELF_AUTHORED=$( [ "$PR_AUTHOR" = "$CURRENT_USER" ] && echo "true" || echo "false" )
```

Self-authored PRs cannot use `gh pr review --approve` or `--request-changes`.
Use `gh pr comment` as fallback.

### 2. Format Review Body

**If blockers found:**
```
## Review: Changes Requested

[summary of blockers]

### Blockers
- [ ] **`file.py:42`** — `actual_code()` — [description]

### Tech Debt (non-blocking)
- **`file.py:15`** — `code()` — [description]

### Verification Results
[output from verification checks if available]

### Screenshots
[screenshot references if captured]
```

**If no blockers:**
```
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
```

### 3. Post the Review

```bash
if [ "$SELF_AUTHORED" = "true" ]; then
  gh pr comment $PR_NUMBER --body "$REVIEW_BODY"
else
  # Use --request-changes if blockers, --approve if clean
  gh pr review $PR_NUMBER --approve --body "$REVIEW_BODY"
fi
```

### 4. Verify Review Was Posted

```bash
REPO="${SDLC_REPO:-${GH_REPO:-}}"
REVIEW_COUNT=$(gh api repos/$REPO/pulls/$PR_NUMBER/reviews --jq length)
COMMENT_COUNT=$(gh api repos/$REPO/issues/$PR_NUMBER/comments --jq '[.[] | select(.body | startswith("## Review:"))] | length')

if [ "$REVIEW_COUNT" -eq 0 ] && [ "$COMMENT_COUNT" -eq 0 ]; then
  echo "WARNING: Review was not posted. Retrying as comment..."
  gh pr comment $PR_NUMBER --body "$REVIEW_BODY"
fi
```

### 5. Get Review URL

```bash
REVIEW_URL=$(gh api repos/$REPO/pulls/$PR_NUMBER/reviews --jq '.[-1].html_url // empty')
if [ -z "$REVIEW_URL" ]; then
  REVIEW_URL=$(gh api repos/$REPO/issues/$PR_NUMBER/comments --jq '.[-1].html_url // empty')
fi
```

### 6. Mark Session Progress

```bash
# On approval (no blockers):
python -m tools.session_progress --session-id "$SESSION_ID" --stage REVIEW --status completed 2>/dev/null || true
```

## Completion

Return the review URL and the outcome contract block:

```
<!-- OUTCOME {"status":"success|partial|fail","stage":"REVIEW","artifacts":{"review_url":"...","blockers":N,"tech_debt":N,"nits":N},"notes":"...","next_skill":"/do-docs|/do-patch"} -->
```
