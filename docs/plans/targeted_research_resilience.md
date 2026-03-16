---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-03-16
tracking:
last_comment_id:
---

# Targeted Research Resilience

## Problem

When Phase 4 (Targeted Research) tasks fail — API errors, timeouts, bad responses — the workflow silently gets stuck with no errors visible and no way to recover.

**Current behavior:**

1. **Errors overwrite each other.** Each failing task calls `workflow.fail_step(episode_id, "Targeted Research", error)`, which writes to the *same* history entry. If Gemini, Together, and Claude all fail, only the last error survives.
2. **Empty placeholders are indistinguishable from running tasks.** Placeholder artifacts (`content=""`) are created before tasks start. When a task fails, the placeholder stays empty. The fan-in signal treats `content=""` as "not yet complete" — identical to "still running."
3. **Fan-in requires ALL sources.** `_check_targeted_research_complete()` requires every p2-* artifact to have content. If one task fails (empty artifact), the workflow is stuck forever at "running."
4. **UI shows green checkmarks for all sources.** `_has_artifact()` checks title existence, not content quality. A placeholder artifact with `content=""` shows as "complete."
5. **No per-source retry.** The only retry is "Retry Step" which re-runs ALL research tasks, including ones that already succeeded.

**Observed incident (ep9-scheduling, 2026-03-13):** GPT research succeeded. Gemini, Claude, and Together all failed silently. Workflow stuck at "running" for hours with green checkmarks for all 4 sources and no error messages.

**Desired outcome:**
- Each research source shows its actual status: complete, failed (with error), skipped, or pending
- Failed tasks write their error to the artifact so it's visible and preserved per-source
- Workflow advances when at least 1 source has real content (not all sources required)
- Users can retry individual failed sources
- HTMX polling stops when the workflow is stuck (no actively running tasks)

## Prior Art

- **#62**: Background Task Service — Established Django 6.0 `@task` framework and `ImmediateBackend` for dev
- **#164**: Fix workflow poll overwriting phase navigation — Fixed HTMX poll interfering with nav, but didn't address stuck detection
- **#166**: Optimistic UI for podcast workflow buttons — Added loading states but didn't address stuck workflows
- **hotfix/fix-workflow-poll-navigation branch**: Already has poll URL derived from `window.location` and OOB swap cleanup — 2 files changed, minor improvements. Should be merged into this work.

No prior attempts to fix the silent failure problem itself.

## Data Flow

1. **Entry:** `step_question_discovery` creates empty placeholder artifacts (`p2-chatgpt`, `p2-gemini`, etc. with `content=""`) then enqueues 5 parallel research tasks
2. **Parallel tasks:** Each `step_*_research` task calls the research service, which writes content to the artifact. On failure, `except` block calls `workflow.fail_step()` then re-raises
3. **Fan-in signal:** `post_save` on `EpisodeArtifact` fires `_check_targeted_research_complete()`, which checks `not artifacts.filter(content="").exists()` — all must have content
4. **UI rendering:** `compute_workflow_progress()` builds Phase 4 sub_steps using `_has_artifact()` (title existence only). `_compute_button_state()` checks workflow status for button label
5. **Stuck state:** If any task fails → artifact stays `content=""` → fan-in never fires → status stays "running" → UI shows "Running..." disabled button with no error → polls forever

**The gap:** Between step 2 (task failure) and step 3 (fan-in check), the failure information is lost. `fail_step()` writes the error to the shared workflow history and sets `status="failed"`, but the first `fail_step()` call sets the workflow to "failed" immediately. The second/third failing tasks find `wf.status="failed"` and still write to the same history entry. Meanwhile, placeholder artifacts remain `content=""` giving no indication of what happened.

**Actually worse with ImmediateBackend:** All 5 tasks run synchronously in sequence. The first failure sets `status="failed"`, then subsequent tasks check `wf.status` and may raise `ValueError` in `_acquire_step_lock` — but these are parallel sub-steps that skip `_acquire_step_lock`. So all 5 run to completion/failure sequentially, each overwriting the error in history.

## Architectural Impact

- **No new model fields** — uses existing `metadata` JSONField on `EpisodeArtifact` and existing `content` TextField with content conventions
- **Interface changes:** `_check_targeted_research_complete()` changes from "all must have content" to "at least 1 has real content AND no tasks still running"
- **New coupling:** `SubStep` dataclass gains `status` and `error` fields for richer UI rendering
- **Reversibility:** High — content conventions (`[FAILED: ...]`) are backwards compatible with existing `[SKIPPED: ...]` pattern

## Appetite

**Size:** Medium

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1 (scope alignment on retry UX)
- Review rounds: 1

