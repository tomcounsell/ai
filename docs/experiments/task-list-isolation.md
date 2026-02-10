# Experiment: CLAUDE_CODE_TASK_LIST_ID Behavior Validation

**Date:** 2026-02-10
**Claude Code version:** 2.1.38
**Branch:** build/session-isolation
**Issue:** #62 (Experiments 4-5)
**Plan:** docs/plans/session-isolation.md

## Objective

Validate whether the `CLAUDE_CODE_TASK_LIST_ID` environment variable properly scopes task lists in Claude Code CLI, as proposed in the session-isolation plan.

## Background: Binary Analysis

Before running experiments, we inspected the Claude Code v2.1.38 binary to understand how `CLAUDE_CODE_TASK_LIST_ID` is handled internally.

**Finding:** The env var IS recognized. A function `WQ()` in the minified source reads it:

```javascript
function WQ() {
  if (process.env.CLAUDE_CODE_TASK_LIST_ID)
    return process.env.CLAUDE_CODE_TASK_LIST_ID;
  let T = eF();       // teammate context
  if (T) return T.teamName;
  return l7() || uDA || kR();  // fallbacks: team name, unknown, session ID
}
```

`WQ()` determines the directory used for **Task tool sub-agent storage** (`~/.claude/tasks/{id}/`). However, it does NOT control **TodoWrite storage** (`~/.claude/todos/{session-agent}.json`), which is always keyed by session+agent ID.

**Key distinction:**
- `~/.claude/tasks/{WQ()}/` -- Sub-agent task files (TaskCreate/TaskList/TaskUpdate). Controlled by `CLAUDE_CODE_TASK_LIST_ID`.
- `~/.claude/todos/{sessionId}-agent-{agentId}.json` -- TodoWrite in-memory state persistence. NOT controlled by `CLAUDE_CODE_TASK_LIST_ID`.

## Available Tools

The `claude --print` CLI exposes `TodoWrite` (write-only, in-memory per-session). It does NOT expose `TaskCreate`, `TaskList`, `TaskGet`, or `TaskUpdate` -- those are internal tools used by the `Task` sub-agent system.

This means: `CLAUDE_CODE_TASK_LIST_ID` scopes the sub-agent task storage, but the only task tool available in `--print` mode (`TodoWrite`) is scoped by session ID, not the env var.

## Experiment 4: Basic Task List Isolation

### 4a: Create task under feature-a scope

```bash
CLAUDE_CODE_TASK_LIST_ID=test-feature-a claude --print \
  "Create a task called 'Feature A Task' using the TodoWrite tool"
```

**Output:**
> Created a single todo item: Feature A Task -- status: in_progress

**Result:** Task created successfully.

### 4b: List tasks under feature-b scope

```bash
CLAUDE_CODE_TASK_LIST_ID=test-feature-b claude --print \
  "List all tasks using the TodoRead tool. Report exactly what you see."
```

**Output:**
> No tasks found -- the todo list is empty at the start of this conversation.

**Result:** Feature B does NOT see Feature A's task. However, this is because **each `--print` invocation starts a fresh session**, not because of `CLAUDE_CODE_TASK_LIST_ID` scoping.

### 4c: List tasks under feature-a scope (same env var, new session)

```bash
CLAUDE_CODE_TASK_LIST_ID=test-feature-a claude --print \
  "What tasks already exist? Do you see 'Feature A Task'?"
```

**Output:**
> Existing tasks found: None. I had no prior todo list state in this conversation.

**Result: FAIL.** Even with the same `CLAUDE_CODE_TASK_LIST_ID`, a new `--print` invocation cannot see tasks from a previous invocation. TodoWrite does not persist across sessions.

### 4d: Same scope with --continue flag

```bash
# Create tasks
CLAUDE_CODE_TASK_LIST_ID=test-feature-a claude --print \
  "Create 'Feature A Task 1' (in_progress) and 'Feature A Task 2' (pending)"

# Continue same session
CLAUDE_CODE_TASK_LIST_ID=test-feature-a claude --print --continue \
  "What tasks do you see?"
```

