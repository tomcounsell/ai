# Claude Managed Agents (CMA) — API Primitives Reference

Vendored and condensed from `anthropics/launch-your-agent` (`cma-primitives.md`) plus the
live API. This is the ground-truth reference `build-agent` reads when staging payloads.
Re-verify against live docs if a call 4xxs: https://platform.claude.com/docs/en/managed-agents/overview

## Authentication (READ THIS FIRST — easy to get wrong)

- **Base URL:** `https://api.anthropic.com/v1`
- **Auth header:** `x-api-key: <key>` — **NOT** `Authorization: Bearer`. Using Bearer returns
  `401 authentication_error` even on a perfectly valid key. (We lost a whole debugging loop to this.)
- **Version header:** `anthropic-version: 2023-06-01`
- **CMA beta header:** `anthropic-beta: managed-agents-2026-04-01`
- **Files API also needs:** `anthropic-beta: files-api-2025-04-14`

Smoke test the key/account before any build:
```bash
# auth works?  -> HTTP 200
curl -s -o /dev/null -w "%{http_code}\n" https://api.anthropic.com/v1/models \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01"
# CMA enabled?  -> HTTP 200 with {"data":[...]}
curl -s https://api.anthropic.com/v1/agents \
  -H "x-api-key: $ANTHROPIC_API_KEY" -H "anthropic-version: 2023-06-01" \
  -H "anthropic-beta: managed-agents-2026-04-01"
```

## The core primitives (explain each the first time it appears)

| Primitive | ID prefix | One-sentence meaning |
|-----------|-----------|----------------------|
| **Agent** | `agent_…` | A reusable, versioned config: model + system prompt + tools + MCP servers + skills. Each config change bumps the version. |
| **Environment** | `env…` | The execution sandbox (cloud or self-hosted). Not versioned; each session gets its own container. |
| **Session** | `sesn_…` | A running agent instance with its own history + sandbox state. |
| **Event** | — | Bidirectional messages. You send `user.*`/`system.*`; you receive `agent.*`/`session.*`/`span.*`. |
| **Outcome** | — | A graded work loop: you give a rubric, an isolated grader scores each iteration pass/fail. |
| **Deployment** | `drun_…` per run | A cron schedule that fires a session every interval with fixed `initial_events`. |
| **Memory store** | `memstore_…` | Cross-session persistence mounted at `/mnt/memory/`. |
| **Vault** | `vlt_…` | Credentials injected at egress so the agent never sees the secret value. |

## Endpoints (the ones build-agent actually calls)

```
# Models
GET    /v1/models                              # pick newest Opus-class slug at launch

# Agents
POST   /v1/agents                              # create
GET    /v1/agents/:id                          # retrieve
PUT    /v1/agents/:id                           # update (pass `version` as concurrency guard)
GET    /v1/agents/:id/versions
POST   /v1/agents/:id/archive

# Environments
POST   /v1/environments
GET    /v1/environments  |  GET /v1/environments/:id
POST   /v1/environments/:id/archive

# Sessions
POST   /v1/sessions                            # provision sandbox
GET    /v1/sessions/:id                        # status + usage + outcome_evaluations[]
POST   /v1/sessions/:id/events                 # send user.*/system.* events
GET    /v1/sessions/:id/events?types[]=…
GET    /v1/sessions/:id/events/stream?beta=true  # SSE — open BEFORE sending events
# NOTE: POST body MUST be {"events": [ <event>, … ]} — a bare event object 400s
#       ("type: Extra inputs are not permitted"). Returns {"data": [ <accepted event> ]}.
POST   /v1/sessions/:id/archive
DELETE /v1/sessions/:id

# Deployments (native cron)
POST   /v1/deployments
GET    /v1/deployments/:id
POST   /v1/deployments/:id/run                 # manual test fire — ALWAYS do this before trusting cron
POST   /v1/deployments/:id/pause | /unpause | /archive
GET    /v1/deployment_runs?deployment_id=…

# Files (outputs)
POST   /v1/files                               # needs files-api beta header
GET    /v1/files?scope_id=<session_id>         # list a session's outputs
GET    /v1/files/:id/content
```

## Payload shapes

### Agent
```json
{
  "name": "string",
  "model": "claude-opus-4-8",
  "system": "job + never-dos + 'write outputs to /mnt/session/outputs/'",
  "tools": [{ "type": "agent_toolset_20260401", "default_config": {"enabled": true} }],
  "mcp_servers": [{ "type": "url", "name": "string", "url": "https://..." }],
  "skills": [{ "type": "anthropic", "skill_id": "pdf" },
             { "type": "custom", "skill_id": "skill_...", "version": "latest" }],
  "description": "string", "metadata": {}
}
```
- `tools` for the prebuilt agent toolset is **exactly** `[{"type": "agent_toolset_20260401"}]`.
- Array fields (`tools`, `mcp_servers`, `skills`) are **full replacements** on update — omit to keep, `[]`/`null` to clear.
- Use **relative dates** in `system` ("today", "last 14 days") — never literal dates if it runs on a schedule.
- `model: "PICKED-AT-LAUNCH"` in the build-sheet; resolve to a real slug from `GET /v1/models` at launch.

### Environment
```json
{ "name": "string",
  "config": { "type": "cloud",
    "packages": ["pip:pandas==2.2.0", "npm:express@4.18.0"],
    "networking": { "type": "limited", "allowed_hosts": ["*.example.com"],
                    "allow_mcp_servers": false, "allow_package_managers": false } } }
```

