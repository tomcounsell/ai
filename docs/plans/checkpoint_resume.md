---
status: Ready
type: feature
appetite: Medium
owner: Valor Engels
created: 2026-03-10
tracking: https://github.com/tomcounsell/ai/issues/332
---

# Checkpoint/Resume: Replace Shallow Revival with Stage-Aware Recovery

## Problem

When a session is interrupted (bridge crash, API timeout, machine reboot), the current revival system is **shallow** — it detects that a branch exists but has no understanding of what work was completed. The revived agent gets a message like "Continue on branch `session/my-feature`" with no stage progress, no artifacts, and no guidance on what to do next.

### Current Revival Flow (What's Inadequate)

```
Bridge startup / new message arrives
    │
    ▼
check_revival() — "does a session branch exist in git?"
    │  ✗ No stage progress
    │  ✗ No artifacts (plan path, PR URL)
    │  ✗ No next-stage guidance
    ▼
Sends Telegram: "Unfinished work detected on branch X. Reply to resume."
    │
    ▼ (BLOCKS: waits for human reply)
    │
User replies to message
    │
    ▼
queue_revival_job() — enqueues "Continue on branch X"
    │  ✗ Almost no context
    │  ✗ Low priority
    │  ✗ Agent must figure out where it was
    ▼
Agent starts fresh, re-runs completed stages, wastes tokens
```

### Specific Failure Modes

1. **Duplicate work**: Revived agent re-creates a plan that already exists, re-opens a PR that's already open
2. **Context gap**: Agent gets "Continue on branch X" but doesn't know PLAN is done and BUILD is next
3. **Human bottleneck**: Revival requires user to reply — unattended sessions stay dead
4. **Thin context**: `queue_revival_job()` passes only branch name + user reply, no stage/artifact data
5. **Cooldown masking**: 24-hour cooldown prevents re-notification, so stale work stays invisible

## Solution: Stage-Aware Checkpoint System

### Target Revival Flow

```
Stage completes (detected by stage_detector)
    │
    ▼
_save_stage_checkpoint() — saves {slug}.json with stages, artifacts, next stage
    │  ✓ Completed stages recorded
    │  ✓ Artifacts accumulated (plan_path, pr_url, branch)
    │  ✓ Next stage computed
    │
    ... session interrupted ...
    │
    ▼
check_revival() — finds branch + loads checkpoint
    │  ✓ Knows PLAN and BUILD completed
    │  ✓ Has plan_path, pr_url, branch artifacts
    │  ✓ Knows TEST is next
    ▼
queue_revival_job() — enqueues with RICH context:
    "Resumed session for: my-feature
     Completed stages: PLAN, BUILD
     Next stage: TEST
     Artifacts:
     - plan_path: docs/plans/my-feature.md
     - pr_url: https://github.com/org/repo/pull/42"
    │
    ▼
Agent picks up at TEST, skips PLAN and BUILD entirely
```

### What Changes (Code Replacements, Not Just Additions)

| File | What Changes | Replaces |
|------|-------------|----------|
| `agent/checkpoint.py` | **NEW**: PipelineCheckpoint dataclass, save/load/delete, stage recording | Nothing (new capability) |
| `bridge/stage_detector.py` | **REPLACE**: Add `_save_stage_checkpoint()` after stage completion | Currently stage completions are fire-and-forget — no persistent record |
| `agent/job_queue.py: check_revival()` | **REPLACE**: Shallow git-only detection with checkpoint-enriched detection | Current returns only `{branch, has_uncommitted, plan_context[:200]}` |
| `agent/job_queue.py: queue_revival_job()` | **REPLACE**: Thin "Continue on branch X" with rich checkpoint context as the primary message | Current message is just branch name + user reply |
| `agent/job_queue.py: _execute_job()` | **ADD**: Delete checkpoint on successful job completion | Currently no cleanup of persistent state |
| `agent/job_queue.py: _recover_interrupted_jobs()` | **ADD**: Stale checkpoint cleanup on startup | Currently no cleanup of abandoned checkpoints |
| `bridge/telegram_bridge.py` revival notification | **ENHANCE**: Include stage progress in notification message | Current shows only branch name |

### What Gets Removed/Deprecated

- `plan_context[:200]` in `check_revival()` return — superseded by full checkpoint context
- The bare `"Continue the unfinished work on branch X"` message in `queue_revival_job()` — replaced by rich checkpoint context

## Prior Art

- **Attractor** (StrongDM): Serializes pipeline state to `checkpoint.json` after each node completes. On resume, restores state and skips completed nodes. We adapt this for our linear SDLC pipeline.
- **PR #352** (closed): First attempt at checkpoint integration. Added the module but didn't replace anything — just appended context as a string alongside the existing shallow revival path. This plan corrects that by making checkpoints the primary detection and context mechanism.

## Data Flow

### Checkpoint Save (on each stage completion)

