---
status: Planning
type: feature
appetite: Medium
owner: Valor
created: 2026-02-10
tracking: https://github.com/tomcounsell/ai/issues/62
---

# Session Isolation: Task Lists + Git Worktrees

## Problem

When handling multiple coding sessions (parallel or back-to-back), task lists collide and filesystem state bleeds between sessions.

**Current behavior:**
- All Claude Code sessions spawned by the bridge share a single global task list. Planning feature A while building feature B mixes their tasks together.
- Back-to-back sessions in different repos see each other's stale tasks.
- Sessions that pause and resume inherit whatever tasks the previous session left behind.
- `_session_branch_name()` creates branch names but doesn't create worktrees — no filesystem isolation.
- Only one job per project can run at a time (serial queue).

**Desired outcome:**
- Every session gets an isolated task list automatically, with zero configuration.
- Planned work items get durable, named task lists that survive session restarts.
- Git worktrees provide filesystem isolation for parallel execution.
- The same slug ties together: task list, branch, worktree, plan doc, and GitHub issue.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on tier model, worktree experiment results)
- Review rounds: 1 (code review before merge)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Claude Code CLI installed | `which claude` | SDK spawns CLI as subprocess |
| `CLAUDE_CODE_TASK_LIST_ID` env var support | `CLAUDE_CODE_TASK_LIST_ID=test claude --print "list tasks" 2>&1` | Verify CLI respects task list scoping |
| Git worktree support | `git worktree list` | Required for filesystem isolation |
| Redis running | `python -c "import redis; redis.Redis().ping()"` | Session metadata storage |

## Solution

### Key Elements

- **Two-tier task list scoping**: Automatic isolation for all sessions (tier 1), graduating to named work-item isolation when a plan is created (tier 2).
- **`CLAUDE_CODE_TASK_LIST_ID` injection**: The SDK client passes a task list ID in the environment when spawning Claude Code, scoped per-session.
- **Git worktrees**: Each active work item operates in its own worktree directory for filesystem isolation.
- **Unified slug convention**: A single slug keys task list, branch, worktree, plan doc, and GitHub issue.

### Flow

**Tier 1 (automatic, pre-plan):**

Telegram message arrives → Bridge creates session → `CLAUDE_CODE_TASK_LIST_ID=thread-{telegram_thread_id}` set in env → Agent creates/reads tasks in isolated list → Session completes or pauses

For local Claude Code sessions: `CLAUDE_CODE_TASK_LIST_ID=session-{claude_session_id}` — ephemeral, no expectation of durability.

**Tier 2 (named, post-plan):**

User invokes `/make-plan {slug}` → Session's `CLAUDE_CODE_TASK_LIST_ID` switches to `{slug}` → Slug stored in session metadata → Branch `session/{slug}` and worktree `.worktrees/{slug}/` created at build time → All artifacts share the slug → Task list persists across session restarts

**Tier transition:**

Tier 1 tasks do NOT migrate to tier 2. They are scratch work. The plan document captures what matters. When `/make-plan` runs, it's a clean slate for the named task list.

### Technical Approach

**1. Inject `CLAUDE_CODE_TASK_LIST_ID` in `sdk_client.py`**

In `ValorAgent._create_options()` (line ~311), add the task list ID to the `env` dict. The value comes from a new parameter on `ValorAgent.__init__()` and `get_agent_response_sdk()`.

```python
# In _create_options():
if self.task_list_id:
    env["CLAUDE_CODE_TASK_LIST_ID"] = self.task_list_id
```

**2. Thread ID extraction in job queue**

In `_execute_job()` (job_queue.py:486), derive the task list ID before calling `get_agent_response_sdk()`:

- If the job has a `work_item_slug` (tier 2): use that
- Else: use `thread-{chat_id}-{reply_root_id}` for Telegram sessions (tier 1)

The `chat_id` + `reply_root_msg_id` combo uniquely identifies a Telegram thread. This is already available — the bridge constructs `session_id` from these same components.

**3. Work-item slug in session metadata**

Add a `work_item_slug` field to the `AgentSession` Redis model and the `Job` model. When `/make-plan {slug}` is invoked during a session, the agent (via a tool or hook) writes the slug to the session. Subsequent jobs in the same thread inherit it.

**4. Git worktrees for tier 2 work**

When `/build` starts execution on a planned work item:
- `git worktree add .worktrees/{slug} -b session/{slug}`
- Agent's `cwd` is set to the worktree directory
- On completion: merge back, remove worktree
- `.worktrees/` added to `.gitignore`

**5. Slug derivation for tier 1**

For tier 1, the "slug" is synthetic and disposable:
- Telegram: `thread-{chat_id}-{message_id}` (where `message_id` is the root of the reply chain)
- Local Claude Code: `session-{session_id}` (the CLI's own session ID, not controllable by us — but we don't need durability here)

