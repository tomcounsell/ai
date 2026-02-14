---
status: Ready
type: feature
appetite: Small
owner: Tom
created: 2026-02-14
tracking: https://github.com/yudame/cuttlefish/issues/73
---

# Pipeline Control Buttons on Episode Workflow UI

## Problem

The episode workflow edit pages show phase progress and sub-step checklists but are entirely read-only. Staff must use the Django shell or management commands to start, resume, or retry the task pipeline.

**Current behavior:**
Staff navigate to `/podcast/<slug>/<episode_slug>/edit/<step>/`, see green/amber/gray dots and checklists, but cannot take any action. Starting or recovering the pipeline requires `produce_episode.enqueue(episode_id=42)` in the shell.

**Desired outcome:**
Contextual action buttons appear on each workflow step page based on the pipeline's current state — start, resume, retry, or a running indicator.

## Appetite

**Size:** Small

**Team:** Solo dev, no review.

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

Three files modified, no new models, no new dependencies.

## Prerequisites

No prerequisites — depends only on the task pipeline from PR #72 which is already merged.

## Solution

### Key Elements

- **Step-to-task mapping** — dict mapping each workflow step name to its task function(s) for re-enqueueing
- **Button state computation** — logic that reads `EpisodeWorkflow` status and returns what button to show
- **POST action endpoint** — single URL that determines the correct action from workflow state
- **Template button block** — contextual UI between the phase header and sub-steps checklist

### Flow

**Staff views step page** → sees contextual button based on workflow state → clicks action → **POST enqueues task(s)** → **HX-Redirect reloads page** → updated state visible (sidebar dots + step content)

### Technical Approach

- Add `STEP_TASK_MAP` dict and `_compute_button_state()` method to `apps/podcast/workflow.py`
- Extend `EpisodeWorkflowView.get()` to include workflow state in template context
- Add `EpisodeWorkflowView.post()` for state-driven action handling
- Single new URL pattern at `.../edit/<step>/action/`
- Response uses `HX-Redirect` header for full page reload (updates sidebar dots)
- Reuse existing `action_button.html` component wrapped in an HTMX form

**Button states by workflow status:**

| Workflow Status | Button | Color |
|----------------|--------|-------|
| `not_started` (step 1 only) | Start Pipeline | green |
| `running` (current step) | Running... (disabled) | amber |
| `paused_for_human` | Resume Pipeline | blue |
| `failed` | Retry Step | red |
| Step completed | No button | — |
| Future step | No button | — |

**Files modified:**
- `apps/podcast/workflow.py` — STEP_TASK_MAP, _compute_button_state(), extend get(), add post()
- `apps/podcast/urls.py` — one new URL pattern
- `apps/public/templates/podcast/_workflow_step_content.html` — button block

## Rabbit Holes

- Don't add OOB sidebar swaps — full page reload via HX-Redirect is simpler and sufficient for this staff-only tool
- Don't add per-tool buttons (e.g., "Run Perplexity Research" standalone) — pipeline controls only
- Don't add polling/auto-refresh for the "Running..." state — staff can manually reload

## Risks

### Risk 1: Double-click enqueues duplicate tasks
**Impact:** Wasted API calls (but no data corruption thanks to `update_or_create` in services)
**Mitigation:** `_acquire_step_lock()` in each task function uses `select_for_update` to prevent duplicate execution. Second task raises `ValueError` harmlessly.

### Risk 2: Retrying parallel steps re-enqueues all sub-tasks
**Impact:** Some sub-tasks may have already completed; re-running wastes API calls
**Mitigation:** Acceptable tradeoff — services use `update_or_create` so results are idempotent. Fan-in signal handles coordination correctly regardless.

## No-Gos (Out of Scope)

- Individual tool triggers outside the pipeline flow
- Auto-polling / live status updates
- Skip-step functionality
- Editing artifact content from the UI

## Update System

No update system changes required — this is a web UI feature with no CLI or bridge impact.

## Agent Integration

No agent integration required — this is a staff-facing Django view feature.

## Documentation

### Inline Documentation
- [ ] Docstrings on `_compute_button_state()` and `post()` methods

No feature doc needed — this is a minor UI addition to existing workflow pages already documented in `docs/features/podcast-services.md`.

## Success Criteria

- [ ] "Start Pipeline" button appears on step 1 when no workflow exists
- [ ] "Running..." indicator shows when pipeline is active at current step
- [ ] "Resume Pipeline" button appears when workflow is paused_for_human (with blocked_on reason displayed)
- [ ] "Retry Step" button appears when workflow has failed (with error displayed)
- [ ] No button on completed or future steps
- [ ] Existing workflow view tests still pass
- [ ] POST actions correctly enqueue task(s) and redirect

## Team Orchestration

### Team Members

- **Builder (workflow-buttons)**
  - Name: workflow-builder
  - Role: Implement view logic, URL, and template changes
  - Agent Type: builder
  - Resume: true

- **Validator (workflow-buttons)**
  - Name: workflow-validator
  - Role: Verify button states render correctly and POST actions work
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add STEP_TASK_MAP and button state logic
- **Task ID**: build-view-logic
- **Depends On**: none
- **Assigned To**: workflow-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `STEP_TASK_MAP` dict to `apps/podcast/workflow.py` mapping all 12 step names to task function(s)
- Add `_compute_button_state()` method returning show/action/label/color/icon/disabled/blocked_reason
- Extend `get()` to call `get_status()` and `_compute_button_state()`, add results to context

### 2. Add POST endpoint
- **Task ID**: build-post-endpoint
- **Depends On**: build-view-logic
- **Assigned To**: workflow-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `post()` method to `EpisodeWorkflowView` with state-driven action logic
- Add URL pattern `episode_workflow_action` in `apps/podcast/urls.py`
- Return `HX-Redirect` response after action

### 3. Update template with button block
- **Task ID**: build-template
- **Depends On**: build-post-endpoint
- **Assigned To**: workflow-builder
- **Agent Type**: builder
- **Parallel**: false
- Add pipeline action block to `_workflow_step_content.html` between header and checklist
- Use `action_button.html` component inside HTMX form
- Show blocked_reason text for paused/failed states
- Show spinner for running state

### 4. Validate implementation
- **Task ID**: validate-all
- **Depends On**: build-template
- **Assigned To**: workflow-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_workflow_views.py -v`
- Verify all success criteria met
- Check no new files were created unnecessarily

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_workflow_views.py -v` — existing workflow view tests pass
- `uv run black apps/podcast/workflow.py apps/podcast/urls.py --check` — formatting passes
