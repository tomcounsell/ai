# Claude Agent SDK Migration Plan

**Status**: Planning
**Created**: 2026-01-21
**Goal**: Replace Clawdbot with Claude Agent SDK so Valor's engineering work is equivalent to Claude Code

---

## Executive Summary

Migrate from Clawdbot (third-party tool) to the official Claude Agent SDK (formerly Claude Code SDK) to:
1. Eliminate third-party dependency
2. Get identical capabilities to Claude Code CLI
3. Have full control over agent behavior, tools, and permissions
4. Simplify architecture and reduce moving parts

---

## Current Architecture (Clawdbot)

```
Telegram
    ↓
Python Bridge (telegram_bridge.py)
    ↓
subprocess: clawdbot agent --local --json
    ↓
Clawdbot Gateway → Anthropic API
    ↓
~/clawd/skills/ (JS-based tools)
    ├── sentry/    (8 tools)
    ├── github/    (10 tools)
    ├── linear/    (9 tools)
    ├── notion/    (8 tools)
    ├── stripe/    (9 tools)
    ├── render/    (9 tools)
    ├── daydream/  (cron)
    └── link-summarization/
```

**Problems with current setup:**
- Third-party dependency (clawd.bot)
- Tool execution logs leak to stdout (we had to filter/use --json)
- Skills are JS-based, separate from our Python codebase
- No direct control over agent system prompt, tools, permissions
- Session management is opaque
- Different behavior than Claude Code CLI

---

## Target Architecture (Claude Agent SDK)

```
┌─────────────────────────────────────────────────────────────────┐
│                    Standalone MCP Servers                        │
│         (Available to Claude Code, Valor, and other clients)     │
│                                                                  │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐            │
│  │  Sentry  │ │  GitHub  │ │  Linear  │ │  Notion  │  ...       │
│  │  Server  │ │  Server  │ │  Server  │ │  Server  │            │
│  └────┬─────┘ └────┬─────┘ └────┬─────┘ └────┬─────┘            │
│       │            │            │            │                   │
└───────┼────────────┼────────────┼────────────┼───────────────────┘
        │            │            │            │
        └────────────┴─────┬──────┴────────────┘
                           │
        ┌──────────────────┴──────────────────┐
        │                                      │
        ▼                                      ▼
┌───────────────────┐                ┌───────────────────┐
│   Claude Code     │                │      Valor        │
│   (direct use)    │                │  (via Agent SDK)  │
│                   │                │                   │
│ Built-in tools:   │                │ Built-in tools:   │
│ Read, Write, Edit │                │ Read, Write, Edit │
│ Bash, Grep, Glob  │                │ Bash, Grep, Glob  │
│ WebSearch, Task   │                │ WebSearch, Task   │
│                   │                │                   │
│ + MCP connections │                │ + MCP connections │
│   to servers above│                │   to servers above│
│                   │                │                   │
│                   │                │ + Valor-specific: │
│                   │                │   telegram_history│
│                   │                │   link_analysis   │
│                   │                │   session_mgmt    │
└───────────────────┘                └───────────────────┘
```

**Key Architecture Decisions:**

1. **Coding tools** (Read, Write, Edit, Bash, etc.) - Built into Claude Code/Agent SDK only
   - These are for software engineering tasks
   - Available when running Claude Code or Valor (via Agent SDK)

2. **Service integrations** (Sentry, GitHub, Linear, Notion, Stripe, Render) - Standalone MCP servers
   - Can be used by Claude Code directly
   - Can be used by Valor via Agent SDK
   - Could be used by other clients (web UI, CLI tools, etc.)
   - May have different calling patterns depending on context

3. **Valor-specific tools** (telegram_history, link_analysis) - In-process with Agent SDK
   - Only relevant to Valor's Telegram integration
   - Not useful for general Claude Code sessions

