---
name: do-build
description: "Use when executing a plan document to ship a feature. Triggered by 'build this', 'execute the plan', 'implement the plan', or any request to run/ship a plan."
argument-hint: "<plan-path-or-issue-number>"
context: fork
---

# Build (Plan Execution)

You are the **team lead** executing a plan document. You orchestrate work using Task tools - you NEVER build directly.

## What this skill does

1. Resolves a plan document (by path or issue number)
2. Creates an isolated worktree for the build
3. Deploys builder/validator agent teams to execute the plan
4. Runs documentation gates and quality checks
5. Opens a PR and migrates the completed plan

## When to load sub-files

| Sub-file | Load when... |
|----------|-------------|
| `WORKFLOW.md` | Starting execution (Steps 1-5: task creation, agent deployment, monitoring) |
| `PR_AND_CLEANUP.md` | All build tasks complete and validated (Steps 6-9: docs gate, PR, cleanup, migration) |

## Invocation Methods

1. **By plan path**: `/do-build docs/plans/my-feature.md`
2. **By issue number**: `/do-build #17` or `/do-build 17`

## Variables

PLAN_ARG: $ARGUMENTS

**If PLAN_ARG is empty or literally `$ARGUMENTS`**: The skill argument substitution did not run. Look at the user's original message in the conversation — they invoked this as `/do-build <argument>`. Extract whatever follows `/do-build` as the value of PLAN_ARG. Do NOT stop or report an error; just use the argument from the message.

## Plan Resolution

**Step 1: Detect argument type**
- If `PLAN_ARG` starts with `#` or is a pure number, treat as issue number
- Otherwise, treat as file path

**Step 2A: If issue number**
1. Extract the number (strip `#` if present)
2. Use Glob tool to find all plan files: `docs/plans/*.md`
3. Read each plan and check frontmatter for `tracking:` field
4. Match pattern: `/issues/{NUMBER}` where NUMBER equals the argument
5. If exactly one match: use that plan path
6. If no matches: Error - "No plan found tracking issue #{N}"
7. If multiple matches: Error - "Multiple plans found tracking issue #{N}: [list paths]"

**Step 2B: If file path**
- Use `PLAN_ARG` directly as `PLAN_PATH`
- Verify file exists (will error naturally if not)

**Step 3: Set PLAN_PATH**
- `PLAN_PATH` now contains the resolved absolute path to the plan document

## Target Repo Resolution (Cross-Repo Support)

After resolving `PLAN_PATH`, determine which git repository the plan belongs to. This is critical for cross-repo builds where the plan lives in a different repo than the orchestrator.

```bash
# Resolve the target repo root from the plan file's location
TARGET_REPO=$(git -C "$(dirname "$PLAN_PATH")" rev-parse --show-toplevel)
ORCHESTRATOR_REPO=$(git rev-parse --show-toplevel)

# Check if this is a cross-repo build
if [ "$TARGET_REPO" != "$ORCHESTRATOR_REPO" ]; then
    echo "CROSS-REPO BUILD: Plan is in $TARGET_REPO (orchestrator is $ORCHESTRATOR_REPO)"
    # Resolve the target repo's GitHub identity for PR creation
    TARGET_GH_REPO=$(git -C "$TARGET_REPO" remote get-url origin | sed 's/.*github.com[:/]//' | sed 's/\.git$//')
fi
```

Or using Python:
```bash
python -c "from agent.worktree_manager import resolve_repo_root; print(resolve_repo_root('$PLAN_PATH'))"
```

**All subsequent git, worktree, and PR operations must use `TARGET_REPO` as the repo root**, not the orchestrator repo. Specifically:
- `create_worktree(Path(TARGET_REPO), slug)` instead of `create_worktree(Path('.'), slug)`
- `git -C $TARGET_REPO/.worktrees/{slug}` for all git commands in the worktree
- `gh pr create --repo $TARGET_GH_REPO` when creating the PR
- Pipeline state is still stored in the orchestrator repo but includes `target_repo` in the state dict

