# Subagent Roster

The canonical catalog of the Claude Code **subagents** defined in this repo's
`.claude/agents/` directory: what each one is, why it exists, and which skill
dispatches it. Subagents are spawned via the `Task` tool (`subagent_type=...`)
or, inside a workflow, via `agent(..., {agentType: ...})`.

This is the agent-side companion to:
- [`skills-global.md`](skills-global.md) — the skill catalog
- [`skills-dependency-map.md`](skills-dependency-map.md) — how skills and agents wire together
- [`agent-definition-fallback.md`](agent-definition-fallback.md) — what happens when an agent definition is missing or malformed

> **Subagent vs. AgentSession vs. skill.** Three different things share the word
> "agent." A **subagent** (this doc) is a Claude Code `Task`-tool worker scoped to
> one delegated job inside a single session. An **AgentSession** (`session_type`
> eng/teammate) is a top-level bridge/worker session — see
> [`eng-session-architecture.md`](eng-session-architecture.md). A **skill** is a
> reusable procedure (`SKILL.md`) the session follows — see
> [`skills-global.md`](skills-global.md). Skills *dispatch* subagents; subagents
> do not invoke skills.

## How the directory syncs

`.claude/agents/*.md` is hardlinked into `~/.claude/agents/` on every machine by
`scripts/update/hardlinks.py` (`_sync_commands` at the `agents` path). That is why
these subagents are available in **every** repo you open, not just this one —
they are general-purpose by design. Deleting an agent's source `.md` and running
`/update` removes the stale hardlink everywhere via `_cleanup_stale_commands`;
no `RENAMED_REMOVALS` entry is needed for agents (that mechanism is for skills
and commands only).

Each agent file is self-documenting via its frontmatter `description` — that text
is what surfaces in the `Task` tool's agent picker. This doc explains the *why*
and the *relationships* the frontmatter can't.

## The roster (16 agents, two groups)

### Group A — SDLC pipeline agents (11)

Dispatched by the `/do-*` skills as the SDLC pipeline runs. These are the working
agents of this repo's development loop.