**Benefits:**
- Same capabilities as Claude Code CLI for coding tasks
- Service integrations reusable across different contexts
- Full control over system prompt, tools, permissions
- Hooks for permission control and logging
- Session state management built-in
- Subagent definitions for specialized tasks

---

## Claude Agent SDK Capabilities

### Built-in Tools (same as Claude Code)
- **File Operations**: Read, Write, Edit, Glob, Grep
- **Code Execution**: Bash (with background support)
- **Web**: WebSearch, WebFetch
- **Notebooks**: NotebookEdit
- **Task**: Spawn subagents

### Custom Tools
```python
from claude_agent_sdk import tool, create_sdk_mcp_server

@tool("sentry_list_issues", "List Sentry issues", {"project": str, "status": str})
async def sentry_list_issues(args: dict) -> dict:
    # Direct API call - no subprocess
    response = await httpx.get(f"{SENTRY_API}/projects/{args['project']}/issues/")
    return {"content": [{"type": "text", "text": json.dumps(response.json())}]}
```

### Session Management
```python
async with ClaudeSDKClient(options=options) as client:
    await client.query(message)
    async for msg in client.receive_response():
        yield msg  # Stream to Telegram
```

---

## Migration Phases

### Phase 1: SDK Setup & Basic Integration
**Goal**: Replace clawdbot subprocess with Claude Agent SDK

**Tasks**:
1. Install claude-agent-sdk: `pip install claude-agent-sdk`
2. Create `agent/sdk_client.py` with basic ClaudeSDKClient wrapper
3. Update `telegram_bridge.py` to use SDK instead of subprocess
4. Configure system prompt from SOUL.md
5. Test basic message flow (coding tools only - Read, Write, Bash, etc.)

**Deliverables**:
- [ ] SDK installed and importable
- [ ] Basic agent wrapper class
- [ ] Bridge uses SDK for responses
- [ ] System prompt loaded from config
- [ ] Built-in coding tools working

### Phase 2: Standalone MCP Servers - Core Services
**Goal**: Build Sentry, GitHub, Linear as standalone MCP servers usable by any client

**Architecture**: Each service becomes a standalone MCP server that can be:
- Connected to Claude Code via `~/.claude/settings.json`
- Connected to Valor via Agent SDK mcp_servers config
- Run as HTTP/SSE server for other clients

**Tasks**:
1. Create `mcp/servers/` directory for standalone MCP servers
2. Build Sentry MCP server (8 tools):
   - list_issues, get_issue, list_events, get_event
   - list_projects, get_performance_data
   - update_issue_status, resolve_issue
3. Build GitHub MCP server (10 tools):
   - list_prs, get_pr, create_pr, merge_pr
   - list_issues, create_issue, get_commits
   - get_checks, search_code, get_file
4. Build Linear MCP server (9 tools):
   - list_issues, get_issue, create_issue, update_issue, close_issue
   - list_cycles, get_team_velocity, search_issues, get_roadmap
5. Test each server standalone
6. Configure Claude Code to use these servers
7. Configure Valor to use these servers

**Deliverables**:
- [ ] mcp/servers/sentry/ (standalone MCP server)
- [ ] mcp/servers/github/ (standalone MCP server)
- [ ] mcp/servers/linear/ (standalone MCP server)
- [ ] Claude Code settings configured
- [ ] Integration tests for each server
- [ ] Tests for both coding and non-coding use cases

### Phase 3: Standalone MCP Servers - Secondary Services
**Goal**: Build Notion, Stripe, Render as standalone MCP servers

**Tasks**:
1. Build Notion MCP server (8 tools):
   - search, get_page, create_page, update_page
   - append_blocks, list_databases, query_database, create_database_entry
2. Build Stripe MCP server (9 tools):
   - list_customers, get_customer, list_subscriptions, get_subscription
   - list_invoices, create_refund, get_balance, get_mrr, cancel_subscription