## Rabbit Holes

- **Migrating tier 1 tasks to tier 2** — Tempting but unnecessary. Tier 1 tasks are investigation scratch work. The plan doc captures what matters. Don't build migration tooling.
- **Smart slug auto-detection** — Using LLM to auto-name work items from message content. Sounds cool, adds unreliable complexity. Slugs come from explicit `/make-plan` invocation only.
- **Worktree `.claude/` config resolution** — The SDK previously crashed in worktrees (d1e17b6b). Experiment first; if symlink works, that's the fix. Don't over-engineer before validating.
- **Cross-repo task coordination** — A single task list spanning multiple repos. Technically possible (task list is repo-independent) but don't build explicit tooling for it now. Just ensure the architecture doesn't prevent it.

## Risks

### Risk 1: `CLAUDE_CODE_TASK_LIST_ID` doesn't work as expected
**Impact:** Task list isolation fails silently — sessions still share tasks.
**Mitigation:** Experiment 4 (in issue #62) validates this before any code changes. If the env var doesn't work, fall back to file-system based isolation or skip task list scoping.

### Risk 2: SDK crashes in worktree directories
**Impact:** Can't use worktrees for filesystem isolation (blocking for parallel execution).
**Mitigation:** Experiments 1-3 in issue #62. Three approaches to try: copy `.claude/`, symlink `.claude/`, or `--cwd` flag. If none work, fall back to Option A (branch checkout, serial execution).

### Risk 3: Thread ID doesn't uniquely scope conversations
**Impact:** Different conversations in the same chat could share task lists.
**Mitigation:** Use `chat_id` + root `message_id` of the reply chain, not just `chat_id`. The bridge already computes this for session ID construction.

## No-Gos (Out of Scope)

- Don't build a slug rename utility yet — premature optimization
- Don't auto-detect slugs from message content
- Don't migrate tier 1 tasks to tier 2
- Don't build a UI for managing task lists
- Don't implement parallel execution in this plan (worktree creation is the foundation; parallelism is a separate change to the worker loop)
- Don't garbage-collect old tier 1 task lists yet (add a cleanup cron later)

## Update System

The update script (`scripts/remote-update.sh`) and update skill need minor changes:

- `.worktrees/` must be excluded from `git clean` operations during updates
- If an update happens while worktrees exist, the update should warn but not remove them
- No new dependencies required (git worktree is built-in, `CLAUDE_CODE_TASK_LIST_ID` is an env var)
- No config file changes needed for propagation

## Agent Integration

The agent's task isolation is transparent — it just sees its own task list via the `CLAUDE_CODE_TASK_LIST_ID` env var set by the SDK client. No MCP server changes needed.

- `sdk_client.py` passes the env var when spawning Claude Code (the only integration point)
- The bridge (`telegram_bridge.py`) passes the thread-derived task list ID through the job queue
- No changes to `.mcp.json` or `mcp_servers/` directory
- No new tools needed — the existing `TaskCreate`/`TaskList`/`TaskUpdate` tools work as-is, just scoped differently

Integration test: spawn two SDK sessions with different `CLAUDE_CODE_TASK_LIST_ID` values, create tasks in each, verify isolation.

## Documentation

- [ ] Create `docs/features/session-isolation.md` describing the two-tier model
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Update `CLAUDE.md` session management section with task list scoping details
- [ ] Update `docs/features/scale-job-queue-with-popoto-and-worktrees.md` to reference this as a prerequisite

## Success Criteria

- [ ] Telegram bridge sessions get automatic task list isolation via thread ID
- [ ] `/make-plan {slug}` switches session to a named, durable task list
- [ ] Two concurrent SDK sessions create tasks that don't leak between them
- [ ] Worktree experiments (1-3 from issue #62) completed with documented results
- [ ] `CLAUDE_CODE_TASK_LIST_ID` passed through `ValorAgent._create_options()` env dict
- [ ] `AgentSession` and `Job` models have `work_item_slug` field
- [ ] `.worktrees/` in `.gitignore`
- [ ] Documentation updated and indexed

## Team Orchestration

### Team Members

- **Builder (sdk-integration)**
  - Name: sdk-builder
  - Role: Wire `CLAUDE_CODE_TASK_LIST_ID` through sdk_client.py and job_queue.py
  - Agent Type: builder
  - Resume: true

- **Validator (sdk-integration)**
  - Name: sdk-validator
  - Role: Verify task list isolation works end-to-end
  - Agent Type: validator
  - Resume: true

- **Builder (worktree-setup)**
  - Name: worktree-builder
  - Role: Run worktree experiments, implement worktree creation/cleanup
  - Agent Type: builder
  - Resume: true

- **Validator (worktree-setup)**
  - Name: worktree-validator
  - Role: Verify worktrees work with SDK, no crashes
  - Agent Type: validator
  - Resume: true

- **Builder (model-updates)**
  - Name: model-builder
  - Role: Add work_item_slug to AgentSession and Job models
  - Agent Type: builder
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature docs and update indexes
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Run worktree experiments
- **Task ID**: experiment-worktrees
- **Depends On**: none
- **Assigned To**: worktree-builder
- **Agent Type**: builder
- **Parallel**: true
- Run experiments 1-3 from issue #62 (copy .claude, symlink .claude, --cwd flag)
- Document results in `docs/experiments/worktree-sdk-compatibility.md`
- Determine which approach works (or if none do)

### 2. Validate `CLAUDE_CODE_TASK_LIST_ID` behavior
- **Task ID**: experiment-task-list-id
- **Depends On**: none
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: true
- Run experiments 4-5 from issue #62
- Confirm env var scopes task lists correctly
- Document results

### 3. Add `work_item_slug` to Redis models
- **Task ID**: build-models
- **Depends On**: none
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `work_item_slug = Field(null=True)` to `AgentSession` in `models/sessions.py`
- Add `work_item_slug = Field(null=True)` to `Job` in `agent/job_queue.py`
- Add `task_list_id = Field(null=True)` to `Job` for the computed value

### 4. Wire task list ID through SDK client
- **Task ID**: build-sdk-integration
- **Depends On**: experiment-task-list-id
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `task_list_id` parameter to `ValorAgent.__init__()`
- Add `CLAUDE_CODE_TASK_LIST_ID` to env dict in `_create_options()`
- Add `task_list_id` parameter to `get_agent_response_sdk()`
- Pass it through from `_execute_job()` in job_queue.py

### 5. Compute task list ID in job execution
- **Task ID**: build-task-list-routing
- **Depends On**: build-sdk-integration, build-models
- **Assigned To**: sdk-builder
- **Agent Type**: builder
- **Parallel**: false
- In `_execute_job()`, compute task list ID:
  - If `job.work_item_slug`: use slug directly
  - Else: use `thread-{job.chat_id}-{root_message_id}`
- Extract root message ID from `job.session_id` (already encoded there)
- Pass computed ID to `get_agent_response_sdk()`

### 6. Validate task list isolation
- **Task ID**: validate-task-isolation
- **Depends On**: build-task-list-routing
- **Assigned To**: sdk-validator
- **Agent Type**: validator
- **Parallel**: false
- Spawn two SDK sessions with different task list IDs
- Create tasks in each, verify they don't leak
- Verify tier 1 → tier 2 transition works (new task list, clean slate)

### 7. Implement worktree creation (if experiments pass)
- **Task ID**: build-worktrees
- **Depends On**: experiment-worktrees
- **Assigned To**: worktree-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `.worktrees/` to `.gitignore`
- Create worktree utility functions (create, remove, cleanup)
- Add worktree creation to `/build` workflow execution path
- Add `git worktree prune` to startup cleanup

### 8. Validate worktree integration
- **Task ID**: validate-worktrees
- **Depends On**: build-worktrees
- **Assigned To**: worktree-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify worktree creation and cleanup works
- Verify SDK runs successfully in worktree directory
- Verify merge-back on completion

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-task-isolation, validate-worktrees
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/session-isolation.md`
- Add entry to `docs/features/README.md`
- Update CLAUDE.md session management section

### 10. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: sdk-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full validation: task isolation + worktree + documentation
- Verify all success criteria met
- Generate final report

## Validation Commands

- `CLAUDE_CODE_TASK_LIST_ID=test-a claude --print "create a task" && CLAUDE_CODE_TASK_LIST_ID=test-b claude --print "list tasks"` - task list isolation
- `git worktree add .worktrees/test-session -b session/test-session && ls .worktrees/test-session` - worktree creation
- `python -c "from models.sessions import AgentSession; print(AgentSession._fields)"` - model field verification
- `grep -r CLAUDE_CODE_TASK_LIST_ID agent/` - env var wired through SDK client
- `test -f docs/features/session-isolation.md` - documentation exists

---

## Open Questions

1. **Tier 1 thread ID composition**: Should it be `thread-{chat_id}-{root_message_id}` or just `thread-{chat_id}`? The former gives per-conversation isolation within a group chat; the latter gives per-chat isolation (simpler but coarser). Recommendation: per-conversation (`chat_id` + `root_message_id`), since a single group chat can have multiple concurrent topics.

2. **Worktree experiment blocking**: If all three worktree experiments fail (SDK still crashes), should we proceed with task list isolation only and defer filesystem isolation? Or block the whole plan?

3. **Tier 2 slug persistence**: When `/make-plan {slug}` sets the work_item_slug on the session, should subsequent messages in the same thread automatically inherit the slug? Or should each message explicitly reference the work item?
