# Managed Agents x Valor: Strategic Analysis Report

*April 13, 2026*

---

## Executive Summary

Claude Managed Agents (MA) is Anthropic's fully hosted agent infrastructure, launched in public beta April 2026. This report analyzes how it overlaps with and complements the Valor AI system, identifies what to adopt, what to protect, and what to test.

**Governing thought:** Valor orchestrates. MA executes. Adopt MA for the hands. Keep the brain local. Hosted Redis is the bridge.

---

## 1. What Is Claude Managed Agents?

Managed Agents is a pre-built, configurable agent harness running in Anthropic's managed cloud infrastructure. Unlike the Messages API (which requires building your own agent loop), MA provides:

- **Pre-built agent loop** — Claude autonomously decides when and how to use tools
- **Managed containers** — secure cloud execution with pre-installed packages
- **Session persistence** — conversation history and filesystem state across interactions
- **Real-time streaming** — Server-sent events (SSE) for live agent responses
- **Built-in optimizations** — prompt caching, compaction, efficient tool execution

### Core Resources

| Resource | Purpose | API |
|----------|---------|-----|
| **Agent** | Reusable config: model, system prompt, tools, skills | `POST /v1/agents` |
| **Environment** | Cloud container definition (packages, networking, mounts) | `POST /v1/environments` |
| **Session** | Running instance of an agent within an environment | `POST /v1/sessions` |
| **Vault** | Secret storage, injected as env vars at session start | Referenced via `vault_ids` |

### Key API Patterns

**Create an agent (reusable configuration):**

```python
client = Anthropic()

agent = client.beta.agents.create(
    name="Coding Assistant",
    model="claude-sonnet-4-6",
    system="You are a helpful coding assistant.",
    tools=[{"type": "agent_toolset_20260401"}],
)
```

**Create an environment (persistent, pre-baked):**

```python
environment = client.beta.environments.create(
    name="valor-fullstack",
    config={
        "type": "cloud",
        "networking": {"type": "unrestricted"},
    },
)
```

**Create a session (ephemeral, per-task):**

```python
session = client.beta.sessions.create(
    agent=agent.id,
    environment_id=environment.id,
    vault_ids=[github_vault.id, redis_vault.id],
)
```

**Send events and stream responses:**

```python
client.beta.sessions.events.send(session.id, events=[
    {"type": "user.message", "content": [{"type": "text", "text": "Build the feature"}]},
])

with client.beta.sessions.events.stream(session.id) as stream:
    for event in stream:
        match event.type:
            case "agent.message":
                print(event.content[0].text)
            case "agent.tool_use":
                print(f"[Tool: {event.name}]")
            case "session.status_idle":
                break
```

### Built-in Tools (`agent_toolset_20260401`)

bash, read, write, edit, glob, grep, web_fetch, web_search — individually toggleable via configs.

### Pricing

| Component | Cost |
|-----------|------|
| Input/output tokens | Standard Claude model pricing |
| Session runtime | $0.08 per session-hour (billed per ms, idle is free) |
| Web search | $10 per 1,000 searches |

### Beta Header

All requests require: `anthropic-beta: managed-agents-2026-04-01`

### SDKs

Python, TypeScript, Go, Java, C#, Ruby, PHP, and the `ant` CLI.

---

## 2. SWOT Analysis

### Strengths

| Dimension | Managed Agents | Valor |
|-----------|----------------|-------|
| **Infrastructure** | Zero-ops — Anthropic hosts everything | Full control over runtime, data, execution |
| **Agent loop** | Battle-tested, prompt caching and compaction built-in | Custom loop with steering, nudging, SDLC routing |
| **Session persistence** | Server-side, automatic | Redis-backed with revival detection, steering queue, cross-session context |
| **Tool ecosystem** | 8 built-in tools + custom + MCP | 48+ custom tools + 10+ MCP servers (Sentry, Linear, Notion, Stripe, Render) |
| **Delivery** | API response / SSE stream | Multi-channel — Telegram, email, file logs, with routing logic |
| **Workflow** | Generic agent execution | Full SDLC pipeline (issue, plan, critique, build, test, review, docs, deploy, merge) |
| **Memory** | Research preview only | Production BM25+RRF memory with activation tracking, bloom filters, importance decay |
| **Identity** | Stateless per-session | Persistent persona system with composable segments (PM, Dev, Teammate) |
| **Monitoring** | Managed (opaque) | Watchdog, crash tracker, resource monitor, Sentry, health checks |
| **Multi-project** | Per-agent config | Single harness manages N projects with per-project sequential execution |