If `TARGET_REPO == ORCHESTRATOR_REPO`, this is a same-repo build and no special handling is needed (all existing behavior works as-is).

## Instructions

1. **Resolve the plan path** using the Plan Resolution logic above
2. **Read the plan** at `PLAN_PATH`
3. **Check pipeline state** - After resolving the plan path, derive `{slug}` from the plan filename and check for existing state:
   ```bash
   python -c "from agent.build_pipeline import load; import json; s = load('{slug}'); print(json.dumps(s) if s else 'null')"
   ```
   - If state exists and `stage != "plan"`: resume from that stage, skip already-completed stages listed in `completed_stages`
   - If no state (output is `null`): proceed normally — initialize state after worktree creation
4. **Check issue comment freshness** - Verify the plan has incorporated the latest issue comments before building:
   ```bash
   # Extract tracking issue number and last_comment_id from plan frontmatter
   ISSUE_NUM=$(grep '^tracking:' {PLAN_PATH} | grep -oP '/issues/\K\d+')
   PLAN_COMMENT_ID=$(grep '^last_comment_id:' {PLAN_PATH} | sed 's/last_comment_id: *//' | tr -d ' ')
   REPO=$(gh repo view --json nameWithOwner -q .nameWithOwner)

   if [ -n "$ISSUE_NUM" ]; then
     LATEST_COMMENT_ID=$(gh api repos/${REPO}/issues/${ISSUE_NUM}/comments --jq '.[-1].id // empty' 2>/dev/null)
     if [ -n "$LATEST_COMMENT_ID" ] && [ "$LATEST_COMMENT_ID" != "$PLAN_COMMENT_ID" ]; then
       echo "STALE PLAN: Issue #${ISSUE_NUM} has new comments (latest: ${LATEST_COMMENT_ID}, plan has: ${PLAN_COMMENT_ID})"
       echo "Run /do-plan to incorporate the latest feedback before building."
       exit 1
     fi
   fi
   ```
   - If the plan's `last_comment_id` matches the latest comment: proceed
   - If there are newer comments: **STOP** and report that the plan needs updating via `/do-plan` first
   - If no tracking issue or no comments exist: skip this check
5. **Run prerequisite validation** - `python scripts/check_prerequisites.py {PLAN_PATH}`. If any check fails, report the failures and stop. Do not proceed to task execution. If no Prerequisites section exists, this passes automatically.
6. **Resolve target repo** - Determine which repo the plan belongs to (see "Target Repo Resolution" above):
   ```bash
   TARGET_REPO=$(git -C "$(dirname "$PLAN_PATH")" rev-parse --show-toplevel)
   ```
7. **Ensure clean git state** - Before creating a worktree, verify the main working tree has no in-progress merge, rebase, or cherry-pick operations that would block git operations:
   ```bash
   python -c "from agent.worktree_manager import ensure_clean_git_state; from pathlib import Path; print(ensure_clean_git_state(Path('$TARGET_REPO')))"
   ```
   This aborts any in-progress merge/rebase/cherry-pick and stashes uncommitted changes. See `docs/features/git-state-guard.md` for details.
7. **Get or create an isolated worktree** - Get the existing worktree or create `.worktrees/{slug}/` with branch `session/{slug}` in the **target repo** using the worktree manager (handles stale worktrees and session resumption automatically):
   ```bash
   python -c "from agent.worktree_manager import get_or_create_worktree; from pathlib import Path; print(get_or_create_worktree(Path('$TARGET_REPO'), '{slug}'))"
   ```
   This is idempotent: if the worktree already exists (e.g., from an interrupted session), it returns the existing path. If not, it creates a fresh one. It also handles stale worktrees from crashed sessions, missing directories with lingering git references, and branch-already-in-use errors. Settings files are copied automatically.
   All subsequent agent work happens inside `$TARGET_REPO/.worktrees/{slug}/`, NOT the orchestrator repo directory.
