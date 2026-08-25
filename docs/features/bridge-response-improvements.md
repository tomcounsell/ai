# Bridge Response Improvements

The Telegram bridge formats responses so Valor behaves like a senior coworker rather than a chatbot.

## Response Filtering

`bridge/response.py:filter_tool_logs()` filters tool execution traces from agent responses before sending to Telegram. `bridge/context.py` re-exports this canonical implementation for reply-chain hydration (`build_conversation_history` and `format_reply_chain`).

**Patterns filtered:**
- `🛠️ exec:` - Bash execution
- `📖 read:` - File read
- `🔎 web_search:` - Web search
- `✏️ edit:` - File edit
- `📝 write:` - File write
- `🔍 search:` - Search
- `📁 glob:` - Glob
- `🌐 fetch:` - Web fetch

If filtering removes everything, no message is sent (reaction emoji suffices).

## Reply-Based Session Continuity

Telegram's reply-to feature drives session management, preventing context pollution.

| Message Type | Session Behavior |
|--------------|------------------|
| Reply to Valor's message | Resume original session (root-resolved) |
| New message (no reply) | Fresh session using message ID |

**Session ID format**: `tg_{project}_{chat_id}_{root_msg_id}` — where `root_msg_id` is the oldest human message in the reply chain, resolved via `resolve_root_session_id()` in `bridge/context.py`. Replying to any message in a thread (including Valor's responses) maps to the original session. See [Session Management](session-management.md) for details.

**Location**: Handler in `bridge/telegram_bridge.py` around line 943

## Retry with Self-Healing

On timeout or failure, `bridge/telegram_bridge.py:get_agent_response_with_retry()` retries up to 3 times with progressive delays of 5s, 15s, 30s. On final failure it creates `docs/plans/fix-bridge-failure-{timestamp}.md` instead of showing an error to the user.

## Activity Context

When a user asks a status question, `bridge/telegram_bridge.py:build_activity_context()` injects recent activity into context.

**Status patterns detected**:
- "what are you working on"
- "what's the status"
- "how's it going"
- "any updates"
- "catch me up"

**Context injected**:
- Recent git commits (last 24h)
- Current branch
- Modified files
- Active plan docs

## Working Directory Configuration

Projects configure `working_directory` in `~/Desktop/Valor/projects.json`; the bridge passes it to the agent subprocess.

## Design Philosophy

A helpful coworker only responds:
- **"Done"** - Task completed (optionally with key decisions made)
- **"Blocked"** - Needs clarification or decision from supervisor
- **"Context"** - When asked status questions, share actual awareness

Never: Play-by-play updates, error dumps, excuses, or "waiting for tasks"

## Test Coverage

| Test Case | Expected |
|-----------|----------|
| Message that triggers tools | No tool logs in response |
| New message (no reply) | Fresh session created |
| Reply to Valor's message | Same session continued |
| Timeout scenario | Retry with 🔄 emoji |
| "What are you working on?" | Response includes recent commits/plans |
