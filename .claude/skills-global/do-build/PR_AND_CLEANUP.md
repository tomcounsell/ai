# PR Creation, Cleanup, and Reporting

Steps 6-9: documentation gate, PR creation, worktree cleanup, documentation cascade, report.

## Step 6: Documentation Gate

After review passes (advance to `document` if a state machine is declared), confirm the docs the
plan's `## Documentation` section names were created or updated. Run checks inside the worktree
via a `(cd $TARGET_REPO/.worktrees/{slug} && ...)` subshell so `git diff` sees the branch and your
CWD stays in the main repo.

- **Validate (blocking)**: a missing required doc stops the build and blocks the PR. Use the
  context file's docs-validation script if declared; otherwise check the named paths appear in
  `git diff --name-only main...HEAD`.
- **Related docs (optional)**: if the context file declares a related-docs scanner and an
  issue-creation helper, run them on the changed files and file review issues for high-confidence
  matches. Otherwise the `/do-docs` cascade in Step 7.6 covers it.

## Step 6.5: Pre-PR Commit Verification

Final safety net before anything leaves the machine:

```bash
COMMIT_COUNT=$(git -C $TARGET_REPO/.worktrees/{slug} log --oneline main..HEAD | wc -l | tr -d ' ')
```

`COMMIT_COUNT` 0 → hard abort: no push, no PR. Report "BUILD FAILED: No commits on session/{slug}
branch" with the tasks that ran and their reported status.

## Step 7: Create Pull Request

Advance to `pr` if a state machine is declared, then push and create or reuse the PR:

```bash
git -C $TARGET_REPO/.worktrees/{slug} push -u origin session/{slug}

# Reuse check against the LIVE ref (the search index lags). Cross-repo builds MUST add
# --repo $TARGET_GH_REPO to BOTH the list and the create, or the guard queries the
# wrong repo, always sees "no PR", and creates a duplicate.
EXISTING_PR=$(gh pr list --head session/{slug} --state open --json number -q '.[0].number')  # cross-repo: add --repo $TARGET_GH_REPO
if [ -n "$EXISTING_PR" ]; then
  echo "Reusing existing PR #$EXISTING_PR on session/{slug} (skipping create)"
else
  # One open PR per head branch makes `gh pr create` the atomic guard: if a concurrent
  # creator won the race, the create fails and we adopt the existing PR.
  if ! gh pr create --head session/{slug} --title "[plan title]" --body "$(cat <<'EOF'
## Summary
[Brief description of what was built]

## Changes
- [Key changes]

## Testing
- [x] Tests passing
- [x] Lint/format checks passing

## Documentation
- [x] Docs created per plan requirements

<!-- If the repo integrates an error-tracker (the context file names it), link any
     tracker issues this PR resolves; omit otherwise. -->

Closes #[issue-number]
EOF
)"; then
    EXISTING_PR=$(gh pr list --head session/{slug} --state open --json number -q '.[0].number')  # cross-repo: add --repo $TARGET_GH_REPO
    if [ -n "$EXISTING_PR" ]; then
      echo "Adopted existing PR #$EXISTING_PR after create collision on session/{slug}"
    else
      echo "ERROR: gh pr create failed and no existing open PR was found for session/{slug}" >&2
      exit 1
    fi
  fi
fi
```

Once the PR is open, record its number if the context file declares a writer, and if Step 0 wrote
the BUILD `in_progress` marker, write `--status completed` the same way.

## Step 7.5: Worktree Cleanup

`cd` to the repo root first (removing the worktree your shell is in kills the shell), then remove
the worktree but keep the branch; the PR still references it:

```bash
cd "$TARGET_REPO"
git -C "$TARGET_REPO" worktree remove "$TARGET_REPO/.worktrees/{slug}"
git -C "$TARGET_REPO" worktree prune
```

If the context file declares a worktree manager, use its removal helper (busy-session guards,
stale-ref pruning). After merge, the local branch can only be deleted once no worktree references
it; the context file names any post-merge cleanup helper.

## Step 7.6: Documentation Cascade

Invoke `/do-docs` with the PR number and plan context:

```
/do-docs {PR-number}

Plan: {PLAN_PATH}
Goal: [1-2 sentence summary from plan]
Issue: #{issue-number}
```

It is best-effort: no edits is fine; any edits are committed to the PR branch.

## Step 8: Plan Stays Until Merge

Do not delete or move the plan. `/do-merge` reads it and migrates it to
`docs/archive/plans-completed/` after merge; the issue closes via `Closes #N`.

## Step 9: Report

Put the PR URL prominently in the final response (the caller reads it from there), followed by:

```
## Plan Execution Complete

**Plan**: [plan name]
**Pull Request**: [PR URL]

### Definition of Done
[each SKILL.md criterion, checked, with the evidence: test counts, lint result, docs gate]

### Task Summary
| Task | Agent | Status | Notes |
|------|-------|--------|-------|

### Follow-ups
- [anything needing human attention]
```

End with the OUTCOME contract line from SKILL.md.
