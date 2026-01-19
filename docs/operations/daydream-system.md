# Daydream System

## Overview

The Daydream System is a long-running autonomous maintenance process. It runs as a single process that can pick up where it left off if interrupted or if it fails.

## Process Steps

### Step 1: Clean Up Legacy Code

- Identify and remove deprecated patterns
- Delete commented-out code blocks
- Remove unused imports and dead code
- Eliminate temporary bridges or half-migrations
- Clean up `.pyc`, `__pycache__`, and other build artifacts

### Step 2: Review Previous Day's Logs

- Analyze application logs from the last 24 hours
- Identify recurring issues or patterns
- Propose improvements based on observed behavior
- Flag anomalies for supervisor review
- Archive processed logs

### Step 3: Check Error Logs (Sentry)

- Query Sentry for new or recurring errors
- Categorize by severity and frequency
- Link errors to relevant code sections
- Suggest fixes for common issues
- Track resolution status

### Step 4: Clean Up Task Management

- Review and update the to-do list
- Close completed or stale items
- Update old plans that are no longer relevant
- Prioritize remaining work
- Archive completed tasks

### Step 5: Update Documentation

- Ensure documentation reflects recent code changes
- Update API docs if endpoints changed
- Refresh configuration examples
- Mark deprecated features
- Verify links and references

### Step 6: Produce Daily Report

- Summarize all maintenance actions taken
- List errors found and fixes proposed
- Report documentation updates
- Highlight items requiring supervisor attention
- Store report for historical reference

## Resumability

Each step persists its progress. If the process fails mid-execution:

- The process restarts from the last incomplete step
- Completed steps are not repeated
- Partial work within a step is preserved where possible

### State Persistence

```python
@dataclass
class DaydreamState:
    """Persisted state for resumability."""
    current_step: int = 1
    step_started_at: datetime | None = None
    step_progress: dict = field(default_factory=dict)
    completed_steps: list[int] = field(default_factory=list)
    daily_report: list[str] = field(default_factory=list)
```

### Recovery Example

```python
async def run_daydream():
    state = await load_state()

    for step in range(state.current_step, 7):
        try:
            await run_step(step, state)
            state.completed_steps.append(step)
            await save_state(state)
        except Exception as e:
            logger.error(f"Step {step} failed: {e}")
            await save_state(state)  # Save progress
            raise  # Exit for retry later
```

## Output

### Daily Report Format

```markdown
# Daydream Report - {date}

## Summary
- Steps completed: 6/6
- Duration: {duration}
- Issues found: {count}

## Legacy Code Cleanup
- Removed {n} dead code blocks
- Cleaned {m} deprecated patterns

## Log Analysis
- Analyzed {n} log entries
- Found {m} recurring patterns
- Flagged {k} anomalies

## Sentry Review
- {n} new errors
- {m} recurring issues
- {k} suggested fixes

## Task Management
- Closed {n} stale items
- Updated {m} priorities

## Documentation
- Updated {n} files
- Verified {m} links

## Action Required
- {list of items needing supervisor attention}
```

### Report Storage

Reports are stored in `logs/daydream/` with timestamp-based naming:
- `logs/daydream/report_2026-01-19.md`
- `logs/daydream/state.json` (current state)

## Running the Daydream

### Manual Execution

```bash
python scripts/daydream.py
```

### Automated (via scheduler)

The daydream process can be triggered by external schedulers (cron, systemd timer, etc.) or run as a long-lived process with internal timing.

### Monitoring

Check daydream status and recent reports:
- State file: `logs/daydream/state.json`
- Reports: `logs/daydream/report_*.md`
- Logs: Standard application logs with `daydream` prefix
