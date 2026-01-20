# Bridge Response Improvements

**Status**: Implemented
**Created**: 2026-01-20
**Completed**: 2026-01-20

---

## Summary

Implemented four improvements to the Telegram bridge to make Valor behave like a senior coworker rather than a chatbot.

## Implemented Features

### 1. Response Filtering ✅

Filters out tool execution traces from agent responses before sending to Telegram.

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

**Location**: `bridge/telegram_bridge.py:filter_tool_logs()`

### 2. Reply-Based Session Continuity ✅

Prevents context pollution by using Telegram's reply-to feature for session management.

| Message Type | Session Behavior |
|--------------|------------------|
| Reply to Valor's message | Continue that session |
| New message (no reply) | Fresh session using message ID |

**Session ID format**: `tg_{project}_{chat_id}_{msg_id}`

**Location**: Handler in `bridge/telegram_bridge.py` around line 1222

### 3. Retry with Self-Healing ✅

On timeout or failure, retries up to 3 times with progressive delays.

**Retry delays**: 5s, 15s, 30s

**Self-healing actions**:
- Kill stuck clawdbot processes
- Brief pause between retries

**On final failure**: Creates `docs/plans/fix-bridge-failure-{timestamp}.md` instead of showing error to user.

**Location**: `bridge/telegram_bridge.py:get_agent_response_with_retry()`

### 4. Activity Context ✅

When user asks status questions like "what are you working on?", injects recent activity into context.

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

**Location**: `bridge/telegram_bridge.py:build_activity_context()`

---

## Previous Improvement (Already Done)

### Working Directory Configuration ✅

Added `working_directory` to project config and pass to clawdbot subprocess.

**Location**: `config/projects.json` and `bridge/telegram_bridge.py:get_agent_response()`

---

## Design Philosophy

A helpful coworker only responds:
- **"Done"** - Task completed (optionally with key decisions made)
- **"Blocked"** - Needs clarification or decision from supervisor
- **"Context"** - When asked status questions, share actual awareness

Never: Play-by-play updates, error dumps, excuses, or "waiting for tasks"

---

## Testing

| Test Case | Expected |
|-----------|----------|
| Message that triggers tools | No tool logs in response |
| New message (no reply) | Fresh session created |
| Reply to Valor's message | Same session continued |
| Timeout scenario | Retry with 🔄 emoji |
| "What are you working on?" | Response includes recent commits/plans |
