---
status: Done
type: feature
appetite: Small
owner: Tom
created: 2026-03-13
tracking: https://github.com/yudame/cuttlefish/issues/166
last_comment_id:
---

# Optimistic UI for Podcast Workflow Buttons

## Problem

Every task-triggering button in the podcast workflow provides zero visual feedback on click. Users click "Start Pipeline", "Resume Pipeline", or "Retry Step" and see nothing happen for seconds (2-30s in dev with ImmediateBackend, shorter but still noticeable in prod with DatabaseBackend).

**Current behavior:**
- User clicks a pipeline action button
- The page appears frozen until the server responds with HX-Redirect
- The existing polling div (`#workflow-poller`) only renders on full page loads when `workflow_is_running` is already true -- it is not activated by button clicks
- Double-clicks are not prevented, risking duplicate task enqueues

**Desired outcome:**
- Every task-triggering button optimistically shows a loading state within 200ms of click
- Buttons are disabled during the request to prevent double-clicks
- The polling mechanism activates after a pipeline action without requiring a full page reload
- The pattern works with both ImmediateBackend (dev) and DatabaseBackend (prod)

## Prior Art

- **PR #164**: Fix workflow poll overwriting phase navigation -- Merged 2026-03-13. Changed poll URL to derive from `window.location.pathname` so it tracks the step the user is viewing. Directly relevant: any optimistic UI must not break this fix.
- **PR #159 / Issue #158**: Fix Safari iPad phase navigation reverting to phase 1 -- Added `event.preventDefault()` to navigation links to prevent races between `href` navigation and HTMX XHR swaps. Relevant because workflow buttons use both `method="post"` and `hx-post`, which could have similar races.
- **PR #138**: Episode brief editor on workflow step 1 -- Added HTMX-based inline editing with save status indicators. Good reference for the save feedback pattern.

## Data Flow

1. **Entry point**: User clicks a pipeline action button (Start/Resume/Retry)
2. **Form submission**: `<form hx-post="..." hx-swap="none">` sends POST to `EpisodeWorkflowView.post()`
3. **Task enqueue**: View resolves the step's task function(s) and calls `.enqueue(episode_id=...)`
4. **Response**: View returns `HX-Redirect` header (204 status) for HTMX, or standard redirect
5. **Page reload**: Browser follows the redirect, full page renders with updated workflow state
6. **Polling**: If `workflow_is_running` is true after reload, `#workflow-poller` div renders and polls every 5s

**Gap**: Between step 1 (click) and step 5 (page reload), the user sees no feedback. Additionally, the poller only activates after the full page reload, not immediately after clicking.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. It uses only HTMX built-in attributes and minimal inline JavaScript.

## Solution

### Key Elements

- **Loading state on buttons**: Use `hx-disabled-elt` to disable the button during the request, and `hx-on::before-request` to swap button text to a loading indicator
- **Polling activation on click**: Inject the `#workflow-poller` div into the DOM when a pipeline action fires, so polling starts immediately without waiting for the redirect/reload
- **Extended action_button.html component**: Add `loading_text` parameter to the component so any button can declare its loading state text

### Flow

**User clicks "Start Pipeline"** --> Button immediately shows "Starting..." with spinner, button disabled --> POST fires to server --> Server enqueues task, returns HX-Redirect --> Meanwhile, polling div injected into DOM, starts polling every 5s --> Page redirects and renders with updated state + active poller

### Technical Approach

1. **HTMX `hx-disabled-elt`**: Add `hx-disabled-elt="this"` to the action button `<button>` element. This is HTMX's built-in mechanism to disable an element during a request, preventing double-clicks.

2. **`hx-on::before-request` for text swap**: On the `<form>`, use `hx-on::before-request` to find the button child and replace its `innerHTML` with a spinner + loading text. This fires synchronously before the XHR, giving instant feedback.

3. **Inject poller on click**: In the same `hx-on::before-request` handler, check if `#workflow-poller` exists; if not, create it and append to the document. This activates polling immediately rather than waiting for the page reload.

4. **Extend `action_button.html`**: Add a `loading_text` parameter. When provided, the button gets a `data-loading-text` attribute that the form's `hx-on::before-request` handler reads.

5. **Apply to all forms**: Audit every `method="post"` form in `_workflow_step_content.html` and apply the pattern. For non-workflow forms (cover art upload, paste research), use simple disable-on-submit.

## Failure Path Test Strategy

### Exception Handling Coverage
- The `EpisodeWorkflowView.post()` method has an `except Exception` block (line 377) that logs and continues. The optimistic UI does not change this behavior -- the redirect still fires even on error, and the reloaded page shows the error state via `button_state.error`.
- No new exception handlers are introduced.

### Empty/Invalid Input Handling
- If JavaScript is disabled, the form submits normally via `method="post"` (graceful degradation). No loading state appears, but functionality is preserved.
- If the button is already disabled (e.g., `button_state.disabled`), `hx-disabled-elt` has no additional effect.

### Error State Rendering
- If the server returns an error, the HX-Redirect still fires and the reloaded page shows the error in `button_state.error`. The loading state is naturally cleared by the page reload.
- If the XHR fails (network error), HTMX fires `htmx:responseError` but the form also has `method="post"` so the browser will follow the standard form submission as fallback.

## Rabbit Holes

