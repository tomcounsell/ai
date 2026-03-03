---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-03-03
tracking: https://github.com/tomcounsell/ai/issues/236
---

# Fix /do-build Skill Silent Failure

## Problem

When invoking `/sdlc` then `/do-build` for issue #232, the build skill completed without producing any code changes, commits, or a PR. The skill returned without error but did no actual work. The user had to implement all three fixes manually on the `session/fix-chat-cross-wire` branch.

**Current behavior:**
`/do-build` reads the plan, creates a worktree, deploys builder sub-agents via Task tools, but the sub-agents silently do nothing. No code changes, no commits, no PR. The orchestrator reports completion without verifying that any actual work was done.

**Desired outcome:**
`/do-build` either produces working code with commits and a PR, or fails loudly with a clear error message explaining what went wrong. The orchestrator should detect when sub-agents produce no output and report the failure rather than silently completing.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

The fix involves changes to the skill markdown files and possibly the agent deployment pipeline. The complexity is in diagnosis — there are multiple possible failure points.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Output Verification Gate**: After each sub-agent completes, the orchestrator verifies that actual file changes were produced in the worktree (via `git diff` or `git status`)
- **Explicit Failure Reporting**: When a sub-agent produces no changes, the orchestrator reports this as a failure with diagnostic information instead of silently proceeding
- **Agent Deployment Hardening**: Ensure the Task tool prompts include explicit working directory validation and commit requirements that agents can verify

### Flow

**Orchestrator deploys agent** → Agent works in worktree → **Orchestrator checks git status** → Changes detected? → Proceed to next task / **No changes** → Log failure, report to user

### Technical Approach

The root cause analysis points to several interconnected failure modes:

1. **No post-task change verification**: The SKILL.md workflow (Steps 3-4) deploys agents and monitors for completion but never checks whether the worktree actually has new commits or file changes. An agent can "complete" its task without producing any code.

2. **Silent agent failures**: When a sub-agent hits a context limit, import error, or can't parse the plan correctly, it may return without error. The orchestrator trusts task completion status without verifying work output.

3. **Missing worktree CWD enforcement**: The agent deployment prompt says "IMPORTANT: You MUST work in the worktree directory" but this is advisory text, not enforced. If the agent works in the wrong directory, changes go nowhere.

**Fix approach — add three verification layers:**

- **Layer 1 (SKILL.md)**: After each builder agent task completes, add a mandatory `git -C .worktrees/{slug} diff --stat HEAD` check. If the diff is empty and the task was a build task (not validation), treat it as a failure.
- **Layer 2 (SKILL.md)**: Before creating the PR (Step 7), add a `git -C .worktrees/{slug} log --oneline main..HEAD` check. If there are zero commits on the session branch, abort with a clear error.
- **Layer 3 (WORKFLOW.md)**: Update the agent deployment template (Step 3) to include an explicit self-check: the agent must run `git status` after completing work and include the output in its response.

## Rabbit Holes

- Trying to debug the original session #6348 to find the exact failure — the session is gone and we should focus on prevention
- Adding retry logic for failed agents — that is a separate enhancement; this fix is about detection and reporting
- Rewriting the entire Task tool deployment mechanism — overkill; the existing mechanism works, it just needs verification gates

## Risks

### Risk 1: Over-strict verification blocks legitimate no-change tasks
**Impact:** Validation-only tasks or documentation-review tasks may not produce code changes, triggering false failures
**Mitigation:** Only apply change verification to tasks with `Agent Type: builder` — skip for `validator`, `code-reviewer`, and `documentarian` types

### Risk 2: Git diff checks add latency to the pipeline
**Impact:** Each git command adds ~100ms; with many tasks this could be noticeable
**Mitigation:** These are fast local git operations; even 10 tasks add only ~1s total, which is negligible compared to agent execution time

## No-Gos (Out of Scope)

- Agent retry/self-healing when failures are detected (separate enhancement)
- Changes to the Claude Agent SDK or Task tool internals
- Modifications to agent type definitions or permissions
- Root cause analysis of specific past session failures