### Weaknesses

| Dimension | Managed Agents | Valor |
|-----------|----------------|-------|
| **Customization** | Limited to what the API exposes | Every layer is custom code that must be maintained |
| **Ops burden** | None | Significant — launchd services, watchdogs, Redis, self-healing, multi-machine deploy |
| **Scaling** | Horizontal by default | Single-machine sequential execution per project |
| **Reliability** | Enterprise SLA (presumably) | Self-healing but DIY — crash recovery, hibernation handling, dead letters |
| **Onboarding** | `pip install anthropic` + 10 lines | Deep system knowledge required |
| **Cost transparency** | Clear per-ms billing | Harder to attribute costs |
| **Security** | Sandboxed containers | Runs with full local permissions |

### Opportunities

- **Replace Valor's agent loop**: Offload session execution to MA, eliminating the SDK client + worker process for BUILD/TEST stages
- **Hybrid architecture**: Use MA for stateless/burst work while keeping Valor for stateful orchestration
- **Environment portability**: Managed containers replace fragile local env setup
- **Cost reduction**: Offloading dev sessions to MA = no local compute during long builds
- **Multi-machine elimination**: MA removes the need for `remote-update.sh` and multi-machine sync for execution

### Threats

- **Vendor lock-in**: Deep dependency on Anthropic's infrastructure and pricing decisions
- **Feature parity risk**: As MA matures (memory, multi-agent, outcomes), Valor's custom systems may become redundant maintenance burden
- **Pricing creep**: Long SDLC sessions (hours) at $0.08/hr + tokens could get expensive at scale
- **Opacity**: Can't debug agent internals, compaction, or tool execution
- **Migration cost**: Switching mid-stream is risky — Valor's memory, session state, and SDLC pipeline have no MA equivalent today
- **Capability gap closing**: Telegram/email delivery, SDLC pipeline, and project-aware memory are Valor's moat — but only until Anthropic ships equivalents

---

## 3. The Strategic Split

Valor's differentiation lives in three layers that MA doesn't touch:

1. **Orchestration** — PM/Dev session split, SDLC pipeline, steering queue
2. **Integration** — Telegram/email delivery, 10+ external services, multi-project routing
3. **Memory** — persistent, ranked, decaying memory with bloom-filter recall

MA is a better **execution engine** (sandboxed, scalable, zero-ops). Valor is a better **orchestration layer** (stateful, multi-channel, workflow-aware).

The natural evolution: **Valor as orchestrator, MA as executor**.

---

## 4. Which SDLC Stages to Offload

### BUILD (clear winner)

- Longest-running stage — minutes to hours of autonomous coding
- Self-contained: takes a plan doc in, produces a branch with commits out
- Benefits most from sandboxed containers (no risk of polluting local env)
- MA's built-in tools (bash, read, write, edit, glob, grep) map 1:1 to builder needs
- Valor's worktree isolation solves a problem that MA containers solve natively
- The dev session already runs as a subprocess — swapping the executor is a clean seam

### TEST (strong candidate)

- Runs pytest, analyzes failures, potentially spawns parallel test suites
- Container isolation prevents test side effects on local machine
- Clean environment every time — no port conflicts, no stale state
- Caveat: needs access to Redis and possibly external services for integration tests

### Why NOT the others