3. Build Render MCP server (9 tools):
   - list_services, get_service, get_service_logs, deploy_service
   - restart_service, scale_service, list_deploys, get_env_vars, update_env_vars
4. Test each server standalone
5. Configure all clients to use these servers

**Deliverables**:
- [ ] mcp/servers/notion/ (standalone MCP server)
- [ ] mcp/servers/stripe/ (standalone MCP server)
- [ ] mcp/servers/render/ (standalone MCP server)
- [ ] Integration tests
- [ ] Tests for different use case contexts

### Phase 3b: Valor-Specific Tools
**Goal**: Migrate Valor-only tools as in-process tools (not standalone MCP)

These tools are specific to Valor's Telegram integration and don't need to be
shared with Claude Code sessions.

**Tasks**:
1. Wrap existing Python tools for Agent SDK:
   - telegram_history (search, store, get_recent)
   - link_analysis (extract_urls, summarize, metadata)
2. Create in-process MCP server for Valor-specific tools

**Deliverables**:
- [ ] agent/valor_tools.py (in-process tools)
- [ ] Integration with Agent SDK

### Phase 4: Session & Context Management
**Goal**: Implement proper session handling for conversation continuity

**Tasks**:
1. Create session manager class
2. Implement session storage (SQLite or file-based)
3. Handle session resumption for reply-based continuity
4. Implement context compaction for long sessions
5. Add session cleanup/expiry

**Deliverables**:
- [ ] agent/session_manager.py
- [ ] Session persistence
- [ ] Reply-based session continuity working
- [ ] Context management for long conversations

### Phase 5: Subagents & Skills
**Goal**: Define specialized subagents matching Claude Code's capabilities

**Tasks**:
1. Define subagent types:
   - `code-reviewer`: PR review specialist
   - `debugger`: Error investigation
   - `architect`: System design
   - `researcher`: Web search and analysis
2. Implement Claude Code slash commands as skills:
   - /commit, /review-pr, /prime, /pthread, /sdlc
3. Configure hooks for permission control

**Deliverables**:
- [ ] agent/subagents.py with agent definitions
- [ ] Skills integrated as agent prompts
- [ ] Permission hooks configured

### Phase 6: Daydream & Background Tasks
**Goal**: Migrate cron-based maintenance to SDK

**Tasks**:
1. Create daydream runner using SDK
2. Implement each step:
   - clean_legacy, review_logs, check_sentry
   - clean_tasks, update_docs, daily_report
3. Schedule via launchd or Python scheduler
4. Test full daydream cycle

**Deliverables**:
- [ ] scripts/daydream_sdk.py
- [ ] All 6 steps working
- [ ] Cron scheduling configured

### Phase 7: Cleanup & Deprecation
**Goal**: Remove Clawdbot dependency

**Tasks**:
1. Remove clawdbot subprocess calls
2. Delete ~/clawd/skills/ (JS tools)
3. Uninstall clawdbot: `clawdbot uninstall`
4. Update documentation
5. Update CLAUDE.md with new architecture

**Deliverables**:
- [ ] No clawdbot references in code
- [ ] Documentation updated
- [ ] Clean git history

---

## Tool Templates

### Standalone MCP Server (for shared tools like Sentry, GitHub, etc.)

These run as separate processes and can be used by any MCP client.

