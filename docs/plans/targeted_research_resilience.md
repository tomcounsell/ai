---
status: Complete
type: bug
appetite: Medium
owner: Valor
created: 2026-03-16
tracking: https://github.com/yudame/cuttlefish/issues/176
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

1. **Entry:** `step_question_discovery` calls `analysis.craft_targeted_research_prompts()` which creates empty placeholder artifacts (`p2-chatgpt`, `p2-gemini`, `p2-together`, `p2-claude`, `p2-mirofish` with `content=""`) and prompt artifacts, then enqueues 5 parallel research tasks
2. **Parallel tasks:** Each `step_*_research` task calls the research service. On success, the service writes content to the artifact via `update_or_create`. On failure, the task's `except` block calls `workflow.fail_step()` then re-raises
3. **Fan-in signal:** `post_save` on `EpisodeArtifact` fires `check_workflow_progression()`. If `current_step == "Targeted Research"`, calls `_check_targeted_research_complete()` which checks `not artifacts.filter(content="").exists()` — all p2-* (except p2-perplexity) must have non-empty content. If complete, calls `_try_enqueue_next_step()` with `select_for_update` to prevent double-advance, then `pause_for_human()` to let the user review and optionally add Grok/manual research
4. **UI rendering:** `compute_workflow_progress()` builds a flat list of phases with artifact counts. Phase 4 shows as "active" with artifact count badge but no per-source status
5. **Stuck state:** Two compounding failures:
   - **Path A:** Task fails → except calls `fail_step()` → sets `wf.status = "failed"` → fan-in signal checks `wf.status != "running"` → returns early → stuck even if other tasks succeed
   - **Path B:** Task fails but service didn't create artifact → placeholder stays `content=""` → fan-in's `filter(content="").exists()` returns True → never completes

**The gap:** `fail_step()` is designed for sequential steps where one failure should halt the pipeline. For parallel sub-steps, it's the wrong tool: it blocks the entire workflow when only one source failed. The correct behavior is to record the failure per-source (in the artifact) and let the fan-in evaluate the aggregate state.

**Two-layer error handling in service functions:** Gemini, Claude, and MiroFish service functions already catch exceptions and write `[SKIPPED: ...]` artifacts — these exceptions never reach the task layer. GPT and Together do NOT catch runtime errors, so their exceptions propagate to the task layer where `fail_step()` kills the workflow. The fix must handle both layers: service-level `[SKIPPED: ...]` for known conditions (missing API keys, quota errors), task-level `[FAILED: ...]` as a safety net for unexpected exceptions that escape the service layer.

**ImmediateBackend:** All 5 tasks run synchronously in sequence. The first failure sets `status="failed"` via `fail_step()`. Subsequent tasks check `wf.current_step != "Targeted Research"` (still matches), so they proceed. Each failing task calls `fail_step()` again, overwriting the error in the history entry. The fan-in signal fires after each save but sees `status == "failed"` and returns early.

## Architectural Impact

- **No new model fields** — uses existing `metadata` JSONField on `EpisodeArtifact` and existing `content` TextField with content conventions
- **Interface changes:** `_check_targeted_research_complete()` changes from "all must have content" to "all must be non-empty AND at least 1 has real content (not FAILED/SKIPPED)"
- **New coupling:** New `SubStep` dataclass in `workflow_progress.py` for richer UI rendering; downstream analysis functions must exclude `[FAILED: ...]` like they already exclude `[SKIPPED: ...]`
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

In each `step_*_research` task's `except` block, call `fail_research_source()` (see #2) instead of `fail_step()`:

```python
except Exception as exc:
    # Write error to artifact so it's visible per-source.
    # Use .save() (not .update()) to trigger post_save signal for fan-in.
    fail_research_source(episode_id, "p2-chatgpt", str(exc))
    raise
```

**Two-layer error handling:** Service functions like `run_gemini_research()` already catch known errors and write `[SKIPPED: ...]` artifacts — these return normally and never reach the task-level except. The task-level except is a safety net for unexpected exceptions (GPT Researcher crashes, network timeouts, etc.) that escape the service layer. Both `[SKIPPED: ...]` and `[FAILED: ...]` make the artifact non-empty, enabling the fan-in to detect completion.

**Important:** Use `artifact.save()` (not `queryset.update()`) to trigger the `post_save` signal, which re-evaluates fan-in completion.

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

#### 4. Downstream `[FAILED: ...]` handling

`analysis.py` already excludes `[SKIPPED: ...]` artifacts in 6 places (question discovery, digest creation, cross-validation). Add matching exclusions for `[FAILED: ...]`:

```python
# Everywhere that currently has:
.exclude(content__startswith="[SKIPPED:")
# Also add:
.exclude(content__startswith="[FAILED:")
```

Locations in `analysis.py`: lines 283-287, 296, 417, 458, 535. Also update `step_question_discovery` auto-retry check in `tasks.py:195-201` to treat `[FAILED: ...]` like `[SKIPPED: ...]` when deciding whether to re-run Perplexity.

#### 5. Per-source status in `SubStep`

Add new `SubStep` dataclass and `status`/`error` fields:

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

#### 6. UI: per-source status icons + retry buttons

Update `workflow_progress.html` Phase 4 sub-steps (currently shows only artifact count badge, no per-source detail):
- **pending**: `far fa-circle` (gray)
- **running**: `fas fa-spinner fa-spin` (amber)
- **complete**: `fas fa-check-circle` (green)
- **failed**: `fas fa-exclamation-triangle` (red) + error message + retry button
- **skipped**: `fas fa-forward` (gray)