**Output (continue):**
> Feature A Task 1 -- in_progress, Feature A Task 2 -- pending

**Result:** Tasks persist within a continued session via `--continue`.

### 4e: Cross-scope with --continue

```bash
# Continue the session but with DIFFERENT task list ID
CLAUDE_CODE_TASK_LIST_ID=test-feature-b claude --print --continue \
  "What tasks do you see?"
```

**Output:**
> Feature A Task 1 -- in_progress, Feature A Task 2 -- pending (still visible)

**Result: FAIL for isolation.** `--continue` resumes the most recent conversation regardless of `CLAUDE_CODE_TASK_LIST_ID`. The env var has no effect on which session is continued.

### 4f: Session ID isolation test

```bash
# Same CLAUDE_CODE_TASK_LIST_ID but different session ID
CLAUDE_CODE_TASK_LIST_ID=scope-alpha claude --print \
  --session-id "11111111-1111-1111-1111-111111111111" \
  "Create Alpha Task 1 (in_progress) and Alpha Task 2 (pending)"

# Resume same session, same env var
CLAUDE_CODE_TASK_LIST_ID=scope-alpha claude --print \
  --resume "11111111-1111-1111-1111-111111111111" \
  "What tasks do you see?"
# Output: Alpha Task 1 and Alpha Task 2 visible. PASS.

# Resume same session, DIFFERENT env var
CLAUDE_CODE_TASK_LIST_ID=scope-beta claude --print \
  --resume "11111111-1111-1111-1111-111111111111" \
  "What tasks do you see?"
# Output: Alpha Task 1 and Alpha Task 2 STILL visible. FAIL for isolation.

# Same env var, DIFFERENT session ID
CLAUDE_CODE_TASK_LIST_ID=scope-alpha claude --print \
  --session-id "22222222-2222-2222-2222-222222222222" \
  "Do you see Alpha Task 1 or Alpha Task 2?"
# Output: No tasks. Empty. Tasks do not leak across sessions.
```

**Result:** TodoWrite isolation is controlled by **session ID**, not `CLAUDE_CODE_TASK_LIST_ID`. The env var has no observable effect on TodoWrite behavior.

### Filesystem verification

```
# TodoWrite storage (keyed by session ID, not CLAUDE_CODE_TASK_LIST_ID):
~/.claude/todos/11111111-...-111111111111-agent-11111111-...-111111111111.json
  -> [{"content":"Alpha Task 1","status":"in_progress",...}, {"content":"Alpha Task 2",...}]

~/.claude/todos/22222222-...-222222222222-agent-22222222-...-222222222222.json
  -> []   (empty, no leakage)

# No files named after our CLAUDE_CODE_TASK_LIST_ID values (test-feature-a, scope-alpha, etc.)
```

## Experiment 5: Thread-based Scoping (Tier 1 simulation)

### 5a: Create task under thread-111 scope

```bash
CLAUDE_CODE_TASK_LIST_ID=thread-111 claude --print \
  "Create 'Thread 111 Investigation' using TodoWrite"
```

**Output:** Task created.

### 5b: List under thread-222 scope

```bash
CLAUDE_CODE_TASK_LIST_ID=thread-222 claude --print \
  "Do you see 'Thread 111 Investigation'?"
```

**Output:**
> No tasks found. This is a fresh session, no existing tasks.

**Result:** Isolation observed, but due to **new session** (fresh `--print` invocation), not due to `CLAUDE_CODE_TASK_LIST_ID`.

### 5c: List under thread-111 scope (same env var, new session)

```bash
CLAUDE_CODE_TASK_LIST_ID=thread-111 claude --print --continue \
  "Do you see 'Thread 111 Investigation'?"
```

**Output:**
> No tasks. The todo list resets with each new conversation session.