8. **Initialize pipeline state** - For fresh builds (no prior state), initialize now:
   ```bash
   python -c "from agent.build_pipeline import initialize; initialize('{slug}', 'session/{slug}', '$TARGET_REPO/.worktrees/{slug}', target_repo='$TARGET_REPO')"
   ```
   Skip this step if state already existed from step 3.
9. **Advance to branch stage** after worktree is ready:
   ```bash
   python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', 'branch')"
   ```
10. **Parse the Team Members** and Step by Step Tasks sections
11. **Create all tasks** using `TaskCreate` before starting execution
12. **Deploy agents** in order, respecting dependencies and parallel flags (agents follow SDLC: Build → Test loop with up to 5 iterations)
13. **Advance to implement stage** before deploying builder agents:
    ```bash
    python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', 'implement')"
    ```
14. **Monitor progress** and handle any issues
15. **Advance to test stage** after implementation tasks complete:
    ```bash
    python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', 'test')"
    ```
16. **Verify Definition of Done** - Ensure all tasks completed with: code working, tests passing, quality checks pass
17. **Advance to review stage** after tests pass:
    ```bash
    python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', 'review')"
    ```
18. **Advance to document stage** after review passes:
    ```bash
    python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', 'document')"
    ```
19. **Run documentation gate** - Validate docs changed, scan related docs, create review issues
20. **Advance to pr stage** after documentation gate passes:
    ```bash
    python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', 'pr')"
    ```
21. **Verify commits exist before PR** - Run `git -C $TARGET_REPO/.worktrees/{slug} log --oneline main..HEAD` and count the output lines. If zero commits exist on the session branch, **ABORT with error**: "BUILD FAILED: No commits on session/{slug}. Builder agents produced no code changes." Do NOT proceed to push or PR creation.
22. **Push and open a PR** - `git -C $TARGET_REPO/.worktrees/{slug} push -u origin session/{slug}` then `gh pr create --repo $TARGET_GH_REPO` (use `--repo` only for cross-repo builds)
23. **Run documentation cascade** - Invoke `/do-docs {PR-number}` to surgically update affected docs
24. **Migrate completed plan** - Delete plan file (issue closes automatically on PR merge via `Closes #N`)
25. **Report completion** with PR URL when all tasks are done

## Lint Discipline

Lint and formatting are handled automatically -- agents should never waste iterations on lint fixes.

- **Intermediate commits**: Use `--no-verify` to skip the pre-commit hook during WIP commits mid-task. This avoids unnecessary lint interruptions while the agent is still working.
- **Final commits**: Let the pre-commit hook run (no `--no-verify`). The hook auto-fixes all fixable lint/format issues via `ruff format` + `ruff check --fix` and re-stages the changes. Only genuinely unfixable issues block the commit.
- **Never run manual lint checks**: Do NOT instruct agents to run `ruff check .` or `ruff format --check .` as a separate step. The pre-commit hook handles this automatically on final commits.
- **PostToolUse hook**: The `format_file.py` hook runs `ruff check --fix` + `ruff format` on individual files after every Write/Edit, so files stay clean as agents work.

## Critical Rules

