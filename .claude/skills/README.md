# Skills Index

Developer reference (not loaded by Claude). This repo has **two** skill trees:

- **Global skills** (`.claude/skills-global/`) — hardlinked to `~/.claude/skills/` on
  every machine; available in all repos. Catalogued in
  [`docs/features/skills-global.md`](../../docs/features/skills-global.md).
- **Project-only skills** (`.claude/skills/`, this directory) — too coupled to this
  repo's infra (Telegram bridge, macOS Messages, system logs, deploy) to generalize;
  **never synced**. Listed below.

See [`docs/features/skill-context-convention.md`](../../docs/features/skill-context-convention.md)
for how a global skill layers in repo-specific behavior, and
[`docs/features/subagent-roster.md`](../../docs/features/subagent-roster.md) for the agents these skills dispatch.

## Project-only skills

| Skill | Invocation | Description |
|-------|------------|-------------|
| authenticity-pass | User + Model | Pre-publish human-signal gate for social content (required by `/linkedin`, `/x-com`) |
| checking-system-logs | Model only | Find bridge events, agent responses, errors in system logs |
| do-deploy | Infra | Deploy merged changes to production across bridge machines |
| ebook-ingest | User + Model | Find, download, and prepare an ebook for AI ingestion |
| linkedin | User + Model | Browse LinkedIn, read/post, comment, check DMs |
| officecli | User + Model | Create, inspect, and edit Office docs (.docx/.xlsx/.pptx) |
| prime | Infra | Codebase onboarding and architecture deep-dive |
| reading-sms-messages | Model only | Read SMS/iMessage from the macOS Messages app |
| sdlc | User + Model | Single-stage router — assess state, dispatch ONE sub-skill, return |
| sentry | User + Model | Check Sentry for unresolved issues and run triage |
| setup | Infra | Configure a new machine for the Valor bridge |
| telegram | Model only | Read and send Telegram messages |
| update | Infra | Pull changes, sync deps, restart the bridge |
| x-com | User + Model | Browse x.com, post, reply, check DMs |

`do-test` also has a directory here, but it is **not** a standalone project skill —
it holds only `PYTHON.md`, a project override (mandating `scripts/pytest-clean.sh`)
that the global `do-test` SKILL.md discovers and merges via the glob at `SKILL.md:28`.
The canonical `do-test` skill lives in `skills-global/`.

**Invocation types:** **User + Model** = triggerable via `/skill-name`;
**Model only** = background reference (`user-invocable: false`);
**Infra** = infrastructure skill (`disable-model-invocation: true`).

---

## Browser surface

There is **one** browser surface in this repo: BYOB MCP
(`mcp__byob__browser_*`). It drives the user's real, logged-in Chrome
via a Chrome extension + native messaging host + MCP server. Public
pages and authenticated dashboards both go through this surface — no
fallback, no anonymous-headless mode. The legacy `agent-browser` and
`bowser` skills were retired in #1256.

**Frontmatter declaration**: skills calling BYOB MCP add the specific
`mcp__byob__browser_*` tools they use to `allowed-tools` (or
`mcp__byob__*` for catch-all access).

**Scheduler gate**: BYOB-driving sessions must have
`AgentSession.requires_real_chrome=True` so the worker scheduler does
not start two real-Chrome sessions concurrently (the active tab is a
single resource). Bridge-spawned sessions get this inferred from the
message text via `agent.byob_skill_triggers.infer_requires_real_chrome`.
CLI-spawned sessions pass `valor-session create --needs-real-chrome ...`.
Skipping this guard lets two BYOB sessions race on the active tab and
corrupt each other's DOM.

**`browser_eval` gate**: BYOB blocks `mcp__byob__browser_eval` by
default for security. Skills that need eval (`mermaid-render`,
`do-discover-paths`) require `BYOB_ALLOW_EVAL=1` in the agent's
environment.

For setup and architecture, see
[`docs/features/byob-browser-control.md`](../../docs/features/byob-browser-control.md).

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
├── .claude/agents/*.md — prompt context for sub-agents (16 files)
├── Only loaded when a Task tool call uses that subagent_type
└── Registered in agent_definitions.py: builder, validator, code-reviewer
```

### Skills with Sub-files (Level 2)

| Skill | Sub-files |
|-------|-----------|
| audit-skills | `scripts/audit_skills.py`, `scripts/sync_best_practices.py`, `references/anthropic-skill-creator.md`, `references/anthropic-skills-docs.txt` |
| do-build | `WORKFLOW.md`, `PR_AND_CLEANUP.md` |
| do-plan | `PLAN_TEMPLATE.md`, `SCOPING.md`, `EXAMPLES.md` |
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

### Agent Roster (16 agents, 2 groups)

Canonical catalog: [`docs/features/subagent-roster.md`](../../docs/features/subagent-roster.md).

**Group A — SDLC pipeline (11):** Dispatched by the `/do-*` skills (and `analyze`).
`builder`, `validator`, `code-reviewer`, `test-engineer`, `baseline-verifier`, `frontend-tester`, `plan-maker`, `plan-reviewer`, `documentarian`, `cruft-auditor`, `strategic-analyst`

**Group B — Service / MCP (5):** Portable per-service agents, dispatched on demand; synced to every machine.
`linear`, `notion`, `sentry`, `stripe`, `render`

The pre-pivot "specialist" pack and generic stubs were deleted as dead weight; their unique framing was salvaged into `do-plan/DOMAIN_FRAMING.md`. See the roster doc for the full rationale.