| Agent | Why it exists | Dispatched by |
|-------|---------------|---------------|
| `builder` | Executes ONE implementation task at a time — writes code, creates files. The default worker for any change. | `do-build`, `do-patch` |
| `validator` | Read-only verification that work meets acceptance criteria. Has no Write/Edit/NotebookEdit tools by construction, so it cannot "fix" what it is judging. | `do-build`, `do-test`, granite prime roles |
| `code-reviewer` | Correctness, maintainability, security, and project-standards review of a diff. | `do-build`, granite prime roles |
| `test-engineer` | Implements test strategy with real integrations and AI judges (the repo's testing philosophy). | `do-test`, `do-build` (test tasks) |
| `test-baseline` → `baseline-verifier` | Classifies a failing test as a real regression vs. pre-existing by re-running it against `main`. Returns structured JSON. The merge-gate's safety net against false regressions. | `do-test` |
| `frontend-tester` | Drives BYOB MCP (real Chrome) to execute UI test scenarios and return pass/fail with screenshot evidence. | `do-test`, `do-pr-review` |
| `plan-maker` | Produces structured feature plans; the plan-creation subagent for team-orchestration plans. | `do-plan` |
| `plan-reviewer` | Plan critic — challenges and validates a plan before build. Read-only. | `do-plan-critique` |
| `documentarian` | Writes the feature doc with knowledge of the full documentation structure so nothing gets missed. | `do-build`, `do-docs` |
| `cruft-auditor` | Scans a PR diff for legacy patterns that should have been cleaned up — the enforcement arm of the NO LEGACY CODE TOLERANCE principle. | `do-pr-review` (cruft pass) |
| `strategic-analyst` | Runs a multi-dimensional strategic analysis (parallel passes → cross-examine → synthesize → HTML report). The only non-engineering agent here. | `analyze` skill |

### Group B — Service / MCP agents (5)

Portable agents that each wrap a SaaS or MCP integration. They exist so that when
you open a **client repo** that uses one of these services, the agent is already
present (synced via `~/.claude/agents/`) with the right domain framing. They are
not wired into this repo's SDLC pipeline — they are dispatched on demand when a
task targets that service.

| Agent | Domain | Notes |
|-------|--------|-------|
| `linear` | Issue tracking, sprints, cycles, roadmaps | Project-management delegation |
| `notion` | Docs, wikis, knowledge bases, databases | Structured-information delegation |
| `sentry` | Error monitoring, stack-trace triage, release health | Distinct from the project-only `/sentry` *skill*: the skill is this-repo Sentry triage automation (org/project pinned); the agent is portable Sentry expertise for any repo. |
| `stripe` | Payments, subscriptions, billing, revenue analytics | Financial-operations delegation |
| `render` | Cloud infra, deploys, logs, scaling, env vars | Infrastructure-operations delegation |

## Built-in agents you also have

Beyond this repo's roster, the harness ships agents that need no definition file
and are often the right choice — prefer them over writing a thin custom agent:

- **`Explore`** — read-only fan-out search across many files; returns conclusions, not file dumps. Use instead of a custom "scout" agent.
- **`Plan`** — software-architect agent for designing implementation plans (read-only).
- **`general-purpose`** — catch-all for multi-step research and search when no specific agent fits.

## Why the roster is 16 and not 34

The directory once held 34 agents. An audit (this cleanup) found that 18 were
**dead weight** — never referenced by any skill, command, or module:

- A **pre-pivot specialist pack** of 13 (`agent-architect`, `api-integration-specialist`,
  `async-specialist`, `data-architect`, `debugging-specialist`,
  `documentation-specialist`, `mcp-specialist`, `migration-specialist`,
  `performance-optimizer`, `security-reviewer`, `test-writer`, `ui-ux-specialist`,
  `designer`) added in one bulk commit (`071bf6d5`, "Add specialized agents", #27)
  **before** the "use native subagents" pivot. They were only ever *advertised* as
  "recruitable by plans" in `PLAN_TEMPLATE.md`; nothing dispatched them.
- A **stub pack** of 5 (`planner`, `reviewer`, `scout`, `documenter`, `red-team`)
  — ~300-byte generic duplicates of the built-in `Plan`/`code-reviewer`/`Explore`
  and the `documentarian` agent.

None cross-referenced each other (each agent is standalone). They were dispatchable
(a plan could assign a task to one and `do-build` would spawn it), but were used in
only ~1% of plans and degrade gracefully when absent (see
[`agent-definition-fallback.md`](agent-definition-fallback.md)).

Before deletion, the 13 specialist prompts were mined for their genuinely unique,
repo-relevant framing — the narrow set a strong general model wouldn't already
apply — and that signal was salvaged into
[`do-plan/DOMAIN_FRAMING.md`](../../.claude/skills-global/do-plan/DOMAIN_FRAMING.md),
a per-domain cheatsheet that plan authors paste into tasks (tagged `Domain: <tag>`)
and that `do-build` injects into the builder's prompt. So domain expertise is now
handled by prompting a `builder`/`code-reviewer` with that framing — no standing
agent required. (The 5 stub agents and the `designer`/`documentation-specialist`
prompts yielded nothing worth keeping — generic boilerplate or visual-UI content
this repo has no surface for.)

## Adding a new agent

1. Create `.claude/agents/<name>.md` with frontmatter (`name`, `description`,
   optionally `tools`, `model`). Keep the `description` action-oriented — it is the
   picker text.
2. Wire it into whatever skill dispatches it (`subagent_type="<name>"`), or document
   it here as a service/on-demand agent if it has no standing dispatcher.
3. Add a row to the table above and, if it participates in the pipeline, to
   `skills-dependency-map.md`.
4. Run `/update` to hardlink it to `~/.claude/agents/` on this machine; it
   propagates to other machines on their next `/update`.

Do **not** add an agent that merely duplicates a built-in (`Explore`, `Plan`,
`general-purpose`) or an existing roster agent — that was the exact cruft this
roster was cleaned of.
