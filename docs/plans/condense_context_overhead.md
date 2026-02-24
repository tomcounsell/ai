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

Every Claude Code session loads ~7.1k tokens of user-controllable overhead (agents, skills, CLAUDE.md) before any work begins. The largest waste is **32 custom agent files** (31 agents + README) of which only 6 are ever referenced — the other 26 contribute ~750 tokens of dead weight. The do-plan template also advertises 13 agent types that no plan has ever used (~200 tokens).

**Prerequisite PRs now merged:**
- PR #152/#156 (Skills & Agents Reorganization) — commands consolidated into skills, `.claude/commands/` emptied, hardlinks updated, `.claude/skills/README.md` created
- PR #154 (SDLC Enforcement) — hooks, pipeline state, do-patch skill added

**Current behavior:**
32 files in `.claude/agents/` (31 `.md` agents + README), only 3 registered in `agent_definitions.py` (builder, validator, code-reviewer). 3 more referenced by skills (plan-maker, documentarian, frontend-tester). The remaining 26 are dead weight.

**Desired outcome:**
6 agent files + a trimmed do-plan template. Combined savings: ~950 tokens.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

This is file deletion and minor template editing. Ship it.

## Prerequisites

- ✅ #152/#156 (Skills & Agents Reorganization) — merged. Commands consolidated, skills reorganized, `.claude/skills/README.md` created.
- ✅ #154 (SDLC Enforcement) — merged. Hooks, pipeline state, do-patch added.

## Progressive Disclosure Hierarchy (Reference)

Before deleting anything, we must understand what's loaded at each level. Anthropic's progressive disclosure model for skills has 3 levels, plus a 4th for agents:

```
Level 0 — System prompt (EVERY session, automatic)
├── Skill names + descriptions from frontmatter YAML
├── 25 skills × ~1 line each = ~25 lines always loaded
└── This is what Claude sees to decide WHICH skill to invoke

Level 1 — SKILL.md body (on-demand, when skill is INVOKED)
├── Full instructions, workflow steps, examples
├── Up to 500 lines per skill, loaded only when Claude invokes it
└── Agent references here: do-test → "frontend-tester", do-patch → "builder"

Level 2 — Sub-files (on-demand, when SKILL.md says "Read file:")
├── Templates, scripts, reference docs — loaded by explicit Read calls
├── do-build → WORKFLOW.md, PR_AND_CLEANUP.md
├── do-plan → PLAN_TEMPLATE.md, SCOPING.md, EXAMPLES.md
├── frontend-design → 7 design principle files
├── new-skill → SKILL_TEMPLATE.md
├── do-skills-audit (PR #157) → scripts/audit_skills.py, sync_best_practices.py
└── Agent references here: PLAN_TEMPLATE.md → "documentarian", "plan-maker"

Level 3 — Agent definitions (on-demand, when Task tool spawns sub-agent)
├── .claude/agents/*.md — prompt context for sub-agents
├── Only loaded when a Task tool call uses that subagent_type
├── Registered in agent_definitions.py: builder, validator, code-reviewer
└── Referenced by skills: + frontend-tester, documentarian, plan-maker
```

**Tracing all `subagent_type` references across Levels 1-2:**

| Agent | Where Referenced | Level |
|-------|-----------------|-------|
| `builder` | do-patch/SKILL.md, agent_definitions.py | L1, code |
| `validator` | do-test/SKILL.md, agent_definitions.py | L1, code |
| `code-reviewer` | agent_definitions.py | code |
| `frontend-tester` | do-test/SKILL.md (3 refs) | L1 |
| `documentarian` | do-plan/PLAN_TEMPLATE.md | L2 |
| `plan-maker` | do-plan/PLAN_TEMPLATE.md | L2 |
| `test-engineer` | do-test/SKILL.md (**stale — fix**) | L1 |
| `[dynamic]` | do-build reads from plan task list | L1 |

**Key insight**: Only 6 agent files are reachable through any disclosure level. The other 25 are orphaned — no skill at any level references them. Safe to delete.

## Solution

### 1. Delete 26 unused files from `.claude/agents/` (~750 tokens saved)

**Keep these 6 agents** (actively referenced in `agent_definitions.py`, skills, or plans):
- `builder` — registered in `agent_definitions.py`, used by do-build, do-patch
- `validator` — registered in `agent_definitions.py`, used by do-build
- `code-reviewer` — registered in `agent_definitions.py`
- `plan-maker` — used by do-plan
- `documentarian` — used in documentation tasks
- `frontend-tester` — used by do-test for browser testing

**Delete these 25 agent files + README (26 total):**

Rebuild-era (never used in any plan or skill):
- `agent-architect`, `api-integration-specialist`, `async-specialist`, `data-architect`, `database-architect`, `debugging-specialist`, `infrastructure-engineer`, `integration-specialist`, `mcp-specialist`, `migration-specialist`, `quality-auditor`, `test-engineer`, `tool-developer`

Redundant specialists (never referenced):
- `designer`, `documentation-specialist`, `performance-optimizer`, `security-reviewer`, `test-writer`, `ui-ux-specialist`, `validation-specialist`

External service wrappers (MCP tools work without agent files):
- `linear`, `notion`, `render`, `sentry`, `stripe`

