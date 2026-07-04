# Sub-Skill: PR Checkout

Mechanical setup: **context resolution**, **mergeability preflight**, then clean git state and checkout the PR branch.

## Context Variables

- `$SDLC_PR_NUMBER` — PR number to checkout (fallback: extract from nudge feedback or `gh pr list`)
- `$SDLC_PR_BRANCH` — expected branch name (informational)

## Context Resolution (runs first)

Resolve context variables — prefer env vars, fall back to manual resolution:

```bash
PR_NUMBER="${SDLC_PR_NUMBER:-$PR_NUMBER}"
REPO="${SDLC_REPO:-${GH_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}}"
PLAN_PATH="${SDLC_PLAN_PATH:-}"
SLUG="${SDLC_SLUG:-}"

# If PLAN_PATH not set, derive from slug or branch
if [ -z "$PLAN_PATH" ] && [ -n "$SLUG" ]; then
  PLAN_PATH="docs/plans/${SLUG}.md"
fi

# Resolve ISSUE_NUMBER — unconditional clobber (never ${ISSUE_NUMBER:-…}).
# IMPORTANT: $ARGUMENTS is the PR number for this skill, NOT the issue number.
# Do NOT use $ARGUMENTS as ISSUE_NUMBER. Do NOT use $SDLC_ISSUE_NUMBER as
# authoritative — a stale inherited env value is exactly the "latched onto
# wrong issue" mechanism this skill must guard against (#1731).
#
# Resolution order (first non-empty positive integer wins):
# 1. PR body extraction: Closes #N / Fixes #N / Resolves #N  (PRIMARY — always run)
# 2. PR body: tracking: https://.../issues/N  (secondary PR-body fallback)
# 3. $SDLC_ISSUE_NUMBER env var (LAST RESORT ONLY — guarded by positive-integer check)
PR_BODY=$(gh pr view "$PR_NUMBER" --json body -q '.body' 2>/dev/null)
ISSUE_NUMBER=$(echo "$PR_BODY" | grep -oiP '(?:closes|fixes|resolves)\s+#\K[0-9]+' | head -1)
if [ -z "$ISSUE_NUMBER" ]; then
  # Also try "tracking: https://.../issues/N" pattern
  ISSUE_NUMBER=$(echo "$PR_BODY" | grep -oP '(?<=issues/)[0-9]+' | head -1)
fi
if [ -z "$ISSUE_NUMBER" ] && [[ "$SDLC_ISSUE_NUMBER" =~ ^[0-9]+$ ]]; then
  ISSUE_NUMBER="$SDLC_ISSUE_NUMBER"
fi

# Assert ISSUE_NUMBER is a positive integer before any recorder call (#1731).
# An unresolvable issue number must fail loudly so the supervisor sees an
# actionable error rather than a silently diverted verdict on a wrong session.
[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || {
  echo "do-pr-review: could not resolve a positive-integer ISSUE_NUMBER from ARGUMENTS='${ARGUMENTS}', PR body, or SDLC_ISSUE_NUMBER. Pass the issue number as skill args or ensure the PR body contains 'Closes #N'." >&2
  exit 1
}
```

## Mergeability Preflight (runs BEFORE any diff reading)

**Why:** Three consecutive `/do-pr-review` passes once APPROVED PR #1100 while
`mergeable=CONFLICTING` and `mergeStateStatus=DIRTY`. No amount of subjective
code-review quality matters if the branch cannot mechanically merge. This
preflight is cheap (single `gh pr view` API call, ~200ms) and MUST run before
the checkout, diff, or any Read of PR files.

### Preflight Steps

1. **Resolve PR number first:**
   ```bash
   PR_NUMBER="${SDLC_PR_NUMBER:-}"
   if [ -z "$PR_NUMBER" ]; then
     echo "WARNING: SDLC_PR_NUMBER not set, attempting to detect from context"
     PR_NUMBER=$(gh pr list --state open --limit 1 --json number --jq '.[0].number')
   fi
   ```

