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

Every Claude Code session loads ~7.1k tokens of user-controllable overhead (agents, skills, CLAUDE.md) before any work begins. The largest waste is **31 custom agent files** of which only 6 are ever referenced — the other 25 contribute ~750 tokens of dead weight. The do-plan template also advertises 13 agent types that no plan has ever used (~200 tokens).

**Note:** Command/skill triple-listing was resolved by #152 (Skills & Agents Reorganization), which consolidated commands into skills and updated the hardlink system.

**Current behavior:**
~989 tokens of agent overhead for 31 agent files, only 6 referenced.

**Desired outcome:**
~240 tokens of agent overhead (6 files) and a trimmed do-plan template. Combined savings: ~950 tokens.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

This is file deletion and minor template editing. Ship it.

## Prerequisites

- #152 (Skills & Agents Reorganization) — completed. Commands are consolidated into skills, frontmatter is set, hardlinks are updated.

## Solution

### 1. Delete 25 unused agent files (~750 tokens saved)

**Keep these 6 agents** (actively referenced in skills/plans/code):
- `builder` — core SDLC (do-build, do-patch)
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

Also delete `README.md` from agents directory (superseded by `.claude/skills/README.md` from #152).

### 2. Trim do-plan template's "Available Agent Types" list (~200 tokens saved)

The do-plan SKILL.md lists 13 agent types, but every real plan only ever uses `builder`, `validator`, and `documentarian`. Replace the long list with just the types that have agent files.

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

## No-Gos (Out of Scope)

- No CLAUDE.md rewrite (separate task)
- No changes to user-level `~/.claude/commands/` or `~/.claude/skills/`
- No changes to the impeccable plugin
- No changes to built-in system tools
- No skill restructuring (handled by #152)

## Update System

No update system changes required — this is purely local cleanup of agent definitions. The symlinks/hardlink system was already updated by #152.

## Agent Integration

No agent integration required — agent files are prompt hints for the Task tool, not MCP-registered tools. Removing unused ones has no effect on the bridge or tool exposure.

## Documentation

- [ ] Delete `.claude/agents/README.md` (agent index now trivial with only 6 files)
- [ ] No feature doc needed — this is internal cleanup

## Success Criteria

- [ ] Agent count reduced from 31 to 6
- [ ] `/context` shows reduced token counts for Custom agents
- [ ] do-plan template lists only the 6 existing agent types
- [ ] All existing skills still invocable (`/do-build`, `/do-plan`, `/do-test`, etc.)
- [ ] No broken agent references in active skills

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Delete agent files, edit do-plan template
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
- Delete `.claude/agents/README.md`
- Keep: builder, validator, code-reviewer, plan-maker, documentarian, frontend-tester

### 2. Trim do-plan template
- **Task ID**: trim-template
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace "Available Agent Types" section with the 6 active types

### 3. Validate
- **Task ID**: validate-cleanup
- **Depends On**: delete-agents, trim-template
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify no broken agent references in active skills
- Count remaining agents = 6
- Verify do-plan template matches actual agent files

## Validation Commands

- `ls .claude/agents/*.md | wc -l` — should be 6
- `grep -r "agent-architect\|database-architect\|test-engineer" .claude/skills/` — should find no references
- `pytest tests/ -v` — no regressions
