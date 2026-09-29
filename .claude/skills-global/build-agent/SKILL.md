---
name: build-agent
description: "Launch, grade, and schedule a Claude Managed Agent from a build-sheet. Use on 'build the agent', 'launch this agent', 'deploy a managed agent'."
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
argument-hint: "[path/to/build-sheet.json or agent-slug]"
---

# Build Agent

Stand up a live Claude Managed Agent (CMA) in the client's Anthropic account from a
build-sheet, scoped to one repo (cloned into each session). It keeps running in the client's
Console after this session ends. The build-sheet comes from `/imagine-agent`, the
non-technical client front door. Without one, run a short technical interview yourself (model,
tools, rubric, schedule, connectors; AskUserQuestion for enumerable choices) and write
`./<slug>/build-sheet.json`.

Done when:
- the build-sheet passes the checklist in [references/build-sheet.md](references/build-sheet.md);
- the agent is launched on a pinned model slug, and a graded run passed, checked by you
  against the output and not only by the grader;
- held-back eval cases ran against the pinned version, with results in
  `evals/results-v<N>.json` (with no golden set yet, the verified winning output becomes
  `evals/case-01/expected`);
- if the agent recurs on a clock, its deployment test-fired and `upcoming_runs_at` was read
  back. If it is event-driven, `NEXT-DIRECTIONS.md` holds the one session-create call their
  backend needs; if on-demand, `LAUNCH.md` re-runs from a clean terminal;
- `agent-overview.html` shows the live IDs, `NEXT-DIRECTIONS.md` lists every deferred item as
  what/why/how by version (including "re-run evals before promoting a new agent version to a
  deployment"), and the hygiene checks below hold.

## Facts

- Read [references/cma-primitives.md](references/cma-primitives.md) and run its smoke test
  before any create call; stop on a 403 there. It has the endpoints, payload shapes, launch
  order, and limits. Auth is
  `x-api-key`, not `Authorization: Bearer`; Bearer returns a confusing 401 on a valid key.
- Resolve `agent.model: "PICKED-AT-LAUNCH"` from `GET /v1/models` to the newest Opus-class
  slug, or Sonnet if the build-sheet flags speed or cost, and pin it.
- Make `LAUNCH.md` resumable: one API call per step, each reading `IDS.env` first and skipping
  objects that already exist. Save every created ID (`ENV_ID`, `AGENT_ID`, `AGENT_VERSION`,
  `VAULT_ID`, `SESSION_ID`, `DEPLOYMENT_ID`) to `IDS.env` as you go.
- Run the first session poll in the foreground and confirm it parses before backgrounding it.
  A silently failing poller wastes the whole wait.
- Read the grader's verdict (`outcome_evaluations[].result` and its explanation) first. Then
  fetch the outputs and grade them yourself against `outcome.md` in a table: criterion,
  verdict, evidence.
- Iterate one change at a time. A sharper rubric means a new session. Instructions, tools, or
  skills mean an agent update (same ID, version bump). A tighter task means editing
  `first_prompt.txt` and kicking off again.
- For the human: narrate each API call in one sentence and checkpoint with Console deep links
  (`platform.claude.com/workspaces/<workspace>/agents/<id>`).

## Constraints

- **Account.** Every object and its bill lands in the account the key belongs to, and that
  must be the client's `meta.workspace`. Confirm the key's workspace with the human before the
  first create call. Never launch on a key from another workspace.
- **Secrets.** The key lives only in `./<slug>/.env` (chmod 600) and never comes from chat. If
  `ANTHROPIC_API_KEY` is already exported and belongs to the right workspace, confirm that and
  reuse it. Otherwise ask once and read the key from the file. `.gitignore` holds `.env` and
  `*.txt`. Never commit `.env` or the client's raw inputs.
- **Scheduled runs replay the same events.** Before creating a deployment, remove every literal
  date from the kickoff, system prompt, and rubric, and use relative dates instead. Test-fire
  with `POST /v1/deployments/:id/run` before trusting cron.
- **Fallbacks.** After two failures on a step, drop one rung and tell the human in one
  sentence:
  1. Re-check the call against the reference and the live docs, then retry once.
  2. Do the step in the Console UI.
  3. Use the closest known-good config.
  4. Build the same design as a local Claude Code workflow plus a `CLAUDE.md`, and make the CMA
     launch v1.

## Working folder

```
./<agent-slug>/
├── build-sheet.json        # source of truth
├── agent.json  environment.json  outcome.md  first_prompt.txt  kickoff.json
├── deployment.json         # if scheduled
├── evals/{case-01/{input,expected}, run-evals.sh, results-v<N>.json}
├── agent-overview.html  NEXT-DIRECTIONS.md  LAUNCH.md
├── IDS.env  .env  .gitignore
```
