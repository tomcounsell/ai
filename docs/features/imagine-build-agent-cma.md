# Imagine/Build Agent (CMA)

Tracking issue: [#1747](https://github.com/tomcounsell/ai/issues/1747)

## What they are

Two globally-synced, paired skills that let a non-technical stakeholder go from a plain-language
goal to a live Claude Managed Agent (CMA) running in their Anthropic account — without touching
model names, rubrics, or API primitives.

- `/imagine-agent` — the **client-facing front door**. Interviews a non-technical stakeholder
  about outcomes ("what do you want your users to get?") and never burdens them with technical
  fields. Researches the target repo to learn what's actually possible, then translates the
  client's goals into the technical spec a builder needs. Emits a `build-sheet.json`.
- `/build-agent` — the **technical executor**. Consumes the `build-sheet.json` produced by
  `/imagine-agent` (or hand-authored) and runs the create → launch → grade → schedule loop
  against the live Anthropic CMA API. The resulting agent keeps running in the client's Console
  after the session ends.

Both skills live in `.claude/skills-global/` and are synced to `~/.claude/skills/` on every
`/update` run, making them available to agents in any repo on any machine without project-specific
configuration.

## The build-sheet.json contract

`build-sheet.json` is the handoff seam between the two skills. `/imagine-agent` writes it;
`/build-agent` reads it. The schema and validation checklist live in
`.claude/skills-global/build-agent/references/build-sheet.md`.

Key fields the build-sheet carries:

| Field | Who sets it | Notes |
|---|---|---|
| `agent_slug` | imagine-agent | URL-safe identifier for the agent |
| `repo` | imagine-agent | Target repo path or URL |
| `goal` | imagine-agent | Client's outcome in one sentence |
| `model` | imagine-agent (from repo research) | e.g. `claude-sonnet-4-5` |
| `tools` | imagine-agent (from repo research) | Connector list derived from the repo |
| `rubric` | imagine-agent | Binary pass/fail criteria for grading |
| `schedule` | imagine-agent | Cron expression, if applicable |
| `client_name` | imagine-agent | Name for personalized output |

The client never sees or edits this file — it is an internal contract between the two skills.

## Workflow

```
Non-technical client → /imagine-agent
                           │
                     Interviews client (outcomes only)
                     Researches target repo
                     Translates goals → spec
                           │
                    build-sheet.json
                           │
                       /build-agent
                           │
                     Reads build-sheet
                     Stages CMA payloads
                     Launches agent via Anthropic API
                     Grades outcome (binary rubric)
                     Iterates if needed
                     Schedules on cron
                           │
                    Live CMA in client's Anthropic Console
```

## Place in the system

This is a **secondary, client-facing, non-SDLC capability** — entirely distinct from the core
development pipeline (Plan → Critique → Build → Test → Review → Merge) used for this repo's
own work. CMAs are agents deployed *for clients* in *their* Anthropic accounts, not improvements
to the Valor Engels AI system itself.

Use these skills when:
- A non-technical stakeholder wants to create an AI agent for their product or repo
- A client has a plain-language goal that maps to an automatable workflow
- You need to spec, launch, and schedule a managed agent against an external codebase

Do **not** use these skills for:
- Improving or extending the Valor Engels AI system itself (use the SDLC pipeline)
- Spinning up internal automation (use the worker + session system)
- One-off research or summarization tasks (use direct tool calls)

## Update system

No manual wiring is required. Both skills are directories under `.claude/skills-global/`,
the sole sync source for user-level skills. The
`sync_claude_dirs()` function in that module hardlinks every skill directory under
`skills-global/` into `~/.claude/skills/` on every `/update` run. Adding a directory with a
`SKILL.md` to `skills-global/` is sufficient for global propagation — no registration step.

```python
# scripts/update/hardlinks.py (excerpt — relevant logic)
def sync_claude_dirs() -> None:
    """Hardlink every skill dir under .claude/skills-global/ into ~/.claude/skills/."""
    ...
```

No new npm packages, pip dependencies, config files, or environment variables are required.

## Agent integration

Skill-tool only — no `valor-*` CLI entry point, no `pyproject.toml [project.scripts]` entry,
and no bridge import. Both skills are invoked via the Claude Code Skill tool exactly like any
other `/do-*` global skill:

```
/imagine-agent <repo-path-or-url> <client-name>
/build-agent <path/to/build-sheet.json>
```

The `/imagine-agent` skill uses `AskUserQuestion` to interview the client interactively. The
`/build-agent` skill uses `Bash` to call the Anthropic CMA API via `curl`. Neither skill
requires the Telegram bridge, the standalone worker, or any project-specific infrastructure.

## CMA API notes (for /build-agent)

The full API reference lives in
`.claude/skills-global/build-agent/references/cma-primitives.md`. Key footgun: the auth header
is `x-api-key: <key>`, **not** `Authorization: Bearer <key>`. Always read the primitives file
before issuing any `curl` command — the skill enforces this as a preflight step.

## Related files

- `.claude/skills-global/imagine-agent/SKILL.md` — `/imagine-agent` skill (client interview phases, research protocol, build-sheet emission)
- `.claude/skills-global/build-agent/SKILL.md` — `/build-agent` skill (preflight, staging, launch, grade, schedule loop)
- `.claude/skills-global/build-agent/references/cma-primitives.md` — Anthropic CMA API reference (auth, endpoints, payload shapes, launch order, limits)
- `.claude/skills-global/build-agent/references/build-sheet.md` — build-sheet schema and validation checklist
- `scripts/update/hardlinks.py` — hardlink sync that propagates both skills globally
- `docs/features/skills-global.md` — global skills library overview
