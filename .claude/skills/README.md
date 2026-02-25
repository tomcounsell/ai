# Skills Index

Human-readable index of all Claude Code skills in this project. Not loaded by Claude — this is for developer reference.

| Skill | Lines | Invocation | Context | Description |
|-------|------:|------------|---------|-------------|
| add-feature | 213 | User + Model | - | Extend the system with new skills, tools, and capabilities |
| agent-browser | 336 | Model only | - | Browser automation for web testing, screenshots, data extraction |
| audit-next-tool | 160 | User + Model | - | Audit new/modified tools for quality and architecture compliance |
| checking-system-logs | 66 | Model only | - | Find bridge events, agent responses, errors in system logs |
| do-build | 115 | User + Model | fork | Execute a plan document using team orchestration |
| do-docs | 268 | User + Model | - | Cascade documentation updates after code changes |
| do-docs-audit | 298 | User + Model | fork | Audit docs against codebase for accuracy |
| do-patch | 204 | User + Model | - | Targeted fix for failing tests or review blockers |
| do-plan | 202 | User + Model | - | Create or update feature plan documents |
| do-pr-review | 218 | User + Model | fork | Review PRs with code analysis and screenshots |
| do-test | 247 | User + Model | - | Run the test suite with intelligent dispatch |
| frontend-design | 134 | User + Model | - | Create production-grade frontend interfaces |
| google-workspace | 249 | Model only | - | Gmail, Calendar, Docs, Sheets, Drive, Chat |
| new-skill | 102 | Infra | - | Generic skill creator (repo-agnostic) |
| new-valor-skill | 184 | User + Model | - | Valor-specific skill creator with repo patterns |
| prepare-app | 306 | User + Model | - | Start services and prepare app for review |
| prime | 102 | Infra | - | Codebase onboarding and architecture guide |
| pthread | 138 | User + Model | fork | Spawn parallel agents for independent tasks |
| reading-sms-messages | 38 | Model only | - | Read SMS/iMessage from macOS Messages app |
| reclassify | 52 | Infra | - | Reclassify plan type during Planning phase |
| sdlc | 284 | User + Model | - | End-to-end autonomous Plan → Build → Test → Review → Ship |
| setup | 407 | Infra | - | Configure new machine for Valor bridge |
| telegram | 68 | Model only | - | Read and send Telegram messages |
| update | 183 | Infra | - | Pull changes, sync deps, restart bridge |

**Invocation types:**
- **User + Model**: Both user and agent can trigger via `/skill-name`
- **Model only**: Agent uses as background reference (`user-invocable: false`)
- **Infra**: Infrastructure skill (`disable-model-invocation: true`)

**Context:**
- **fork**: Spawns sub-agents in separate context
- **-**: Runs in current context

**Scope:**
- Project-only (not synced to `~/.claude/skills/`): telegram, reading-sms-messages, checking-system-logs, google-workspace
- All other skills sync to personal scope via `scripts/update/symlinks.py`

---

## Progressive Disclosure Hierarchy

Claude Code loads context in layers. Understanding which layer loads what is critical for keeping sessions lean and for deciding what to delete vs. keep.

```
Level 0 — System prompt (EVERY session, automatic)
├── Skill names + descriptions from SKILL.md frontmatter YAML
├── 26 skills x ~1 line each = ~26 lines always loaded
└── This is what Claude sees to decide WHICH skill to invoke

Level 1 — SKILL.md body (on-demand, when skill is INVOKED)
├── Full instructions, workflow steps, examples
├── Up to 500 lines per skill, loaded only when Claude invokes it
└── Agent references here determine which agents get spawned

Level 2 — Sub-files (on-demand, when SKILL.md says "Read file:")
├── Templates, scripts, reference docs — loaded by explicit Read calls
└── Only loaded when a skill explicitly references them

Level 3 — Agent definitions (on-demand, when Task tool spawns sub-agent)
├── .claude/agents/*.md — prompt context for sub-agents (25 files)
├── Only loaded when a Task tool call uses that subagent_type
└── Registered in agent_definitions.py: builder, validator, code-reviewer
```

### Skills with Sub-files (Level 2)

| Skill | Sub-files |
|-------|-----------|
| do-build | `WORKFLOW.md`, `PR_AND_CLEANUP.md` |
| do-plan | `PLAN_TEMPLATE.md`, `SCOPING.md`, `EXAMPLES.md` |
| do-skills-audit | `scripts/audit_skills.py`, `scripts/sync_best_practices.py`, `references/anthropic-skill-creator.md`, `references/anthropic-skills-docs.txt` |
| frontend-design | 7 design principle files in `reference/` |
| new-skill | `SKILL_TEMPLATE.md` |

### Agents Referenced by Skills (Level 1 and 2)

| Agent | Where Referenced | Level |
|-------|-----------------|-------|
| `builder` | do-patch/SKILL.md, agent_definitions.py | L1, code |
| `validator` | do-test/SKILL.md, agent_definitions.py | L1, code |
| `code-reviewer` | agent_definitions.py | code |
| `frontend-tester` | do-test/SKILL.md | L1 |
| `test-engineer` | do-test/SKILL.md, do-plan/PLAN_TEMPLATE.md | L1, L2 |
| `documentarian` | do-plan/PLAN_TEMPLATE.md | L2 |
| `plan-maker` | do-plan/PLAN_TEMPLATE.md | L2 |
| `[dynamic]` | do-build reads agent type from plan task list | L1 |

### Agent Roster (25 agents across 3 tiers)

**Tier 1 — Core (7):** Wired into automated SDLC pipeline.
`builder`, `validator`, `code-reviewer`, `test-engineer`, `documentarian`, `plan-maker`, `frontend-tester`

**Tier 2 — Specialists (13):** Genuine unique expertise, recruitable by plans.
`debugging-specialist`, `async-specialist`, `security-reviewer`, `performance-optimizer`, `mcp-specialist`, `agent-architect`, `api-integration-specialist`, `data-architect`, `migration-specialist`, `documentation-specialist`, `test-writer`, `ui-ux-specialist`, `designer`

**Tier 2b — Service agents (5):** Domain-focused task delegation.
`linear`, `notion`, `sentry`, `stripe`, `render`
