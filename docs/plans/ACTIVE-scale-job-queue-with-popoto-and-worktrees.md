# Scale Job Queue with Popoto + Git Worktrees

## Problem

The current job queue (`agent/job_queue.py`) has two scaling bottlenecks:

1. **JSON file persistence** -- Every push/pop reads and rewrites the entire file. A crash mid-write corrupts the file and silently loses all queued jobs. No atomicity, no crash recovery.

2. **Serial execution per project** -- Git checkout changes the entire working tree, so only one job can run at a time per project. A 10-minute SDK task blocks all other messages for that project.

Both are solvable with minimal code changes by leveraging infrastructure we already have.

## Solution

### Part 1: Replace JSON persistence with Popoto (Redis ORM)

**Why Popoto**: Redis is already running on all machines. Popoto is our own library with Django-like syntax, atomic Redis operations, and sorted sets for priority ordering. No new infrastructure needed.

**Reference**: [popoto.readthedocs.io](https://popoto.readthedocs.io/en/latest/)
- [Fields documentation](https://popoto.readthedocs.io/en/latest/fields/) -- KeyField, SortedField, AutoKeyField
- [Query documentation](https://popoto.readthedocs.io/en/latest/query/) -- Filtering, ordering, sorted field range queries

**Job model**:

```python
from popoto import Model, AutoKeyField, KeyField, SortedField, Field

class Job(Model):
    job_id = AutoKeyField()
    project_key = KeyField()
    status = KeyField(default="pending")       # pending | running | completed | failed
    priority = SortedField(type=int, sort_by="project_key")
    created_at = SortedField(type=float, sort_by="project_key")
    session_id = Field()
    working_dir = Field()
    message_text = Field()
    sender_name = Field()
    chat_id = Field()
    message_id = Field(type=int)
    chat_title = Field(null=True)
    chat_title = Field(null=True)
    revival_context = Field(null=True)
    worktree_dir = Field(null=True)            # Set when job starts (Part 2)
```

**What this replaces**: The entire `ProjectJobQueue` class (load/save JSON, fcntl locking). Instead:

```python
# Push (atomic Redis HSET + ZADD)
Job.create(project_key="valor", status="pending", priority=10, ...)

# Pop highest priority pending job (atomic read + update)
job = Job.query.filter(project_key="valor", status="pending", _order_by_="-priority", _limit_=1)[0]
job.status = "running"
job.save()

# Remove
job.delete()

# Count
depth = Job.query.count(project_key="valor", status="pending")
```

**What survives crashes**: Everything. Redis persists with RDB/AOF. No corrupt JSON, no lost jobs.

**Code changes required**:
- `agent/job_queue.py`: Replace `ProjectJobQueue` class (~40 lines) with the popoto `Job` model (~20 lines). The `_worker_loop`, `_execute_job`, callback registry, and revival detection stay the same.
- `requirements.txt`: Add `popoto`
- Delete: `data/job_queue/` directory (no longer needed)
- Delete: `MessageQueue` class from `bridge/telegram_bridge.py` (fully superseded)

### Part 2: Git worktrees for parallel job execution

**Why**: The current architecture serializes jobs because `git checkout` mutates the entire working tree. Git worktrees let each job operate in its own isolated directory, enabling parallel execution.

**How it works**:

```
/Users/valorengels/src/ai/                    # Main worktree (main branch, always clean)
  .git/worktrees/                             # Git manages these
  .worktrees/                                 # Our convention for worktree dirs
    session-tg_valor_12345_678/               # Job 1's isolated working tree
    session-tg_valor_12345_999/               # Job 2's isolated working tree (parallel)
```

```bash
# Create worktree for a job (replaces git checkout)
git worktree add .worktrees/session-abc123 -b session/abc123

# Agent runs with cwd=.worktrees/session-abc123 (isolated from other jobs)

# Finish: merge back and clean up
git merge --no-ff session/abc123
git worktree remove .worktrees/session-abc123
git branch -d session/abc123
```

**Code changes required**:
- `agent/job_queue.py`: Replace `_checkout_session_branch()` (~35 lines) with worktree create (~15 lines). Replace `_finish_branch()` (~95 lines) with worktree merge+remove (~40 lines). Remove serialization constraint from `_worker_loop` (allow concurrent jobs).
- No changes to `bridge/telegram_bridge.py` (working_dir resolution happens in job_queue)
- No changes to `agent/sdk_client.py` (receives `cwd` from job_queue, works the same)

**Concurrency model change**:

```
BEFORE (serial):
  Worker pops job -> checkout branch -> run agent -> merge -> pop next job

AFTER (parallel):
  Worker pops job -> create worktree -> spawn agent task (non-blocking)
  Worker pops job -> create worktree -> spawn agent task (non-blocking)
  ...each agent task merges its own worktree on completion
```

Optional: cap concurrent jobs per project (e.g., max 3) to limit resource usage.

### Part 3: Remove dead code

With Parts 1 and 2, these become unnecessary:
- `MessageQueue` class in `bridge/telegram_bridge.py` (JSON file queue, fully replaced by popoto)
- `data/pending_messages.json` (message queue file)
- `data/job_queue/*.json` (per-project queue files)
- `agent/pr_manager.py` (unused, already identified in PR #10 review)

## Implementation Order

**Phase 1 -- Popoto job model** (smallest change, biggest reliability win):
1. Add `popoto` to requirements
2. Define `Job` model
3. Replace `ProjectJobQueue._load/_save/push/pop` with popoto CRUD
4. Keep serial execution (don't change worker loop yet)
5. Test: verify jobs persist across bridge restarts

**Phase 2 -- Git worktrees** (unlocks parallelism):
1. Replace `_checkout_session_branch` with `git worktree add`
2. Replace `_finish_branch` with worktree merge+remove
3. Remove serial wait from worker loop
4. Add configurable concurrency cap
5. Test: send multiple messages rapidly, verify parallel processing

**Phase 3 -- Cleanup**:
1. Remove `MessageQueue`, `pending_messages.json`, `data/job_queue/`
2. Remove `agent/pr_manager.py`
3. Update CLAUDE.md to document new architecture

## Research Questions to Resolve Before Building

1. **Popoto async wrapping** -- Popoto is synchronous. The bridge is async. Options:
   - `asyncio.to_thread()` (Python 3.9+, simplest)
   - `loop.run_in_executor()` (more control)
   - Profile first: Redis calls are sub-millisecond, blocking may be acceptable in practice

2. **Worktree disk usage** -- Each worktree is a full copy of the working tree (not .git objects, just files). For this repo that's ~small. But for large projects monitored via multi-project config, need to check sizes. Worktrees share the .git object store so it's only file copies, not full clones.

3. **Worktree cleanup on crash** -- If the bridge dies with active worktrees, they remain on disk. Startup should run `git worktree prune` and clean up orphaned directories.

4. **Merge conflicts** -- Parallel worktrees writing to the same files will conflict on merge. Options:
   - Accept: most agent work touches different files per session
   - Mitigate: merge serially (parallel execution, serial merge step)
   - Detect: if merge fails, keep the branch and notify user

5. **Redis connection** -- Verify popoto picks up `REDIS_URL` from `.env` or uses localhost default. Check which Redis DB index to use (avoid collisions with other services).

6. **Worker-per-project vs shared pool** -- Current architecture: one worker task per project. With worktrees: could use a shared worker pool across projects. Research whether the per-project model still makes sense or if a global pool with per-project concurrency limits is simpler.

## Success Criteria

- Jobs survive bridge crashes without data loss
- Multiple messages to the same project process in parallel (not queued for minutes)
- No increase in code complexity (net line count should decrease)
- Redis is the single source of truth for queue state (no JSON files)
- Works across all machines with no additional infrastructure