Also delete:
- `README.md` from agents directory (superseded by `.claude/skills/README.md` from PR #156)

### 2. Trim do-plan template's "Available Agent Types" list (~200 tokens saved)

The `PLAN_TEMPLATE.md` (moved from SKILL.md to sub-file by PR #156) lists 13 agent types across Builders/Validators/Service Agents categories. Replace with just the 6 that have agent files.

**File:** `.claude/skills/do-plan/PLAN_TEMPLATE.md` (lines 165-183)

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

Also update line 154's example from `[builder | designer | tool-developer | database-architect | etc.]` to `[builder | code-reviewer | documentarian | etc.]`.

### 3. Fix stale references to deleted agents

**`do-test/SKILL.md` line 115:** Uses `subagent_type: "test-engineer"`. Change to `"builder"` — the Task tool falls back to defaults anyway, but using a known type is cleaner.

**`scan_secrets.py` line 66:** Has `security-reviewer.md` in ignore patterns. Remove the line — the file won't exist.

### 4. Update `.claude/skills/README.md` agent count

The README created by PR #156 may reference agent counts. Update to reflect 6 agents.

## Rabbit Holes

- **Trimming CLAUDE.md** — tempting but separate concern. The CLAUDE.md has important operational info and hooks context. Save for a separate pass.
- **Removing the impeccable plugin** — that's a user-level plugin decision, not a repo change.
- **Reducing system tools (15.1k)** — built into Claude Code, not actionable here.
- **Dynamically loading agent definitions** — `agent_definitions.py` currently hardcodes 3 agents. Could be made dynamic but that's overengineering for 6 files.

## Risks

### Risk 1: Deleted agent referenced in future plan
**Impact:** Plan would reference a non-existent agent type; Task tool would fall back to defaults.
**Mitigation:** Agent types are just prompt hints — the Task tool works fine with any `subagent_type` string even without a matching `.md` file. Re-adding an agent file later is trivial.

## No-Gos (Out of Scope)

- No CLAUDE.md rewrite (separate task)
- No changes to user-level `~/.claude/commands/` or `~/.claude/skills/`
- No changes to the impeccable plugin
- No changes to built-in system tools
- No skill restructuring (already handled by #152/#156)
- No changes to `agent_definitions.py` (only 3 agents registered there; the other 3 kept agents are referenced by name in skills)

## Update System

No update system changes required — this is purely local cleanup of agent definitions. The hardlink system was already updated by PR #156.

## Agent Integration

No agent integration required — agent files are prompt hints for the Task tool, not MCP-registered tools. Removing unused ones has no effect on the bridge or tool exposure.

## Documentation

- [ ] Delete `.claude/agents/README.md` (agent index now trivial with only 6 files)
- [ ] Add progressive disclosure hierarchy diagram to `.claude/skills/README.md` — document all 4 levels (L0 system prompt → L1 SKILL.md body → L2 sub-files → L3 agent definitions) with which skills have sub-files and which agents are referenced at each level. This is the canonical reference so future cleanup can trace what's reachable before deleting.
- [ ] No new feature doc needed — this is internal cleanup

## Success Criteria

- [ ] Agent file count reduced from 32 to 6 (`ls .claude/agents/*.md | wc -l` = 6)
- [ ] do-plan `PLAN_TEMPLATE.md` lists only the 6 existing agent types
- [ ] `do-test/SKILL.md` no longer references `test-engineer`
- [ ] `scan_secrets.py` no longer references `security-reviewer.md`
- [ ] All existing skills still invocable (`/do-build`, `/do-plan`, `/do-test`, etc.)
- [ ] No broken agent references in active skills
- [ ] `.claude/skills/README.md` contains progressive disclosure hierarchy diagram
- [ ] `pytest tests/ -v` passes

## Team Orchestration

### Team Members

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Delete agent files, edit templates, fix stale references
  - Agent Type: builder
  - Resume: true

- **Validator (verify)**
  - Name: cleanup-validator
  - Role: Verify no references broken, run tests
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Delete unused agent files
- **Task ID**: delete-agents
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Delete 25 agent `.md` files listed in Solution section 1
- Delete `.claude/agents/README.md`
- Keep only: builder, validator, code-reviewer, plan-maker, documentarian, frontend-tester

### 2. Trim do-plan template and fix stale references
- **Task ID**: trim-and-fix
- **Depends On**: none
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace "Available Agent Types" section in `.claude/skills/do-plan/PLAN_TEMPLATE.md` with the 6 active types
- Update line 154 example agent type list
- Change `subagent_type: "test-engineer"` to `"builder"` in `.claude/skills/do-test/SKILL.md`
- Remove `security-reviewer.md` ignore pattern from `scripts/scan_secrets.py`
- Update `.claude/skills/README.md` if it references agent counts

### 3. Add progressive disclosure hierarchy to skills README
- **Task ID**: add-hierarchy-doc
- **Depends On**: delete-agents
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add a "Progressive Disclosure" section to `.claude/skills/README.md`
- Document all 4 levels (L0→L3) with which skills have sub-files and which agents are referenced
- This becomes the canonical reference for future cleanup decisions

### 4. Validate
- **Task ID**: validate-cleanup
- **Depends On**: delete-agents, trim-and-fix, add-hierarchy-doc
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- `ls .claude/agents/*.md | wc -l` = 6
- `grep -rn "agent-architect\|database-architect\|test-engineer\|quality-auditor\|security-reviewer\|tool-developer\|designer\b" .claude/skills/ scripts/` — should find no references to deleted agents
- Verify do-plan `PLAN_TEMPLATE.md` "Available Agent Types" lists exactly 6 types
- `pytest tests/ -v` — no regressions

## Validation Commands

- `ls .claude/agents/*.md | wc -l` — should be 6
- `grep -rn "agent-architect\|database-architect\|test-engineer\|quality-auditor\|security-reviewer" .claude/skills/ scripts/` — should find no references
- `pytest tests/ -v` — no regressions
