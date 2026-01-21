# Claude Agent SDK Migration - Session Prompt

Copy everything below this line into a new Claude Code session:

---

## Task: Migrate from Clawdbot to Claude Agent SDK

Read the migration plan at `docs/plans/claude-agent-sdk-migration.md` first.

### Context

Valor is an AI coworker that runs on a Mac and communicates via Telegram. Currently it uses **Clawdbot** (a third-party tool) to call Claude. We're replacing Clawdbot with the official **Claude Agent SDK** so Valor has the same capabilities as Claude Code.

### Current Architecture (to be replaced)
```
Telegram → Python Bridge → subprocess: clawdbot agent --local --json → Claude API
```

### Target Architecture
```
Telegram → Python Bridge → Claude Agent SDK (ClaudeSDKClient) → Claude API
                                    ↓
                          Standalone MCP Servers (Sentry, GitHub, etc.)
```

### Key Files
- `bridge/telegram_bridge.py` - Main bridge, currently calls clawdbot via subprocess
- `config/SOUL.md` - Valor's system prompt/persona
- `docs/plans/claude-agent-sdk-migration.md` - Full migration plan

### Phase 1 Tasks

1. **Install SDK**: `pip install claude-agent-sdk` and add to pyproject.toml

2. **Create agent wrapper** at `agent/sdk_client.py`:
   - Wrap `ClaudeSDKClient` for Valor's use
   - Load system prompt from `config/SOUL.md`
   - Handle session management
   - Return response text (no tool logs - that was a Clawdbot issue)

3. **Update bridge** at `bridge/telegram_bridge.py`:
   - Replace `get_agent_response()` function
   - Remove clawdbot subprocess call
   - Use new SDK client instead
   - Keep the same interface (message in, response out)

4. **Test basic flow**:
   - Restart bridge
   - Send test message via Telegram
   - Verify response works with built-in tools (Read, Write, Bash, etc.)

### Important Notes

- Keep Clawdbot installed during migration (for rollback)
- Add feature flag `USE_CLAUDE_SDK=true` to switch between old and new
- The SDK provides the same built-in tools as Claude Code (Read, Write, Edit, Bash, Grep, Glob, WebSearch, Task)
- MCP server integration (Sentry, GitHub, etc.) comes in Phase 2 - just get basic flow working first

### SDK Quick Reference

```python
from claude_agent_sdk import ClaudeSDKClient, ClaudeAgentOptions

options = ClaudeAgentOptions(
    system_prompt="...",
    cwd="/path/to/working/dir",
    allowed_tools=["Read", "Write", "Edit", "Bash", "Grep", "Glob", "WebSearch", "Task"],
    permission_mode='acceptEdits',
)

async with ClaudeSDKClient(options=options) as client:
    await client.query(message)
    async for msg in client.receive_response():
        # Process streaming response
        pass
```

### Success Criteria

- [ ] SDK installed
- [ ] `agent/sdk_client.py` created with working wrapper
- [ ] Bridge uses SDK when `USE_CLAUDE_SDK=true`
- [ ] Basic Telegram message → response flow works
- [ ] Built-in coding tools (Read, Write, Bash) work

Start by reading the full migration plan, then implement Phase 1.