| Stage | Why keep local |
|-------|----------------|
| ISSUE | Fast, conversational, needs Telegram context |
| PLAN | Research-heavy but fast; could be a future candidate |
| CRITIQUE | Fast, pure reasoning, no tools needed |
| REVIEW | Security-sensitive, needs local git state, screenshot tooling |
| PATCH | Short, surgical — container overhead isn't worth it |
| DOCS | Fast, needs to read/write in the actual repo |
| MERGE/DEPLOY | Needs local git credentials, SSH keys, `gh` auth |

---

## 5. Environment and Session Model

### Environments persist, sessions don't

Environments are created once and reused. They're analogous to Docker images — pre-baked with all dependencies. Sessions spin up within them per-task, start clean, and are discarded after.

This means:
- No per-session rebuild of Chromium, Playwright, Python deps
- Cold start time depends on environment caching (needs to be tested)
- Filesystem state does NOT carry over between sessions (each starts fresh)

### Recommended environments

| Name | Contents | Used by |
|------|----------|---------|
| `valor-backend` | Python, pytest, Redis client | Backend BUILD/TEST |
| `valor-frontend` | Node.js, Chromium, Playwright | Frontend BUILD/TEST |
| `valor-fullstack` | Both | Mixed work |

---

## 6. Secrets and Authentication

MA uses a **vault** system for secrets. Secrets are injected as environment variables at session start — never in context, never visible to the model.

```python
vault = client.beta.vaults.create(
    name="valor-github",
    secrets={"GITHUB_TOKEN": "ghp_..."}
)

session = client.beta.sessions.create(
    agent=build_agent.id,
    environment_id=fullstack_env.id,
    vault_ids=[vault.id],
)
```

### Secrets Valor's BUILD/TEST stages need

| Secret | Needed in MA? | Notes |
|--------|---------------|-------|
| `ANTHROPIC_API_KEY` | No | MA handles its own auth |
| `GITHUB_TOKEN` | Yes | If the agent pushes branches |
| `REDIS_URL` | Yes | For memory system access |
| `SENTRY_DSN` | No | Not needed during build |
| `TELEGRAM_*` | No | Bridge-only |
| `STRIPE_*`, `LINEAR_*` | Only if MCP tools are enabled | |

### Source of truth

Local `.env` remains canonical for local work. Anthropic vault is canonical for MA sessions. The overlap is small (mostly `GITHUB_TOKEN` and `REDIS_URL`).

---

## 7. Frontend Testing in Containers

### Headless browser capability

MA containers run headless Linux. Chromium runs fine without a display server (same as Docker). The agent can:

1. Install or use pre-baked Playwright/Chromium
2. Start a dev server inside the container
3. Run headless browser tests
4. Take screenshots
5. Analyze them visually (Claude is multimodal)

This may be **more reliable than local** — clean environment every time, no port conflicts, no background processes competing for resources.

### What works where

| Scenario | Container | Local |
|----------|-----------|-------|
| Backend unit/integration | Clean env, hermetic | Works |
| Frontend unit (jsdom/happy-dom) | Clean env | Works |
| Frontend E2E (headless Chromium) | No port conflicts, no state leaks | Port collisions, background noise |
| Screenshot self-validation | Claude reads its own screenshots | Same |
| Mobile native | Not supported | Simulator only |
| Final visual review | Not supported | Valor's REVIEW gate |

### The rule

MA runs tests and takes screenshots for self-validation. Valor's REVIEW gate does the final human-facing visual check before merge. **Never let a managed agent approve its own frontend work.**

---

## 8. The Hard Problem: Subconscious Memory

### How it works locally

Valor's subconscious memory system operates through Claude Code hooks:

1. **PostToolUse hook** fires on every tool call
2. **Bloom filter** checks if the current context might match stored memories
3. If hit, queries Redis for relevant memories via BM25+RRF search
4. Injects `<thought>` blocks into `additionalContext`
5. The agent never decides to recall — it just happens

Post-session, a **Haiku extraction** step saves new categorized observations (corrections, decisions, patterns, surprises) back to Redis with importance scores.

