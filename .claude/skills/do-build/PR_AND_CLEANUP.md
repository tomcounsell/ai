# PR Creation, Cleanup, and Plan Migration

Steps 6-9 of the build workflow: documentation gate, PR creation, worktree cleanup, documentation cascade, plan migration, and final reporting.

## Step 6: Documentation Gate

After all validation tasks pass, run the documentation lifecycle checks.

**Execute each command below exactly as written, including the parentheses.** The `(...)` subshell syntax ensures the `cd` happens in a child process — the orchestrator's CWD stays in the main repo.

### 6.1 Validate Documentation Changes

Run the doc validation script to verify documentation was created/updated. This script runs `git diff` internally and needs the worktree as CWD to see the session branch changes.

```bash
(cd $TARGET_REPO/.worktrees/{slug} && python scripts/validate_docs_changed.py {PLAN_PATH})
```

- **Exit 0**: Documentation requirements met, proceed to next step
- **Exit 1**: Documentation missing or insufficient, **STOP and report failure**
- This check BLOCKS PR creation if it fails
- The script checks that documentation matching the plan was created in `docs/features/` or `docs/`

### 6.2 Scan for Related Documentation

Collect all changed files from git and scan for related docs:

```bash
(cd $TARGET_REPO/.worktrees/{slug} && CHANGED_FILES=$(git diff --name-only main...HEAD | tr '\n' ' ') && python scripts/scan_related_docs.py --json $CHANGED_FILES > /tmp/related_docs.json)
```

This identifies existing documentation that may need updates based on code changes.

### 6.3 Create Review Issues for Discrepancies

Pipe the scan results to create GitHub issues for HIGH/MED-HIGH confidence matches:

```bash
cat /tmp/related_docs.json | python scripts/create_doc_review_issue.py
```

This creates tracking issues for documentation that should be reviewed for updates.

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
- [x] Linting (ruff, black) passing

## Documentation
- [x] Docs created per plan requirements
- [x] Related docs scanned for updates

## Sentry
[List any Sentry issues resolved by this PR, e.g. "Fixes VALOR-2", "Fixes VALOR-12".
Sentry auto-resolves these when the PR merges. Omit this section if no Sentry issues apply.]

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

After pushing and creating the PR, return to the repo root and clean up the worktree. The `cd` prevents CWD death if the shell is inside the worktree (issue #301):

```bash
# Return to repo root BEFORE cleanup (prevents CWD death)
cd ~/src/ai

python -c "
from pathlib import Path
from agent.worktree_manager import remove_worktree, prune_worktrees
# Use TARGET_REPO for cross-repo builds, orchestrator repo for same-repo builds
repo = Path('$TARGET_REPO')
remove_worktree(repo, '{slug}', delete_branch=False)
prune_worktrees(repo)
"
```

Note: `delete_branch=False` because the PR still references `session/{slug}`. The branch is cleaned up when the PR is merged via the post-merge cleanup step (see below).

### Post-Merge Cleanup (after PR is merged)

After the PR is merged (auto-merge for eligible PRs, or human-initiated via `gh pr merge --squash --delete-branch`), return to repo root and run the post-merge cleanup:

```bash
# Return to repo root BEFORE cleanup
cd ~/src/ai

python scripts/post_merge_cleanup.py {slug}
```

This calls `cleanup_after_merge()` from `agent/worktree_manager.py`, which:
1. Removes the worktree at `.worktrees/{slug}/` if it still exists
2. Prunes stale git worktree references
3. Deletes the local `session/{slug}` branch

Without this step, `gh pr merge --delete-branch` fails to delete the local branch because git refuses to delete a branch referenced by an active worktree.

## Step 7.6: Documentation Cascade

After the PR is created, run the `/do-docs` cascade to find and surgically update any existing documentation affected by the code changes in this build. Pass the PR number so the cascade can inspect the full diff:

```
/do-docs {PR-number}
```

This invokes the cascade skill defined in `.claude/skills/do-docs/SKILL.md`, which:
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
- [x] Quality: Ruff and Black checks pass
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
