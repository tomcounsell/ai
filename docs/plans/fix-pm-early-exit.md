---
status: in_progress
type: bug
appetite: Small
owner: valorengels
created: 2026-04-16
tracking: https://github.com/tomcounsell/ai/issues/1005
---

# Fix PM Session Early Exit Before Merge Gate

## Problem

PM sessions running single-issue SDLC pipelines complete (emit `[PIPELINE_COMPLETE]` or simply stop producing output) before invoking the `/do-merge` stage. PR #1002 for issue #1001 was left open and unmerged because the PM session exited with `status=completed` after the DOCS stage.

**Current behavior:**
- PM session drives the pipeline through PLAN -> CRITIQUE -> BUILD -> TEST -> REVIEW -> DOCS
- After DOCS completes, the PM exits without invoking `/do-merge`
- PR is orphaned — requires manual intervention to merge

**Desired outcome:**
- PM sessions always invoke `/do-merge` before emitting `[PIPELINE_COMPLETE]`
- If the PM hits the nudge cap before merge, the system catches the gap
- The `/sdlc` router's Row 10 conditions are reachable after normal DOCS completion

## Root Cause Analysis

Three contributing factors:

1. **PM persona lacks explicit merge instruction**: The PM persona (`config/personas/project-manager.md`) defines the stage sequence as `ISSUE -> PLAN -> CRITIQUE -> BUILD -> TEST -> REVIEW -> DOCS -> MERGE` but has no explicit rule saying "MERGE is mandatory before `[PIPELINE_COMPLETE]`". The PM can interpret DOCS completion as "pipeline done" and emit the completion marker.

2. **`/sdlc` router Row 10 conditions may be unreachable**: Row 10 in `SKILL.md` requires ALL display stages to show `completed` in `stage_states`. If the DOCS stage marker is not written (e.g., because docs are done as a side effect of BUILD rather than a dedicated DOCS dispatch), the merge gate never fires.

3. **`_handle_dev_session_completion` does not guard against premature PM exit**: When the last dev session (DOCS stage) completes, the worker steers the PM with a completion message, but does not check whether merge is still pending. The PM may finalize before processing the steering message (the race described in #987), and the continuation PM may not carry enough context to know merge is needed.

## Solution

### Change 1: Add merge-before-complete rule to PM persona

Add a new Hard Rule to `config/personas/project-manager.md`:

> **Rule 5 — MERGE is Mandatory Before Pipeline Complete**
>
> NEVER emit `[PIPELINE_COMPLETE]` while an open PR exists for the current issue. Before completing:
> 1. Check: `gh pr list --search "#{issue_number}" --state open`
> 2. If an open PR exists, invoke `/sdlc` which will dispatch `/do-merge`
> 3. Only emit `[PIPELINE_COMPLETE]` after the PR is merged or no open PR exists

### Change 2: Add defensive check to output router

In `agent/output_router.py`, when a PM/SDLC session emits `[PIPELINE_COMPLETE]`, the router currently delivers immediately. Add a comment noting that the PM is responsible for checking merge state — the router trusts the PM's judgment. (No code change needed here — the PM persona fix is the right layer.)

### Change 3: Strengthen `/sdlc` router Row 10 fallback

In `.claude/skills/sdlc/SKILL.md`, add explicit fallback for when `stage_states` is unavailable: if a PR exists and is open, dispatch `/do-merge` regardless of stage_states. The current text says "or stage_states unavailable" but does not explicitly say to check PR state as a fallback.

### Change 4: Enhance `_handle_dev_session_completion` steering message

When steering the PM after a dev session completion, include an explicit reminder about pending merge if an open PR exists for the issue. This gives the PM clear signal that merge is still needed.

## Scope

### In Scope
- [x] `config/personas/project-manager.md` — Add Rule 5 (merge-before-complete)
- [x] `.claude/skills/sdlc/SKILL.md` — Strengthen Row 10 fallback language
- [x] `agent/agent_session_queue.py` — Enhance steering message in `_handle_dev_session_completion`
- [x] Unit tests for the output router (PM pipeline complete with open PR scenario)
- [x] Unit tests for the continuation PM steering message content

### Out of Scope (No-Gos)
- Changing the nudge loop fundamental design (PM controls pipeline, bridge just nudges)
- Adding Redis stage_states hard dependency (graceful fallback required)
- Modifying the merge gate skill itself (`do-merge.md`)
- Changing bridge code or Telegram delivery

## Failure Path Test Strategy

- Test that `determine_delivery_action` returns `deliver_pipeline_complete` for PM/SDLC sessions with the marker (existing behavior, verify not broken)
- Test that the continuation PM steering message includes merge reminder text when an open PR exists
- Test that the PM persona document contains the new Rule 5 text

## Test Impact

- [x] `tests/unit/test_steering_mechanism.py` — UPDATE: add test for pipeline complete marker behavior
- [x] `tests/unit/test_continuation_pm.py` — UPDATE: add test for steering message content with merge reminder

No existing tests are broken by these changes — the changes are additive (new rule in persona, enhanced steering message, updated skill doc).

## Implementation Tasks

- [x] Add Rule 5 to PM persona (`config/personas/project-manager.md`)
- [x] Update `/sdlc` SKILL.md Row 10 with explicit PR-state fallback
- [x] Enhance `_handle_dev_session_completion` steering message to include merge reminder
- [x] Write unit tests for the new behavior
- [x] Run full unit test suite and verify passing

## Documentation

- [x] Update `docs/features/pipeline-state-machine.md` with note about merge-before-complete enforcement
- [x] No new feature doc needed — this is a bug fix to existing pipeline behavior

## Update System

No update system changes required — this fix modifies persona prompts, a skill doc, and worker steering logic, all of which are pulled via `git pull` during updates.

## Agent Integration

No agent integration changes required — the fix operates within existing PM persona instructions and worker steering. No new MCP servers or tool wrappers needed.

## Rabbit Holes

- **Automated PR-state check in output router**: Tempting to add a `gh pr list` check inside the output router before delivering `[PIPELINE_COMPLETE]`. This would add network I/O to the hot path and violate the principle that the router is a pure function. The PM persona is the right layer for this check.
- **Blocking `[PIPELINE_COMPLETE]` delivery**: Could modify the router to reject `PIPELINE_COMPLETE` unless merge is confirmed. This creates a coupling between the router and GitHub state that makes the system brittle. Trust the PM.

## Open Questions

None — the fix is straightforward: tell the PM not to complete before merge, give the SDLC router a clearer fallback, and enhance the steering message.
