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

Every review body MUST include (in this order): the mechanical Rubric, the Pre-Verdict Checklist, all finding sections with empty-markers where applicable, the Acknowledged Deferrals (verified) section, the Miscellaneous bucket, the Review Delta (if a prior review existed on a different HEAD SHA), and the `<!-- REVIEW_CONTEXT ... -->` marker before the OUTCOME block. These are produced by the code-review sub-skill.

**If blockers found:**
```
## Review: Changes Requested

[summary of blockers]

## Rubric
[10-item Rubric with pass/fail/acknowledged/n/a per item]

## Pre-Verdict Checklist
[12-item Pre-Verdict Checklist]

### Blockers
- [ ] **`file.py:42`** — `actual_code()` — [description]

### Tech Debt
- **`file.py:15`** — `code()` — [description]

### Nits
- None

### Miscellaneous
- None

### Acknowledged Deferrals (verified)
- **[disclosure text]** — tracked by #N (OPEN)

### Review Delta (vs prior review on HEAD {prior_sha})
[optional — only if a prior review existed on a different HEAD SHA]

### Verification Results
[output from verification checks if available]

### Screenshots
[screenshot references if captured]

<!-- REVIEW_CONTEXT head_sha=<HEAD_SHA> pr_body_hash=<PR_BODY_HASH> -->
```

**If no blockers but has tech_debt or nits:**
```
## Review: Changes Requested — Tech Debt

[summary — no blockers, but outstanding tech debt/nits must be resolved before merge]

## Rubric
[10-item Rubric]

## Pre-Verdict Checklist
[12-item Pre-Verdict Checklist]

### Verified
- [x] Code correctness
- [x] Security (no vulnerabilities found)
- [x] Plan requirements met

### Blockers
- None

### Tech Debt
- [ ] **`file.py:15`** — `code()` — [description]

### Nits
- [ ] **`file.py:30`** — `code()` — [description]

### Miscellaneous
- None

### Acknowledged Deferrals (verified)
- None

### Screenshots
[screenshot references if captured]

<!-- REVIEW_CONTEXT head_sha=<HEAD_SHA> pr_body_hash=<PR_BODY_HASH> -->
```

**If zero findings (no blockers, no tech_debt, no nits, empty Miscellaneous):**
```
## Review: Approved

[summary of review]

## Rubric
[10-item Rubric — all pass/acknowledged/n/a]

## Pre-Verdict Checklist
[12-item Pre-Verdict Checklist]

### Verified
- [x] Code correctness
- [x] Test coverage
- [x] Security (no vulnerabilities found)
- [x] Plan requirements met

### Blockers
- None

### Tech Debt
- None

### Nits
- None

### Miscellaneous
- None

### Acknowledged Deferrals (verified)
- [bullets if any, else "- None"]

### Screenshots
[screenshot references if captured]

<!-- REVIEW_CONTEXT head_sha=<HEAD_SHA> pr_body_hash=<PR_BODY_HASH> -->
```

**Idempotent replay:** If the code-review sub-skill's Prior Review Context fired (same HEAD SHA, same PR body hash), emit the prior verdict unchanged with a short idempotency note:

```
## Review: [prior verdict]

_Idempotent: prior review on HEAD {head_sha:0:7} / body hash {body_hash:0:7} is still valid — returning prior verdict without regenerating findings._

<!-- REVIEW_CONTEXT head_sha=<HEAD_SHA> pr_body_hash=<PR_BODY_HASH> -->
```

### 3. Post the Review

**Three-tier decision (apply in order):**
1. **Blockers found** → `--request-changes`
2. **No blockers, but tech_debt or nits** → `--request-changes`
3. **Zero findings** → `--approve`

```bash
if [ "$SELF_AUTHORED" = "true" ]; then
  gh pr comment $PR_NUMBER --body "$REVIEW_BODY"
elif [ "$HAS_ANY_FINDINGS" = "true" ]; then
  # Blockers, tech_debt, or nits — all require changes
  gh pr review $PR_NUMBER --request-changes --body "$REVIEW_BODY"
else
  # Zero findings — the ONLY path to approval
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
python -m tools.sdlc_stage_marker --stage REVIEW --status completed --issue-number {issue_number} 2>/dev/null || true
```

## Completion

Return the review URL and the outcome contract block:

```
<!-- OUTCOME {"status":"success|partial|fail","stage":"REVIEW","artifacts":{"review_url":"...","blockers":N,"tech_debt":N,"nits":N},"notes":"...","next_skill":"/do-docs|/do-patch"} -->
```