```
stage_detector.apply_transitions()
    │ stage marked "completed"
    ▼
_save_stage_checkpoint(session, stage)
    │ loads or creates PipelineCheckpoint
    │ records stage completion + session artifacts
    ▼
data/checkpoints/{slug}.json (atomic write: .tmp → rename)
```

### Checkpoint Load (on revival)

```
check_revival()
    │ finds session branches in Redis + git
    │ extracts slug from branch name
    ▼
load_checkpoint(slug)
    │ returns PipelineCheckpoint or None
    ▼
build_compact_context(checkpoint)
    │ "Completed: PLAN, BUILD. Next: TEST. Artifacts: ..."
    ▼
Returned in revival_info["checkpoint_context"]
```

### Checkpoint Cleanup

```
Successful job completion (_execute_job)
    ▼
delete_checkpoint(slug)    # removes data/checkpoints/{slug}.json

Bridge startup (_recover_interrupted_jobs)
    ▼
cleanup_old_checkpoints(max_age_days=7)    # removes abandoned checkpoints
```

## Implementation

### Phase 1: Checkpoint Module (`agent/checkpoint.py`)

```python
@dataclass
class PipelineCheckpoint:
    session_id: str
    slug: str                            # work_item_slug
    timestamp: str                       # ISO 8601
    current_stage: str                   # last completed stage
    completed_stages: list[str]          # ["PLAN", "BUILD"]
    artifacts: dict[str, str]            # {plan_path, pr_url, branch, ...}
    retry_counts: dict[str, int]         # per-stage retry counters
    human_messages: list[str]            # queued steering messages

# Core API:
save_checkpoint(cp)                      # atomic write to data/checkpoints/{slug}.json
load_checkpoint(slug) -> CP | None       # deserialize, None if missing/corrupt
delete_checkpoint(slug)                  # remove on completion
record_stage_completion(cp, stage, artifacts) -> CP
get_next_stage(cp) -> str | None         # first stage not in completed_stages
build_compact_context(cp) -> str         # human-readable summary for revival
cleanup_old_checkpoints(max_age_days=7)  # remove stale checkpoints
```

### Phase 2: Save Points in Stage Detector

In `bridge/stage_detector.py`, add `_save_stage_checkpoint()` called after each "completed" transition is applied:

```python
def _save_stage_checkpoint(session, stage: str) -> None:
    """Save checkpoint after stage completion. Only for sessions with work_item_slug."""
    slug = getattr(session, "work_item_slug", None)
    if not slug:
        return

    checkpoint = load_checkpoint(slug) or PipelineCheckpoint(session_id=..., slug=slug)

    # Extract artifacts from session links
    artifacts = {}
    for attr, key in [("issue_url", "issue_url"), ("pr_url", "pr_url"),
                       ("plan_url", "plan_path"), ("branch_name", "branch")]:
        val = getattr(session, attr, None)
        if val:
            artifacts[key] = str(val)

    record_stage_completion(checkpoint, stage, artifacts=artifacts or None)
    save_checkpoint(checkpoint)
```

### Phase 3: Replace Revival Path in Job Queue

**`check_revival()`** — replace the shallow return with checkpoint-enriched data:

```python
# BEFORE (current):
return {
    "branch": branches[0],
    "all_branches": branches,
    "has_uncommitted": state.has_uncommitted_changes,
    "plan_context": plan_context[:200] if plan_context else "",
}

# AFTER:
checkpoint_context = ""
slug = branches[0].replace("session/", "", 1)
checkpoint = load_checkpoint(slug)
if checkpoint:
    checkpoint_context = build_compact_context(checkpoint)

return {
    "branch": branches[0],
    "all_branches": branches,
    "has_uncommitted": state.has_uncommitted_changes,
    "plan_context": plan_context[:200] if plan_context else "",  # kept for backward compat
    "checkpoint_context": checkpoint_context,  # NEW: rich stage-aware context
}
```

**`queue_revival_job()`** — replace thin message with checkpoint context as PRIMARY:

```python
# BEFORE (current):
revival_text = f"Continue the unfinished work on branch `{revival_info['branch']}`."
if additional_context:
    revival_text += f"\n\nUser responded with: {additional_context}"

# AFTER:
checkpoint_ctx = revival_info.get("checkpoint_context", "")
if checkpoint_ctx:
    # Checkpoint is the PRIMARY context — not a supplement
    revival_text = checkpoint_ctx
    if additional_context:
        revival_text += f"\n\nUser context: {additional_context}"
else:
    # Fallback: no checkpoint available (ad-hoc session or old data)
    revival_text = f"Continue the unfinished work on branch `{revival_info['branch']}`."
    if additional_context:
        revival_text += f"\n\nUser responded with: {additional_context}"
```

**`_execute_job()` completion** — delete checkpoint after success:

```python
# After mark_work_done() call:
if job.work_item_slug:
    try:
        from agent.checkpoint import delete_checkpoint
        delete_checkpoint(job.work_item_slug)
    except Exception as e:
        logger.warning(f"Checkpoint cleanup failed for {job.work_item_slug}: {e}")
```

