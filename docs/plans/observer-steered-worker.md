# Observer-Steered Worker: Single-Stage Pipeline Control

**Issue:** #354
**Branch:** `session/observer-steered-worker`
**Status:** In Progress
**Appetite:** Medium (2-3 hours)

## Problem

Two systems fight over SDLC pipeline control:

1. **Worker system prompt** contains `SDLC_WORKFLOW` — 40-line constant telling the worker to invoke `/sdlc` and self-orchestrate the full pipeline. **Fixed in PR #356** (replaced with `WORKER_RULES` safety rails).

2. **`/sdlc` skill** (`.claude/skills/sdlc/SKILL.md`) is a 210-line full-pipeline dispatcher that creates 9-stage task lists, runs assess-invoke loops, contains auto-merge logic, gate checking, and review verification — all within a single worker turn. Even after PR #356, when the Observer steers with "invoke /sdlc issue 123", the worker enters this monolithic dispatcher that tries to do everything, defeating single-stage steering.

## Solution

### Work Item 1: Replace `SDLC_WORKFLOW` with `WORKER_RULES` ✅ (PR #356)

Already built and passing tests. `SDLC_WORKFLOW` → `WORKER_RULES` in `agent/sdk_client.py`.

### Work Item 2: Convert `/sdlc` to single-stage router

Rewrite `.claude/skills/sdlc/SKILL.md` from a 210-line full-pipeline dispatcher to a ~60-line single-stage router:

**Current behavior (what `/sdlc` does now):**
1. Creates task items for all 9 stages upfront (Step 0)
2. Tracks session ID for progress (Step 0.5)
3. Ensures GitHub issue exists (Step 1)
4. Assesses current state via git/gh checks (Step 2)
5. Runs goal gate checks before advancing (Step 2.5)
6. Invokes the next sub-skill (Step 3)
7. **Loops back to Step 2** — assess state again, invoke next sub-skill
8. Continues looping until MERGE or blocker
9. Contains auto-merge eligibility logic, review verification, tech debt patching

**Target behavior (what `/sdlc` should do):**
1. Parse the issue number from args
2. Fetch the issue (`gh issue view`)
3. Assess current pipeline state (plan exists? branch exists? PR exists? tests passing?)
4. Determine the ONE next stage to execute
5. Invoke that ONE sub-skill (`/do-plan`, `/do-build`, `/do-test`, `/do-pr-review`, `/do-docs`)
6. **Return.** Do NOT loop. Do NOT assess again.
7. The Observer sees the worker output, detects the completed stage, and steers to the next one.

**What gets removed from `/sdlc`:**
- Step 0: Full 9-stage task list creation
- Step 0.5: Session progress tracking (stage detector handles this)
- Step 2.5: Goal gate checking (Observer can do this)
- The assess → invoke → loop back pattern in "After Dispatching"
- Auto-merge eligibility logic (move to Observer or keep as `/merge` skill)
- Review verification (`gh api` checks for review existence)
- Tech debt patching loop

**What stays in `/sdlc`:**
- Issue lookup/creation (Step 1 — still needed for first invocation)
- State assessment (Step 2 — needed to determine which stage is next)
- Stage dispatch table (Step 3 — the core routing logic)
- Hard rules (never commit to main, never skip issue/plan)

## Implementation

### Files Changed

| File | Change |
|------|--------|
| `.claude/skills/sdlc/SKILL.md` | Rewrite: full dispatcher → single-stage router |
| `agent/sdk_client.py` | Already done in PR #356 |
| `tests/unit/test_sdk_client_sdlc.py` | Already done in PR #356 |
| `docs/features/observer-agent.md` | Already done in PR #356 |

### `/sdlc` SKILL.md Structure (Target)

```markdown
# SDLC — Single-Stage Router

Determines the next pipeline stage for an issue and invokes it.
Does NOT loop or orchestrate the full pipeline — the Observer
handles stage progression via coaching messages.

## Step 1: Resolve the Issue
- Parse issue number from args
- gh issue view {number}
- If no issue number, invoke /do-issue

## Step 2: Assess Current State
- Check: plan doc exists? branch/PR exists? tests passing? review done? docs done?
- Determine next stage from the table below

## Step 3: Invoke ONE Sub-Skill
| State | Invoke |
|-------|--------|
| No plan | /do-plan |
| Plan exists, no branch/PR | /do-build |
| Tests failing | /do-patch then /do-test |
| Tests passing, no review | /do-pr-review |
| Review blockers | /do-patch |
| Review clean, no docs | /do-docs |
| All complete | Report done (Observer delivers to human) |

## Hard Rules
- NEVER write code directly
- NEVER skip issue or plan
- NEVER commit to main
```

### Verification

| # | Check | Command |
|---|-------|---------|
| 1 | SKILL.md has no loop/assess-again pattern | `grep -c "Step 2\|assess.*again\|After Dispatching" .claude/skills/sdlc/SKILL.md` → 0 |
| 2 | SKILL.md has no 9-stage task creation | `grep -c "TaskCreate" .claude/skills/sdlc/SKILL.md` → 0 |
| 3 | SKILL.md has no auto-merge logic | `grep -c "auto-merge\|Auto-Merge" .claude/skills/sdlc/SKILL.md` → 0 |
| 4 | SKILL.md is under 100 lines | `wc -l .claude/skills/sdlc/SKILL.md` → <100 |
| 5 | SKILL.md still has issue lookup | `grep -c "gh issue view" .claude/skills/sdlc/SKILL.md` → ≥1 |
| 6 | SKILL.md still has hard rules | `grep -c "NEVER" .claude/skills/sdlc/SKILL.md` → ≥2 |
| 7 | PR #356 tests still pass | `pytest tests/unit/test_sdk_client_sdlc.py` → all pass |
| 8 | Observer unchanged | `git diff bridge/observer.py` → empty |

## Success Criteria

1. `/sdlc` SKILL.md is a lean single-stage router under 100 lines (currently 210)
2. No loop/assess-again pattern — worker invokes one sub-skill and returns
3. No 9-stage task list creation upfront
4. No auto-merge logic in `/sdlc` (Observer handles progression)
5. User message format ("sdlc issue 123") still works unchanged
6. Observer (`bridge/observer.py`) has zero changes
7. PR #356 tests still pass (work item 1 compatibility)
8. Hard rules preserved (never commit to main, never skip issue/plan)

## No-Gos

- Do NOT modify `bridge/observer.py` — it already steers correctly
- Do NOT add new Python code for this change — it's a SKILL.md rewrite (markdown)
- Do NOT remove the hard rules (never push to main, never skip issue/plan)
- Do NOT change the user-facing message format ("sdlc issue 123" still works)

## Update System

No update system changes required. The `/sdlc` skill is a markdown file loaded by Claude Code's skill system, not a deployed binary. Changes take effect immediately in new sessions.

## Agent Integration

No agent integration changes required. The `/sdlc` skill is already registered in `.claude/skills/sdlc/SKILL.md` and invoked via Claude Code's skill dispatch. The Observer already steers via coaching messages that reference `/do-*` skills directly — it does not invoke `/sdlc`.

## Documentation

- [x] `docs/features/observer-agent.md` updated in PR #356 (notes Observer is sole pipeline controller)
- [ ] `.claude/skills/sdlc/SKILL.md` is self-documenting (the skill file IS the documentation)
- [ ] Update `CLAUDE.md` SDLC section if it references the old dispatcher pattern
