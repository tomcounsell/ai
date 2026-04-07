---
name: do-plan-critique
description: "Use when reviewing a plan before build. Spawns parallel war-room critics (Skeptic, Operator, Archaeologist, Adversary, Simplifier, User) plus automated structural checks. Triggered by 'critique this plan', 'review the plan', 'war room', or 'do-plan-critique'."
argument-hint: "<plan-path-or-issue-number>"
context: fork
---

# Plan Critique (War Room)

## What this skill does

Critiques a plan document from six expert perspectives plus automated structural validation. Each critic has a defined lens and returns severity-rated findings. The skill aggregates, deduplicates, and produces a verdict: READY TO BUILD, NEEDS REVISION, or MAJOR REWORK.

## When to load sub-files

- Spawning war room critics → read [CRITICS.md](CRITICS.md) for critic definitions and prompt templates

## Quick start

1. Resolve the plan path from `$ARGUMENTS` (issue number or file path)
2. Read the plan and fetch linked issue/prior art context
3. Run automated structural checks (Step 2)
4. Spawn six parallel critics with the plan text (Step 3)
5. Aggregate findings and output the report (Steps 4-5)

## Plan Resolution

Resolve the plan document path from `$ARGUMENTS`:

```bash
ARG="$ARGUMENTS"

# If argument is a number, resolve from GitHub issue
if [[ "$ARG" =~ ^#?[0-9]+$ ]]; then
  ISSUE_NUM="${ARG#\#}"
  PLAN_PATH=$(gh issue view "$ISSUE_NUM" --json body -q '.body' | grep -oP '(?<=docs/plans/)[^\s)]+\.md' | head -1)
  if [ -n "$PLAN_PATH" ]; then
    PLAN_PATH="docs/plans/$PLAN_PATH"
  fi
fi

# If argument is a path, use directly
if [[ "$ARG" == *.md ]]; then
  PLAN_PATH="$ARG"
fi

# Verify plan exists
if [ ! -f "$PLAN_PATH" ]; then
  echo "Plan not found: $PLAN_PATH"
  exit 1
fi
```

## Instructions

### Step 1: Load Context

1. Read the plan document in full
2. If plan references a tracking issue, fetch it: `gh issue view N --json title,body,comments`
3. If plan has a "Prior Art" section, fetch referenced PRs/issues (up to 5):
   ```bash
   gh issue view N --json title,state,body --jq '{title, state}'
   gh pr view N --json title,state,mergedAt --jq '{title, state, mergedAt}'
   ```

### Step 2: Structural Checks (Automated)

Run these checks directly — no LLM needed:

**2a. Required Sections**
Verify these sections exist and are non-empty (per CLAUDE.md):
- `## Documentation`
- `## Update System`
- `## Agent Integration`
- `## Test Impact`

**2b. Task Integrity**
- Check for gaps in task numbering (e.g., 1, 2, 4 — missing 3)
- Verify all `Depends On` references point to valid task IDs
- Check for circular dependencies
- Flag any task with no validation command

**2c. Internal References**
- Extract file paths mentioned in the plan (e.g., `models/agent_session.py`, `bridge/observer.py`)
- Check which ones exist and which don't — report non-existent paths as findings
- Extract test file paths from Test Impact section — verify they exist

**2d. Prerequisite Status**
- For each prerequisite with a check command, run it and report current pass/fail status

**2e. Cross-Reference Consistency**
- Every Success Criterion should map to at least one task
- Every No-Go should not appear in the Solution section as planned work
- Every Rabbit Hole should not appear in the tasks as planned work

Report structural findings with severity:
- Missing required section → BLOCKER
- Task numbering gap → CONCERN
- Invalid dependency reference → BLOCKER
- Non-existent file path → CONCERN (could be intentionally new)
- Orphaned success criterion → CONCERN

### Step 3: War Room (Parallel Critics)

Read [CRITICS.md](CRITICS.md) for the full critic definitions and prompt templates.

Spawn **six critics in parallel** using the Agent tool. Each critic gets:
- The full plan text
- The issue context (if available)
- Prior art summaries (if fetched)
- Their specific lens and instructions from CRITICS.md

**IMPORTANT**: Use `run_in_background: true` for all six. Each critic is a general-purpose Agent with a focused prompt. Use `model: "sonnet"` for each critic — fast enough for 0-3 findings, saves cost.

Each critic returns **0-3 findings** in this format:

```
SEVERITY: BLOCKER | CONCERN | NIT
LOCATION: Section name or line reference in the plan
FINDING: What's wrong (1-2 sentences)
SUGGESTION: How to fix it (1-2 sentences)
```

### Step 4: Aggregate and Deduplicate

After all critics complete:

1. Collect all findings (structural + critic)
2. **Deduplicate**: If two critics flagged the same issue, keep the higher-severity version and note which critics agreed
3. **Sort by severity**: BLOCKERs first, then CONCERNs, then NITs
4. **Cross-validate**: If the Skeptic and Simplifier both flagged the same component, elevate to BLOCKER if not already

### Step 5: Report

Output the final report in this format:

```markdown
# Plan Critique: {plan name}

**Plan**: {plan_path}
**Issue**: #{issue_number} (if applicable)
**Critics**: Skeptic, Operator, Archaeologist, Adversary, Simplifier, User
**Findings**: {N} total ({blockers} blockers, {concerns} concerns, {nits} nits)

## Blockers

### {finding title}
- **Severity**: BLOCKER
- **Critics**: {which critics flagged this}
- **Location**: {section reference}
- **Finding**: {description}
- **Suggestion**: {how to fix}

## Concerns

### {finding title}
...

## Nits

### {finding title}
...

## Structural Check Results

| Check | Status | Detail |
|-------|--------|--------|
| Required sections | PASS/FAIL | ... |
| Task numbering | PASS/FAIL | ... |
| Dependencies valid | PASS/FAIL | ... |
| File paths exist | PASS/FAIL | N of M exist |
| Prerequisites met | PASS/FAIL | ... |
| Cross-references | PASS/FAIL | ... |

## Verdict

{One of:}
- **READY TO BUILD** — No blockers. Concerns are acknowledged risks, not plan defects.
- **NEEDS REVISION** — {N} blockers must be resolved before build.
- **MAJOR REWORK** — Fundamental issues identified. Recommend re-planning.
```

## Outcome Contract

The skill returns a structured verdict that the SDLC pipeline can use:

| Verdict | SDLC Action |
|---------|-------------|
| READY TO BUILD | Proceed to `/do-build` |
| NEEDS REVISION | Return to `/do-plan` with findings |
| MAJOR REWORK | Return to issue discussion |

## What This Skill Does NOT Do

- **Does not rewrite the plan** — output is findings, not a revised document
- **Does not expand scope** — critics flag gaps, they don't suggest features
- **Does not re-architect** — validates internal consistency, not whether a different approach is better
- **Does not block on NITs** — only BLOCKERs prevent a READY TO BUILD verdict

## Version history

- v1.0.0 (2026-03-21): Initial — war room critique with six parallel critics + structural checks
