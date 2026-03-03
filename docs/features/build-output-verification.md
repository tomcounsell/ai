# Build Output Verification

Prevents the `/do-build` skill from silently completing when builder agents produce no code changes. Adds three verification layers that detect empty output and abort with clear error messages instead of creating empty PRs.

## Problem

Issue #236: The `/do-build` orchestrator deployed builder sub-agents that silently completed without producing any code changes, commits, or PRs. The orchestrator reported success because it only checked whether agents completed their tasks -- not whether they actually produced output.

## Solution

Three verification layers added to the build pipeline:

### Layer 1: Post-Task Change Verification (WORKFLOW.md Step 3.5)

After each **builder** agent task completes, the orchestrator checks:
```bash
git -C .worktrees/{slug} diff --stat HEAD
git -C .worktrees/{slug} status --porcelain
```

If both are empty (no committed or uncommitted changes), the task is marked as **FAILED** with diagnostic information. This check is skipped for non-builder agent types (validator, code-reviewer, documentarian) since they legitimately may not produce file changes.

### Layer 2: Pre-Validation Commit Check (WORKFLOW.md Step 4.5)

Before proceeding to final validation, the orchestrator verifies at least one commit exists on the session branch:
```bash
git -C .worktrees/{slug} log --oneline main..HEAD | wc -l
```

If zero commits exist, the build is **aborted** with a clear error message listing all builder tasks and their status.

### Layer 3: Pre-PR Commit Verification (PR_AND_CLEANUP.md Step 6.5)

Final safety net before pushing and creating the PR:
```bash
COMMIT_COUNT=$(git -C .worktrees/{slug} log --oneline main..HEAD | wc -l | tr -d ' ')
```

If zero commits exist, the orchestrator **hard aborts** -- no push, no PR creation. Reports which tasks ran and their status.

### Agent Self-Check

Builder agents are now instructed to run a mandatory self-check before marking their task complete:
1. Run `git status` and include output in response
2. Run `git log --oneline main..HEAD` and include output
3. If zero changes were made, explicitly state "NO CHANGES MADE" with explanation

## Files Modified

- `.claude/skills/do-build/WORKFLOW.md` -- Steps 3.5 and 4.5 added
- `.claude/skills/do-build/SKILL.md` -- Step 19 added (pre-PR verification), agent prompt updated
- `.claude/skills/do-build/PR_AND_CLEANUP.md` -- Step 6.5 added

## Design Decisions

- **Builder-only verification**: Change verification only applies to `builder` agent types. Validators and reviewers may legitimately produce no file changes.
- **Three layers, not one**: Redundant checks at different pipeline stages ensure no single point of failure. Even if one check is somehow bypassed, the next catches it.
- **Abort, don't retry**: When no changes are detected, the build aborts rather than retrying. Retry logic is a separate concern (and could mask deeper issues).
- **Git-based detection**: Uses `git diff`, `git status`, and `git log` rather than file system checks. This is more reliable since agents work in git worktrees.
