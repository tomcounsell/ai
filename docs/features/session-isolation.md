# Session Isolation: Task Lists + Git Worktrees

## Overview

Session isolation prevents task lists and filesystem state from bleeding between concurrent or back-to-back coding sessions. It uses a two-tier model:

- **Tier 1 (automatic, thread-scoped):** Every session gets an isolated task list automatically, with zero configuration. Scoped by Telegram thread ID or local session ID. Ephemeral and disposable.
- **Tier 2 (named, slug-scoped):** When `/do-plan {slug}` is invoked, the session graduates to a durable, named task list keyed by the slug. The same slug ties together the task list, branch, worktree, plan doc, and GitHub issue.

This approach ensures ad-hoc conversations never pollute each other's tasks, while planned work items get persistent, resumable task isolation that survives session restarts.

## Technical Implementation

### Task List ID Injection

The bridge injects `CLAUDE_CODE_TASK_LIST_ID` into the environment when spawning Claude Code via the SDK client:

- **Tier 1**: `CLAUDE_CODE_TASK_LIST_ID=thread-{chat_id}-{root_message_id}` for Telegram sessions, or `session-{session_id}` for local Claude Code sessions.
- **Tier 2**: `CLAUDE_CODE_TASK_LIST_ID={slug}` when a work item slug is assigned via `/do-plan`.

The env var is set in `ValorAgent._create_options()` and passed through `get_agent_response_sdk()`.

**Important distinction:** `CLAUDE_CODE_TASK_LIST_ID` scopes **sub-agent Task storage** (`~/.claude/tasks/{id}/`) used by `TaskCreate`/`TaskList`/`TaskUpdate` when spawned via the `Task` tool. It does **not** affect `TodoWrite`, which is scoped by Claude Code's internal session ID automatically. In practice, this means:

- Sub-agent tasks (spawned during `/do-build`) are isolated by the env var
- In-session todos (TodoWrite) are isolated by session ID -- no env var needed
- For cross-session persistence, use `--session-id` with a deterministic ID derived from the thread

### Model Fields

- `AgentSession.slug` -- Redis model field storing the active slug for a session. Set when `/do-plan {slug}` runs.
- `Job.slug` -- Propagated from the session to each session for task list routing.
- `Job.task_list_id` -- The computed task list ID (either slug or thread-derived).

### Tier Transition

Tier 1 tasks do not migrate to tier 2. They are scratch work from investigation and exploration. When `/do-plan` runs, it creates a clean slate for the named task list. The plan document captures what matters.

### Git Worktrees for Filesystem Isolation

Each tier 2 work item gets its own git worktree for filesystem isolation:

- Worktrees live under `.worktrees/{slug}/` (added to `.gitignore`)
- Branch convention: `session/{slug}`
- Created at `/do-build` time via `agent/worktree_manager.py`
- `settings.local.json` is copied into the worktree's `.claude/` directory (since it's not tracked by git)
- On completion: changes are merged back, worktree is removed
- Stale worktree references are automatically detected and cleaned up by `create_worktree()`
- `get_or_create_worktree()` is the preferred idempotent entry point: it returns an existing worktree path or creates a new one, making session resumption seamless

The worktree manager provides six operations: `get_or_create_worktree()`, `create_worktree()`, `remove_worktree()`, `list_worktrees()`, `prune_worktrees()`, and `cleanup_after_merge()`.

### Stale Worktree Recovery

When a session crashes or times out, it may leave a stale worktree that blocks future builds for the same slug. The `create_worktree()` function handles this automatically by detecting and cleaning up stale worktrees before creation. Three recovery cases are handled:

1. **Worktree directory exists and is valid**: Returns the existing path as a no-op.
2. **Worktree directory is gone but git still tracks it**: Runs `git worktree prune` to clean the stale reference, then creates a fresh worktree.
3. **Branch is locked by a worktree at a different path**: Force-removes the stale worktree via `git worktree remove --force`, then creates at the expected path. Falls back to `shutil.rmtree` + prune if force-remove fails.

Detection uses `git worktree list --porcelain` to find branches already associated with a worktree (via the `_find_worktree_for_branch()` helper). All recovery actions are logged with warnings so operators can see what was cleaned up.

This makes the SDLC pipeline resilient to stale worktree state -- no manual `git worktree remove --force` is needed.