2. **Query mergeable state (single API call):**
   ```bash
   PREFLIGHT_JSON=$(gh pr view "$PR_NUMBER" --json mergeable,mergeStateStatus,state)
   PR_STATE=$(echo "$PREFLIGHT_JSON" | jq -r '.state')
   PR_MERGEABLE=$(echo "$PREFLIGHT_JSON" | jq -r '.mergeable')
   PR_MERGE_STATUS=$(echo "$PREFLIGHT_JSON" | jq -r '.mergeStateStatus')
   echo "Preflight: state=$PR_STATE mergeable=$PR_MERGEABLE mergeStateStatus=$PR_MERGE_STATUS"
   ```

   Note: GitHub computes `mergeable` asynchronously. On a just-opened PR the
   first query may return `mergeable=UNKNOWN`. If so, retry once after 2s:
   ```bash
   if [ "$PR_MERGEABLE" = "UNKNOWN" ]; then
     sleep 2
     PREFLIGHT_JSON=$(gh pr view "$PR_NUMBER" --json mergeable,mergeStateStatus,state)
     PR_STATE=$(echo "$PREFLIGHT_JSON" | jq -r '.state')
     PR_MERGEABLE=$(echo "$PREFLIGHT_JSON" | jq -r '.mergeable')
     PR_MERGE_STATUS=$(echo "$PREFLIGHT_JSON" | jq -r '.mergeStateStatus')
   fi
   ```

3. **Decision table — apply in order, short-circuit on first match:**

   | Condition | Action | Verdict | Proceed? |
   |-----------|--------|---------|----------|
   | `state != "OPEN"` (CLOSED, MERGED) | Emit `PR_CLOSED` verdict, post a short comment noting the PR is not open, skip all further review. | `PR_CLOSED` | NO |
   | `mergeable == "CONFLICTING"` OR `mergeStateStatus == "DIRTY"` | Emit `BLOCKED_ON_CONFLICT` verdict. Post a comment that explicitly cites the `mergeStateStatus` value and asks the author to rebase/resolve. Skip checkout, diff reading, and code review. | `BLOCKED_ON_CONFLICT` | NO |
   | `mergeStateStatus == "BEHIND"` | Note it in the preflight log — the branch is behind base but has no conflicts. Proceed with full code review; the branch will be mergeable once updated. | (informational) | YES |
   | `mergeable == "MERGEABLE"` AND `mergeStateStatus IN ("CLEAN", "HAS_HOOKS", "UNSTABLE", "BLOCKED")` | Normal path — proceed with checkout and full code review. `BLOCKED` here is a GitHub status for missing-required-review/check, which IS what this review is supposed to produce. `UNSTABLE` means non-required checks failed; surface in findings but do not short-circuit. | (informational) | YES |
   | `mergeable == "UNKNOWN"` after retry | Treat conservatively as `CONFLICTING`: emit `BLOCKED_ON_CONFLICT` verdict, post `gh pr comment` only (§2b template), and stop. Do NOT proceed to checkout or code review when GitHub cannot confirm mergeability. | `BLOCKED_ON_CONFLICT` | NO |

4. **Short-circuit verdict emission (if preflight fails):**

   Use the corresponding verdict template from `post-review.md` (§2c for
   `PR_CLOSED`, §2b for `BLOCKED_ON_CONFLICT`). Post the comment via
   `gh pr comment` (not `gh pr review --request-changes` — a closed PR cannot
   take a review). Then emit the terminal OUTCOME block and exit. Do NOT
   continue to Step 5 below.

5. **Only if preflight passes:** continue to the checkout steps.

## Checkout Steps

1. **Clean git state** (abort any in-progress merge/rebase, stash uncommitted changes). Generic baseline:
   ```bash
   git merge --abort 2>/dev/null; git rebase --abort 2>/dev/null; git stash --include-untracked 2>/dev/null
   ```
   If the repo-context file declares a clean-git-state helper, use it instead.

2. **Checkout PR branch:**
   ```bash
   gh pr checkout $PR_NUMBER
   ```

3. **Verify checkout:**
   ```bash
   CURRENT_BRANCH=$(git branch --show-current)
   echo "Checked out branch: $CURRENT_BRANCH"
   ```

## Completion

Report the checked-out branch name and PR number. All subsequent sub-skills
will read files from this branch.