```python
# mcp/servers/sentry/server.py
"""
Sentry MCP Server - Standalone server for error monitoring.

Usage:
  python -m mcp.servers.sentry.server  # stdio mode
  python -m mcp.servers.sentry.server --http --port 8001  # HTTP mode

Configure in Claude Code (~/.claude/settings.json):
  "mcpServers": {
    "sentry": {
      "command": "python",
      "args": ["-m", "mcp.servers.sentry.server"],
      "env": {"SENTRY_API_KEY": "..."}
    }
  }
"""
from mcp.server import Server
from mcp.types import Tool, TextContent
import httpx
import os

SENTRY_API = "https://sentry.io/api/0"
SENTRY_TOKEN = os.getenv("SENTRY_API_KEY")

server = Server("sentry")

@server.tool()
async def list_issues(project: str, status: str = "unresolved", limit: int = 10) -> list[TextContent]:
    """List error issues from Sentry with optional filters.

    Args:
        project: Project slug
        status: Filter by status (unresolved, resolved, ignored)
        limit: Max results (default 10)
    """
    headers = {"Authorization": f"Bearer {SENTRY_TOKEN}"}
    params = {"query": f"is:{status}", "limit": limit}

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{SENTRY_API}/projects/{os.getenv('SENTRY_ORG_SLUG')}/{project}/issues/",
            headers=headers,
            params=params
        )
        response.raise_for_status()

    return [TextContent(type="text", text=json.dumps(response.json(), indent=2))]

@server.tool()
async def resolve_issue(issue_id: str) -> list[TextContent]:
    """Mark a Sentry issue as resolved."""
    # ... implementation

if __name__ == "__main__":
    import sys
    if "--http" in sys.argv:
        # Run as HTTP server
        from mcp.server.http import run_http_server
        port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8001
        run_http_server(server, port=port)
    else:
        # Run as stdio server (default for Claude Code)
        from mcp.server.stdio import run_stdio_server
        run_stdio_server(server)
```

### In-Process Tools (for Valor-specific tools)

These run within Valor's process and don't need to be shared.

```python
# agent/valor_tools.py
"""
Valor-specific tools - not shared with Claude Code sessions.
"""
from claude_agent_sdk import tool, create_sdk_mcp_server
from tools.telegram_history import store_message, search_history, get_recent_messages
from tools.link_analysis import extract_urls, summarize_url_content

@tool(
    name="telegram_search_history",
    description="Search Telegram conversation history",
    parameters={
        "query": {"type": "string", "description": "Search query"},
        "chat_id": {"type": "string", "description": "Chat ID to search"},
        "limit": {"type": "integer", "description": "Max results"}
    }
)
async def telegram_search_history(args: dict) -> dict:
    result = search_history(
        query=args["query"],
        chat_id=args["chat_id"],
        max_results=args.get("limit", 10)
    )
    return {"content": [{"type": "text", "text": json.dumps(result, indent=2)}]}

@tool(
    name="telegram_store_link",
    description="Store a link shared in Telegram with metadata",
    parameters={...}
)
async def telegram_store_link(args: dict) -> dict:
    # ... implementation

# Create in-process MCP server for Valor
valor_tools_server = create_sdk_mcp_server(
    name="valor-tools",
    version="1.0.0",
    tools=[telegram_search_history, telegram_store_link, ...]
)
```

---

## Agent Configuration

