# Queue Status & Management

Inspect and manipulate the job queue. All operations go through `python -m tools.agent_session_scheduler`.

## Quick Status

```bash
python -m tools.agent_session_scheduler status
```

Shows: pending count, running jobs, recent completions, per-job details including priority, scheduled_after, and issue links.

## Queue Inspection

### View all projects
```bash
for project in valor django-project-template popoto; do
  echo "=== $project ==="
  python -m tools.agent_session_scheduler status --project "$project"
done
```

### View specific project
```bash
python -m tools.agent_session_scheduler status --project <project_key>
```

## Queue Manipulation

### Schedule SDLC job for a GitHub issue
```bash
python -m tools.agent_session_scheduler schedule --issue <NUMBER>
python -m tools.agent_session_scheduler schedule --issue <NUMBER> --priority high
python -m tools.agent_session_scheduler schedule --issue <NUMBER> --after "2026-03-12T02:00:00Z"
```

### Push arbitrary message as job
```bash
python -m tools.agent_session_scheduler push --message "What is the current architecture?"
python -m tools.agent_session_scheduler push --message "Fix the bug in bridge.py" --priority high
```

### Bump job to top of queue
```bash
python -m tools.agent_session_scheduler bump --job-id <JOB_ID>
```
Sets priority to `urgent` and resets created_at to now.

### Pop next job (remove without executing)
```bash
python -m tools.agent_session_scheduler pop
python -m tools.agent_session_scheduler pop --project <project_key>
```

### Cancel specific job
```bash
python -m tools.agent_session_scheduler cancel --job-id <JOB_ID>
```

## Priority Levels

| Priority | Rank | Use Case |
|----------|------|----------|
| urgent   | 0    | Production outage, critical fix |
| high     | 1    | Recovery jobs, interrupted work |
| normal   | 2    | Default for all new jobs |
| low      | 3    | Catchup messages, revival, reflections |

Within the same priority tier, jobs are processed FIFO (oldest first).

## Safety Limits

- **Scheduling depth cap**: 3 levels deep (a scheduled job can schedule further jobs, but max chain depth = 3)
- **Rate limit**: 30 scheduled jobs per hour per project
- **Deferred jobs**: Jobs with `--after` in the future are skipped by the worker until the time arrives

## Output Format

All commands return structured JSON:
```json
{"status": "queued", "agent_session_id": "...", "queue_position": 2}
{"status": "error", "message": "Rate limit exceeded"}
```
