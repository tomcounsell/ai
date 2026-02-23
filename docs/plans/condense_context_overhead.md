---
status: Planning
type: chore
appetite: Small
owner: Tom
created: 2026-02-23
tracking: https://github.com/tomcounsell/ai/issues/155
---

# Condense Context Overhead

## Problem

Every Claude Code session loads ~7.1k tokens of user-controllable overhead (agents, skills, CLAUDE.md) before any work begins. Much of this is duplicate or unused:

- **31 custom agents** but only 6 are ever referenced in skills/plans
- **Skills appear up to 3x** in `/context` output due to hardlinked command + skill + user copies
- **do-plan template** advertises 13 agent types that no plan has ever used

**Current behavior:**
7.1k tokens of controllable overhead (agents: 989, skills: 3k, CLAUDE.md: 3.1k) on top of 15.1k fixed system tools.

**Desired outcome:**
~4k tokens of controllable overhead — cut nearly in half by removing dead agents, eliminating command/skill duplication, and trimming the do-plan template.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

This is file deletion and minor template editing. Ship it.

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### 1. Delete 25 unused agent files (~750 tokens saved)

**Keep these 6 agents** (actively referenced in skills/plans/code):
- `builder` — core SDLC (do-build)
- `validator` — core SDLC (do-build)
- `code-reviewer` — agent_definitions.py
- `plan-maker` — do-plan subagent
- `documentarian` — documentation tasks
- `frontend-tester` — do-test browser testing

**Delete these 25:**

Rebuild-era (never used in any plan or skill):
- `agent-architect`, `api-integration-specialist`, `async-specialist`, `data-architect`, `database-architect`, `debugging-specialist`, `infrastructure-engineer`, `integration-specialist`, `mcp-specialist`, `migration-specialist`, `quality-auditor`, `test-engineer`, `tool-developer`

Redundant specialists (never referenced):
- `designer`, `documentation-specialist`, `performance-optimizer`, `security-reviewer`, `test-writer`, `ui-ux-specialist`, `validation-specialist`

External service wrappers (MCP tools work without agent files):
- `linear`, `notion`, `render`, `sentry`, `stripe`

Also delete `README.md` from agents directory (not needed).

### 2. Remove project-level commands that duplicate skills (~800 tokens saved)

Six commands exist as thin wrappers that just say "follow the skill." Since the skills are also hardlinked to user-level, these project-level commands create triple-listing.

**Delete from `.claude/commands/`:**
- `do-build.md` (skill exists at `.claude/skills/do-build/`)
- `do-docs.md` (skill exists at `.claude/skills/do-docs/`)
- `do-plan.md` (skill exists at `.claude/skills/do-plan/`)
- `do-pr-review.md` (skill exists at `.claude/skills/do-pr-review/`)
- `do-test.md` (skill exists at `.claude/skills/do-test/`)
- `update.md` (skill exists at `.claude/skills/update/`)

**Keep these commands** (no skill equivalent exists):
- `add-feature.md`, `audit-next-tool.md`, `prepare_app.md`, `prime.md`, `pthread.md`, `sdlc.md`, `setup.md`

**Note:** The user-level commands at `~/.claude/commands/` stay — they're needed for other repos. The duplication drops from 3x to 2x (user command + project skill), which is acceptable.

### 3. Trim do-plan template's "Available Agent Types" list (~200 tokens saved)

The do-plan SKILL.md lists 13 builder agent types, but every real plan only ever uses `builder`, `validator`, and `documentarian`. Replace the long list with just the types that exist.

**Current** (in do-plan SKILL.md):
```
### Available Agent Types
**Builders:**
- builder, designer, tool-developer, database-architect, agent-architect,
  test-engineer, documentarian, integration-specialist
**Validators:**
- validator, code-reviewer, quality-auditor
**Service Agents:**
- github, notion, linear, stripe, sentry, render
```

**Replace with:**
```
### Available Agent Types
- `builder` - General implementation (default for most work)
- `validator` - Read-only verification (no Write/Edit tools)
- `code-reviewer` - Code review, security checks
- `plan-maker` - Planning subagent
- `documentarian` - Documentation updates
- `frontend-tester` - Browser testing
```

## Rabbit Holes

- **Trimming CLAUDE.md** — tempting but separate concern. The CLAUDE.md has important operational info and hooks context. Save for a separate pass.
- **Removing the impeccable plugin** — that's a user-level plugin decision, not a repo change.
- **Reducing system tools (15.1k)** — built into Claude Code, not actionable here.

## Risks

### Risk 1: Deleted agent referenced in future plan
**Impact:** Plan would reference a non-existent agent type; Task tool would fall back to defaults.
**Mitigation:** Agent types are just prompt hints — the Task tool works fine with any `subagent_type` string even without a matching `.md` file. Re-adding an agent file later is trivial.

### Risk 2: Removing project commands breaks invocation for this repo
**Impact:** `/do-build` etc. might not resolve if skill takes precedence differently.
**Mitigation:** Skills are the newer format and take priority. The user-level command also serves as fallback. Test after deletion.

## No-Gos (Out of Scope)

- No CLAUDE.md rewrite (separate task)
- No changes to user-level `~/.claude/commands/` or `~/.claude/skills/`
- No changes to the impeccable plugin
- No changes to built-in system tools

## Update System

No update system changes required — this is purely local cleanup of agent definitions and duplicate commands. The user-level commands remain intact for other repos.

## Agent Integration

No agent integration required — agent files are prompt hints for the Task tool, not MCP-registered tools. Removing unused ones has no effect on the bridge or tool exposure.

## Documentation

- [ ] Update `.claude/agents/README.md` or delete it (if it just lists agents)
- [ ] No feature doc needed — this is internal cleanup

## Success Criteria

- [ ] Agent count reduced from 31 to 6
- [ ] `/context` shows reduced token counts for Custom agents and Skills
- [ ] Project commands reduced from 13 to 7
- [ ] No triple-listed skills in `/context` output
- [ ] do-plan template lists only existing agent types
- [ ] All existing skills still invocable (`/do-build`, `/do-plan`, `/do-test`, etc.)

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Delete files, edit do-plan template
  - Agent Type: builder
  - Resume: true

- **Validator (verify)**
  - Name: cleanup-validator
  - Role: Verify no references broken
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Delete unused agent files
- **Task ID**: delete-agents
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete 25 agent `.md` files listed in Solution section
- Keep: builder, validator, code-reviewer, plan-maker, documentarian, frontend-tester

### 2. Delete duplicate project commands
- **Task ID**: delete-commands
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete 6 command files: do-build.md, do-docs.md, do-plan.md, do-pr-review.md, do-test.md, update.md

### 3. Trim do-plan template
- **Task ID**: trim-template
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace "Available Agent Types" section with the 6 active types

### 4. Validate
- **Task ID**: validate-cleanup
- **Depends On**: delete-agents, delete-commands, trim-template
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify skills still resolve (grep for skill references)
- Verify no broken agent references in active skills/commands
- Count remaining agents = 6

## Validation Commands

- `ls .claude/agents/*.md | wc -l` — should be 6
- `ls .claude/commands/*.md | wc -l` — should be 7
- `grep -r "agent-architect\|database-architect\|test-engineer" .claude/skills/ .claude/commands/` — should find only do-plan template reference (now removed)