```python
# agent/config.py
from claude_agent_sdk import ClaudeAgentOptions, AgentDefinition
from agent.valor_tools import valor_tools_server  # In-process Valor-specific tools

def create_agent_options(
    session_id: str,
    system_prompt: str,
    working_dir: str,
) -> ClaudeAgentOptions:

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=working_dir,

        # Built-in coding tools (same as Claude Code)
        allowed_tools=[
            # File operations
            "Read", "Write", "Edit", "Glob", "Grep",
            # Execution
            "Bash",
            # Web
            "WebSearch", "WebFetch",
            # Agents
            "Task",
            # Standalone MCP servers (shared with Claude Code)
            "mcp__sentry__*",
            "mcp__github__*",
            "mcp__linear__*",
            "mcp__notion__*",
            "mcp__stripe__*",
            "mcp__render__*",
            # Valor-specific tools (in-process)
            "mcp__valor__*",
        ],

        # MCP servers configuration
        mcp_servers={
            # Standalone servers (same ones Claude Code uses)
            "sentry": {
                "command": "python",
                "args": ["-m", "mcp.servers.sentry.server"],
                "env": {"SENTRY_API_KEY": os.getenv("SENTRY_API_KEY")}
            },
            "github": {
                "command": "python",
                "args": ["-m", "mcp.servers.github.server"],
                "env": {"GITHUB_TOKEN": os.getenv("GITHUB_TOKEN")}
            },
            "linear": {
                "command": "python",
                "args": ["-m", "mcp.servers.linear.server"],
                "env": {"LINEAR_API_KEY": os.getenv("LINEAR_API_KEY")}
            },
            "notion": {
                "command": "python",
                "args": ["-m", "mcp.servers.notion.server"],
                "env": {"NOTION_API_KEY": os.getenv("NOTION_API_KEY")}
            },
            "stripe": {
                "command": "python",
                "args": ["-m", "mcp.servers.stripe.server"],
                "env": {"STRIPE_API_KEY": os.getenv("STRIPE_API_KEY")}
            },
            "render": {
                "command": "python",
                "args": ["-m", "mcp.servers.render.server"],
                "env": {"RENDER_API_KEY": os.getenv("RENDER_API_KEY")}
            },
            # In-process server for Valor-specific tools
            "valor": valor_tools_server,
        },

        # Permissions
        permission_mode='acceptEdits',  # Auto-approve file edits

        # Subagents
        agents={
            "code-reviewer": AgentDefinition(
                description="Code review specialist",
                prompt="You review code for bugs, security, and best practices...",
                tools=["Read", "Grep", "Glob"],
                model="sonnet"
            ),
            "debugger": AgentDefinition(
                description="Debug and fix errors",
                prompt="You investigate and fix bugs...",
                tools=["Read", "Bash", "Edit", "mcp__sentry__*"],
                model="sonnet"
            ),
        }
    )
```

### Claude Code Configuration

The same standalone MCP servers work with Claude Code. Add to `~/.claude/settings.json`:

```json
{
  "mcpServers": {
    "sentry": {
      "command": "python",
      "args": ["-m", "mcp.servers.sentry.server"],
      "env": {"SENTRY_API_KEY": "${SENTRY_API_KEY}"}
    },
    "github": {
      "command": "python",
      "args": ["-m", "mcp.servers.github.server"],
      "env": {"GITHUB_TOKEN": "${GITHUB_TOKEN}"}
    },
    "linear": {
      "command": "python",
      "args": ["-m", "mcp.servers.linear.server"],
      "env": {"LINEAR_API_KEY": "${LINEAR_API_KEY}"}
    },
    "notion": {
      "command": "python",
      "args": ["-m", "mcp.servers.notion.server"],
      "env": {"NOTION_API_KEY": "${NOTION_API_KEY}"}
    },
    "stripe": {
      "command": "python",
      "args": ["-m", "mcp.servers.stripe.server"],
      "env": {"STRIPE_API_KEY": "${STRIPE_API_KEY}"}
    },
    "render": {
      "command": "python",
      "args": ["-m", "mcp.servers.render.server"],
      "env": {"RENDER_API_KEY": "${RENDER_API_KEY}"}
    }
  }
}
```

This means the same MCP servers are available whether you're:
- Running Claude Code directly
- Using Valor via Telegram
- Building other tools that need these integrations

---

## Bridge Integration

```python
# bridge/telegram_bridge.py (updated)
from claude_agent_sdk import ClaudeSDKClient
from agent.config import create_agent_options

async def get_agent_response(
    message: str,
    session_id: str,
    system_prompt: str,
    working_dir: str,
) -> str:

    options = create_agent_options(
        session_id=session_id,
        system_prompt=system_prompt,
        working_dir=working_dir,
    )

    response_parts = []

    async with ClaudeSDKClient(options=options) as client:
        await client.query(message)

        async for msg in client.receive_response():
            if hasattr(msg, 'content'):
                response_parts.append(msg.content)

    return "\n".join(response_parts)
```

---