## Prerequisites

No prerequisites — all changes are internal to existing models and services.

## Solution

### Key Elements

- **Per-source error capture**: Failed tasks write `[FAILED: error message]` to artifact content + error details in artifact `metadata`
- **Threshold-based fan-in**: Require >= 1 artifact with real content (not empty, not SKIPPED, not FAILED) to advance
- **Rich sub-step status**: `SubStep` gains status/error fields; UI renders per-source status icons
- **Per-source retry**: Individual retry buttons on Phase 4 that re-enqueue a single research task
- **Polling cutoff**: Stop HTMX polling when workflow is stuck (failed or all tasks done)

### Flow

**Phase 4 starts** → Tasks run in parallel → Some succeed, some fail → Failed tasks write `[FAILED: ...]` to artifact → Fan-in detects >= 1 real content → Pauses for human review → UI shows per-source status (checkmark/error/skip) → User can retry failed sources individually → User clicks Resume when satisfied

### Technical Approach

#### 1. Failed artifact content convention

In each `step_*_research` task's `except` block, write the error to the artifact BEFORE calling `fail_step()`:

```python
except Exception as exc:
    # Write error to artifact so it's visible per-source
    EpisodeArtifact.objects.filter(
        episode_id=episode_id, title="p2-chatgpt"
    ).update(
        content=f"[FAILED: {exc}]",
        metadata={"error": str(exc), "failed_at": now().isoformat()},
    )
    workflow.fail_step(episode_id, "Targeted Research", str(exc))
    raise
```

This makes the artifact self-describing. The `post_save` signal fires on the update, which triggers fan-in re-evaluation.

#### 2. Don't set workflow to "failed" for individual sub-task failures

The critical behavior change: individual research task failures should NOT set `wf.status = "failed"` (which blocks the entire workflow). Instead, only the artifact records the failure. The fan-in signal decides what to do based on aggregate state.

New helper: `fail_research_source(episode_id, artifact_title, error)` — writes error to artifact only, does NOT touch workflow status.

Keep `fail_step()` for non-parallel steps (Perplexity, Cross-Validation, etc.) where a single failure should halt the workflow.

#### 3. Threshold-based fan-in

Update `_check_targeted_research_complete()`:

```python
def _check_targeted_research_complete(episode_id: int) -> bool:
    targeted = EpisodeArtifact.objects.filter(
        episode_id=episode_id, title__startswith="p2-"
    ).exclude(title="p2-perplexity")

    if not targeted.exists():
        return False

    # Still have pending tasks (empty content = still running or not started)
    if targeted.filter(content="").exists():
        return False

    # At least 1 must have real content (not FAILED, not SKIPPED)
    has_real = targeted.exclude(
        content__startswith="[FAILED:"
    ).exclude(
        content__startswith="[SKIPPED:"
    ).exclude(content="").exists()

    return has_real
```

This means: all tasks must finish (no empty placeholders), and at least one must have succeeded.

#### 4. Per-source status in `SubStep`

Add `status` and `error` fields to `SubStep`:

```python
@dataclass
class SubStep:
    label: str
    complete: bool
    detail: str = ""
    optional: bool = False
    artifact_key: str = ""
    status: str = "pending"  # pending | running | complete | failed | skipped
    error: str = ""
```

Update `compute_workflow_progress()` Phase 4 to query artifact content (not just title existence) and set status accordingly:
- `content=""` → "pending" (or "running" if workflow status is "running")
- `content.startswith("[FAILED:")` → "failed", extract error message
- `content.startswith("[SKIPPED:")` → "skipped"
- Non-empty real content → "complete"

#### 5. UI: per-source status icons + retry buttons

Update `_workflow_step_content.html` Phase 4 sub-steps:
- **pending**: `far fa-circle` (gray)
- **running**: `fas fa-spinner fa-spin` (amber)
- **complete**: `fas fa-check-circle` (green)
- **failed**: `fas fa-exclamation-triangle` (red) + error message + retry button
- **skipped**: `fas fa-forward` (gray)

Per-source retry button POSTs to a new endpoint that re-enqueues just that one research task.

#### 6. Per-source retry endpoint

New view/URL: `POST /podcast/<slug>/<episode>/workflow/4/retry/<source>/`

Maps source name to task function, clears the artifact (reset `content=""`, clear `metadata.error`), and enqueues just that task. Sets workflow status back to "running" if it was "failed".

#### 7. Polling cutoff

In `_compute_button_state()`, the "running" status already has stuck detection for Targeted Research. Extend it: if all p2-* artifacts have content (some FAILED, some real) but workflow is still "running", show "Resume Pipeline" instead of "Running..." — the fan-in should have caught this but in edge cases it may not.