- **Custom JavaScript framework**: The solution must use only HTMX attributes and minimal inline JS. Do not introduce Alpine.js, Stimulus, or any other framework.
- **Server-Sent Events (SSE)**: SSE would be a better long-term polling replacement, but that is a separate, larger effort. Stick with the existing 5s polling mechanism.
- **Rollback on error**: True optimistic UI would roll back the button state if the server returns an error. Since the page redirects on both success and failure, rollback is unnecessary -- the redirect naturally resets the UI.
- **CSS animations/transitions**: Keep loading indicators simple (spinner icon + text). Do not spend time on elaborate animations.

## Risks

### Risk 1: Race between optimistic UI and HX-Redirect
**Impact:** The button shows loading state, but then the page immediately redirects (within ms for DatabaseBackend), causing a flash of loading state.
**Mitigation:** This is acceptable UX -- a brief flash of "Starting..." before redirect is better than no feedback. For ImmediateBackend (dev), the loading state displays for the full task duration (correct behavior).

### Risk 2: Poller injection creates duplicate pollers
**Impact:** Multiple `#workflow-poller` divs could cause redundant XHR requests.
**Mitigation:** Check for existing `#workflow-poller` before injecting. Use `document.getElementById` guard.

## Race Conditions

### Race 1: Double-click before `hx-disabled-elt` activates
**Location:** `_workflow_step_content.html` form buttons
**Trigger:** User clicks rapidly before HTMX processes the first click
**Data prerequisite:** N/A
**State prerequisite:** Button must be non-disabled
**Mitigation:** `hx-disabled-elt="this"` disables the button synchronously when HTMX begins processing the request. Combined with the `hx-on::before-request` handler, the window for double-click is negligible.

## No-Gos (Out of Scope)

- Replacing the 5s polling with SSE or WebSockets
- Adding loading states to non-podcast forms elsewhere in the app
- Changing the server-side POST handling or redirect behavior
- Adding progress percentages or step-by-step status updates during loading

## Update System

No update system changes required -- this is a frontend-only change within the cuttlefish web application.

## Agent Integration

No agent integration required -- this is a UI/UX enhancement to the web interface.

## Documentation

### Inline Documentation
- [ ] Add comments in `action_button.html` documenting the `loading_text` parameter
- [ ] Add comments in `_workflow_step_content.html` documenting the optimistic UI pattern

No feature documentation file needed -- this is a UX enhancement, not a new feature surface.

## Success Criteria

- [ ] Every task-triggering button shows a loading state within 200ms of click
- [ ] Buttons are disabled during the request (no double-clicks possible)
- [ ] The polling mechanism activates after a pipeline action without requiring a full page reload
- [ ] Pattern works with both ImmediateBackend (dev) and DatabaseBackend (prod)
- [ ] Pattern degrades gracefully without JavaScript (forms still submit normally)
- [ ] All `method="post"` forms in `_workflow_step_content.html` are updated
- [ ] `action_button.html` supports `loading_text` parameter
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (optimistic-ui)**
  - Name: ui-builder
  - Role: Implement loading states and polling activation
  - Agent Type: builder
  - Resume: true

- **Validator (optimistic-ui)**
  - Name: ui-validator
  - Role: Verify loading states work across all button types
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Extend action_button.html with loading_text support
- **Task ID**: build-action-button
- **Depends On**: none
- **Assigned To**: ui-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `loading_text` parameter to `action_button.html`
- When `loading_text` is provided, add `data-loading-text="{{ loading_text }}"` attribute to the button element
- Add `hx-disabled-elt="this"` to the button element when `loading_text` is present

### 2. Add optimistic UI to pipeline action forms
- **Task ID**: build-optimistic-forms
- **Depends On**: build-action-button
- **Assigned To**: ui-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `hx-on::before-request` handlers to pipeline action forms in `_workflow_step_content.html`
- The handler should: (a) swap button innerHTML to spinner + loading text, (b) inject `#workflow-poller` if not present
- Apply to: Start Pipeline, Resume Pipeline, Retry Step, Publish Episode forms
- Apply simple disable-on-submit to: Reset to Draft, Upload Audio, Paste Research, Regenerate Cover Art, Upload Cover Art forms

### 3. Validate all forms
- **Task ID**: validate-forms
- **Depends On**: build-optimistic-forms
- **Assigned To**: ui-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `action_button.html` renders correctly with and without `loading_text`
- Verify all `method="post"` forms in `_workflow_step_content.html` have loading states
- Verify forms still work without JavaScript (graceful degradation)
- Run existing tests to ensure no regressions

### 4. Final Validation
- **Task ID**: validate-all
- **Depends On**: validate-forms
- **Assigned To**: ui-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `DJANGO_SETTINGS_MODULE=settings pytest -x -q` | exit code 0 |
| Lint clean | `uv run pre-commit run --all-files` | exit code 0 |
| action_button loading_text | `grep -c 'loading_text' apps/public/templates/components/forms/action_button.html` | output > 0 |
| Forms have hx-disabled-elt | `grep -c 'hx-disabled-elt' apps/public/templates/podcast/_workflow_step_content.html` | output > 0 |

---

## Resolved Questions

1. **Spinner implementation**: Use Font Awesome spinner (`fa-spinner fa-spin`) + disable the button. Consistent with existing icon usage.

2. **Loading text per button**: Use distinct loading text per button type ("Starting...", "Resuming...", "Retrying...", "Publishing...") for better UX clarity.

3. **Polling interval on activation**: Use 2s polling interval when injecting the poller on button click for more responsive feedback.
