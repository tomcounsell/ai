# Sub-Skill: PR Checkout

Context resolution, then the mergeability preflight, then a clean checkout of
the PR branch. Shell state does not persist between Bash calls, so carry
resolved values forward explicitly.

## Context Resolution (runs first)

Set `PR_NUMBER` to the skill argument (strip a leading `#`), falling back to
`$SDLC_PR_NUMBER` when the context file declares it. If neither resolves, stop
and ask for the PR number; never guess it from `gh pr list`.

```bash
REPO="${SDLC_REPO:-${GH_REPO:-$(gh repo view --json nameWithOwner -q .nameWithOwner)}}"
PLAN_PATH="${SDLC_PLAN_PATH:-}"
SLUG="${SDLC_SLUG:-}"
if [ -z "$PLAN_PATH" ] && [ -n "$SLUG" ]; then
  PLAN_PATH="docs/plans/${SLUG}.md"
fi

# ISSUE_NUMBER: unconditional clobber (never ${ISSUE_NUMBER:-…}). The skill
# argument is the PR number, not the issue number, and an inherited
# $SDLC_ISSUE_NUMBER may be stale (#1731). First positive integer wins:
# 1. PR body: Closes #N / Fixes #N / Resolves #N
# 2. PR body: tracking: https://.../issues/N
# 3. $SDLC_ISSUE_NUMBER (last resort, positive-integer guarded)
PR_BODY=$(gh pr view "$PR_NUMBER" --json body -q '.body' 2>/dev/null)
ISSUE_NUMBER=$(echo "$PR_BODY" | grep -oiP '(?:closes|fixes|resolves)\s+#\K[0-9]+' | head -1)
if [ -z "$ISSUE_NUMBER" ]; then
  ISSUE_NUMBER=$(echo "$PR_BODY" | grep -oP '(?<=issues/)[0-9]+' | head -1)
fi
if [ -z "$ISSUE_NUMBER" ] && [[ "$SDLC_ISSUE_NUMBER" =~ ^[0-9]+$ ]]; then
  ISSUE_NUMBER="$SDLC_ISSUE_NUMBER"
fi

# Fail loudly rather than divert a verdict onto the wrong issue (#1731).
[[ "$ISSUE_NUMBER" =~ ^[0-9]+$ ]] || {
  echo "do-pr-review: could not resolve a positive-integer ISSUE_NUMBER for PR $PR_NUMBER from the PR body or SDLC_ISSUE_NUMBER. Ensure the PR body contains 'Closes #N'." >&2
  exit 1
}
```

## Mergeability Preflight (runs BEFORE checkout or any diff read)

A branch that cannot merge gets no code review (PR #1100 was once approved
three times while `DIRTY`).

```bash
PREFLIGHT_JSON=$(gh pr view "$PR_NUMBER" --json mergeable,mergeStateStatus,state)
echo "$PREFLIGHT_JSON"
```

GitHub computes `mergeable` asynchronously: if it is `UNKNOWN`, wait 2s and
query once more.

Apply in order; the first match wins:

| Condition | Verdict | Proceed? |
|-----------|---------|----------|
| `state != "OPEN"` (CLOSED, MERGED) | `PR_CLOSED` | NO |
| `mergeable == "CONFLICTING"` OR `mergeStateStatus == "DIRTY"` | `BLOCKED_ON_CONFLICT` | NO |
| `mergeable == "UNKNOWN"` after retry | `BLOCKED_ON_CONFLICT` (GitHub cannot confirm mergeability) | NO |
| `mergeStateStatus == "BEHIND"` | informational: no conflicts, mergeable once updated | YES |
| `MERGEABLE` with `CLEAN`, `HAS_HOOKS`, `UNSTABLE`, or `BLOCKED` | normal path (`BLOCKED` is the missing review this skill produces; surface `UNSTABLE` checks in findings) | YES |

On a NO verdict, set `PREFLIGHT_VERDICT`, post the matching template from
`post-review.md` (§2c for `PR_CLOSED`, §2b for `BLOCKED_ON_CONFLICT`) via
`gh pr comment`, emit the OUTCOME block, and exit. The `BLOCKED_ON_CONFLICT`
comment cites the `mergeStateStatus` value.

## Checkout

1. Clean git state: abort any in-progress merge or rebase and stash uncommitted
   changes (`git merge --abort; git rebase --abort; git stash --include-untracked`,
   each tolerating failure). Use the context file's clean-git-state helper if it
   declares one.
2. `gh pr checkout "$PR_NUMBER"`, then confirm with `git branch --show-current`.

Report the checked-out branch and PR number; every later sub-skill reads files
from this branch.
