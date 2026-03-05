---
status: Ready
type: bug
appetite: Small
owner: Valor
created: 2026-03-06
tracking: https://github.com/tomcounsell/ai/issues/263
---

# Fix do-build Skill Argument Parsing

## Problem

`/do-build` uses `PLAN_ARG: $1` in its Variables section, but the Claude Code skill system uses `$ARGUMENTS` for argument substitution (not `$1`). This causes the argument to never be substituted, making the skill unable to parse its plan path or issue number.

Other skills (`do-test`, `do-patch`) correctly use `$ARGUMENTS` and work fine.

The fallback instruction tells the agent to extract the argument from the user's original message, but when invoked via the Skill tool programmatically (e.g., from `/sdlc`), the "original message" context is not always available, causing the skill to fail.

**Current behavior:** `/do-build docs/plans/fix-hook-infinite-loop.md` → "No PLAN_ARG was provided"

**Desired outcome:** `/do-build docs/plans/fix-hook-infinite-loop.md` → skill loads the plan and begins building

## Appetite

**Size:** Small (single file, 2-line change)

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites.

## Solution

### Key Elements

- Change `PLAN_ARG: $1` to `PLAN_ARG: $ARGUMENTS` in the do-build SKILL.md
- Update the fallback instruction to reference `$ARGUMENTS` instead of `$1`

### Technical Approach

In `.claude/skills/do-build/SKILL.md`, line 34:

```diff
-PLAN_ARG: $1
+PLAN_ARG: $ARGUMENTS
```

And line 36, update the fallback:

```diff
-**If PLAN_ARG is empty or literally `$1`**: The skill argument substitution did not run.
+**If PLAN_ARG is empty or literally `$ARGUMENTS`**: The skill argument substitution did not run.
```

## Rabbit Holes

- Investigating the Skill tool dispatch vs slash command dispatch — both use `$ARGUMENTS`, so the fix applies universally
- Changing how Claude Code handles argument substitution — that's upstream

## Risks

### Risk 1: Argument format differences
**Impact:** If the Skill tool passes args differently than slash commands, substitution may still fail
**Mitigation:** The fallback instruction already handles this case — the agent extracts the argument from the conversation context

## No-Gos (Out of Scope)

- Changing Claude Code's argument substitution mechanism
- Refactoring all skill argument handling into a unified system

## Update System

No update system changes required — skill files are synced via the standard update process.

## Agent Integration

No agent integration required — this is a skill prompt change, not a tool or MCP server change.

## Documentation

- [ ] No separate documentation file needed — the fix is self-documenting (SKILL.md is both the code and the docs)

## Success Criteria

- [ ] `PLAN_ARG: $ARGUMENTS` in SKILL.md line 34
- [ ] Fallback text references `$ARGUMENTS` not `$1`
- [ ] Consistent with do-test and do-patch skills

## Step by Step Tasks

### 1. Fix PLAN_ARG variable
- **Task ID**: fix-plan-arg
- **Depends On**: none
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Change `PLAN_ARG: $1` to `PLAN_ARG: $ARGUMENTS`
- Change fallback text from `$1` to `$ARGUMENTS`

## Validation Commands

- `grep "PLAN_ARG: \\\$ARGUMENTS" .claude/skills/do-build/SKILL.md` — verify substitution
- `grep "\\\$1" .claude/skills/do-build/SKILL.md` — verify no remaining `$1` references
