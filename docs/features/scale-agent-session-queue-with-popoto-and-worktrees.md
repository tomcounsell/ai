# Scale Agent Session Queue with Popoto + Git Worktrees

The agent session queue (`agent/agent_session_queue.py`) persists jobs in Redis
through Popoto (a Django-style ORM over Redis) and executes them in parallel git
worktrees, one per session. Redis is already running on every machine, so no
additional infrastructure is required.

## Persistence

Jobs live in the Popoto `Job` model:

```python
from popoto import Model, AutoKeyField, KeyField, SortedField, Field

class Job(Model):
    agent_session_id = AutoKeyField()
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
    revival_context = Field(null=True)
    worktree_dir = Field(null=True)            # Set when the job starts
```

Push, pop, remove, and count are atomic Redis operations (`HSET` + `ZADD`):

```python
# Push (atomic)
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

Redis persists with RDB/AOF, so a crash cannot corrupt a JSON file or lose
queued jobs. `check_revival()` queries Redis by `chat_id` rather than scanning
git branches.

Popoto reference: [popoto.readthedocs.io](https://popoto.readthedocs.io/en/latest/)
([Fields](https://popoto.readthedocs.io/en/latest/fields/),
[Query](https://popoto.readthedocs.io/en/latest/query/)).

## Parallel execution with git worktrees

Each job runs in its own isolated worktree, so jobs for the same project run in
parallel instead of serializing on `git checkout`:

```
~/src/ai/                    # Main worktree (main branch, always clean)
  .git/worktrees/                             # Git manages these
  .worktrees/                                 # Our convention for worktree dirs
    session-tg_valor_12345_678/               # Job 1's isolated working tree
    session-tg_valor_12345_999/               # Job 2's isolated working tree (parallel)
```

The worker creates a worktree for a job, runs the agent with `cwd` set inside
it, then merges the branch back and removes the worktree:

```bash
# Create worktree for a job
git worktree add .worktrees/session-abc123 -b session/abc123

# Agent runs with cwd=.worktrees/session-abc123 (isolated from other jobs)

# Finish: merge back and clean up
git merge --no-ff session/abc123
git worktree remove .worktrees/session-abc123
git branch -d session/abc123
```

Each agent task merges and removes its own worktree on completion. A
per-project concurrency cap (for example, a maximum of three) bounds resource
usage.

## Concurrency model

The worker pops a job, creates its worktree, and spawns the agent task without
blocking, so the next job is popped immediately. Each agent task merges its own
worktree when it finishes.

## Async Redis access

The enqueue/write path wraps Popoto calls in `asyncio.to_thread()` directly
(`agent/agent_session_queue.py`). The worker drain loop's hot-path read (the
idle-check query) runs through `offload_redis()`, a dedicated measured seam on a
bounded `run_in_executor()` bulkhead — see
[Off-Loop Redis Access](redis-durability.md#off-loop-redis-access-fix-4) for the
thread-safety contract and bulkhead sizing.

## Known Limitations

### Worktree disk usage

A worktree is a full copy of the working-tree files (the `.git` object store is
shared, so it is file copies, not a full clone). Large multi-project repositories
are worth checking for per-worktree size.

### Worktree cleanup on crash

If the bridge dies with active worktrees, they remain on disk. Recovery requires
`git worktree prune` and cleanup of orphaned directories.

### Merge conflicts

Parallel worktrees that write the same files conflict on merge. Mitigations:
merge serially (parallel execution, serial merge step), or, if a merge fails,
keep the branch and notify the user.
