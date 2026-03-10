# Checkpoint/Resume for Abandoned and Interrupted Sessions

**Issue:** #332
**Branch:** `session/checkpoint_resume`
**Status:** Complete

## Problem

When a session is interrupted -- bridge crash, API timeout, machine reboot, or Claude Code process death -- the work in progress is lost. The revived agent starts with limited context about what was already accomplished, leading to duplicate work, lost progress, and context gaps.

## Solution

Structured checkpoint files saved after each SDLC stage completes, enabling deterministic resume that skips completed work and reconstructs context.

## Implementation

### Phase 1: Checkpoint serialization (Complete)
- [x] `agent/checkpoint.py` with `PipelineCheckpoint` dataclass
- [x] Save/load to `data/checkpoints/{slug}.json`
- [x] Atomic writes (write to .tmp then rename)
- [x] Corrupt file handling (returns None, logs warning)

### Phase 2: Checkpoint save points (Complete)
- [x] `bridge/stage_detector.py` saves checkpoint after each stage completion
- [x] Checkpoint includes accumulated artifacts (plan_path, branch, pr_url)
- [x] Only triggers for sessions with a work_item_slug

### Phase 3: Resume from checkpoint (Complete)
- [x] `agent/job_queue.py:check_revival` loads checkpoint if it exists
- [x] Builds compact context from checkpoint artifacts
- [x] `queue_revival_job` includes checkpoint context in revival message
- [x] Resumed agent sees completed stages and can skip to next

### Phase 4: Worktree recovery (Complete)
- [x] `check_worktree_recovery()` detects if worktree exists for a slug
- [x] Reports worktree state for callers to decide recovery actions

### Phase 5: Cleanup (Complete)
- [x] `delete_checkpoint()` for post-completion cleanup
- [x] `cleanup_old_checkpoints(max_age_days=7)` for abandoned checkpoints

## No-Gos

- No LLM session serialization (impossible -- acknowledged by Attractor too)
- No checkpoint after every tool call (too granular -- stage-level is sufficient)
- No thread ID resolution cascade (we use session IDs)

## Update System

No update system changes required -- this feature is purely internal to the agent pipeline. The checkpoint module uses only stdlib (json, dataclasses, pathlib) with no new dependencies.

## Agent Integration

No new MCP server needed. The checkpoint system integrates directly into existing bridge components:
- `bridge/stage_detector.py` saves checkpoints automatically on stage completion
- `agent/job_queue.py` loads checkpoints during revival checks
- No changes to `.mcp.json` required

## Documentation

- [x] Create `docs/features/checkpoint-resume.md` describing the feature
- [x] Plan document at `docs/plans/checkpoint_resume.md`

## Testing

- [x] 23 unit tests in `tests/unit/test_checkpoint.py`
- [x] Full lifecycle test (create -> save -> crash -> reload -> resume -> cleanup)
- [x] Edge cases: corrupt files, missing checkpoints, duplicate stages, old cleanup