### Why it doesn't port to MA

MA has **no hook equivalent**. The API is request-response: once a model turn starts generating, there is no way to insert tokens into the middle of generation. There are no PostToolUse hooks, no additionalContext injection, no bloom filter checks.

This is the **gating technical risk** for the hybrid architecture.

### The injection spectrum

There are four possible injection points, ordered by granularity:

| Point | Mechanism | Subconscious? | Available in MA? |
|-------|-----------|---------------|------------------|
| **Pre-Session** | Recall memories into system prompt or first message | Yes | Yes |
| **Between Turns** | Send `user.message` when agent is idle | Yes | Yes, but only at turn boundaries |
| **Tool Result** | Append `<thought>` blocks to custom tool results | Yes | Only for custom tools |
| **Mid-Generation** | Inject during model output | Yes (local hooks) | **No** |

### Approaches considered and evaluated

**Approach 1: Conscious tool call (rejected)**

Create a custom `recall` or `think` tool that the agent calls to get memories. The system prompt instructs it to call at decision points.

*Rejected because:* Agent-initiated recall is **conscious, not subconscious**. The agent has to decide to call it. It will forget, skip it under pressure, or call at the wrong times. This contradicts the core design principle.

**Approach 2: Full tool proxying (rejected for now)**

Disable all built-in tools. Re-expose them as custom tools that route through Valor. Every tool call becomes an injection point.

*Rejected because:* This defeats the purpose of MA. If Valor proxies every tool call, the container isn't executing — Valor is. You've rebuilt the agent loop with MA as just the model.

**Approach 3: Stealth injection via workflow tools (recommended)**

The agent MUST call certain tools as part of its workflow: `run_tests`, `git_commit`, `submit_work`. These are defined as custom tools that route through Valor. When the agent calls them, Valor:

1. Executes the actual operation
2. Queries Redis for relevant memories based on the current context
3. Returns the operation result **plus `<thought>` blocks**

The agent sees verbose tool output. It doesn't know some of that output is injected memory. **Subconscious by design.**

This doesn't match the local per-tool-use granularity, but it targets **decision points** — which is where steering actually matters.

**Approach 4: Mid-turn `user.message` injection (needs testing)**

The critical open question: can you send a `user.message` event while a session is in `running` state? If the API accepts and injects it into the current turn, this enables true external steering — the same pattern as Valor's `queued_steering_messages` but over the MA API.

If this works, Valor can watch the SSE stream, observe what the agent is doing, query memory based on context, and inject steering messages between tool calls within a single turn. This would be the cleanest architecture.

**If it doesn't work**, the workflow tool approach (Approach 3) combined with pre-session memory loading is the fallback.

### Recommended architecture

```
Session start:
  1. Valor (local PM) queries memory for task-relevant context
  2. Injects memories into the MA session's first user message
  3. Registers workflow tools (run_tests, git_commit, submit_work) as custom tools

During session:
  4. Built-in tools (bash, read, write, edit) run natively in container
  5. Workflow tools route through Valor for stealth memory injection
  6. (If Approach 4 works) Valor also injects between-turn steering

Session end:
  7. Valor pulls the session transcript via MA API
  8. Runs Haiku extraction locally — saves new memories to Redis
  9. Memories available to ALL future sessions (local or managed)
```

---

## 9. Shared Redis as the Linchpin

If Redis is reachable over the network (hosted Redis like Upstash, Redis Cloud, or a VPS), the MA container connects to the **same memory pool** as local sessions. No code changes — Popoto ORM and memory search just use `REDIS_URL`.

### What this unlocks

| System | What MA gets |
|--------|-------------|
| Memory search | Full BM25+RRF recall — same memories as local |
| Session state | Can read/write AgentSession records |
| Telegram history | Chat logs stored via Popoto — full conversation context |
| Observation extraction | Post-session learning feeds back to shared pool |

### Latency consideration

