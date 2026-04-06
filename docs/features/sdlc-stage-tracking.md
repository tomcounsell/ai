# SDLC Stage Tracking

## Overview

Pipeline stage completion is tracked exclusively through `PipelineStateMachine.stage_states` stored in Redis. There is no artifact inference — a stage is considered complete only if it was explicitly dispatched and completed.

## How Stage State Is Written

Two complementary paths write stage markers:

### 1. Bridge hooks (primary path — bridge-initiated sessions)

- `pre_tool_use.py` detects dev-session Agent tool use, extracts the stage name from the prompt, and calls `PipelineStateMachine.start_stage()` on the parent PM session.
- `subagent_stop.py` fires on dev-session completion, calls `classify_outcome()` then `complete_stage()` or `fail_stage()`.

This path fires automatically for all sessions initiated through the Telegram bridge.

### 2. Skill stage markers (belt-and-suspenders — local Claude Code sessions)

Each SDLC skill writes explicit in_progress/completed markers using `tools/sdlc_stage_marker.py`:

```bash
python -m tools.sdlc_stage_marker --stage DOCS --status in_progress 2>/dev/null || true
# ... skill work ...
python -m tools.sdlc_stage_marker --stage DOCS --status completed 2>/dev/null || true
```

Skills with markers:
- `do-issue` → ISSUE stage
- `do-plan` → PLAN stage
- `do-plan-critique` → CRITIQUE stage
- `do-pr-review` → REVIEW stage
- `do-docs` → DOCS stage

The tool resolves the PM session from `VALOR_SESSION_ID` or `AGENT_SESSION_ID` environment variables. It fails silently (exit 0, empty JSON `{}`) if no session is found — skill execution is never interrupted by marker failures.

## `tools/sdlc_stage_marker.py` CLI

```bash
python -m tools.sdlc_stage_marker --stage <STAGE> --status <in_progress|completed>
python -m tools.sdlc_stage_marker --stage REVIEW --status completed --session-id <ID>
```

Exit code is always 0. Returns `{}` on error, `{"stage": "DOCS", "status": "completed"}` on success.

## `get_display_progress()` — Stored State Only

`PipelineStateMachine.get_display_progress()` returns stored stage states only. It does NOT:

- Infer stages from plan files on disk
- Query GitHub PRs to infer BUILD/TEST/REVIEW/DOCS completion
- Accept a `slug=` parameter (removed in #729)

This was intentional. Artifact inference caused stage skipping (#723, #729): a plan file created by `/do-build` as a deliverable would incorrectly satisfy DOCS without `/do-docs` ever running.

## do-merge Gate

The merge gate (`/do-merge`) reads stored state only via `get_display_progress()`. When stages show as pending/unrecorded:

- **All stages pending** (cold start / Redis cleared): Shows a warning that no pipeline state was found. Requires explicit acknowledgment before proceeding.
- **Specific stages skipped**: Shows a strong warning listing every skipped stage. Requires explicit acknowledgment of each. Emergency hotfixes may proceed after acknowledgment.

The gate is a reminder, not a hard blocker. The agent can choose to proceed, but only after explicit on-record acknowledgment.

## SDLC Router Fallback

When `stage_states` is unavailable (local Claude Code, no PM session), the SDLC router uses conversation dispatch history to determine what has already been dispatched in the current session. It does not infer from artifacts. If nothing has been dispatched, it starts from the beginning of the pipeline.

## Why Artifact Inference Was Removed

Artifact inference was deleted in PR #733 (issue #729) because:

1. Build artifacts can satisfy stages that were never dispatched. A `docs/features/` file created by `/do-build` would mark DOCS as completed, skipping `/do-docs`.
2. The inference was unreliable (GitHub API timeouts, plan file format edge cases).
3. Stored state is the single source of truth. Any deviation from that creates two conflicting signals.

See `docs/plans/sdlc-stage-skip-prevention.md` for the full problem analysis and fix rationale.
