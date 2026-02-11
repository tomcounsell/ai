# Session Isolation: Task Lists + Git Worktrees

## Overview

Session isolation prevents task lists and filesystem state from bleeding between concurrent or back-to-back coding sessions. It uses a two-tier model:

- **Tier 1 (automatic, thread-scoped):** Every session gets an isolated task list automatically, with zero configuration. Scoped by Telegram thread ID or local session ID. Ephemeral and disposable.
- **Tier 2 (named, slug-scoped):** When `/make-plan {slug}` is invoked, the session graduates to a durable, named task list keyed by the slug. The same slug ties together the task list, branch, worktree, plan doc, and GitHub issue.

This approach ensures ad-hoc conversations never pollute each other's tasks, while planned work items get persistent, resumable task isolation that survives session restarts.

## Technical Implementation

### Task List ID Injection

The bridge injects `CLAUDE_CODE_TASK_LIST_ID` into the environment when spawning Claude Code via the SDK client:

- **Tier 1**: `CLAUDE_CODE_TASK_LIST_ID=thread-{chat_id}-{root_message_id}` for Telegram sessions, or `session-{session_id}` for local Claude Code sessions.
- **Tier 2**: `CLAUDE_CODE_TASK_LIST_ID={slug}` when a work item slug is assigned via `/make-plan`.

The env var is set in `ValorAgent._create_options()` and passed through `get_agent_response_sdk()`.

**Important distinction:** `CLAUDE_CODE_TASK_LIST_ID` scopes **sub-agent Task storage** (`~/.claude/tasks/{id}/`) used by `TaskCreate`/`TaskList`/`TaskUpdate` when spawned via the `Task` tool. It does **not** affect `TodoWrite`, which is scoped by Claude Code's internal session ID automatically. In practice, this means:

- Sub-agent tasks (spawned during `/build`) are isolated by the env var
- In-session todos (TodoWrite) are isolated by session ID -- no env var needed
- For cross-session persistence, use `--session-id` with a deterministic ID derived from the thread

### Model Fields

- `AgentSession.work_item_slug` -- Redis model field storing the active slug for a session. Set when `/make-plan {slug}` runs.
- `Job.work_item_slug` -- Propagated from the session to each job for task list routing.
- `Job.task_list_id` -- The computed task list ID (either slug or thread-derived).

### Tier Transition

Tier 1 tasks do not migrate to tier 2. They are scratch work from investigation and exploration. When `/make-plan` runs, it creates a clean slate for the named task list. The plan document captures what matters.

### Git Worktrees for Filesystem Isolation

Each tier 2 work item gets its own git worktree for filesystem isolation:

- Worktrees live under `.worktrees/{slug}/` (added to `.gitignore`)
- Branch convention: `session/{slug}`
- Created at `/build` time via `agent/worktree_manager.py`
- `settings.local.json` is copied into the worktree's `.claude/` directory (since it's not tracked by git)
- On completion: changes are merged back, worktree is removed
- Stale worktree references are pruned on startup via `git worktree prune`

The worktree manager provides four operations: `create_worktree()`, `remove_worktree()`, `list_worktrees()`, and `prune_worktrees()`.

## Key Experiment Findings

Experiments validated the approach before implementation:

1. **Worktree + SDK compatibility**: The Claude Code SDK v2.1.38 works in bare worktree directories with no modifications. Even with `.claude/` completely absent, the SDK no longer crashes. `settings.local.json` is copied for convenience (local settings), not for crash prevention.
2. **`CLAUDE_CODE_TASK_LIST_ID` scoping**: The env var scopes sub-agent Task storage (`~/.claude/tasks/{id}/`) but does **not** affect TodoWrite, which is always scoped by session ID. See `docs/experiments/task-list-isolation.md` for detailed findings.
3. **Thread ID uniqueness**: Using `chat_id` + `root_message_id` provides per-conversation isolation within group chats, not just per-chat isolation.

## Relevant Files

| File | Purpose |
|------|---------|
| `agent/worktree_manager.py` | Git worktree create/remove/list/prune operations |
| `agent/sdk_client.py` | Injects `CLAUDE_CODE_TASK_LIST_ID` into SDK environment |
| `agent/job_queue.py` | Computes task list ID in `_execute_job()` and passes to SDK |
| `models/sessions.py` | `AgentSession` model with `work_item_slug` field |
| `docs/experiments/task-list-isolation.md` | Experiment results for CLAUDE_CODE_TASK_LIST_ID behavior |
| `docs/experiments/worktree-sdk-compatibility.md` | Experiment results for SDK + worktree compatibility |

## Completion Signal

Sessions transition to the **Complete** state when work is finished. Two mechanisms signal completion:

1. **Automatic** -- `mark_work_done()` is called in `agent/job_queue.py` when a job finishes successfully. This is the primary completion mechanism.
2. **Human signal** -- The thumbs-up emoji reaction (👍) in the Telegram group chat serves as a visual acknowledgment between humans that work is done.

Note: **Telethon cannot receive emoji reaction events** for user accounts (Telegram API limitation). The 👍 reaction is purely a human-to-human signal -- it does not trigger any programmatic state change. No reaction handler is needed in the bridge.

## See Also

- [Scale Job Queue (Popoto + Worktrees)](scale-job-queue-with-popoto-and-worktrees.md) -- The parallel execution foundation that this feature enables
- [Session Watchdog](session-watchdog.md) -- Active session monitoring that works alongside isolation
- [Bridge Workflow Gaps](bridge-workflow-gaps.md) -- Auto-continue, output classification, session logs
- GitHub Issue [#62](https://github.com/tomcounsell/ai/issues/62) -- Tracking issue with experiment details
