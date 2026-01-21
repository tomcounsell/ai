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
Telegram
    ↓
Python Bridge (telegram_bridge.py)
    ↓
Claude Agent SDK (Python)
    ├── ClaudeSDKClient (session management)
    ├── Built-in tools (Read, Write, Edit, Bash, Grep, Glob, WebSearch, etc.)
    ├── Custom tools via @tool decorator
    │   ├── sentry_*
    │   ├── github_*
    │   ├── linear_*
    │   ├── notion_*
    │   ├── stripe_*
    │   ├── render_*
    │   └── telegram_history_*
    └── MCP servers (optional, for complex integrations)
    ↓
Anthropic API (direct)
```

**Benefits:**
- Same capabilities as Claude Code CLI
- Full control over system prompt, tools, permissions
- Python-native tools (no JS)
- In-process tool execution (faster, no subprocess overhead)
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
5. Test basic message flow

**Deliverables**:
- [ ] SDK installed and importable
- [ ] Basic agent wrapper class
- [ ] Bridge uses SDK for responses
- [ ] System prompt loaded from config

### Phase 2: Tool Migration - Core Services
**Goal**: Migrate Sentry, GitHub, Linear tools to Python @tool decorators

**Tasks**:
1. Create `tools/mcp/` directory for custom tool definitions
2. Migrate Sentry tools (8 tools):
   - list_issues, get_issue, list_events, get_event
   - list_projects, get_performance_data
   - update_issue_status, resolve_issue
3. Migrate GitHub tools (10 tools):
   - list_prs, get_pr, create_pr, merge_pr
   - list_issues, create_issue, get_commits
   - get_checks, search_code, get_file
4. Migrate Linear tools (9 tools):
   - list_issues, get_issue, create_issue, update_issue, close_issue
   - list_cycles, get_team_velocity, search_issues, get_roadmap
5. Create MCP server with all tools
6. Test each tool individually

**Deliverables**:
- [ ] tools/mcp/sentry.py (8 tools)
- [ ] tools/mcp/github.py (10 tools)
- [ ] tools/mcp/linear.py (9 tools)
- [ ] Integration tests for each tool

### Phase 3: Tool Migration - Secondary Services
**Goal**: Migrate Notion, Stripe, Render tools

**Tasks**:
1. Migrate Notion tools (8 tools):
   - search, get_page, create_page, update_page
   - append_blocks, list_databases, query_database, create_database_entry
2. Migrate Stripe tools (9 tools):
   - list_customers, get_customer, list_subscriptions, get_subscription
   - list_invoices, create_refund, get_balance, get_mrr, cancel_subscription
3. Migrate Render tools (9 tools):
   - list_services, get_service, get_service_logs, deploy_service
   - restart_service, scale_service, list_deploys, get_env_vars, update_env_vars
4. Migrate existing Python tools:
   - telegram_history (search, store, get_recent)
   - link_analysis (extract_urls, summarize, metadata)

**Deliverables**:
- [ ] tools/mcp/notion.py (8 tools)
- [ ] tools/mcp/stripe.py (9 tools)
- [ ] tools/mcp/render.py (9 tools)
- [ ] tools/mcp/telegram.py (existing tools wrapped)
- [ ] Integration tests

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

## Tool Migration Template

Each Clawdbot JS tool becomes a Python function:

```python
# tools/mcp/sentry.py
from claude_agent_sdk import tool
import httpx
import os

SENTRY_API = "https://sentry.io/api/0"
SENTRY_TOKEN = os.getenv("SENTRY_API_KEY")

@tool(
    name="sentry_list_issues",
    description="List error issues from Sentry with optional filters",
    parameters={
        "project": {"type": "string", "description": "Project slug"},
        "status": {"type": "string", "description": "Filter by status: unresolved, resolved, ignored"},
        "limit": {"type": "integer", "description": "Max results (default 10)"}
    }
)
async def sentry_list_issues(args: dict) -> dict:
    headers = {"Authorization": f"Bearer {SENTRY_TOKEN}"}
    params = {
        "query": f"is:{args.get('status', 'unresolved')}",
        "limit": args.get("limit", 10)
    }

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{SENTRY_API}/projects/{os.getenv('SENTRY_ORG_SLUG')}/{args['project']}/issues/",
            headers=headers,
            params=params
        )
        response.raise_for_status()

    return {
        "content": [{
            "type": "text",
            "text": json.dumps(response.json(), indent=2)
        }]
    }
```

---

## Agent Configuration

```python
# agent/config.py
from claude_agent_sdk import ClaudeAgentOptions, AgentDefinition
from tools.mcp import sentry, github, linear, notion, stripe, render, telegram

def create_agent_options(
    session_id: str,
    system_prompt: str,
    working_dir: str,
) -> ClaudeAgentOptions:

    # Create MCP server with all custom tools
    tools_server = create_sdk_mcp_server(
        name="valor-tools",
        version="1.0.0",
        tools=[
            # Sentry
            sentry.list_issues, sentry.get_issue, sentry.resolve_issue, ...
            # GitHub
            github.list_prs, github.get_pr, github.create_pr, ...
            # Linear
            linear.list_issues, linear.create_issue, ...
            # Notion
            notion.search, notion.get_page, ...
            # Stripe
            stripe.list_customers, stripe.get_mrr, ...
            # Render
            render.list_services, render.deploy_service, ...
            # Telegram
            telegram.search_history, telegram.store_link, ...
        ]
    )

    return ClaudeAgentOptions(
        system_prompt=system_prompt,
        cwd=working_dir,

        # Built-in tools (same as Claude Code)
        allowed_tools=[
            "Read", "Write", "Edit", "Bash", "Grep", "Glob",
            "WebSearch", "WebFetch", "Task",
            "mcp__valor-tools__*",  # All custom tools
        ],

        # Custom MCP server
        mcp_servers={"valor-tools": tools_server},

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
                tools=["Read", "Bash", "Edit", "mcp__valor-tools__sentry_*"],
                model="sonnet"
            ),
        }
    )
```

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

## Open Questions

1. **Session storage**: SQLite vs file-based vs in-memory?
2. **Tool permissions**: Which tools need user confirmation?
3. **Rate limiting**: How to handle API rate limits for external services?
4. **Error handling**: How to surface tool errors to user gracefully?
5. **Streaming**: Stream partial responses to Telegram or wait for complete?

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
