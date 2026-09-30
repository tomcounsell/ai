# Sub-Skill: Post Review

Mechanical work: format findings and post the review to GitHub.

## Context Variables

- `$SDLC_PR_NUMBER` — PR number to post review on
- `$SDLC_REPO` — repo in org/name format (fallback: `$GH_REPO`)

## Prerequisites

Code review findings must be available from the code-review sub-skill — UNLESS
the mergeability preflight short-circuited (see §2b and §2c below).

## Steps

### 0. Identity Setup

**Generic default:** post under the operator's `gh` credential — set
`GH_TOKEN_FOR_REVIEW=""` and call `gh` directly.

If the repo-context file declares a bot/service-account review identity, resolve
its token here (BEFORE any `gh pr review`/`gh pr comment` call) and follow its
rules:
- When a bot token is resolved: wrap ONLY the review-post `gh` calls in
  `env GH_TOKEN="$GH_TOKEN_FOR_REVIEW" gh ...`, and prepend the context file's
  review marker as the first line of every code-review body. Never pass an empty
  `GH_TOKEN` to `gh` (it corrupts the stored credential).
- The marker is omitted for `BLOCKED_ON_CONFLICT` / `PR_CLOSED` comment-only
  paths (informational, not code-review verdicts).

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

### 2.5. Plan Checkbox Sync (APPROVED verdicts only)

When the verdict is `APPROVED` (zero findings), sync the plan-file's criteria
checkboxes to match the rubric's per-criterion verdicts BEFORE posting the
review. The four-value rubric contract maps to plan-file writes as:

| Rubric value | Plan-file action |
|--------------|------------------|
| `pass`         | `tick [x]` (criterion satisfied by diff) |
| `fail`         | `untick [ ]` (closes the dishonest-tick loophole) |
| `acknowledged` | `untick [ ]` (verified deferral exists, but criterion is still unmet) |
| `n/a`          | no plan write (existing checkbox state preserved) |

**Special case (disclosure-vs-pass override, see code-review.md Step 4):** If a
criterion is BOTH covered by a verified disclosure AND demonstrably satisfied
by the diff, the rubric MUST emit `pass`, not `acknowledged`. The disclosure is
informational only — the plan write reflects the `pass`.

**This step does NOT fire when** the verdict is any of:
`CHANGES_REQUESTED`, `BLOCKED_ON_CONFLICT`, `PR_CLOSED`, or any other
non-APPROVED state. The `Tier 2 (Tech Debt)` and `BLOCKER` paths produce no
plan-file writes; this step is silent on those paths.

**Procedure (commit-then-post-review ordering — non-negotiable):**

```bash
SLUG=$(echo "$BRANCH" | sed 's|^session/||')
PLAN_PATH="docs/plans/${SLUG}.md"
PLAN_MUTATED=false

# For each criterion the rubric judged in Pre-Verdict item 1, sync its plan
# checkbox per the table above (pass => tick, fail/acknowledged => untick,
# n/a => skip).
#
# If the context file declares a plan-checkbox updater, run its exact
# invocation per criterion and honor its exit-code semantics. Generic default:
# make the tick/untick as a surgical text edit to $PLAN_PATH.
#
# Set PLAN_MUTATED=true on any real checkbox change. Match failures are soft:
#   - Criterion matches zero or 2+ plan items => preserve the existing checkbox
#     state AND append a manual-review comment to the review body:
#       > Could not auto-sync "{criterion}" — please review manually.
#   - Plan has no criteria section => log a one-line warning and skip; some
#     chore plans legitimately omit the section.

if [ "$PLAN_MUTATED" = "true" ]; then
  git add "$PLAN_PATH"
  git commit -m "docs(#${SDLC_ISSUE_NUMBER}): sync plan checkboxes with review verdict"
  if ! git push origin "HEAD:${BRANCH}"; then
    # Push failure (network / branch protection / conflict).
    # Abort posting the review — never approve without ticks pushed.
    echo "ERROR: failed to push tick commit; aborting review post" >&2
    cat <<'OUTCOME'
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"PUSH_FAILED","artifacts":{},"notes":"tick commit failed to push; review not posted","next_skill":"/do-patch"} -->
OUTCOME
    exit 1
  fi
fi
```

**Why commit-then-post-review (non-negotiable invariant):** `/do-merge`'s
review-comment freshness gate filters out reviews whose `created_at` is older than the latest commit's
`committer.date`. If the tick commit is pushed AFTER the review is posted, the
review immediately becomes stale and the next merge attempt forces a re-review
— which is exactly the oscillation symptom this work removes. Pushing the tick
commit FIRST guarantees the review's `created_at` is strictly newer than every
commit on the branch.

**Why match failure is non-fatal:** An ambiguous or missing match on a single
criterion is a soft signal. The review still gets posted with the
manual-review comment surfaced to the human reviewer; the existing plan-file
checkbox state is preserved. A push failure (after a successful tick commit)
IS fatal because we already created a commit that lives only in the local
repo — proceeding to post the approval would publish a review against a SHA
that doesn't exist on the remote, and the next merge attempt would either
deadlock or accept a review that points at the wrong commit.

### 2b. Preflight Short-Circuit: BLOCKED_ON_CONFLICT

