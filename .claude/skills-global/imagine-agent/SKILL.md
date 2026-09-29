---
name: imagine-agent
description: "Turn a client's goals into a build-sheet for /build-agent. Use on 'imagine an agent', 'what agent should we build for them'."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Agent
argument-hint: "[repo path or url] [client name]"
---

# Imagine Agent

The client-facing front door to a Claude Managed Agent. Interview a non-technical client about
the experience they want, research their repo to learn what is possible and what house
standards exist, then translate their goals into a `build-sheet.json` that `/build-agent`
launches. The client answers only non-technical questions. Everything technical (model,
rubric, schedule, connectors, memory) is derived from their words plus evidence in the repo.

Done when:
- the client approved a plain-language read-back: what their users get, how often, where, and
  what comes in v1/v2;
- `./<agent-slug>/build-sheet.json` passes the checklist in
  `../build-agent/references/build-sheet.md` (schema and field rules are there too), with
  `meta.ownership = "client_account"`, and `meta.workspace` and `meta.repo_url` confirmed;
- you have invoked `/build-agent` with its path.

## Constraints

- **Ask the client only about outcomes:** who it is for, what should happen for them, what
  great looks like, how often, and where they already work. Never say model, tokens, tools,
  MCP, rubric, cron, vault, or "API" to them. If they volunteer a mechanism, note it and steer
  back to the outcome. Use AskUserQuestion for enumerable answers.
- **Keep their words verbatim** in `outcome.*`. Derive the technical fields separately; never
  paraphrase their language into jargon.
- **Research before promising.** The repo bounds what you can offer. Don't promise real-time,
  phone, or sub-second behavior that CMA can't deliver. Name the ceiling, reshape the goal, and
  offer the upgrade path.

## Research (`$ARGUMENTS` repo, or ask which one)

Record in `repo_findings`:
- the capability ceiling (frameworks, services, data sources);
- the connectors already wired and how secrets load;
- house standards (`CLAUDE.md`, conventions docs, design system);
- reusable skills and tools.

For a large repo, dispatch parallel Explore agents, one per question, and aggregate.

## Translation

- cadence → `schedule` (cron and timezone), or `null` for one-off or event-driven work.
- delivery → a `connectors[]` entry: `wired` if the repo already has it, otherwise `mock` for
  v0 with the real connector as v1 and its credential gate named.
- "what great looks like" → 3-6 binary `rubric.criteria`.
- goal plus capability → `agent.system` (the job, the never-dos, "write outputs to
  /mnt/session/outputs/", relative dates), `agent.tools` (the prebuilt toolset),
  `agent.skills` (reuse what research found), and `environment`.
- Anything not achievable in v0 goes under `versions.v1`/`v2` as what/why/how, with its gate
  named.
- Leave `agent.model: "PICKED-AT-LAUNCH"`.