#### 8. Accumulate errors in fail_step instead of overwriting

For the shared workflow history (used by non-parallel steps), change `fail_step()` to append errors rather than overwrite:

```python
entry["error"] = entry.get("error", "")
if entry["error"]:
    entry["error"] += f"\n---\n{error}"
else:
    entry["error"] = error
```

#### 9. Signal filtering

Ensure the fan-in signal only reacts to `p2-*` artifact saves (not digest artifacts like `digest-p2-chatgpt`). Current code already checks `title__startswith="p2-"` in `_check_targeted_research_complete()`. The signal handler itself fires on ALL artifact saves — add an early return for non-p2 titles during "Targeted Research" step to avoid unnecessary DB queries.

#### 10. Add MiroFish to Phase 4 sub_steps

`workflow_progress.py` is missing `p2-mirofish` from the `targeted_sources` list. Add it:

```python
("MiroFish research", "p2-mirofish", False),
```

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Each `step_*_research` task's `except` block now writes to artifact — test that artifact content is `[FAILED: ...]` after exception
- [ ] `fail_research_source()` does NOT set `wf.status = "failed"` — test workflow status remains "running"
- [ ] Fan-in with mixed results (1 success, 2 failures) correctly advances — test end-to-end

### Empty/Invalid Input Handling
- [ ] Empty artifact content (`""`) is correctly treated as "pending/running" not "complete"
- [ ] `[FAILED: ]` with empty error message doesn't break UI rendering
- [ ] `[SKIPPED: ]` artifacts count as "done" for fan-in but not as "real content"

### Error State Rendering
- [ ] Failed sub-step shows red icon + error message in UI
- [ ] Per-source retry button appears only for failed sources
- [ ] Multiple failures show all errors, not just the last one

## Rabbit Holes

- **Automatic retry with backoff**: Tempting to add automatic retries for transient API errors. Out of scope — manual retry is sufficient for now.
- **Task queue status introspection**: Checking `DatabaseBackend` for running tasks is complex and backend-specific. Use artifact state as the source of truth instead.
- **WebSocket for real-time updates**: HTMX polling at 5s intervals is good enough. No need to add WebSocket complexity.
- **Refactoring all parallel steps**: Publishing Assets has the same pattern. Fix Targeted Research first, apply to Publishing Assets later if needed.

## Risks

### Risk 1: ImmediateBackend sequential execution changes behavior
**Impact:** In dev, tasks run synchronously. If a failing task writes `[FAILED: ...]` to the artifact, the `post_save` signal fires immediately and may try to advance before other tasks have run.
**Mitigation:** Fan-in checks for `content=""` (pending tasks) before advancing. Sequential execution means placeholder artifacts exist before any task runs. The fan-in won't fire until the last task finishes.

### Risk 2: Race between retry and fan-in signal
**Impact:** User clicks "retry source" while fan-in signal is evaluating.
**Mitigation:** Retry clears artifact content to `""` before enqueuing, which prevents fan-in from advancing. `_try_enqueue_next_step()` already uses `select_for_update` for atomicity.

## Race Conditions

### Race 1: Concurrent artifact saves during fan-in
**Location:** `signals.py:_check_targeted_research_complete` + `_try_enqueue_next_step`
**Trigger:** Two research tasks complete near-simultaneously (production with DatabaseBackend)
**Data prerequisite:** All p2-* artifacts must have non-empty content
**State prerequisite:** Workflow must be at "Targeted Research" with status "running"
**Mitigation:** Already handled — `_try_enqueue_next_step()` uses `select_for_update` to prevent double-advance

### Race 2: Retry while fan-in evaluating
**Location:** Retry endpoint clears artifact → fan-in signal fires on the cleared artifact
**Trigger:** User clicks retry at the exact moment fan-in runs
**Data prerequisite:** Artifact was about to be the "last" non-empty artifact
**State prerequisite:** Workflow at "Targeted Research"
**Mitigation:** Retry sets content to `""`, which causes `_check_targeted_research_complete()` to return False (pending task exists). `select_for_update` in `_try_enqueue_next_step` prevents advancement.

## No-Gos (Out of Scope)

- Automatic retry with exponential backoff (separate feature)
- Applying same pattern to Publishing Assets fan-in (follow-up issue)
- New model fields or migrations (use existing `metadata` JSONField)
- Task queue introspection (rely on artifact state as truth)
- WebSocket real-time updates (HTMX polling is sufficient)
- Changing the number of research sources or adding new ones

## Update System

No update system changes required — this is purely application-level logic within the podcast app. No new dependencies, config files, or migration steps.

## Agent Integration

