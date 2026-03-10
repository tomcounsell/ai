# Checkpoint/Resume for Abandoned Sessions

**Status**: Shipped
**Issue**: [#332](https://github.com/tomcounsell/ai/issues/332)
**PR**: [#357](https://github.com/tomcounsell/ai/pull/357)

## Problem

When a session is interrupted (bridge crash, API timeout, machine reboot), the previous revival system was shallow: it detected that a branch existed but had no understanding of what work was completed. The revived agent got "Continue on branch `session/my-feature`" with no stage progress, no artifacts, and no guidance on what to do next. This caused duplicate work, wasted tokens, and required human intervention.

## Solution

Stage-aware checkpoints that persist pipeline progress to disk after each stage completion. On revival, the checkpoint provides rich context about completed stages and accumulated artifacts, allowing the agent to resume at the correct stage instead of starting over.

## How It Works

### Checkpoint Lifecycle

1. **Save**: When `apply_transitions()` in the stage detector records a stage as "completed", it calls `_save_stage_checkpoint()` which writes a JSON file to `data/checkpoints/{slug}.json`
2. **Load**: When `check_revival()` detects an existing session branch, it loads the checkpoint and builds a compact context string with completed stages, next stage, and artifacts
3. **Inject**: `queue_revival_job()` uses the checkpoint context as the PRIMARY revival message (the old bare "Continue on branch X" is now fallback only)
4. **Delete**: On successful job completion, `_execute_job()` deletes the checkpoint file
5. **Cleanup**: On bridge startup, `_recover_interrupted_jobs()` removes stale checkpoints older than 7 days

### Checkpoint Data

```python
@dataclass
class PipelineCheckpoint:
    session_id: str
    slug: str
    timestamp: str                    # ISO 8601
    current_stage: str                # last completed stage
    completed_stages: list[str]       # ["PLAN", "BUILD"]
    artifacts: dict[str, str]         # {plan_path, pr_url, branch, ...}
    retry_counts: dict[str, int]      # per-stage retry counters
    human_messages: list[str]         # queued steering messages
```

### Revival Message (example)

```
Resumed session for: my-feature
Completed stages: PLAN, BUILD
Next stage: TEST
Artifacts:
  - plan_path: docs/plans/my-feature.md
  - pr_url: https://github.com/org/repo/pull/42
  - branch: session/my-feature
```

## Key Files

| File | Role |
|------|------|
| `agent/checkpoint.py` | PipelineCheckpoint dataclass, save/load/delete, compact context builder, stale cleanup |
| `bridge/stage_detector.py` | `_save_stage_checkpoint()` called after each stage completion |
| `agent/job_queue.py` | `check_revival()` loads checkpoint; `queue_revival_job()` uses it as primary message; cleanup on completion and startup |
| `bridge/telegram_bridge.py` | Revival notification includes stage progress from checkpoint |

## Design Decisions

- **File-based persistence**: Checkpoints use `data/checkpoints/{slug}.json` (not Redis) because they must survive bridge restarts and Redis flushes
- **Atomic writes**: Uses tmp file + `os.replace()` for crash safety
- **Stage-level granularity**: Checkpoints are saved per-stage, not per-tool-call — sufficient for SDLC pipeline recovery
- **Graceful degradation**: All checkpoint operations are wrapped in try/except; failures log warnings but never break the main flow
- **Deduplication**: `record_stage_completion()` checks before appending to prevent duplicate stage entries
- **No auto-resume**: Revival still requires human confirmation ("Reply to resume") — the checkpoint enriches the context, not the trigger
