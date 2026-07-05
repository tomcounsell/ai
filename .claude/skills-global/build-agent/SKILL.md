---
name: build-agent
description: Stage, launch, grade, and schedule a Claude Managed Agent (CMA) from a build-sheet. Triggered by 'build the agent', 'launch this agent', 'deploy a managed agent', or as /imagine-agent's handoff.
allowed-tools: Read, Write, Edit, Glob, Grep, Bash, AskUserQuestion
argument-hint: "[path/to/build-sheet.json or agent-slug]"
---

# Build Agent

Also fires on 'build-agent' or any request to stand up a Claude Managed Agent in a client's
Anthropic account, scoped to a specific repo.

## What this skill does

Takes a **build-sheet** (the contract written by `/imagine-agent`, or one this skill produces itself)
and stands up a live **Claude Managed Agent** in the client's Anthropic account: stage payloads →
launch the agent → run a graded outcome → iterate → schedule it on cron. The agent is scoped to a
repo (cloned into the session) and keeps running in the client's Console after this session ends.

This is the technical half of the pair. The non-technical client interview lives in `/imagine-agent`.

## When to load sub-files

- Need the CMA API (auth header, endpoints, payload shapes, launch order, limits) → read
  [references/cma-primitives.md](references/cma-primitives.md). **Always read this before any curl** —
  the auth header (`x-api-key`, not Bearer) is the #1 footgun.
- Need the build-sheet schema / validation checklist → read [references/build-sheet.md](references/build-sheet.md).

## Preflight — do this first, every time

