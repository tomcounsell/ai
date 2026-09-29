# Sub-Skill: Post Review

Format the review body and post it to GitHub. Input: the code-review artifacts,
or a preflight short-circuit verdict (see the short-circuit sections below).
`PR_NUMBER` and `ISSUE_NUMBER` come from `checkout.md`.

## Steps

### 0. Identity Setup

**Generic default:** post under the operator's `gh` credential: set
`GH_TOKEN_FOR_REVIEW=""` and call `gh` directly.

If the repo-context file declares a bot/service-account review identity, resolve
its token here, before any post, and follow its rules:
- With a bot token resolved, wrap ONLY the review-post `gh` calls in
  `env GH_TOKEN="$GH_TOKEN_FOR_REVIEW" gh ...`, and put the context file's
  review marker on the first line of every code-review body. Never pass an empty
  `GH_TOKEN` to `gh` (it corrupts the stored credential).
- Omit the marker on the `BLOCKED_ON_CONFLICT` / `PR_CLOSED` comments; they are
  informational, not code-review verdicts.

### 1. Detect Self-Authored PR

```bash
PR_AUTHOR=$(gh pr view "$PR_NUMBER" --json author --jq .author.login)
CURRENT_USER=$(gh api user --jq .login)
SELF_AUTHORED=$( [ "$PR_AUTHOR" = "$CURRENT_USER" ] && echo "true" || echo "false" )
```

GitHub refuses `--approve` and `--request-changes` on your own PR; use
`gh pr comment` instead.

### 2. Format Review Body

Heading by verdict: `## Review: Changes Requested` (blockers),
`## Review: Changes Requested — Tech Debt` (no blockers, but tech_debt or nits),
or `## Review: Approved` (zero findings, empty Miscellaneous). Then:

```
[one-paragraph summary]

## Rubric
[10-item Rubric from code-review.md]

### Blockers
- [ ] **`file.py:42`** — `actual_code()` — [description]   (or "- None")

### Tech Debt
- [ ] **`file.py:15`** — `code()` — [description]           (or "- None")

### Nits
- None

### Miscellaneous
- None

### Acknowledged Deferrals (verified)
- **[disclosure text]** — tracked by #N (OPEN)             (or "- None")

### Review Delta (vs prior review on HEAD {prior_sha})
[only when a prior review existed on a different HEAD SHA or body hash]

### Legacy Cruft
[advisory cruft-audit results]

### Verification Results
[verification-table output, if any]

### Screenshots
[screenshot references and what each shows, if captured]

<!-- REVIEW_CONTEXT head_sha=<HEAD_SHA> pr_body_hash=<PR_BODY_HASH> -->
```

**Idempotent replay** (same HEAD SHA and PR body hash as the prior review):

```
## Review: [prior verdict]

_Idempotent: prior review on HEAD {head_sha:0:7} / body hash {body_hash:0:7} is still valid — returning prior verdict without regenerating findings._

<!-- REVIEW_CONTEXT head_sha=<HEAD_SHA> pr_body_hash=<PR_BODY_HASH> -->
```

### 2.5. Plan Checkbox Sync (APPROVED verdicts only)

On an `APPROVED` verdict, sync the plan file's criteria checkboxes to the
rubric's per-criterion results BEFORE posting the review:

| Rubric value | Plan-file action |
|--------------|------------------|
| `pass`         | tick `[x]` |
| `fail`         | untick `[ ]` |
| `acknowledged` | untick `[ ]` (deferral verified, criterion still unmet) |
| `n/a`          | no write |

A criterion both covered by a disclosure and demonstrably satisfied by the diff
is `pass` (see code-review.md section 4).

This step does not fire on `CHANGES_REQUESTED`, `BLOCKED_ON_CONFLICT`,
`PR_CLOSED`, or any other non-APPROVED verdict.

If the context file declares a plan-checkbox updater, run its exact invocation
per criterion and honor its exit codes; otherwise make each tick or untick as a
surgical edit to the plan file. Match failures are soft: when a criterion
matches zero or several plan items, keep the existing state and add
`> Could not auto-sync "{criterion}" — please review manually.` to the review
body; a plan with no criteria section gets a one-line warning and is skipped.

On any real change, commit and push BEFORE posting:

```bash
BRANCH=$(git branch --show-current)
git add "$PLAN_PATH"
git commit -m "docs(#${ISSUE_NUMBER}): sync plan checkboxes with review verdict"
if ! git push origin "HEAD:${BRANCH}"; then
  echo "ERROR: failed to push tick commit; aborting review post" >&2
  cat <<'OUTCOME'
<!-- OUTCOME {"status":"fail","stage":"REVIEW","verdict":"PUSH_FAILED","artifacts":{},"notes":"tick commit failed to push; review not posted","next_skill":"/do-patch"} -->
OUTCOME
  exit 1
fi
```

The order is an invariant: a merge gate that judges review freshness against
the latest commit treats a review posted before the tick commit as stale. A
failed push is fatal because the approval would point at a commit that exists
only locally.

### 2b. Preflight Short-Circuit: BLOCKED_ON_CONFLICT

No diff reading, no code review, no approval. Post this comment, citing the
observed values, then emit the OUTCOME block:

```
## Review: Blocked on Conflict

This PR cannot be reviewed until it merges cleanly against its base branch.

- **mergeable:** `{PR_MERGEABLE}`
- **mergeStateStatus:** `{PR_MERGE_STATUS}`

### Required action

Rebase onto the current base (or merge the base into your branch), resolve the
conflicts, push, and re-run `/do-pr-review`.

> No code review was performed: the mergeability gate runs before any diff reading.
```

```bash
gh pr comment "$PR_NUMBER" --body "$REVIEW_BODY"
```

### 2c. Preflight Short-Circuit: PR_CLOSED

```
## Review: PR Closed

This PR is no longer open (`state={PR_STATE}`); review skipped.

If the PR was closed in error, reopen it and re-run `/do-pr-review`. If it was
already merged, no review is needed.
```

```bash
gh pr comment "$PR_NUMBER" --body "$REVIEW_BODY"
```

`gh pr review` requires an open, reviewable PR, so both short-circuit paths
always use `gh pr comment`.

### 3. Post the Review

This section is the single source of truth for the post decision. First match
wins:

1. Preflight `PR_CLOSED` → §2c comment via `gh pr comment`. Never `gh pr review`.
2. Preflight `BLOCKED_ON_CONFLICT` (including `UNKNOWN` after retry) → §2b comment via `gh pr comment`. Never `gh pr review`.
3. Self-authored → `gh pr comment`.
4. Any blocker, tech_debt, or nit → `--request-changes`.
5. Zero findings → `--approve`, the only path to approval.

```bash
_gh_post() {
  if [ -n "$GH_TOKEN_FOR_REVIEW" ]; then
    env GH_TOKEN="$GH_TOKEN_FOR_REVIEW" gh "$@"
  else
    gh "$@"
  fi
}

if [ "$PREFLIGHT_VERDICT" = "PR_CLOSED" ] || [ "$PREFLIGHT_VERDICT" = "BLOCKED_ON_CONFLICT" ]; then
  _gh_post pr comment "$PR_NUMBER" --body "$REVIEW_BODY"
elif [ "$SELF_AUTHORED" = "true" ]; then
  _gh_post pr comment "$PR_NUMBER" --body "$REVIEW_BODY"
elif [ "$HAS_ANY_FINDINGS" = "true" ]; then
  _gh_post pr review "$PR_NUMBER" --request-changes --body "$REVIEW_BODY"
else
  _gh_post pr review "$PR_NUMBER" --approve --body "$REVIEW_BODY"
fi
```

### 4. Verify the Post and Capture the URL

Confirm the post command exited 0 and that the newest formal review or newest
`## Review:` comment on the PR is the one you just posted:

```bash
# --paginate: the API returns 30 items per page, so `.[-1]` alone is not the newest on a busy PR
gh api --paginate "repos/{owner}/{repo}/pulls/$PR_NUMBER/reviews" --jq '.[] | {submitted_at, html_url}' | tail -1
gh api --paginate "repos/{owner}/{repo}/issues/$PR_NUMBER/comments" \
  --jq '.[] | select(.body | startswith("## Review:")) | {created_at, html_url}' | tail -1
```

If neither is yours, retry once as `gh pr comment`. Use its `html_url` as
`{review_url}`.

### 5. Mark Session Progress (only if the context file declares a substrate)

The REVIEW completion marker is written together with the verdict record (parent
SKILL.md Step 5). With no substrate, the posted review is the completion signal.

## Completion

Return `{review_url}` and the verdict for the OUTCOME block
(`outcome-contract.md`). `BLOCKED_ON_CONFLICT` and `PR_CLOSED` use
`next_skill: null` so the pipeline does not auto-advance.
