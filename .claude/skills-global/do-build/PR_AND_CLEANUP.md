# PR Creation, Cleanup, and Plan Migration

Steps 6-9 of the build workflow: documentation gate, PR creation, worktree cleanup, documentation cascade, plan migration, and final reporting.

## Step 6: Documentation Gate

After all validation tasks pass, run the documentation lifecycle checks. Run doc
checks inside the worktree so `git diff` sees the session branch changes — use a
`(cd $TARGET_REPO/.worktrees/{slug} && ...)` subshell so the orchestrator's CWD
stays in the main repo.

### 6.1 Validate Documentation Changes

Confirm the plan's required documentation was created/updated. Inspect the
session branch diff for the doc paths the plan's `## Documentation` section
named. If a required doc is missing, **STOP and report failure** — this gate
BLOCKS PR creation.

If the context file declares a docs-validation script, run it (deterministic
enforcement). Generic default: verify the plan's named doc paths appear in
`git diff --name-only main...HEAD`.

### 6.2 Scan for Related Documentation (optional)

If the context file declares a related-docs scanner, collect the changed files
(`git diff --name-only main...HEAD`) and run it to identify existing docs that
may need updates. Otherwise rely on the `/do-docs` cascade in Step 7.6.

### 6.3 Create Review Issues for Discrepancies (optional)

If a scanner ran and the context file declares an issue-creation helper, file
review issues for HIGH/MED-HIGH confidence matches. Otherwise skip — the
`/do-docs` cascade flags conflicts itself.

## Step 6.5: Pre-PR Commit Verification

Before creating the PR, verify that the session branch has actual commits. This is the final safety net against silent build failures where all agents completed but produced no work.

```bash
COMMIT_COUNT=$(git -C $TARGET_REPO/.worktrees/{slug} log --oneline main..HEAD | wc -l | tr -d ' ')
echo "Commits on session/{slug}: $COMMIT_COUNT"
```

**If `COMMIT_COUNT` is 0:**
- **ABORT** -- do not push or create a PR
- Report: "BUILD FAILED: No commits on session/{slug} branch. Builder agents completed but produced zero code changes."
- Include a summary of which tasks ran and their reported status
- This is a hard failure -- the orchestrator must stop and report, not silently succeed

**If `COMMIT_COUNT` > 0:** Proceed to Step 7.

## Step 7: Create Pull Request

After documentation gate passes and pre-PR verification succeeds, push and create the PR:

```bash
git -C $TARGET_REPO/.worktrees/{slug} push -u origin session/{slug}
# For cross-repo builds, add: --repo $TARGET_GH_REPO
gh pr create --head session/{slug} --title "[plan title]" --body "$(cat <<'EOF'
## Summary
[Brief description of what was built]

## Changes
- [List key changes made]

## Testing
- [x] Unit tests passing
- [x] Integration tests passing
- [x] Lint/format checks passing

## Documentation
- [x] Docs created per plan requirements
- [x] Related docs scanned for updates

<!-- If the repo integrates an error-tracker (the context file names it), add a
     section linking any tracker issues this PR resolves; omit otherwise. -->

## Definition of Done
- [x] Built: Code implemented and working
- [x] Tested: All tests passing
- [x] Documented: Docs created/updated
- [x] Quality: Lint and format checks pass

Closes #[issue-number]
EOF
)"
```

**Important**: The PR creation step is handled by the BUILD ORCHESTRATOR (this skill), NOT by individual builder agents. Builder agents focus on their assigned tasks, while the orchestrator creates the final PR after all tasks complete and gates pass.

## Step 7.5: Worktree Cleanup

After pushing and creating the PR, return to the repo root and clean up the worktree. The `cd` to the repo root FIRST prevents CWD death if the shell is inside the worktree:

```bash
# Return to repo root BEFORE cleanup (prevents CWD death)
cd "$TARGET_REPO"

# Remove the worktree but KEEP the branch — the PR still references session/{slug}.
git -C "$TARGET_REPO" worktree remove "$TARGET_REPO/.worktrees/{slug}"
git -C "$TARGET_REPO" worktree prune
```

If the context file declares a worktree manager, use its removal helper instead (it adds busy-session guards and stale-ref pruning). Do NOT delete the branch — it is cleaned up when the PR is merged (see below).

### Post-Merge Cleanup (after PR is merged)

After the PR is merged (auto-merge for eligible PRs, or human-initiated via `gh pr merge --squash --delete-branch`), return to repo root and remove the worktree so the local branch can be deleted:

```bash
cd "$TARGET_REPO"
git -C "$TARGET_REPO" worktree remove "$TARGET_REPO/.worktrees/{slug}" 2>/dev/null
git -C "$TARGET_REPO" worktree prune
git -C "$TARGET_REPO" branch -D session/{slug} 2>/dev/null || true
```

`gh pr merge --delete-branch` cannot delete the local branch while a worktree still references it, so the worktree removal must happen first. If the context file declares a post-merge cleanup helper (with busy-session guards), use it instead.

## Step 7.6: Documentation Cascade

After the PR is created, run the `/do-docs` cascade to find and surgically update any existing documentation affected by the code changes in this build. Pass the PR number so the cascade can inspect the full diff:

```
/do-docs {PR-number}
```

This invokes the cascade skill defined in `.claude/skills-global/do-docs/SKILL.md`, which:
- Launches parallel agents to explore the change diff and inventory all docs
- Cross-references changes against every doc in the repo (triage questions)
- Makes targeted surgical edits to affected docs (read before edit, preserve structure)
- Creates GitHub issues for conflicts needing human review
- Commits any doc updates to the PR branch before merge

**Note**: The cascade is best-effort. If it finds nothing to update, that's fine — proceed to reporting. If it makes edits, those are committed directly to the PR branch.

## Step 8: Plan Stays Until Merge

After PR is created and documentation cascade completes, the plan document is **not deleted here**. It remains at `{PLAN_PATH}` so that:
- `do-merge` can read it to verify all checklist items are done
- `do-docs` can use it as context during the DOCS stage

The plan will be deleted by `do-merge` after the PR is successfully merged. The tracking issue closes automatically when the PR merges (via `Closes #N` in the PR body).

## Step 9: Report PR Link

After plan migration completes, include the PR URL prominently in your final response. When running via Telegram bridge, the agent's response (containing the PR link) will be automatically sent back to the chat where the build was initiated. No special action required - just ensure the PR URL is visible in your completion report.

### Report Format

```
## Plan Execution Complete

**Plan**: [plan name]
**Pull Request**: [PR URL]
**Total Tasks**: [count]

### Definition of Done
- [x] Built: All code implemented and working
- [x] Tested: Unit tests passing, integration tests passing
- [x] Documented: Docs created per plan requirements (validated by docs gate)
- [x] Quality: the repo's lint/format checks pass
- [x] Plans migrated: Plan moved from docs/plans/ to completed state

### Task Summary
| Task | Agent | Status | Test Iterations | Notes |
|------|-------|--------|----------------|-------|
| [name] | [agent] | Done | [N] | [brief note] |

### Validation Results
- [x] All build tasks completed
- [x] All validators passed
- [x] Documentation gate passed
- [x] Documentation cascade completed (`/do-docs`)
- [x] Success criteria met

### Artifacts Created
- [list of files created/modified]

### Next Steps
- Review and merge PR: [PR URL]
- PR link has been sent to Telegram chat
- [Any follow-up items or manual steps needed]
```