**Result: FAIL.** Even with `--continue`, the task was not recovered. `--continue` resumed the most recent session (which may have been a different thread-222 session), not the session that matches the env var.

## Summary of Findings

### Does `CLAUDE_CODE_TASK_LIST_ID` work as expected?

**No -- not for TodoWrite, which is the tool available in `--print` mode.**

| Aspect | Expected | Actual | Status |
|--------|----------|--------|--------|
| Env var recognized by binary | Yes | Yes -- `WQ()` reads it | PASS |
| Env var scopes TodoWrite storage | Yes | No -- TodoWrite uses session ID | FAIL |
| Env var scopes Task sub-agent storage | Yes | Yes -- `WQ()` returns it for `~/.claude/tasks/` path | PASS |
| Tasks persist across `--print` invocations | Yes | No -- each invocation is a fresh session | FAIL |
| `--continue` respects env var for session selection | Yes | No -- continues most recent session regardless | FAIL |
| `--session-id` provides TodoWrite isolation | N/A | Yes -- different session IDs have separate TodoWrite state | PASS |

### Key Insights

1. **Two separate storage systems exist:**
   - `~/.claude/tasks/{listId}/` -- Used by Task sub-agent tool. Controlled by `CLAUDE_CODE_TASK_LIST_ID` via `WQ()`.
   - `~/.claude/todos/{sessionId}-agent-{agentId}.json` -- Used by TodoWrite. Controlled by session ID only.

2. **`CLAUDE_CODE_TASK_LIST_ID` affects sub-agent task routing, not TodoWrite.** It determines which directory the Task tool reads/writes sub-agent task files from. This is relevant for multi-agent orchestration (teams of agents), not for individual session task lists.

3. **TodoWrite isolation is session-scoped automatically.** Each `--print` invocation creates a new session with a fresh TodoWrite state. No env var needed for basic isolation.

4. **The `--session-id` flag is the real isolation mechanism for TodoWrite.** Using deterministic session IDs (e.g., based on thread ID) would give us cross-session TodoWrite persistence AND isolation.

5. **`--continue` does not respect `CLAUDE_CODE_TASK_LIST_ID`.** It always resumes the most recent conversation in the current directory.

## Recommendations for Session Isolation Plan

### Option 1: Use `--session-id` for TodoWrite isolation (Recommended)
Instead of relying on `CLAUDE_CODE_TASK_LIST_ID`, pass a deterministic `--session-id` based on the Telegram thread ID:

```python
# In sdk_client.py
session_id = uuid5(NAMESPACE_URL, f"thread-{chat_id}-{root_message_id}")
# Pass: claude --session-id {session_id} --print ...
```

This provides:
- Automatic TodoWrite isolation (different threads get different sessions)
- TodoWrite persistence across interactions (same thread resumes same session)
- No dependency on an undocumented env var

### Option 2: Use `CLAUDE_CODE_TASK_LIST_ID` for Task sub-agent isolation
If the bridge spawns sub-agents using the `Task` tool, the env var DOES scope their file-based storage. This could be useful for tier 2 (planned work) where sub-agents collaborate:

```python
env = {"CLAUDE_CODE_TASK_LIST_ID": slug}  # Scopes sub-agent tasks to the work item
```

### Option 3: Combine both
Use `--session-id` for TodoWrite isolation (tier 1) and `CLAUDE_CODE_TASK_LIST_ID` for sub-agent task isolation (tier 2).

## Risk Assessment Update

**Risk 1 from plan (CLAUDE_CODE_TASK_LIST_ID doesn't work as expected):** **Confirmed.**
- The env var works for sub-agent Task storage but NOT for TodoWrite.
- The plan's assumption that it scopes all task tools is incorrect.
- Mitigation: Use `--session-id` for TodoWrite isolation instead.
- Impact: Low -- the alternative (`--session-id`) is actually more reliable and explicit.