**`_recover_interrupted_jobs()` startup** — clean up stale checkpoints:

```python
# At the end of the function:
try:
    from agent.checkpoint import cleanup_old_checkpoints
    cleaned = cleanup_old_checkpoints(max_age_days=7)
    if cleaned:
        logger.info(f"Cleaned {len(cleaned)} stale checkpoint(s)")
except Exception as e:
    logger.warning(f"Stale checkpoint cleanup failed: {e}")
```

### Phase 4: Enhance Revival Notification

In `bridge/telegram_bridge.py`, include stage progress in the revival message:

```python
# BEFORE:
revival_msg = f"Unfinished work detected on branch `{revival_info['branch']}`"
if revival_info.get("plan_context"):
    revival_msg += f"\n\n> {revival_info['plan_context']}"

# AFTER:
revival_msg = f"Unfinished work detected on branch `{revival_info['branch']}`"
checkpoint_ctx = revival_info.get("checkpoint_context", "")
if checkpoint_ctx:
    revival_msg += f"\n\n{checkpoint_ctx}"
elif revival_info.get("plan_context"):
    revival_msg += f"\n\n> {revival_info['plan_context']}"
```

## Failure Path Test Strategy

### Exception Handling Coverage
- `save_checkpoint()` failure → log warning, don't block stage detection
- `load_checkpoint()` corrupt file → return None, log warning
- `load_checkpoint()` missing file → return None (normal case)
- Checkpoint cleanup failure → log warning, don't block startup

### Edge Cases
- Session with no `work_item_slug` → no checkpoint saved (tier 1 only)
- Checkpoint for slug that no longer has a branch → cleaned up by age-based cleanup
- Crash between stage completion and checkpoint save → next stage re-detected on resume
- Duplicate stage completion calls → `record_stage_completion()` deduplicates

## No-Gos

- Do NOT auto-resume without human confirmation (keep "Reply to resume" for now)
- Do NOT modify `bridge/observer.py` (Observer is unrelated to revival)
- Do NOT serialize LLM conversation state (impossible, acknowledged by Attractor)
- Do NOT checkpoint after every tool call (stage-level granularity is sufficient)
- Do NOT add new dependencies (checkpoint uses only stdlib: json, dataclasses, pathlib)

## Update System

No update system changes required. Checkpoint files are local to `data/checkpoints/` and use only stdlib. No new dependencies or config propagation needed.

## Agent Integration

No new MCP server needed. The checkpoint system integrates directly into existing bridge components:
- `bridge/stage_detector.py` saves checkpoints automatically on stage completion
- `agent/job_queue.py` loads checkpoints during revival checks
- No changes to `.mcp.json` required

## Documentation

- [ ] Create `docs/features/checkpoint-resume.md` describing the feature and API
- [ ] Add entry to `docs/features/README.md` index table

## Verification

| # | Check | Command | Expected |
|---|-------|---------|----------|
| 1 | Checkpoint saved on stage completion | Unit test: apply_transitions with slug → checkpoint file exists | Pass |
| 2 | Checkpoint accumulates stages | Unit test: two completions → both in completed_stages | Pass |
| 3 | Checkpoint extracted artifacts | Unit test: session with pr_url → checkpoint.artifacts has pr_url | Pass |
| 4 | Revival loads checkpoint context | Unit test: load_checkpoint → build_compact_context contains stages | Pass |
| 5 | Revival message uses checkpoint as primary | Unit test: queue_revival_job with checkpoint_ctx → message starts with checkpoint | Pass |
| 6 | Checkpoint deleted on completion | Unit test: delete_checkpoint → load returns None | Pass |
| 7 | Stale checkpoints cleaned on startup | Unit test: old file + cleanup → file removed | Pass |
| 8 | No checkpoint without slug | Unit test: session without work_item_slug → no checkpoint file | Pass |
| 9 | Corrupt checkpoint handled | Unit test: bad JSON → load returns None, no crash | Pass |
| 10 | Full lifecycle E2E | Unit test: save → crash → load → resume → complete → cleanup | Pass |
| 11 | All existing tests pass | `pytest tests/unit/ -q` | 0 failures |
| 12 | Lint clean | `python -m ruff check agent/checkpoint.py bridge/stage_detector.py agent/job_queue.py` | 0 errors |

## Success Criteria

- [ ] `check_revival()` returns checkpoint context (not just branch name)
- [ ] `queue_revival_job()` uses checkpoint as PRIMARY message (not just branch name)
- [ ] Revival notification includes stage progress (not just "Unfinished work on branch X")
- [ ] Checkpoints deleted after successful completion (no stale data)
- [ ] Stale checkpoints cleaned on startup (>7 days)
- [ ] No checkpoint saved for sessions without `work_item_slug`
- [ ] All unit tests pass with zero regressions
- [ ] PR diff shows lines REMOVED from revival path (not just additions)
