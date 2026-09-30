# Build Output Verification

The `/do-build` skill verifies that builder agents actually produced code changes before creating a PR. Three verification layers detect empty output and abort with clear error messages instead of creating empty PRs.

## Layer 1: Post-Task Change Verification (WORKFLOW.md Step 3.5)

After each **builder** agent task completes, the orchestrator checks:
```bash
git -C .worktrees/{slug} diff --stat HEAD
git -C .worktrees/{slug} status --porcelain
```

If both are empty (no committed or uncommitted changes), the task is marked as **FAILED** with diagnostic information. This check is skipped for non-builder agent types (validator, code-reviewer, documentarian) since they legitimately may not produce file changes.

## Layer 2: Pre-Validation Commit Check (WORKFLOW.md Step 4.5)

Before proceeding to final validation, the orchestrator verifies at least one commit exists on the session branch:
```bash
git -C .worktrees/{slug} log --oneline main..HEAD | wc -l
```

If zero commits exist, the build is **aborted** with a clear error message listing all builder tasks and their status.

## Layer 3: Pre-PR Commit Verification (PR_AND_CLEANUP.md Step 6.5)

Final safety net before pushing and creating the PR:
```bash
COMMIT_COUNT=$(git -C .worktrees/{slug} log --oneline main..HEAD | wc -l | tr -d ' ')
```

If zero commits exist, the orchestrator **hard aborts** -- no push, no PR creation. Reports which tasks ran and their status.

## Agent Self-Check

Builder agents run a mandatory self-check before marking their task complete:
1. Run `git status` and include output in response
2. Run `git log --oneline main..HEAD` and include output
3. If zero changes were made, explicitly state "NO CHANGES MADE" with explanation

## Related Files

- `.claude/skills/do-build/WORKFLOW.md` -- Steps 3.5 and 4.5
- `.claude/skills/do-build/SKILL.md` -- Pre-PR verification step and agent prompt
- `.claude/skills/do-build/PR_AND_CLEANUP.md` -- Step 6.5

## Design Decisions

- **Builder-only verification**: Change verification only applies to `builder` agent types. Validators and reviewers may legitimately produce no file changes.
- **Three layers, not one**: Redundant checks at different pipeline stages ensure no single point of failure. Even if one check is somehow bypassed, the next catches it.
- **Abort, don't retry**: When no changes are detected, the build aborts rather than retrying. Retry logic is a separate concern (and could mask deeper issues).
- **Git-based detection**: Uses `git diff`, `git status`, and `git log` rather than file system checks. This is more reliable since agents work in git worktrees.