No agent integration required — this is a Django web app change. The podcast workflow runs via Django tasks, not via the Telegram bridge or MCP servers.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/podcast-workflow.md` (if it exists) with per-source error handling behavior
- [ ] Add inline code comments explaining the `[FAILED: ...]` content convention

### Inline Documentation
- [ ] Docstrings for `fail_research_source()` helper
- [ ] Updated docstring for `_check_targeted_research_complete()` explaining threshold logic
- [ ] Comments on `SubStep.status` field explaining valid values

## Success Criteria

- [ ] Failed research tasks write `[FAILED: error_message]` to artifact content + error in metadata
- [ ] UI shows per-source status icons: spinner (running), checkmark (complete), error triangle (failed), forward (skipped), circle (pending)
- [ ] UI shows the actual error message for each failed source
- [ ] Users can retry individual failed research sources via per-source buttons
- [ ] Workflow advances when at least 1 source has real content (fan-in threshold)
- [ ] HTMX polling stops when all tasks have resolved (no empty placeholders)
- [ ] Multiple `fail_step` calls for same step accumulate errors, not overwrite
- [ ] Fan-in signal only evaluates on p2-* artifact saves (early return for others)
- [ ] MiroFish appears in Phase 4 sub_steps list
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (backend)**
  - Name: backend-builder
  - Role: Implement task error capture, fan-in threshold, fail_research_source, retry endpoint, signal filtering
  - Agent Type: builder
  - Resume: true

- **Builder (frontend)**
  - Name: frontend-builder
  - Role: Implement per-source status rendering, retry buttons, polling cutoff in templates
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify end-to-end: task failure → artifact state → fan-in → UI rendering → retry
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update feature docs and inline documentation
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Backend: Per-source error capture and fan-in threshold
- **Task ID**: build-backend
- **Depends On**: none
- **Assigned To**: backend-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `fail_research_source(episode_id, artifact_title, error)` to `services/workflow.py` — writes `[FAILED: error]` to artifact content + `{"error": str, "failed_at": iso}` to metadata, does NOT change workflow status
- Update each `step_*_research` task's `except` block in `tasks.py` to call `fail_research_source()` instead of `fail_step()` for Targeted Research sub-tasks
- Update `_check_targeted_research_complete()` in `signals.py` to use threshold: all artifacts must be non-empty, at least 1 must have real content
- Update `fail_step()` in `services/workflow.py` to accumulate errors instead of overwriting
- Add early return in signal handler for non-p2 artifact saves during Targeted Research
- Add MiroFish (`p2-mirofish`) to `targeted_sources` in `workflow_progress.py`
- Add `status` and `error` fields to `SubStep` dataclass
- Update `compute_workflow_progress()` Phase 4 to query artifact content and set per-source status
- Add per-source retry view: `POST .../workflow/4/retry/<source>/` — clears artifact, re-enqueues single task
- Add URL pattern for retry endpoint

### 2. Frontend: Per-source status UI and retry buttons
- **Task ID**: build-frontend
- **Depends On**: build-backend
- **Assigned To**: frontend-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `_workflow_step_content.html` sub-steps rendering to use `step.status` for icon selection (pending/running/complete/failed/skipped)
- Show error message text for failed sub-steps
- Add per-source retry button (POST form) for failed sources in Phase 4
- Update `_compute_button_state()` stuck detection to handle mixed artifact states
- Merge `hotfix/fix-workflow-poll-navigation` changes (poll URL from window.location)

### 3. Validate integration
- **Task ID**: validate-integration
- **Depends On**: build-frontend
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify task failure writes `[FAILED: ...]` to artifact
- Verify fan-in advances with 1 success + N failures
- Verify UI renders correct status icons per source
- Verify per-source retry clears artifact and re-enqueues
- Verify polling stops when all tasks resolved
- Run full test suite

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update feature docs with per-source error handling
- Add inline code comments
- Update docstrings

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Fan-in threshold logic | `grep -n "has_real" apps/podcast/signals.py` | output contains "has_real" |
| FAILED convention used | `grep -rn "FAILED:" apps/podcast/tasks.py` | output contains "[FAILED:" |
| MiroFish in sub_steps | `grep "mirofish" apps/podcast/services/workflow_progress.py` | output contains "p2-mirofish" |
| SubStep has status | `grep "status:" apps/podcast/services/workflow_progress.py` | output contains "status" |
| Retry endpoint exists | `grep "retry" apps/podcast/urls.py` | output contains "retry" |

---

## Open Questions

None — the issue is well-specified and the solution builds on established patterns (`[SKIPPED: ...]` convention, existing metadata JSONField, existing fan-in architecture). Ready for implementation.