Per-source retry button POSTs to a new endpoint that re-enqueues just that one research task.

#### 7. Per-source retry endpoint

New view/URL: `POST /podcast/<slug>/<episode>/workflow/4/retry/<source>/`

Maps source name to task function, clears the artifact (reset `content=""`, clear `metadata.error`), and enqueues just that task. Sets workflow status back to "running" if it was "paused_for_human".

**Guard:** Retry endpoint must verify `wf.current_step == "Targeted Research"`. If the workflow has already advanced past Phase 4 (user clicked Resume), reject the retry with an error message. This prevents late artifact updates from confusing downstream phases.

#### 8. Polling cutoff

After the fan-in fires and calls `pause_for_human()`, the workflow status becomes `"paused_for_human"` which naturally stops the "active" polling indicator. As a safety net: if all p2-* artifacts have non-empty content but the workflow is still `"running"` (fan-in didn't fire due to an edge case), the UI should detect this in `compute_workflow_progress()` and show the phase as "paused" rather than "active".

#### 9. Accumulate errors in fail_step instead of overwriting

For the shared workflow history (used by non-parallel steps), change `fail_step()` to append errors rather than overwrite:

```python
entry["error"] = entry.get("error", "")
if entry["error"]:
    entry["error"] += f"\n---\n{error}"
else:
    entry["error"] = error
```

#### 10. Signal filtering

The `check_workflow_progression` signal handler fires on ALL `EpisodeArtifact` saves. It already gates on `current_step` before calling `_check_targeted_research_complete()`. No additional filtering needed — digest artifacts (e.g., `digest-p2-chatgpt`) don't start with `p2-` so they're excluded by the `title__startswith="p2-"` filter in `_check_targeted_research_complete()`.

#### 11. Add MiroFish to Phase 4 display

`workflow_progress.py` PHASES list for `targeted_research` is missing `p2-mirofish`. Add it:

```python
(
    2,
    "targeted_research",
    "Targeted Research",
    ["p2-perplexity", "p2-gemini", "p2-together", "p2-claude", "p2-mirofish"],
),
```

Note: The fan-in signal's `_check_targeted_research_complete()` already includes MiroFish via the `title__startswith="p2-"` query — this is only a display fix.

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

## Test Impact

No existing tests are affected — the podcast workflow tests (`test_ux_episode_flows.py`, `test_ui_episode_editor.py`) have xfail tests for quality gates and audio upload, which are unrelated to targeted research error handling. The changes are additive: new `fail_research_source()` function, modified fan-in logic, new SubStep dataclass, and new retry endpoint. All require new tests.

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
- Add `fail_research_source(episode_id, artifact_title, error)` to `services/workflow.py` — writes `[FAILED: error]` to artifact content via `.save()` (not `.update()`, to trigger post_save signal) + `{"error": str, "failed_at": iso}` to metadata, does NOT change workflow status
- Update each `step_*_research` task's `except` block in `tasks.py` to call `fail_research_source()` instead of `fail_step()` for Targeted Research sub-tasks
- Update `_check_targeted_research_complete()` in `signals.py` to use threshold: all artifacts must be non-empty, at least 1 must have real content (exclude `[FAILED: ...]` and `[SKIPPED: ...]`)
- Update `fail_step()` in `services/workflow.py` to accumulate errors instead of overwriting
- Update downstream `[FAILED: ...]` exclusions in `analysis.py` (6 places) and `step_question_discovery` auto-retry check in `tasks.py`
- Add MiroFish (`p2-mirofish`) to PHASES list in `workflow_progress.py`
- Add `SubStep` dataclass with `status` and `error` fields to `workflow_progress.py`
- Update `compute_workflow_progress()` Phase 4 to query artifact content and set per-source status
- Add per-source retry view: `POST .../workflow/4/retry/<source>/` — guard: must verify `current_step == "Targeted Research"` before allowing retry
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

## RFC Feedback

| Severity | Critic | Feedback | Plan Response |
|----------|--------|----------|---------------|
| CONCERN | code-reviewer | Service functions already catch errors and create `[SKIPPED: ...]` artifacts — task-level handling may be redundant | Clarified: two-layer design is intentional. Service `[SKIPPED:]` handles known conditions (missing keys, quotas). Task `[FAILED:]` catches unexpected exceptions that escape the service. GPT and Together have no service-level error handling for runtime errors. |
| CONCERN | code-reviewer | Publishing Assets fan-in has the same vulnerability | Acknowledged in No-Gos as explicit follow-up. Fix Targeted Research first, apply same pattern later. |
| CONCERN | async-specialist | Retry after fan-in has advanced — late artifact updates could confuse downstream phases | Added guard: retry endpoint must verify `current_step == "Targeted Research"` before allowing retry. Rejects if workflow has already advanced. |
| CONCERN | async-specialist | Document `select_for_update` + `current_step` check as critical safety invariant | Already documented in Race Conditions section. `_try_enqueue_next_step()` in `signals.py` uses `select_for_update` and checks both `current_step` and `status` inside the atomic block. |
| CONCERN | code-reviewer | `advance_step` has no `select_for_update` | Not applicable to this bug: the fan-in path uses `_try_enqueue_next_step()` which has `select_for_update`. `advance_step` is only called from non-parallel steps and from `_try_enqueue_next_step` (after the lock). Pre-existing, not in scope. |

---

## Open Questions

None — RFC feedback has been incorporated. The solution builds on established patterns (`[SKIPPED: ...]` convention, existing metadata JSONField, existing fan-in architecture with `select_for_update`). Ready for implementation.