## Update System

No update system changes required — this fix modifies only SDLC skill markdown files and adds no new dependencies or config files.

## Agent Integration

No agent integration required — this is a skill-internal change affecting the orchestrator's workflow logic. The builder agents themselves are unchanged; only the orchestrator's verification of their output is enhanced.

## Documentation

- [ ] Update `docs/features/sdlc-pipeline.md` (if it exists) to document the new verification gates
- [ ] Add inline comments in the modified skill files explaining why each verification step exists

## Success Criteria

- [ ] After each builder agent task completes, orchestrator runs `git diff --stat` in the worktree and logs the result
- [ ] If a builder agent produces zero file changes, orchestrator reports failure with diagnostic info (agent name, task description, worktree path)
- [ ] Before PR creation, orchestrator verifies at least one commit exists on the session branch (via `git log main..HEAD`)
- [ ] If zero commits exist when PR creation is attempted, orchestrator aborts with a clear error message
- [ ] Agent deployment prompt includes explicit self-check instruction (run `git status` after work)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (skill-hardening)**
  - Name: skill-builder
  - Role: Modify SKILL.md, WORKFLOW.md, and PR_AND_CLEANUP.md to add verification gates
  - Agent Type: builder
  - Resume: true

- **Validator (verification-check)**
  - Name: skill-validator
  - Role: Verify the modified skill files contain all required verification gates
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add post-task change verification to WORKFLOW.md
- **Task ID**: build-workflow-verification
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: false
- Update Step 3 (Deploy Agents) in `.claude/skills/do-build/WORKFLOW.md` to include a post-completion git diff check after each builder agent task
- Update Step 4 (Monitor and Coordinate) to treat empty diffs from builder agents as failures
- Add a new verification block between Step 4 and Step 5 that checks `git -C .worktrees/{slug} log --oneline main..HEAD` and aborts if zero commits

### 2. Add pre-PR commit verification to SKILL.md
- **Task ID**: build-skill-prepr-gate
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Update Step 7 (Create Pull Request) in `.claude/skills/do-build/SKILL.md` to add a pre-PR gate checking for commits on the session branch
- Add explicit abort-with-error if `git log main..HEAD` returns empty
- Update the agent deployment prompt template in SKILL.md Step 3 to include self-check instructions

### 3. Update PR_AND_CLEANUP.md with pre-PR verification
- **Task ID**: build-cleanup-gate
- **Depends On**: none
- **Assigned To**: skill-builder
- **Agent Type**: builder
- **Parallel**: true
- Add a verification step before Step 7 (Create Pull Request) in `.claude/skills/do-build/PR_AND_CLEANUP.md`
- Verify commits exist on the session branch before pushing
- Add clear error message template for zero-commit failure case

### 4. Validate all verification gates
- **Task ID**: validate-all
- **Depends On**: build-workflow-verification, build-skill-prepr-gate, build-cleanup-gate
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `.claude/skills/do-build/WORKFLOW.md` contains post-task git diff check
- Verify `.claude/skills/do-build/SKILL.md` contains pre-PR commit verification
- Verify `.claude/skills/do-build/PR_AND_CLEANUP.md` contains pre-PR verification
- Verify agent deployment prompt includes self-check instruction
- Confirm no temporary files were created in the repo

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: skill-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Create or update documentation describing the build verification gates
- Add entry to documentation index if creating a new doc

### 6. Final Validation
- **Task ID**: validate-final
- **Depends On**: document-feature
- **Assigned To**: skill-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Validation Commands

- `grep -c "git.*diff.*stat" .claude/skills/do-build/WORKFLOW.md` - Confirms post-task diff check exists
- `grep -c "git.*log.*main..HEAD" .claude/skills/do-build/SKILL.md` - Confirms pre-PR commit check exists
- `grep -c "git.*log.*main..HEAD" .claude/skills/do-build/PR_AND_CLEANUP.md` - Confirms pre-PR gate in cleanup doc
- `grep -c "git status" .claude/skills/do-build/WORKFLOW.md` - Confirms agent self-check instruction