If hosted Redis is in a different region from the MA container, every memory lookup adds network round-trip time. For occasional recalls this is fine. For per-tool-use checks (if we ever implement full proxying), it could add up. Solution: co-locate Redis and MA containers in the same region, or batch memory pre-fetching at session start.

### Migration path

Moving to hosted Redis is **not contingent on MA adoption**. It unblocks multi-machine memory sharing today and increases strategic value regardless of execution backend. This is a quick win that should happen first.

---

## 10. Quick Wins

### 1. Move to hosted Redis

- Not contingent on MA adoption
- Unblocks multi-machine memory sharing today
- Every improvement to Redis-based memory increases Valor's strategic value regardless of execution backend
- No MA integration code required

### 2. Stop investing in worktree edge cases

- Worktree isolation, process management, crash recovery — all surface area that gets replaced by MA containers
- Redirect effort to orchestration layer and memory recall quality
- The moat is orchestration and memory, not execution

---

## 11. Experiment Pipeline

Sequential experiments, each gating the next:

### Experiment A: Platform Validation

**Goal:** Create an MA environment with Chromium, Playwright, and Python pre-installed.

**Success gate:** Cold start < 30s

**What we learn:** Whether the environment model works for our use case. If cold start is too slow, the hybrid model needs a different seam.

### Experiment B: Tool Cadence Profiling

**Goal:** Run a real BUILD task in MA, profile how many tool calls happen per turn before the agent goes idle.

**Success gate:** Average < 5 calls per turn

**What we learn:** Whether between-turn injection (Approach 4) is frequent enough to be useful. If the agent chains 20 tool calls per turn, between-turn injection is too infrequent and we rely more on workflow tool injection (Approach 3).

### Experiment C: Mid-Turn Injection Test

**Goal:** Send a `user.message` event while a session is in `running` state. Determine if it gets injected into the current turn or queued until idle.

**Success gate:** Mid-turn injection works

**What we learn:** This is the **critical fork**. If mid-turn injection works, we get true external steering and the workflow tool approach becomes a bonus, not the only path. If not, Approach 3 is the primary mechanism.

### Experiment D: Redis Connectivity

**Goal:** Connect an MA container to hosted Redis, verify memory search works with acceptable latency.

**Success gate:** < 50ms p95 latency for memory queries

**What we learn:** Whether the shared brain architecture is viable. If memory queries are too slow from inside a container, we fall back to pre-session-only memory loading.

### Experiment E: Full BUILD Offload

**Goal:** End-to-end proof: pre-fetch memories, run a real BUILD task in MA with stealth injection via workflow tools, extract observations post-session.

**Success gate:** Code quality matches local builds

**What we learn:** Whether the hybrid architecture produces work of the same quality as fully local execution. This is the go/no-go for production adoption.

---

## 12. What to Watch

### Signals from Anthropic

| Signal | Impact on Valor | Urgency |
|--------|----------------|---------|
| **`user.message` during `running` state** | Unlocks true external steering — eliminates need for workflow tool proxying | Test in Experiment C |
| **MA Memory stores (research preview → GA)** | Could replace our Redis-based memory system entirely | Watch closely |
| **MA Multi-agent (research preview → GA)** | Could subsume the PM/Dev session split | Medium |
| **Hook-equivalent API** | Makes subconscious memory fully portable to MA — this is the real unlock | High |
| **Pricing changes** | Long BUILD sessions (hours) at $0.08/hr + tokens could add up at scale | Monitor |
| **Custom skills GA** | Could package Valor's skills for reuse | Low |

### Re-architecture trigger

**Memory stores GA + hook-equivalent API.** When both ship, Valor's subconscious memory system — currently the primary moat — becomes fully portable to MA. At that point, the cost/benefit of maintaining Valor's custom execution infrastructure shifts dramatically, and a deeper migration to MA becomes the right move.

Until then, Valor's memory system is the moat.

---

## 13. Target Hybrid Architecture