1. **Read `references/cma-primitives.md`.** Don't curl from memory.
2. **Smoke-test the key + CMA access** (substitute the client's key — see Account model below):
   ```bash
   curl -s -o /dev/null -w "auth %{http_code}\n" https://api.anthropic.com/v1/models \
     -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01"
   curl -s -o /dev/null -w "cma  %{http_code}\n" https://api.anthropic.com/v1/agents \
     -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \
     -H "anthropic-beta: managed-agents-2026-04-01"
   ```
   Both must be `200`. A `401` is almost always the wrong auth header (must be `x-api-key`) or a dead key.
   A `403` on `/v1/agents` means the account lacks the managed-agents beta — stop and tell the human.
3. **Locate the build-sheet.** If `$ARGUMENTS` points to one, read it. If it names a slug, read
   `./<slug>/build-sheet.json`. If none exists, run the **standalone interview** (below) to create one.
4. **Validate** the build-sheet against the checklist in `references/build-sheet.md`. Fix or ask before staging.

## Account model (client_account)

Every CMA lives in **one** Anthropic account — for us that's always the *client's* account
(`meta.ownership == "client_account"`; Yudame is just one such client). Before launch, confirm which
key/workspace this is:

- The key must be the client's, scoped to `meta.workspace`. Never launch a client's agent on a key from
  a different workspace — the agent, its sessions, and its bill all land wherever the key points.
- Source the key from a `.env` in the working folder (chmod 600), never from chat. If `ANTHROPIC_API_KEY`
  is already exported and belongs to the right workspace, confirm that and reuse it; otherwise ask once,
  in one small table (step · where · what to do), and read it from the file.

## The build loop

Run the launch order from `references/cma-primitives.md`. Narrate each call in one sentence and checkpoint
with emoji + Console deep links (`platform.claude.com/workspaces/<workspace>/agents/<id>`).

### 1. Stage everything (no key needed yet)
- Project the build-sheet into `agent.json`, `environment.json`, `outcome.md`, `first_prompt.txt`,
  `deployment.json` (if `schedule != null`), and `evals/`.
- Validate every JSON payload parses.
- Write `LAUNCH.md` — a resumable, one-API-call-per-step sequence; each step reads `IDS.env` first and
  skips objects that already exist.
- Create `.gitignore` containing `.env` and `*.txt`. Never commit secrets or the client's raw inputs.

### 2. Launch
- `GET /v1/models` → resolve `agent.model` from `PICKED-AT-LAUNCH` to the newest Opus-class slug
  (Sonnet if the build-sheet flags speed/cost).
- Create environment → agent (pinned slug) → vault (if connectors) → session (with the repo as a
  `resources` entry) → send the `user.define_outcome` kickoff with `max_iterations: 3`.
- Save `ENV_ID`, `AGENT_ID`, `AGENT_VERSION`, `VAULT_ID`, `SESSION_ID` to `IDS.env` as you go.
- Checkpoint: `✅ 📦 env env_…` / `✅ 🤖 agent agent_… (v1, <slug>)` / `✅ ▶️ run sesn_…`.
- Poll `GET /v1/sessions/:id` — run the **first poll in the foreground** and confirm it parses before
  backgrounding. A silently-failing poller wastes the whole wait.

### 3. Grade & iterate ("test the agent")
- When the run idles, read the grader verdict (`outcome_evaluations[].result` + explanation) **first**.
- Fetch outputs (`GET /v1/files?scope_id=$SESSION_ID`) and grade them yourself against `outcome.md` and
  the known-good answer. Present a table: criterion | verdict | evidence. Read the output — don't just
  relay the grader.
- Iterate by changing **one thing** (sharper rubric → new session; instructions/tool/skill → agent
  update, same ID, bump version; tighter task → edit `first_prompt.txt`, re-kickoff).
- Once a version passes, fire the held-back eval cases (`evals/run-evals.sh`: one session per case,
  pinned agent version) and collect verdicts into `evals/results-v<N>.json`.
- No golden set yet? Save the verified winning output as `evals/case-01/expected` now — that's the baseline.

### 4. Schedule (only if it repeats on a clock)
- Re-read the kickoff for literal dates first — a scheduled deployment replays the same events every run.
- `POST /v1/deployments` (cron + timezone + kickoff as `initial_events`), then `POST /deployments/:id/run`
  to test-fire **before** trusting cron. Read back `upcoming_runs_at`. Save `DEPLOYMENT_ID`.
- Event-driven instead? Put the single `POST /v1/sessions` + kickoff curl their backend needs into
  `NEXT-DIRECTIONS.md`. On-demand? `LAUNCH.md` is the interface — confirm it re-runs from a clean terminal.

### 5. Close out
- Regenerate `agent-overview.html` with live IDs (status ● Launched).
- Finalize `NEXT-DIRECTIONS.md`: every deferred item as *what / why / how*, slotted into v1, v2, …,
  including "re-run evals before promoting any new agent version to a deployment."
- Hygiene sweep: key only in `.env`, no literal dates in any deployment, eval case-01 saved, client raw
  inputs never committed.

## Standalone interview (no build-sheet present)

If invoked without a build-sheet, run a **short technical interview** to populate one yourself
(model/tools/rubric/schedule/connectors), using `AskUserQuestion` for enumerable choices. This is the
fast path for a technical operator who doesn't need the non-technical front door. The output is still a
`build-sheet.json` validated against `references/build-sheet.md` — then continue the build loop above.

## Working folder layout

```
./<agent-slug>/
├── build-sheet.json        # source of truth (from imagine-agent or standalone interview)
├── agent.json  environment.json  outcome.md  first_prompt.txt  kickoff.json
├── deployment.json         # if scheduled
├── evals/{case-01/{input,expected}, run-evals.sh, results-v<N>.json}
├── agent-overview.html  NEXT-DIRECTIONS.md  LAUNCH.md
├── IDS.env                 # every created object's ID (resumable launch)
├── .env                    # chmod 600, client's API key, never committed
└── .gitignore              # contains .env, *.txt
```

## Fallbacks (drop one rung after two failures on a step; tell the human in one sentence)

1. Re-check the failing call against `references/cma-primitives.md` and live docs; fix, retry once.
2. Do the same step in the Console UI.
3. Use the closest known-good archetype config.
4. CMA unreachable → build the same design as a **local** Claude Code workflow + `CLAUDE.md` so the
   client still leaves with a working assistant; the CMA launch becomes v1.

## Anti-patterns

- Curling with `Authorization: Bearer` — Anthropic uses `x-api-key`. Wrong header → confusing 401s.
- Launching on the wrong workspace's key — the agent and its bill land in the wrong account.
- Hard-coded dates in a scheduled agent — every run replays them; always use relative dates.
- Trusting the grader without reading the output yourself.
- Trusting cron without a manual `POST /deployments/:id/run` test fire.
- Committing `.env` or the client's raw input data.
