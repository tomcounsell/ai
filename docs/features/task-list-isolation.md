# Task List Isolation

`CLAUDE_CODE_TASK_LIST_ID` scopes Claude Code's **Task tool sub-agent storage**
(`~/.claude/tasks/{id}/`). It does not control **TodoWrite storage**
(`~/.claude/todos/{session-agent}.json`), which is keyed by session and agent ID.

## How the environment variable is read

Claude Code recognizes `CLAUDE_CODE_TASK_LIST_ID`. A function `WQ()` in the
minified source reads it:

```javascript
function WQ() {
  if (process.env.CLAUDE_CODE_TASK_LIST_ID)
    return process.env.CLAUDE_CODE_TASK_LIST_ID;
  let T = eF();       // teammate context
  if (T) return T.teamName;
  return l7() || uDA || kR();  // fallbacks: team name, unknown, session ID
}
```

`WQ()` determines the directory used for **Task tool sub-agent storage**
(`~/.claude/tasks/{id}/`). It does not control **TodoWrite storage**
(`~/.claude/todos/{session-agent}.json`), which is always keyed by
session+agent ID.

## Two storage systems

- `~/.claude/tasks/{listId}/` — Task sub-agent task files
  (`TaskCreate`/`TaskList`/`TaskUpdate`), controlled by
  `CLAUDE_CODE_TASK_LIST_ID`.
- `~/.claude/todos/{sessionId}-agent-{agentId}.json` — TodoWrite state
  persistence, controlled by session ID only.

`CLAUDE_CODE_TASK_LIST_ID` therefore affects sub-agent task routing, not
TodoWrite. It determines which directory the Task tool reads and writes
sub-agent task files from — relevant for multi-agent orchestration (teams of
agents), not for individual session task lists.

## Isolation mechanics

TodoWrite isolation is session-scoped. Each `--print` invocation starts a fresh
session with a fresh TodoWrite state, so no environment variable is needed for
basic isolation. Tasks persist within a continued session via `--continue`.

`--session-id` is the isolation mechanism for TodoWrite. A deterministic session
ID gives cross-session TodoWrite persistence and isolation: different session
IDs have separate TodoWrite state, and tasks never leak across sessions.

`--continue` resumes the most recent conversation in the current directory
regardless of `CLAUDE_CODE_TASK_LIST_ID`. The environment variable has no effect
on which session is continued.

## Recommended wiring

Pass a deterministic `--session-id` for TodoWrite isolation, derived from the
Telegram thread ID:

```python
# In sdk_client.py
session_id = uuid5(NAMESPACE_URL, f"thread-{chat_id}-{root_message_id}")
# Pass: claude --session-id {session_id} --print ...
```

This provides TodoWrite isolation (different threads get different sessions),
TodoWrite persistence across interactions (the same thread resumes the same
session), and no dependency on the undocumented environment variable.

Set `CLAUDE_CODE_TASK_LIST_ID` to scope sub-agent task files when the bridge
spawns sub-agents using the `Task` tool:

```python
env = {"CLAUDE_CODE_TASK_LIST_ID": slug}  # Scopes sub-agent tasks to the work item
```

Both combine naturally: `--session-id` for TodoWrite isolation and
`CLAUDE_CODE_TASK_LIST_ID` for sub-agent task isolation.