See GitHub issue [#237](https://github.com/tomcounsell/ai/issues/237) for the original bug report.

### Post-Merge Worktree Cleanup

When a PR is merged via `gh pr merge --squash --delete-branch`, the remote branch is deleted but local branch deletion fails if a git worktree still references it. The `cleanup_after_merge()` function handles this:

1. Removes the worktree at `.worktrees/{slug}/` if it still exists
2. Prunes stale git worktree references
3. Deletes the local `session/{slug}` branch if it still exists

A CLI script is available for manual or automated use:

```bash
python scripts/post_merge_cleanup.py {slug}
```

The function returns a status dict with `worktree_removed`, `branch_deleted`, `already_clean`, and `errors` fields. It is safe to call in any state -- if everything is already cleaned up, it is a no-op.

## Key Experiment Findings

Experiments validated the approach before implementation:

1. **Worktree + SDK compatibility**: The Claude Code SDK v2.1.38 works in bare worktree directories with no modifications. Even with `.claude/` completely absent, the SDK no longer crashes. `settings.local.json` is copied for convenience (local settings), not for crash prevention.
2. **`CLAUDE_CODE_TASK_LIST_ID` scoping**: The env var scopes sub-agent Task storage (`~/.claude/tasks/{id}/`) but does **not** affect TodoWrite, which is always scoped by session ID. See `docs/features/task-list-isolation.md` for detailed findings.
3. **Thread ID uniqueness**: Using `chat_id` + `root_message_id` provides per-conversation isolation within group chats, not just per-chat isolation.

## Relevant Files

| File | Purpose |
|------|---------|
| `agent/worktree_manager.py` | Git worktree create/remove/list/prune/cleanup operations |
| `scripts/post_merge_cleanup.py` | CLI script for post-merge worktree and branch cleanup |
| `agent/hooks/session_registry.py` | Maps Claude Code UUIDs to bridge session IDs for hook-side resolution |
| `agent/sdk_client.py` | Injects `CLAUDE_CODE_TASK_LIST_ID` into SDK environment; registers/unregisters sessions in the hook registry |
| `agent/agent_session_queue.py` | Computes task list ID in `_execute_agent_session()` and passes to SDK |
| `models/agent_session.py` | `AgentSession` model with `slug` field |
| `docs/features/task-list-isolation.md` | Experiment results for CLAUDE_CODE_TASK_LIST_ID behavior |
| `docs/features/worktree-sdk-compatibility.md` | Experiment results for SDK + worktree compatibility |

## Completion Signal

Sessions transition to the **Complete** state when work is finished. Two mechanisms signal completion:

1. **Automatic** -- `mark_work_done()` is called in `agent/agent_session_queue.py` when a session finishes successfully. This is the primary completion mechanism.
2. **Human signal** -- The thumbs-up emoji reaction (👍) in the Telegram group chat serves as a visual acknowledgment between humans that work is done.

Note: **Telethon cannot receive emoji reaction events** for user accounts (Telegram API limitation). The 👍 reaction is purely a human-to-human signal -- it does not trigger any programmatic state change. No reaction handler is needed in the bridge.

## Session Continuation Gate

When spawning a Claude Code subprocess, `_create_options()` in `agent/sdk_client.py` decides whether to set `continue_conversation=True`. Previously, this was set for any non-None `session_id`, which could cause fresh sessions to reuse stale Claude Code session files on disk -- leaking context between unrelated conversations (see issue #232).

Now, `_has_prior_session(session_id)` queries the AgentSession Redis model to check if a prior session ran for this session_id with a status of `completed`, `running`, `active`, or `dormant`. Only when a prior session exists is `continue_conversation` (and `resume`) set to True. This prevents cross-contamination between concurrent DM and group conversations while preserving reply-thread continuation (which reuses the original session_id and thus has a prior AgentSession record).

The check fails safe: if Redis is unavailable, `_has_prior_session()` returns False (don't continue), ensuring fresh sessions never accidentally inherit stale context.

### Claude Code UUID Mapping (Issue #374)

The session continuation gate was extended to fix three compounding bugs that caused the Observer to prematurely deliver output on continuation sessions:

1. **Session identity mapping**: `AgentSession` now has a `claude_session_uuid` field that stores the Claude Code transcript UUID (from `ResultMessage.session_id`). The `resume` parameter in `_create_options()` uses this stored UUID instead of the Telegram session ID. This prevents Claude Code from falling back to the most recent unrelated session file on disk. The function `_get_prior_session_uuid()` replaces the boolean `_has_prior_session()` check with a UUID lookup, and `_store_claude_session_uuid()` persists the mapping after each query.

2. **Watchdog count scoping**: The health check hook (`agent/health_check.py`) uses the session registry (see below) for tool count tracking instead of Claude Code's internal session ID. A `reset_session_count()` function is called at the start of each SDK query to clear stale counts from prior runs. This prevents continuation sessions from inheriting inflated tool counts that trigger premature health check kills.

3. **Deterministic record selection**: When re-reading `AgentSession` records (in both `agent_session_queue.py` and `bridge/observer.py`), the code now filters by active statuses (`running`, `active`, `pending`) first, then falls back to all records, sorted by `created_at` descending. This ensures the newest relevant record is always selected when duplicates exist. Additionally, `_push_agent_session()` marks old completed records as `superseded` to prevent ambiguity.

The `claude_session_uuid` field is included in `_AGENT_SESSION_FIELDS` so it is preserved across the delete-and-recreate pattern used by `_enqueue_continuation()`.

### Hook Session Registry (Issue #597)

Hooks fired by the Claude Agent SDK execute in the **parent bridge process**, not inside the Claude Code subprocess. The `VALOR_SESSION_ID` env var (injected into the subprocess at `sdk_client.py`) is invisible to hooks because they run in a different process context. This caused all hook-side session lookups to fall back to Claude Code's internal UUID, breaking activity logging, Redis session tracking, heartbeat enrichment, and DevSession registration.

The fix is a **module-level registry** (`agent/hooks/session_registry.py`) that maps Claude Code UUIDs to bridge session IDs within the parent process. The registry uses a two-phase registration pattern:

1. **Pre-registration**: `SDKAgentClient.query()` calls `register_pending(bridge_session_id)` before starting the SDK query. At this point the Claude Code UUID is not yet known.
2. **Promotion**: The first hook callback calls `complete_registration(claude_uuid)` (or `resolve()` which auto-promotes) using the UUID from `input_data["session_id"]`. This promotes the pending entry to a full UUID-keyed mapping.
3. **Lookup**: All subsequent hook calls use `resolve(claude_uuid)` to look up the bridge session ID. This replaces the previous `os.environ.get("VALOR_SESSION_ID")` calls.
4. **Cleanup**: `SDKAgentClient.query()` calls `unregister(claude_uuid)` in its `finally` block.

The registry also tracks per-session tool activity (tool count and last 3 tool names) via `record_tool_use()` and `get_activity()`. The bridge watchdog (`BackgroundTask._watchdog()` in `agent/messenger.py`) reads this data to enrich heartbeat logs with tool-level progress (e.g., `"running 120s, tools=15, last=Bash"`).

**Thread safety**: The bridge is single-threaded asyncio, so dict operations on distinct keys are safe without locking. A TTL-based sweep (`cleanup_stale()`) removes entries older than 30 minutes as a safety net for entries not cleaned up due to uncaught exceptions.

**Hook call sites using the registry**:
- `agent/health_check.py` -- watchdog tool count tracking
- `agent/hooks/pre_tool_use.py` -- DevSession registration
- `agent/hooks/subagent_stop.py` -- completion tracking (two call sites)

Note: The `VALOR_SESSION_ID` env var injection in `sdk_client.py` is retained for code running inside the Claude Code subprocess (shell scripts, Python tools via Bash). The registry is only for parent-process hook resolution.

## History Truncation Warning

Session history is capped at `HISTORY_MAX_ENTRIES` (currently 20) entries via `AgentSession.append_event()`. When a session exceeds this cap, the oldest entries are silently dropped to stay within the limit. A `WARNING`-level log message is emitted each time truncation occurs, including the original length and number of entries lost:

```
WARNING Session abc123 history truncated from 25 to 20, 5 oldest entries lost
```

This is particularly relevant for long-running SDLC sessions that may accumulate many lifecycle events. The warning enables operators to diagnose issues where early history (e.g., initial classification or stage transitions) is no longer available, without needing to reproduce the session.

## Auto-Continue and Session Scope

The auto-continue system uses session re-enqueue rather than steering queue injection. When a status update triggers auto-continue, a new session is enqueued through the normal session queue with the same session context:

- `session_id` -- preserves thread identity
- `slug` -- preserves slug-scoped task list binding
- `task_list_id` -- preserves the CLAUDE_CODE_TASK_LIST_ID value

This ensures auto-continued work remains within the correct isolation scope. The previous approach (steering queue injection) could bypass session scoping if the agent process had already exited.

See [Reaction Semantics](reaction-semantics.md) for details on the re-enqueue design and the race condition it fixes.

## Semantic Session Routing

In addition to mechanical routing (reply-to message ID), sessions can be matched semantically. When the summarizer produces structured output, it extracts `context_summary` and `expectations` fields that describe what a session is working on and what it needs from the human. Unthreaded messages are then evaluated against sessions with expectations, and high-confidence matches are routed based on session status:

- **Active/running sessions**: The message is pushed to the session's steering queue (`push_steering_message`). The user gets an ack ("Noted — I'll incorporate this on my next checkpoint.") and the Observer picks it up at its next stop. No competing session is created.
- **Dormant sessions**: The session is resumed using the matched session_id (existing behavior).

This complements the isolation model: sessions remain isolated, but messages can find their way to the correct session even without explicit reply-to threading. See [Semantic Session Routing](semantic-session-routing.md) for full details.

## See Also

- [Semantic Session Routing](semantic-session-routing.md) -- Semantic matching of unthreaded messages to sessions with expectations
- [Scale Session Queue (Popoto + Worktrees)](scale-agent-session-queue-with-popoto-and-worktrees.md) -- The parallel execution foundation that this feature enables
- [Session Watchdog](session-watchdog.md) -- Active session monitoring that works alongside isolation
- [Bridge Workflow Gaps](bridge-workflow-gaps.md) -- Auto-continue, output classification, session logs
- GitHub Issue [#62](https://github.com/tomcounsell/ai/issues/62) -- Tracking issue with experiment details