### Session
```json
{ "agent": "agent_..." | {"type":"agent","id":"agent_...","version":2},
  "environment_id": "env...", "title": "string",
  "vault_ids": ["vlt_..."],
  "resources": [
    { "type": "github_repository", "url": "https://github.com/org/repo",
      "authorization_token": "<gh token for private repos>" },
    { "type": "memory_store", "memory_store_id": "memstore_...", "access": "read_only",
      "instructions": "≤4096 chars" },
    { "type": "file", "file_id": "file_..." } ] }
```
- A git-repo resource is `type: "github_repository"` with `url` (NOT
  `repository_url`); pass `authorization_token` (a `gh` token) to clone a private
  repo. Verified against the live API 2026-07-24; read-back nests the clone under
  `mount_path` (e.g. `/workspace/<repo>`) and redacts the token.
- `resources: [{type: "repository", repository_url}]` clones the target repo into the sandbox — this is the
  hook for "an agent for any repo."

### Events envelope (live sessions) — FOOTGUN #2

Every `POST /v1/sessions/:id/events` body is wrapped in an `events` array. The fields below go
*inside* one array element, never at the top level (a bare object 400s on `type`/`description`).
`user.message` content is a **block array** (Messages-API style), not a bare string:
```json
{ "events": [ { "type": "user.message", "content": [ { "type": "text", "text": "hi" } ] } ] }
```
Smoke-test verified 2026-06-20 (the live launch loop in this repo's `build-agent`).

### Outcome (graded loop) — sent as an event (wrapped, as above)
```json
{ "events": [ { "type": "user.define_outcome", "description": "the task",
  "rubric": { "type": "text", "content": "markdown, explicit per-criterion checks" },
  "max_iterations": 3 } ] }
```
- Returns `{"data":[{... "outcome_id": "outc_…"}]}`. Note: this `events`-wrapper applies only to the
  live events endpoint — the deployment `initial_events` field below is already a bare array of events.
- Results: `satisfied` | `needs_revision` (next cycle) | `max_iterations_reached` | `failed` | `interrupted`.
- `max_iterations` default 3, max 20. Read via `GET /v1/sessions/:id` → `outcome_evaluations[].result`
  (each entry also carries `iteration`, `explanation`, `completed_at`).

### Deployment (cron)
```json
{ "name": "string", "agent": "agent_..." | {...}, "environment_id": "env...",
  "initial_events": [ { "type": "user.define_outcome", "description": "...", "rubric": {...} } ],
  "schedule": { "type": "cron", "expression": "0 9 * * 1-5", "timezone": "America/New_York" },
  "vault_ids": ["vlt_..."], "resources": [...] }
```
- 5-field POSIX cron, minute granularity. Each firing → one session (`drun_…`).
- **Every run replays the same `initial_events`** → the task text must say "today"/"as of this run", never a hard date.

### Vault credential
```
POST /v1/vaults                      # { "display_name": "...", "metadata": {} }
POST /v1/vaults/:id/credentials      # {
                                     #   "auth": {
                                     #     "type": "environment_variable" | "static_bearer" | "mcp_oauth",
                                     #     "secret_name": "GH_TOKEN",
                                     #     "secret_value": "<the token>",
                                     #     "networking": { "type": "limited", "allowed_hosts": ["api.github.com","github.com"] }
                                     #   } }
```
- **Verified against the live API 2026-07-24.** The credential body nests
  everything under an `auth` object: the env-var NAME is `secret_name` (NOT
  top-level `key`), the secret VALUE is `auth.secret_value` (NOT `access_token`),
  and egress scoping is `auth.networking.allowed_hosts` (NOT top-level
  `allowed_hosts`). `injection_location` defaults to `{body:true,header:true}`.
  Read-back redacts the value and shows `auth.{secret_name,networking,injection_location}`.
- `environment_variable` is substituted at egress; the agent never sees the value. Use for GitHub tokens etc.

## Limits worth remembering

| Aspect | Limit |
|--------|-------|
| Create / Read endpoints | 300 / 600 req/min per org |
| Outcome `max_iterations` | default 3, max 20 |
| Skills per session | 20 |
| Memory store | 100 kB, 2,000 memories, 8 stores/session |
| Vault credentials | 20 per vault |
| Deployments per org | 1,000 |
| ZDR / HIPAA BAA | Not eligible (stateful by design) |

## Launch order (the happy path)

1. `GET /v1/models` → pick newest Opus-class slug (Sonnet if speed/cost matters).
2. `POST /v1/environments` → save `ENV_ID`.
3. `POST /v1/agents` (pinned model slug) → save `AGENT_ID`, `AGENT_VERSION`.
4. (if connectors) `POST /v1/vaults` + credentials → save `VAULT_ID`.
5. `POST /v1/sessions` (agent + env + repo resource + vault) → save `SESSION_ID`.
6. `POST /v1/sessions/:id/events` with the `user.define_outcome` kickoff (`max_iterations: 3`),
   wrapped in `{"events": [ … ]}` (see Events envelope above — FOOTGUN #2).
7. Poll `GET /v1/sessions/:id` (run first poll in foreground; confirm it parses before backgrounding).
8. (if recurring) `POST /v1/deployments` + `POST /v1/deployments/:id/run` to test-fire before trusting cron.

Persist every ID to `IDS.env` so the launch sequence is resumable and skips already-created objects.