```
                    ┌─────────────────────────────┐
                    │        Hosted Redis          │
                    │  (Memory, Sessions, History) │
                    └──────────┬──────────────────┘
                               │
              ┌────────────────┼────────────────┐
              │                │                │
    ┌─────────▼──────┐  ┌─────▼──────┐  ┌──────▼──────┐
    │  Valor (Local)  │  │ MA: BUILD  │  │ MA: TEST    │
    │                 │  │ Container  │  │ Container   │
    │ • PM Session    │  │            │  │             │
    │ • SDLC Router   │  │ • Code     │  │ • pytest    │
    │ • Telegram      │  │ • Commits  │  │ • Playwright│
    │ • Email         │  │ • Browser  │  │ • Coverage  │
    │ • Memory Hooks  │  │            │  │             │
    │ • REVIEW Gate   │  │            │  │             │
    └────────┬────────┘  └─────┬──────┘  └──────┬──────┘
             │                 │                │
             │    dispatch     │   results      │
             ├────────────────►│◄───────────────┤
             │◄────────────────┤                │
             │   branch/PR     │                │
             └─────────────────┴────────────────┘
```

**Flow:**

1. Work arrives via Telegram → Bridge → Worker → PM Session
2. PM runs SDLC pipeline: ISSUE → PLAN → CRITIQUE locally
3. BUILD stage dispatches to MA container with pre-fetched memories
4. MA container executes, workflow tools route through Valor for stealth injection
5. BUILD returns branch → PM runs TEST in MA container
6. TEST returns results → PM runs REVIEW locally (screenshots, visual validation)
7. If review passes → DOCS, MERGE, DEPLOY run locally
8. Post-session extraction saves new memories to shared Redis

---

## Appendix: MA API Reference Summary

### Agents

| Operation | Endpoint | Notes |
|-----------|----------|-------|
| Create | `POST /v1/agents` | Returns `id` and `version` |
| Update | `PATCH /v1/agents/{id}` | Versioned automatically; pass current `version` |
| Retrieve | `GET /v1/agents/{id}` | |
| List | `GET /v1/agents` | |
| Delete | `DELETE /v1/agents/{id}` | |

### Sessions

| Operation | Endpoint | Notes |
|-----------|----------|-------|
| Create | `POST /v1/sessions` | Requires `agent` and `environment_id` |
| Send events | `POST /v1/sessions/{id}/events` | `user.message` or `tool.result` |
| Stream | `GET /v1/sessions/{id}/stream` | SSE stream |
| Retrieve | `GET /v1/sessions/{id}` | Includes status |
| Archive | `POST /v1/sessions/{id}/archive` | Prevents new events |
| Delete | `DELETE /v1/sessions/{id}` | Permanent |

### Session Statuses

| Status | Description |
|--------|-------------|
| `idle` | Waiting for input |
| `running` | Actively executing |
| `rescheduling` | Transient error, retrying |
| `terminated` | Unrecoverable error |

### Tool Configuration

```python
# Full toolset
tools=[{"type": "agent_toolset_20260401"}]

# Selective disable
tools=[{
    "type": "agent_toolset_20260401",
    "configs": [
        {"name": "web_fetch", "enabled": False},
        {"name": "web_search", "enabled": False},
    ],
}]

# Selective enable
tools=[{
    "type": "agent_toolset_20260401",
    "default_config": {"enabled": False},
    "configs": [
        {"name": "bash", "enabled": True},
        {"name": "read", "enabled": True},
    ],
}]

# Custom tool
tools=[{
    "type": "custom",
    "name": "run_tests",
    "description": "Run the project test suite",
    "input_schema": {
        "type": "object",
        "properties": {"command": {"type": "string"}},
        "required": ["command"],
    },
}]
```

### Rate Limits

| Operation | Limit |
|-----------|-------|
| Create (agents, sessions) | 60 req/min |
| Read (retrieve, list, stream) | 600 req/min |

### Research Preview Features (request access)

- **Outcomes** — define success criteria for agent tasks
- **Multi-agent** — orchestrate multiple agents in one session
- **Memory** — persistent agent memory across sessions