## Testing Strategy

### Unit Tests
- Each tool function tested individually
- Mock API responses for external services
- Test parameter validation

### Integration Tests
- Full agent flow with real APIs (test accounts)
- Session continuity tests
- Tool chaining tests

### E2E Tests
- Telegram message → response flow
- Multi-turn conversations
- Subagent delegation

### Comparison Tests
- Same prompt to Clawdbot vs SDK
- Verify equivalent behavior
- Performance comparison

---

## Rollback Plan

If migration fails:
1. Keep clawdbot installed during migration
2. Feature flag to switch between SDK and clawdbot
3. Monitor logs for errors
4. Quick rollback: change flag, restart bridge

```python
USE_CLAUDE_SDK = os.getenv("USE_CLAUDE_SDK", "false").lower() == "true"

if USE_CLAUDE_SDK:
    response = await get_agent_response_sdk(...)
else:
    response = await get_agent_response_clawdbot(...)
```

---

## Success Criteria

1. **Functional Parity**: All current capabilities work with SDK
2. **Performance**: Response time equal or better than Clawdbot
3. **Reliability**: No increase in errors or timeouts
4. **Code Quality**: Python-native, well-tested, documented
5. **Claude Code Equivalence**: Same tools, same behavior as Claude Code CLI

---

## Timeline Estimate

| Phase | Scope | Dependencies |
|-------|-------|--------------|
| Phase 1 | SDK setup, basic integration | None |
| Phase 2 | Core tools (Sentry, GitHub, Linear) | Phase 1 |
| Phase 3 | Secondary tools (Notion, Stripe, Render) | Phase 1 |
| Phase 4 | Session management | Phase 1 |
| Phase 5 | Subagents & skills | Phase 2-4 |
| Phase 6 | Daydream migration | Phase 2-3 |
| Phase 7 | Cleanup & deprecation | All phases |

---

## Directory Structure (Target)

```
ai/
├── agent/                       # Claude Agent SDK integration
│   ├── __init__.py
│   ├── sdk_client.py            # ClaudeSDKClient wrapper
│   ├── config.py                # Agent options configuration
│   ├── valor_tools.py           # Valor-specific in-process tools
│   └── session_manager.py       # Session persistence
│
├── mcp/
│   └── servers/                 # Standalone MCP servers (shared)
│       ├── sentry/
│       │   ├── __init__.py
│       │   ├── server.py        # MCP server entry point
│       │   └── tools.py         # Tool implementations
│       ├── github/
│       ├── linear/
│       ├── notion/
│       ├── stripe/
│       └── render/
│
├── bridge/
│   └── telegram_bridge.py       # Uses agent/sdk_client.py
│
└── tools/                       # Existing tools (wrapped by valor_tools.py)
    ├── telegram_history/
    └── link_analysis/
```

---

## Open Questions

1. **Session storage**: SQLite vs file-based vs in-memory?
2. **Tool permissions**: Which tools need user confirmation?
3. **Rate limiting**: How to handle API rate limits for external services?
4. **Error handling**: How to surface tool errors to user gracefully?
5. **Streaming**: Stream partial responses to Telegram or wait for complete?
6. **MCP server packaging**: How to distribute MCP servers for easy installation?
7. **Testing contexts**: How to test tools in both coding and non-coding contexts?

---

## References

- [Claude Agent SDK Overview](https://platform.claude.com/docs/en/agent-sdk/overview)
- [Python SDK Reference](https://platform.claude.com/docs/en/agent-sdk/python.md)
- [Custom Tools Guide](https://platform.claude.com/docs/en/agent-sdk/custom-tools)
- [MCP Integration](https://platform.claude.com/docs/en/agent-sdk/mcp.md)
- [Claude Agent SDK GitHub](https://github.com/anthropics/claude-agent-sdk-python)

---

*Document created: 2026-01-21*
*Last updated: 2026-01-21*
