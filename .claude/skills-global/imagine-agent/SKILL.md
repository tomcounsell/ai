---
name: imagine-agent
description: Use when a non-technical client wants to imagine an AI agent for their product or repo and you need to turn their plain-language goals into a buildable spec. Triggered by 'help a client design an agent', 'what agent should we build for them', 'imagine an agent', 'scope an agent with the client', or 'imagine-agent'. Interviews the client with ONLY end-user/outcome questions, researches the target repo to map goals to technical requirements, and emits a build-sheet that /build-agent launches.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion, Agent
argument-hint: "[repo path or url] [client name]"
---

# Imagine Agent

## What this skill does

The **client-facing front door** to building a managed agent. It talks to a *non-technical* client in
their own language — outcomes, users, what "good" looks like — and never burdens them with model names,
tools, rubrics, or cron syntax. Then it **researches the target repo** to learn what's actually possible
and what house standards exist, and **translates** the client's goals into the technical fields a builder
needs. The output is a `build-sheet.json` it hands to `/build-agent`.

Two skills, one seam: `imagine-agent` writes the build-sheet; `build-agent` reads it. See the contract in
the build-agent skill's `references/build-sheet.md`.

## The core move

`/build-agent`'s own interview asks semi-technical things ("which sources? what schedule? which connector?").
**This skill answers those from research instead of asking the client.** A client goal like *"my users
should get a summary every morning"* becomes — without the client ever seeing it — a model pick, a binary
rubric, a cron deployment, the repo's already-wired Slack connector, and a dedup memory store. The only
things that bounce back to the client are genuinely non-technical ("where do you want to receive it?").

## Phase 1 — Interview the client (non-technical ONLY)

Open warm and short. Ask about the *experience*, never the mechanism. Use `AskUserQuestion` wherever the
answer is enumerable. Capture answers as **verbatim client language** into the build-sheet's `outcome.*`
fields — never paraphrase into jargon.

The only questions allowed here:
- **Who is this for?** (the end users, in the client's words)
- **What should happen for them?** (the outcome they want to exist)
- **What does great look like?** (their definition of done — becomes the rubric, translated later)
- **How often / when?** (cadence — becomes the schedule, translated later)
- **Where do they already work?** (delivery surface — becomes a connector, translated later)

Forbidden in this phase: model, tokens, tools, MCP, rubric, cron, vault, "API". If the client volunteers a
mechanism, note it but keep steering back to the outcome. Raise a boundary only when it actually constrains
their goal — and attach the upgrade path ("we can start with X and add Y next").

## Phase 2 — Research the repo (this is where the value is)

Point research at the target repo (`$ARGUMENTS` path/url, or ask which repo). Read for four things and
record them in `repo_findings`. For a large repo, dispatch parallel `Explore` agents — one per question —
and aggregate; for a small one, read directly.

| What to find | Why it matters | Where to look |
|---|---|---|
| **Capability ceiling** | Bounds what you can promise the client | frameworks, services, data sources, `README`, package manifests |
| **Wired connectors + auth patterns** | Decides v0 (use now) vs v1 (needs credential) delivery | existing API clients, MCP server config, how secrets load (`.env`, settings module) |
| **House standards** | The agent follows existing style, not invented style | `CLAUDE.md`, `ONTOLOGIES.md`, design system, conventions docs |
| **Reusable skills/tools** | Reuse instead of rebuild | `.claude/skills*`, `pyproject.toml`/`package.json` scripts, `tools/` |

This step is what makes `imagine-agent` more than a thin wrapper: it answers the builder's technical
questions from evidence in the repo, so the client never has to.

## Phase 3 — Translate goals → technical requirements

Map each client outcome to a build-sheet technical field, justified by a repo finding:

- `outcome.cadence` → `schedule` (cron + timezone). One-off or event-driven → `schedule: null`.
- `outcome.delivery` → a `connectors[]` entry. If the repo already has that connector wired →
  `status: "wired"`; if not → `status: "mock"` for v0 with the real one as v1, naming the credential gate.
- `outcome.success_looks_like` → 3-6 **binary** `rubric.criteria` (pass/fail, not vibes).
- `outcome.goal` + repo capability → `agent.system` (job + never-dos + "write outputs to
  /mnt/session/outputs/" + relative dates), `agent.tools` (default to the prebuilt toolset),
  `agent.skills` (reuse repo/Anthropic skills found in Phase 2), `environment` (packages/networking).
- Anything not achievable in v0 → `versions.v1`/`v2` with *what / why / how* and the gate named
  (not possible / needs credential / scheduled for later). Be honest about the capability ceiling, but
  don't undersell — name the gate and offer the upgrade path.
- Leave `agent.model: "PICKED-AT-LAUNCH"` — the builder resolves it.

## Phase 4 — Read back, approve, emit, hand off

1. **Read the brief back to the client as scannable outcomes**, not a technical spec: what their users
   will get, how often, where, and what's coming in v1/v2. Approve via `AskUserQuestion`
   (looks right / tweak something). Keep the translation invisible — they approve the *experience*.
2. **Write `./<agent-slug>/build-sheet.json`** following the schema in build-agent's
   `references/build-sheet.md`. Set `meta.ownership = "client_account"` and confirm `meta.workspace`
   (the client's Anthropic workspace) and `meta.repo_url`.
3. **Validate** against that file's checklist.
4. **Hand off:** invoke `/build-agent` with the build-sheet path. From here the technical loop
   (stage → launch → grade → schedule) takes over.

## Anti-patterns

- Asking the client anything technical. If you typed "model", "rubric", "cron", or "MCP" to the client,
  you broke the contract — translate it from research instead.
- Skipping Phase 2. Without repo research you're guessing at the capability ceiling and will over-promise.
- Paraphrasing the client's words into jargon in `outcome.*`. Keep their language verbatim; derive
  everything technical separately.
- Promising real-time / phone / sub-second behavior CMA can't do. Name the ceiling, reshape, offer v1.
- Emitting a build-sheet that fails build-agent's validation checklist. Validate before handoff.
