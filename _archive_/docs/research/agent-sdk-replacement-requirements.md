# Agent SDK Replacement: Requirements & Considerations

> **Purpose:** Requirements gathering for evaluating drop-in replacements for the Claude Agent SDK (`claude-agent-sdk`).
> **Date:** 2026-04-04
> **Current SDK:** `claude-agent-sdk==0.1.56` + `anthropic==0.89.0` + `mcp>=1.8.0`

---

## 1. Core Runtime Requirements

### 1.1 Async Python Client
- Must be fully async (`await client.query()`, `async for msg in client.receive_response()`)
- Must support concurrent sessions (multiple agents running simultaneously)
- Must be importable as a Python library, not just a CLI wrapper

### 1.2 Streaming Response Protocol
- Must stream intermediate `AssistantMessage` objects (text blocks) as they arrive
- Must emit a final `ResultMessage` with stop_reason, session_id, cost metadata
- Must distinguish between partial output and final completion
- Must support detecting rate limiting vs normal end-of-turn vs error states

### 1.3 Conversation Continuation
- Must support resuming a prior conversation by session/transcript UUID
- Must maintain full conversation history across resume boundaries
- Must prevent cross-session contamination (session A's history leaking into session B)

### 1.4 Error Recovery
- Must allow feeding error messages back into the agent loop for retry
- Must expose error classification (auth error, rate limit, timeout, API failure)
- Must support max-retry configuration with graceful degradation

---

## 2. Tool & MCP Integration

### 2.1 MCP Server Support
- Must support registering external MCP servers (Notion, Sentry, GitHub, Linear, Stripe, etc.)
- Must support MCP protocol >= 1.8.0
- Must allow dynamic tool registration (not just static config)

### 2.2 Built-in Tool Surface
- Must provide or allow registration of core tools: Read, Write, Edit, Bash, Glob, Grep, WebFetch, WebSearch
- Must support Agent tool (spawning sub-agents from within an agent)
- Must support NotebookEdit or equivalent for Jupyter workflows
- Must support TodoWrite or equivalent for task list management

### 2.3 Tool Filtering & Restriction
- Must support per-session tool filtering (e.g., read-only agents get only Read/Glob/Grep)
- Must support per-agent tool definitions (agent definitions with restricted tool lists)
- Must allow blocking specific tools or file paths dynamically via hooks

---

## 3. Hook System (Critical)

### 3.1 Required Hook Types
| Hook | Purpose | Our Usage |
|------|---------|-----------|
| **PreToolUse** | Intercept before any tool executes | Block sensitive file writes, enforce PM read-only, register Dev sessions |
| **PostToolUse** | Execute after any tool completes | Watchdog health checks, stall detection, memory injection |
| **Stop** | Execute when agent signals completion | SDLC branch enforcement, delivery review gate, session logging |
| **SubagentStop** | Execute when a spawned sub-agent completes | Dev session registration, pipeline stage progression, GitHub issue comments |
| **PreCompact** | Execute before context window compaction | Logging, context preservation |

### 3.2 Hook Capabilities
- Hooks must receive tool name, arguments, and session context
- Hooks must be able to **block** tool execution (return deny/error)
- Hooks must be able to **inject context** back into the conversation (additionalContext)
- Hooks must support **matcher patterns** (match specific tools or all tools)
- Hooks must be async Python callables (not shell scripts)
- SubagentStop must provide the sub-agent's output and identity

---

## 4. Agent Orchestration

### 4.1 Sub-Agent Spawning
- Must support spawning child agents from within a parent agent (Agent tool)
- Child agents must support independent tool sets, models, and system prompts
- Must support 30+ agent definitions with distinct personas and capabilities
- Must support `isolation: "worktree"` or equivalent for filesystem isolation

### 4.2 Agent Definitions
- Must support defining agents with: name, description, system prompt, tool list, model override
- Must support loading agent definitions from markdown files (frontmatter + body)
- Must support runtime agent registration (not just static config)

### 4.3 Model Selection
- Must support specifying different models per agent (e.g., Haiku for lightweight tasks, Opus for complex)
- Must support model override at invocation time

---

## 5. Authentication & Billing

### 5.1 Authentication Methods
- Must support API key authentication (`ANTHROPIC_API_KEY`)
- **Strongly preferred:** Support subscription/OAuth authentication (Claude Max plan — zero per-token cost)
- Must support stripping API key to force subscription fallback

### 5.2 Cost Tracking
- Must expose token usage / cost metadata on ResultMessage
- Must support billing attribution per session

---

## 6. System Prompt & Context Engineering

### 6.1 System Prompt Injection
- Must support multi-part system prompts (persona base + overlay + rules + project context)
- Must support dynamic system prompt construction at session creation time
- Must support CLAUDE.md-style project instructions (auto-loaded from working directory)

### 6.2 Environment Variable Injection
- Must support injecting env vars into the agent's execution environment
- Critical vars: SDLC context, GitHub repo, session type, task list ID, chat IDs

### 6.3 Permission Modes
- Must support a "bypass permissions" / YOLO mode (no confirmation prompts)
- Must support restricting write permissions per session type

---

## 7. Session Management

### 7.1 Session Lifecycle
- Must expose session UUID for tracking and continuation
- Must support activity timestamps (last tool call, last output) for stall detection
- Must support external kill/cancel of running sessions
- Must support timeout configuration (both inactivity and hard wall-clock)

### 7.2 Concurrency
- Must support multiple concurrent agent sessions on one machine
- Must handle rate limiting gracefully (backoff, not crash)
- Circuit breaker pattern support (fail fast after N failures in window)

---

## 8. Working Directory & Filesystem

### 8.1 Project Scoping
- Must support setting a working directory per agent session
- Must support git operations within that directory
- Must support worktree isolation (separate git worktrees per work item)

### 8.2 File Access
- Must have unrestricted filesystem access (read/write anywhere, not sandboxed)
- Must support reading images, PDFs, notebooks (multimodal)

---

## 9. Integration Surface

### 9.1 Bridge Compatibility
- Must work as a library called from async Python (Telethon event loop)
- Must not require a separate daemon or server process
- Must support callback-based output delivery (not just return value)

### 9.2 CLI Compatibility
- Should support CLI invocation for local development (equivalent to `claude` CLI)
- Should support `.claude/` directory conventions (settings, hooks, commands, agents)

---

## 10. Non-Functional Requirements

### 10.1 Performance
- Session startup latency: < 5 seconds
- Must not leak memory across long-running sessions (1+ hour sessions are normal)
- Must handle 50+ nudge cycles without degradation

### 10.2 Reliability
- Must recover from transient API failures without losing conversation state
- Must support graceful shutdown (no orphaned processes)
- Must work with launchd service management (auto-restart on crash)

### 10.3 Observability
- Must expose enough metadata for external health monitoring
- Must support logging integration points (session start, tool calls, completion)

---

## 11. Current Coupling Points (Migration Risk)

These are the specific SDK interfaces our code depends on. A replacement must either match these or we must adapt.

| Interface | Location | Risk |
|-----------|----------|------|
| `ClaudeSDKClient` class | `agent/sdk_client.py` | High — central integration point |
| `ClaudeAgentOptions` dataclass | `agent/sdk_client.py` | High — session configuration |
| `AssistantMessage` / `TextBlock` | `agent/sdk_client.py` | Medium — response parsing |
| `ResultMessage` (stop_reason, session_id, is_error) | `agent/sdk_client.py` | High — routing decisions depend on these |
| `HookMatcher` / hook registration | `agent/hooks/__init__.py` | High — 5 hook types, complex logic |
| `AgentDefinition` model | `agent/agent_definitions.py` | Medium — 30+ definitions |
| `continue_conversation` / `resume` params | `agent/sdk_client.py` | High — session continuity |
| `bypassPermissions` mode | `agent/sdk_client.py` | Medium — but critical for autonomy |
| `.claude/` directory conventions | Various | Low — can be adapted |

---

## 12. Evaluation Criteria (Weighted)

| Criterion | Weight | Notes |
|-----------|--------|-------|
| Hook system parity | **Critical** | Without hooks, we lose SDLC enforcement, security, and orchestration |
| Sub-agent spawning | **Critical** | PM→Dev session architecture depends on this |
| Conversation continuation | **Critical** | Session continuity across nudge loops |
| Streaming responses | **Critical** | Real-time activity detection and stall monitoring |
| MCP server support | **High** | 6+ MCP integrations currently active |
| Subscription auth (Max plan) | **High** | Cost difference is significant at our volume |
| Async Python library | **High** | Bridge is async; sync would require major refactor |
| Tool filtering per agent | **Medium** | Nice for security but could be enforced in hooks |
| Agent definitions from files | **Medium** | Could be adapted |
| CLI parity | **Low** | Local dev convenience, not production-critical |

---

## 13. Open Questions for Research Phase

1. **Which frameworks support Claude models?** (vs being OpenAI-only or model-agnostic)
2. **Do any support the Claude subscription/Max plan billing?** (or only API key)
3. **Hook system equivalents?** Most frameworks have middleware/callbacks — how rich are they?
4. **Sub-agent patterns?** Native support vs DIY orchestration?
5. **MCP protocol support?** First-class or via adapter?
6. **Migration effort?** Wrapper/adapter layer vs full rewrite of `sdk_client.py`?
7. **Vendor lock-in?** Model-agnostic frameworks could enable multi-model strategies
8. **Community & maintenance?** SDK maturity, release cadence, breaking changes history
