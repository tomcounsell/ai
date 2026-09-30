# The Build-Sheet — handoff contract between `imagine-agent` and `build-agent`

`build-sheet.json` is the **single source of truth** for a managed agent. Every other file
(`agent.json`, `environment.json`, `outcome.md`, `evals/`, `deployment.json`, `agent-overview.html`)
is a *projection* of it. The two skills meet here and only here:

- **`imagine-agent` WRITES it** — from a non-technical client interview + repo research. It fills
  `outcome`, `repo_findings`, and translates those into `agent`/`environment`/`rubric`/`schedule`/`connectors`.
- **`build-agent` READS it** — validates, resolves `PICKED-AT-LAUNCH`, stages payloads, launches, grades.

`build-agent` can also run standalone: if no build-sheet exists, it runs a short technical interview
to populate one itself. The sheet is the contract; the front door is optional.

## Location

Write it to `./<agent-slug>/build-sheet.json` in the working repo (the same folder that will hold
`agent.json`, `evals/`, `IDS.env`, `.env`, etc. — see the working-folder layout in the build-agent SKILL).

## Schema

```json
{
  "meta": {
    "agent_slug": "morning-digest",              // kebab-case; names the working folder
    "client": "Acme Corp",                       // who this is for (their words)
    "repo_url": "https://github.com/org/repo",   // repo cloned into the session as a resource
    "ownership": "client_account",               // always client_account for now (Yudame counts as one)
    "workspace": "acme-prod",                     // which Anthropic workspace the key belongs to
    "created_by": "imagine-agent" | "build-agent"
  },

  "outcome": {                                   // CLIENT WORDS — captured by imagine-agent, never technical
    "goal": "Every user gets a short morning summary of what changed.",
    "users": "Our 40 internal ops staff",
    "success_looks_like": "They stop asking 'what changed overnight' in standup.",
    "cadence": "Every weekday morning",          // becomes schedule
    "delivery": "Wherever they already work — Slack"  // becomes a connector
  },

  "repo_findings": {                             // imagine-agent's research → bounds what's possible
    "frameworks": ["FastAPI", "Postgres"],
    "wired_connectors": ["Slack (slack_sdk, token in env)"],   // already plumbed → v0 candidates
    "auth_patterns": "secrets in .env loaded via config/settings.py",
    "standards": ["CLAUDE.md house style", "ONTOLOGIES.md domain terms"],
    "available_skills": ["pdf", "xlsx"],         // reuse instead of rebuild
    "data_sources": ["the `events` table", "GitHub commits API"],
    "capability_ceiling": "No real-time; batch/cron only. No phone/SMS."
  },

  "agent": {
    "name": "Morning Digest",
    "model": "PICKED-AT-LAUNCH",                 // build-agent resolves from GET /v1/models
    "system": "You are ... . Never ... . Write outputs to /mnt/session/outputs/. Use relative dates (today, last 24h).",
    "tools": [{ "type": "agent_toolset_20260401" }],
    "mcp_servers": [],
    "skills": [{ "type": "anthropic", "skill_id": "pdf" }]
  },

  "environment": {
    "type": "cloud",
    "packages": [],                              // e.g. ["pip:psycopg2-binary"]
    "networking": { "type": "limited", "allowed_hosts": ["*.acme.com", "api.github.com"] }
  },

  "rubric": {                                    // 3-6 BINARY criteria — the grader's checklist
    "criteria": [
      "Summary covers all events from the last 24h window, none older.",
      "No event appears twice.",
      "Output is <= 200 words and posted to the #ops Slack channel.",
      "Tone matches the house style in CLAUDE.md."
    ]
  },

  "schedule": {                                  // null if one-off / event-driven
    "type": "cron",
    "expression": "0 8 * * 1-5",
    "timezone": "America/New_York"
  },

  "connectors": [
    { "name": "Slack", "status": "wired" | "mock" | "v1",   // wired=credential in hand; mock=v0 outbox; v1=deferred
      "vault_field": "SLACK_BOT_TOKEN" }
  ],

  "memory_store": {                              // null if no cross-run state needed
    "name": "digest-dedup",
    "purpose": "Remember which events were already summarized so they aren't repeated."
  },

  "evals": [                                     // case-01 = today's real input (verified later); rest held back
    { "case": "case-01", "input": "<today's real data>", "expected": "<fill from verified winning run>" }
  ],

  "versions": {                                  // everything deferred, with WHY + HOW
    "v0": ["core morning digest on weekday cron, Slack delivery"],
    "v1": ["real Slack credential swap (currently mocked)", "per-user personalization"],
    "v2": ["weekend low-priority digest", "thread replies for follow-up questions"]
  }
}
```

## Field rules

- **`outcome.*` is sacred client language.** `imagine-agent` captures it verbatim; never paraphrase it into
  jargon. Everything technical is *derived* from it, not asked of the client.
- **`PICKED-AT-LAUNCH`** is the only allowed placeholder in `agent.model`. `build-agent` must resolve it.
- **No literal dates anywhere** in `agent.system` or `rubric` if `schedule != null` — the deployment replays
  the same events every run.
- **`connectors[].status`** drives v0 scope: `wired` → use now; `mock` → v0 outbox of schema-true payloads,
  real connector becomes v1; `v1` → don't build in v0, list under `versions.v1` with the gate named.
- **`rubric.criteria`** must be binary (pass/fail), not vibes. The grader is a separate isolated context.

## Validation checklist (build-agent runs this before staging)

- [ ] `meta.agent_slug` is kebab-case and unused as a folder name
- [ ] `meta.ownership == "client_account"` and `meta.workspace` is set
- [ ] `agent.model == "PICKED-AT-LAUNCH"` (will be resolved) or a real slug
- [ ] `agent.system` writes outputs to `/mnt/session/outputs/` and uses relative dates
- [ ] `rubric.criteria` has 3-6 binary items
- [ ] If `schedule != null`: no literal dates in system/rubric; cron is valid 5-field POSIX
- [ ] Every `connectors[].status == "wired"` has a `vault_field` and a plan to load the credential
- [ ] `evals[0].case == "case-01"` exists (expected may be empty until the first verified run)