- **You are the orchestrator, not a builder** - Never use Write/Edit tools directly
- **Deploy agents via Task tool** - Each task in the plan becomes a Task tool call
- **Respect dependencies** - Don't start a task until its `Depends On` tasks are complete
- **Run parallel tasks together** - Tasks with `Parallel: true` and no blocking dependencies can run simultaneously
- **Validators wait for builders** - A `validate-*` task always waits for its corresponding `build-*` task
- **No temporary files** - Agents must not create temporary documentation, test results, or scratch files in the repo. Use /tmp for any temporary work. Only create files that are part of the deliverable.
- **Never cd into worktrees** - The orchestrator's CWD must stay in the main repo. Use `git -C $TARGET_REPO/.worktrees/{slug}` for git commands, subshells `(cd $TARGET_REPO/.worktrees/{slug} && ...)` when Python scripts need worktree CWD, and `--head session/{slug}` for `gh pr create`. For cross-repo builds, use `--repo $TARGET_GH_REPO` with `gh pr create`. Only subagents (Task tool) should have bare `cd` into worktrees — their shell sessions are independent and disposable. If the orchestrator's CWD ends up inside a worktree and that worktree is deleted, the shell breaks permanently and cannot recover.
- **SDLC enforcement** - All builder agents follow Plan → Branch → Implement → Test → Review → Document → PR with fix-and-retry loops at Test and Review stages (up to 5 iterations)
- **Definition of Done** - Tasks are complete only when: Built (code working), Tested (tests pass), Reviewed (review passes), Documented (docs created after review), Quality (lint/format pass)
- **Commits at logical checkpoints** - Commits happen at logical checkpoints throughout Implement — not batched at end. The commit message hook enforces hygiene at each commit.

## Workflow

### Step 1: Initialize Task List

Read the plan and create tasks:

```typescript
// For each task in "Step by Step Tasks":
TaskCreate({
  subject: "[Task Name]",
  description: "[Full task details from plan]",
  activeForm: "[Description in progress form]"
})
// Note the returned taskId for dependency tracking
```

### Step 2: Set Dependencies

After creating all tasks, set up the dependency chain:

```typescript
TaskUpdate({
  taskId: "[task-id]",
  addBlockedBy: ["dependency-task-id-1", "dependency-task-id-2"]
})
```

### Step 3: Deploy Agents

For each task, deploy the assigned agent:

```typescript
Task({
  description: "[Task subject]",
  prompt: `Execute task: [Task Name]

IMPORTANT: You MUST work in the worktree directory: {absolute_path_to}/.worktrees/{slug}/
Run \`cd {absolute_path_to}/.worktrees/{slug}/\` before doing any work.
All file reads, writes, and commands should use this worktree path, not the main repo.

NEVER use \`git checkout\` or \`git checkout -b\` on session/ branches. The worktree IS the checkout — just \`cd\` into it. Running \`git checkout session/{slug}\` will fail with a fatal error because the branch is locked by the worktree.

Plan context: [relevant plan sections]

Your assignment:
- [specific actions from task]

Commit at logical checkpoints as you work — not as one batch at the end. The commit message hook enforces hygiene at each commit.

SELF-CHECK (mandatory before marking task complete):
1. Run \`git status\` in the worktree and include the output in your response
2. Run \`git log --oneline main..HEAD\` and include the output
3. If you made zero file changes, explicitly state "NO CHANGES MADE" and explain why

When complete, update your task status.`,
  subagent_type: "[agent type from task]",
  run_in_background: [true if Parallel: true]
})
```

### Step 4: Monitor and Coordinate

- Check `TaskList({})` to see overall progress
- Use `TaskOutput({task_id, block: false})` to check on background agents
- When a blocker completes, dependent tasks auto-unblock

**Health Monitoring for Background Agents:**

After deploying background agents, actively monitor their health:

1. Poll `TaskOutput({task_id, block: false, timeout: 30000})` for each background agent when checking progress
2. Check `TaskList` to see if tasks have moved to completed status
3. If a background agent's TaskOutput returns completion but TaskList still shows `in_progress`, use `TaskUpdate` to mark it
4. **Warning threshold (5 min):** If an agent has produced no new output for 5+ minutes, note this as a potential issue
5. **Failure threshold (15 min):** If an agent has been completely silent for 15+ minutes:
   - Attempt to resume the agent using its agentId
   - If resume fails, mark the task as failed
   - Report the failure prominently so the user is aware
6. **On any agent failure:** Commit whatever work exists in the worktree as a safety net:
   ```bash
   git -C .worktrees/{slug} add -A && git -C .worktrees/{slug} commit -m "[WIP] partial work before agent failure" || true
   ```

### Step 5: Final Validation and Definition of Done

When the final `validate-all` task completes, verify Definition of Done criteria:

**Pipeline stage at this point:** `test` → advance to `review` before proceeding.

```bash
python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', 'review')"
```

**Definition of Done Checklist (pre-documentation):**
- [x] **Built**: All code implemented and working
- [x] **Tested**: All unit tests passing, integration tests passing
- [x] **Quality**: Ruff and Black checks pass, no lint errors
- [x] **Reviewed**: Review passes (no blocking issues)
- [x] **Demonstrated**: Feature produces intended user-visible output (e.g., rendered message, API response, UI state)

If any criterion is not met, report the issue and do NOT proceed to the Document stage.

**Note**: Documentation validation happens AFTER review passes — see Step 6. The canonical pipeline order is: Plan → Branch → Implement → Test → Review → Document → PR. Fix-and-retry loops re-enter at Test (for test failures) or Review (for review failures).

### Step 5.1: Run Verification Checks from Plan

If the plan has a `## Verification` section with a machine-readable table, extract and run each check automatically. This replaces manual validation judgment with deterministic pass/fail:

```bash
(cd .worktrees/{slug} && python -c "
from agent.verification_parser import parse_verification_table, run_checks, format_results
from pathlib import Path
plan = Path('{PLAN_PATH}').read_text()
checks = parse_verification_table(plan)
if checks:
    results = run_checks(checks)
    print(format_results(results))
    if not all(r.passed for r in results):
        raise SystemExit(1)
else:
    print('No verification table found in plan -- skipping automated checks.')
")
```

- **Exit 0**: All verification checks passed, proceed
- **Exit 1**: Some checks failed -- fix the specific failures (check name, command, expected vs actual) and re-run verification
- If the plan has no `## Verification` section, this step is a no-op

### Step 5.5: CWD Safety Reset

Before running any orchestrator bash commands, verify the shell CWD is the main repo root (not inside a worktree). Run this as a sanity check:

```bash
cd $(git rev-parse --show-toplevel) && pwd
```

The output should be the main repo path, NOT a `.worktrees/` path. If the CWD is somehow inside the worktree, this resets it. All subsequent orchestrator commands depend on CWD being the repo root.

### Step 6: Documentation Gate

After review passes, advance to the `document` stage and run documentation lifecycle checks:

```bash
python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', 'document')"
```

This is the Document phase of the pipeline: `Plan → Branch → Implement → Test → Review → **Document** → PR`. Documentation is written and validated here, after implementation is reviewed — not interleaved with implementation.

**6.1 Validate Documentation Changes**

Run the doc validation script to verify documentation was created/updated. This script runs `git diff` internally and needs the worktree as CWD to see the session branch changes.

**Execute each command below exactly as written, including the parentheses.** The `(...)` subshell syntax ensures the `cd` happens in a child process — the orchestrator's CWD stays in the main repo.

```bash
(cd .worktrees/{slug} && python scripts/validate_docs_changed.py {PLAN_PATH})
```

- **Exit 0**: Documentation requirements met, proceed to next step
- **Exit 1**: Documentation missing or insufficient, **STOP and report failure**
- This check BLOCKS PR creation if it fails
- The script checks that documentation matching the plan was created in `docs/features/` or `docs/`

**6.2 Scan for Related Documentation**

Collect all changed files from git and scan for related docs:

```bash
(cd .worktrees/{slug} && CHANGED_FILES=$(git diff --name-only main...HEAD | tr '\n' ' ') && python scripts/scan_related_docs.py --json $CHANGED_FILES > /tmp/related_docs.json)
```

This identifies existing documentation that may need updates based on code changes.

**6.3 Create Review Issues for Discrepancies**

Pipe the scan results to create GitHub issues for HIGH/MED-HIGH confidence matches:

```bash
cat /tmp/related_docs.json | python scripts/create_doc_review_issue.py
```

This creates tracking issues for documentation that should be reviewed for updates.