When the mergeability preflight (`checkout.md` → "Mergeability Preflight")
detected `mergeable=CONFLICTING` or `mergeStateStatus=DIRTY`, the skill MUST
NOT read the diff, run code review, or post an approval. Instead, post this
comment and emit the terminal OUTCOME block. The comment MUST explicitly cite
the `mergeStateStatus` value so the author knows why review was skipped.

Template:
```
## Review: Blocked on Conflict

This PR cannot be reviewed until it merges cleanly against its base branch.

- **mergeable:** `{PR_MERGEABLE}` (e.g. `CONFLICTING`)
- **mergeStateStatus:** `{PR_MERGE_STATUS}` (e.g. `DIRTY`)

### Required action

Please rebase onto the current base (or merge base into your branch) and
resolve the conflicts, then push. Re-run `/do-pr-review` after the branch is
mergeable.

> No code review was performed. The mechanical mergeability gate runs before
> any diff reading — if the branch cannot merge, no amount of code review
> can make the PR mergeable.
```

**Posting (always `gh pr comment` — do NOT use `gh pr review --request-changes`
for the short-circuit path; the preflight is orthogonal to the code-review
verdict):**
```bash
gh pr comment "$PR_NUMBER" --body "$REVIEW_BODY"
```

### 2c. Preflight Short-Circuit: PR_CLOSED

When the mergeability preflight detected `state != OPEN` (CLOSED or MERGED),
post a short note and exit. No full review body, no code analysis.

Template:
```
## Review: PR Closed

This PR is no longer open (`state={PR_STATE}`); review skipped.

If the PR was closed in error, reopen it and re-run `/do-pr-review`. If it was
already merged, no review is needed.
```

**Posting:**
```bash
gh pr comment "$PR_NUMBER" --body "$REVIEW_BODY"
```

Note: `gh pr review` requires the PR to be open, so the short-circuit paths
always use `gh pr comment`, even on non-self-authored PRs.

### 3. Post the Review

**This section is the single source of truth for the review-post decision.**
The preflight short-circuit paths are checked FIRST — `gh pr review` is
unreachable when `PREFLIGHT_VERDICT` is `BLOCKED_ON_CONFLICT` or `PR_CLOSED`.

**Decision tree (apply in order, first match wins):**
1. **Preflight: `PR_CLOSED`** → post §2c comment via `gh pr comment`. NEVER call `gh pr review`.
2. **Preflight: `BLOCKED_ON_CONFLICT`** (includes `mergeable=UNKNOWN` after retry) → post §2b comment via `gh pr comment`. NEVER call `gh pr review`.
3. **Blockers found** → `--request-changes` (or `gh pr comment` for self-authored).
4. **No blockers, but tech_debt or nits** → `--request-changes` (or `gh pr comment`).
5. **Zero findings** → `--approve` (or `gh pr comment`).

```bash
# Helper: invoke gh with optional bot identity injection
# Usage: _gh_post <review|comment> [args...]
_gh_post() {
  if [ -n "$GH_TOKEN_FOR_REVIEW" ]; then
    env GH_TOKEN="$GH_TOKEN_FOR_REVIEW" gh "$@"
  else
    gh "$@"
  fi
}

if [ "$PREFLIGHT_VERDICT" = "PR_CLOSED" ] || [ "$PREFLIGHT_VERDICT" = "BLOCKED_ON_CONFLICT" ]; then
  # Preflight short-circuit — always post as a plain comment.
  # gh pr review is UNREACHABLE on this path.
  _gh_post pr comment "$PR_NUMBER" --body "$REVIEW_BODY"
elif [ "$SELF_AUTHORED" = "true" ]; then
  _gh_post pr comment "$PR_NUMBER" --body "$REVIEW_BODY"
elif [ "$HAS_ANY_FINDINGS" = "true" ]; then
  # Blockers, tech_debt, or nits — all require changes
  _gh_post pr review "$PR_NUMBER" --request-changes --body "$REVIEW_BODY"
else
  # Zero findings — the ONLY path to approval
  _gh_post pr review "$PR_NUMBER" --approve --body "$REVIEW_BODY"
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

### 6. Mark Session Progress (only if the context file declares a substrate)

On approval (no blockers), if the repo-context file declares a stage-marker
substrate, write the REVIEW completion marker (co-located with the verdict
record — see the parent SKILL.md "Record the verdict"). In the generic case,
skip — the posted GitHub review is the completion signal.

## Completion

Return the review URL and the outcome contract block. The verdict field
distinguishes between the normal code-review verdicts (`APPROVED`,
`CHANGES_REQUESTED`) and the preflight short-circuit verdicts
(`BLOCKED_ON_CONFLICT`, `PR_CLOSED`):

```
<!-- OUTCOME {"status":"success|partial|fail","stage":"REVIEW","verdict":"APPROVED|CHANGES_REQUESTED|BLOCKED_ON_CONFLICT|PR_CLOSED","artifacts":{"review_url":"...","blockers":N,"tech_debt":N,"nits":N},"notes":"...","next_skill":"/do-docs|/do-patch|null"} -->
```

See the SKILL.md "Outcome Contract" section for the full verdict taxonomy and
examples for each variant. For `BLOCKED_ON_CONFLICT` and `PR_CLOSED`, use
`next_skill: null` so the pipeline does not auto-advance.
