# Checkpoint/Resume for Interrupted Sessions

**Issue:** #332
**Module:** `agent/checkpoint.py`

## Overview

When SDLC sessions are interrupted (bridge crash, API timeout, machine reboot), the checkpoint system preserves structured state so resumed sessions can skip completed work and reconstruct context.

## How It Works

### Checkpoint Files

Checkpoints are JSON files at `data/checkpoints/{slug}.json`. They are written atomically (write to `.tmp`, then rename) and contain:

- **session_id**: Links back to the AgentSession
- **slug**: Work item identifier (also the filename)
- **completed_stages**: Ordered list of stages that finished (e.g., `["PLAN", "BUILD"]`)
- **current_stage**: The last completed stage
- **artifacts**: Accumulated key-value pairs from each stage (plan_path, branch, pr_url, etc.)
- **retry_counts**: Per-stage retry counters
- **timestamp**: Last update time

### Save Points

Checkpoints are saved automatically by `bridge/stage_detector.py` whenever a stage transitions to "completed" status. This happens for any session that has a `work_item_slug` set. No manual checkpoint calls are needed.

### Resume Flow

1. `agent/job_queue.py:check_revival()` detects abandoned sessions
2. If the session's branch matches `session/{slug}`, load `data/checkpoints/{slug}.json`
3. Build compact context from checkpoint (completed stages, artifacts, next stage)
4. Include checkpoint context in the revival message sent to the agent
5. Revived agent sees what was already done and picks up at the next stage

### Cleanup

- `delete_checkpoint(slug)` removes a checkpoint after successful completion
- `cleanup_old_checkpoints(max_age_days=7)` removes stale checkpoints from abandoned sessions

## API Reference

```python
from agent.checkpoint import (
    PipelineCheckpoint,
    save_checkpoint,
    load_checkpoint,
    delete_checkpoint,
    record_stage_completion,
    record_stage_retry,
    get_next_stage,
    build_compact_context,
    check_worktree_recovery,
    cleanup_old_checkpoints,
)

# Create a checkpoint
cp = PipelineCheckpoint(session_id="sess-001", slug="my-feature")

# Record stage completion with artifacts
cp = record_stage_completion(cp, "PLAN", artifacts={"plan_path": "docs/plans/my-feature.md"})
save_checkpoint(cp)

# Resume: load and determine next stage
cp = load_checkpoint("my-feature")
next_stage = get_next_stage(cp)  # Returns "BUILD"

# Build context for the resumed agent
context = build_compact_context(cp)

# Cleanup after completion
delete_checkpoint("my-feature")
```

## Worktree Recovery

`check_worktree_recovery(repo_root, slug)` checks if a worktree directory exists for a slug. Returns a dict with `worktree_exists` (bool) and `worktree_path` (str, if exists). Callers decide what recovery actions to take (e.g., auto-commit uncommitted changes).

## Testing

23 unit tests in `tests/unit/test_checkpoint.py` covering:
- Checkpoint creation and serialization
- Save/load round-trips with atomic writes
- Stage advancement and deduplication
- Resume logic (next stage determination)
- Compact context generation
- Cleanup (deletion and age-based expiry)
- Worktree recovery detection
- Full lifecycle integration (create -> crash -> resume -> complete -> cleanup)