### Step 7: Create Pull Request

After documentation gate passes, advance to the `pr` stage and push:

```bash
python -c "from agent.build_pipeline import advance_stage; advance_stage('{slug}', 'pr')"
```

Then push and create the PR. For cross-repo builds, use `$TARGET_REPO` and `--repo $TARGET_GH_REPO`:

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

### Step 7.5: Worktree Cleanup

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

Note: `delete_branch=False` because the PR still references `session/{slug}`. The branch is cleaned up when the PR is merged.

### Step 7.6: Documentation Cascade

After the PR is created, run the `/do-docs` cascade to find and surgically update any existing documentation affected by the code changes in this build. Pass the PR number AND plan context so the cascade understands the feature intent:

```
/do-docs {PR-number}

Plan: {PLAN_PATH}
Goal: [1-2 sentence summary from plan]
Issue: #{issue-number}
```

This invokes the cascade skill defined in `.claude/skills/do-docs/SKILL.md`, which:
- Launches parallel agents to explore the change diff and inventory all docs
- Cross-references changes against every doc in the repo (triage questions)
- Makes targeted surgical edits to affected docs (read before edit, preserve structure)
- Creates GitHub issues for conflicts needing human review
- Commits any doc updates to the PR branch before merge

**Note**: The cascade is best-effort. If it finds nothing to update, that's fine — proceed to plan migration. If it makes edits, those are committed directly to the PR branch.

### Step 8: Plan Migration

After PR is successfully created and documentation cascade completes, clean up the completed plan:

```bash
cd $(git rev-parse --show-toplevel) && python scripts/migrate_completed_plan.py {PLAN_PATH}
```

This deletes the plan document and closes the tracking issue, completing the lifecycle.

### Step 9: Report PR Link

After plan migration completes, include the PR URL prominently in your final response. When running via Telegram bridge, the agent's response (containing the PR link) will be automatically sent back to the chat where the build was initiated. No special action required - just ensure the PR URL is visible in your completion report.

## Agent Deployment Context

When deploying an agent, include:
1. The specific task actions from the plan
2. Relevant file paths from the plan's "Relevant Files" section
3. Success criteria from the plan
4. Validation commands they should run (for validators)
5. Reminder: No temporary files in repo - use /tmp for scratch work, only commit deliverables

## Example Invocations

**By file path:**
```
/do-build docs/plans/implement-auth.md
```

**By issue number:**
```
/do-build #42
/do-build 42
```

Both methods will execute the same plan if the plan file has:
```yaml
tracking: https://github.com/valor-labs/ai/issues/42
```

## Example Execution

Given a plan with tasks:
```
1. build-api (Parallel: true)
2. build-frontend (Parallel: true)
3. validate-api (Depends On: build-api)
4. validate-frontend (Depends On: build-frontend)
5. integration-test (Depends On: validate-api, validate-frontend)
```

Execution order:
1. Create all 5 tasks
2. Set dependencies
3. Deploy build-api AND build-frontend simultaneously (both parallel, no deps)
4. When build-api completes → validate-api starts
5. When build-frontend completes → validate-frontend starts
6. When BOTH validators complete → integration-test starts

## Report Format

After all tasks complete:

```
## Plan Execution Complete

**Plan**: [plan name]
**Pull Request**: [PR URL]
**Total Tasks**: [count]

### Definition of Done
- [x] Built: All code implemented and working
- [x] Tested: Unit tests passing, integration tests passing
- [x] Reviewed: Review passed (no blocking issues)
- [x] Documented: Docs created after review (validated by docs gate)
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

## Error Handling

If a task fails:
1. Check the agent's output for details
2. Decide: retry, skip, or abort
3. For validators: if validation fails, report what's wrong
4. Don't proceed past blocking failures

## Notes

- The plan document is the source of truth
- Agents can be resumed using their agentId if they need to continue work
- Background agents continue running even if you move to other tasks
- Use `TaskStop` only if you need to abort a runaway agent
