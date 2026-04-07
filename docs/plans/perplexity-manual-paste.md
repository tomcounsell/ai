---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-07
tracking: https://github.com/yudame/cuttlefish/issues/224
last_comment_id:
---

# Perplexity Manual Paste Fallback

## Problem

When the Perplexity Deep Research step (Phase 2) is skipped or fails, the podcast production workflow stalls at Phase 3 (Question Discovery). The system generates a high-quality `prompt-perplexity` artifact but offers no way for the user to run it manually and paste the result back.

**Current behavior:**
Phase 3 shows the Perplexity step as skipped/failed. The only recovery option is the auto-retry button, which re-runs the same API call and will likely fail again for the same reason (quota, rate limit, empty response). There is no escape hatch.

**Desired outcome:**
When `p2-perplexity` is in a `skipped` or `failed` state, Phase 3 also shows:
1. A read-only display of the `prompt-perplexity` content with a "Copy prompt" button
2. A textarea for pasting the manual Perplexity result
3. A "Save research" button that writes the pasted content to `p2-perplexity` and re-enqueues Question Discovery

## Prior Art

- **PR #157**: Fix empty Perplexity research breaking question discovery — added auto-retry logic in `step_question_discovery`; the manual paste fallback was not addressed. The auto-retry is still correct and should coexist with the new manual path.
- **PR #228** (open, `session/perplexity-error-surfacing`): Surfaces errors as `[FAILED:]` artifacts — does NOT implement the manual paste panel. This plan builds on top of that work, or lands independently.

## Data Flow

1. **Entry point**: User visits Phase 3 workflow page; `p2-perplexity` content starts with `[SKIPPED:]` or `[FAILED:]`
2. **Template render**: `_workflow_step_content.html` receives `current_step == 3`; new context variable `perplexity_manual_paste_enabled` signals the fallback panel should render
3. **Prompt display**: `prompt-perplexity` artifact content is fetched from context and shown in a `<pre>` block with a copy-to-clipboard button
4. **User pastes**: User copies prompt, runs it on Perplexity, pastes result into `<textarea>`
5. **POST to new endpoint**: `PastePerplexityResearchView` (new) receives `episode_slug`, `podcast_slug`, and `content` body
6. **Validation**: Endpoint rejects empty content or content beginning with `[SKIPPED:` / `[FAILED:`
7. **Write artifact**: `EpisodeArtifact` where `title == "p2-perplexity"` is updated with the clean pasted content; `metadata["manually_pasted"] = True` is set for auditability
8. **Re-enqueue**: `step_question_discovery.enqueue(episode_id=episode.pk)` is called; workflow status is resumed if `paused_for_human` or `failed`
9. **Redirect**: User is redirected to Phase 3 workflow page; the updated `p2-perplexity` content now passes `_resolve_substep_status()` as `complete`, unblocking Phase 3

## Architectural Impact

- **New dependencies**: None
- **Interface changes**: One new POST endpoint (`PastePerplexityResearchView`), one new URL pattern, one new context variable in the Phase 3 template rendering path
- **Coupling**: Low — new view mirrors `RetryResearchSourceView` pattern; no existing interfaces modified
- **Data ownership**: `p2-perplexity` artifact content continues to be owned by `EpisodeArtifact`; the new endpoint writes to it the same way as the Perplexity task
- **Reversibility**: Fully reversible — the endpoint is additive; removing it has no effect on existing data

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — no new external dependencies or API keys required.

## Solution

### Key Elements

- **`PastePerplexityResearchView`**: New Django view (POST only) that validates and writes pasted content to `p2-perplexity`, then re-enqueues `step_question_discovery`
- **Phase 3 template section**: When `p2-perplexity` is `skipped` or `failed`, render the prompt display block + copy button + textarea + save button
- **Context variable**: The Phase 3 template block needs `perplexity_prompt_content` and `perplexity_paste_failed` flags from the view that renders `_workflow_step_content.html`

### Flow

Phase 3 (skipped state) → see "Copy prompt" + textarea → copy prompt → paste into Perplexity → paste result into textarea → click "Save research" → POST to endpoint → re-enqueue Question Discovery → Phase 3 reloads showing progress

### Technical Approach

- New view `PastePerplexityResearchView` in `apps/podcast/workflow.py`, modeled on the existing `PasteResearchView`
- New URL at `podcasts/<slug>/episodes/<episode_slug>/workflow/perplexity-paste/` registered in `apps/podcast/urls.py`
- Validation: reject if content is empty, whitespace-only, or starts with `[SKIPPED:` or `[FAILED:`
- On success: update `p2-perplexity` content, set `metadata["manually_pasted"] = True`, call `step_question_discovery.enqueue(episode_id=episode.pk)`, resume workflow if paused
- Template: add conditional block in `_workflow_step_content.html` under `{% if current_step == 3 %}` — check if `perplexity_status` is `skipped` or `failed`; render prompt display + paste form
- Pass `perplexity_prompt_content` and `perplexity_status` to template context in the Phase 3 rendering path (the view that constructs `_workflow_step_content.html` context)
- HTMX: redirect to Phase 3 on success, same pattern as `RetryResearchSourceView._redirect()`

## Failure Path Test Strategy

### Exception Handling Coverage
- `EpisodeArtifact.objects.get()` will raise `DoesNotExist` if the `p2-perplexity` artifact has not been created yet (race: Phase 2 never ran). Handle with a guard and log a warning; redirect without re-enqueuing.
- `step_question_discovery.enqueue()` may raise if the task queue is unavailable. Wrap in try/except, log the error; the artifact write should already be committed.

### Empty/Invalid Input Handling
- Empty content → HTTP 400 or redirect with no action (log warning)
- Whitespace-only content → same as empty
- Content starting with `[SKIPPED:` or `[FAILED:` → reject; these are error sentinels, not real research

### Error State Rendering
- If validation fails, the user stays on Phase 3 with the paste form still visible (no silent failure)
- The redirect-only response (no flash message) is acceptable for this scope; a toast is a rabbit hole

## Test Impact

- [ ] `tests/unit/test_workflow_progress.py` — no changes needed; `_resolve_substep_status()` logic is unchanged
- [ ] `tests/integration/test_podcast_workflow_views.py` (or equivalent) — ADD: test `PastePerplexityResearchView` with valid content, empty content, and sentinel-prefixed content
- [ ] Existing retry/paste view tests — no changes needed; new view is additive

No existing tests broken — this is a purely additive new view and template block.

## Rabbit Holes

- Flash/toast messages on validation failure — not worth the complexity for this scope
- Real-time HTMX form updates (inline success/error without redirect) — overkill for a low-frequency fallback path
- Storing the pasted content version history — the existing `EpisodeArtifact` model handles a single content value; versioning is a separate concern
- Validating that the pasted content is "real" Perplexity output (e.g., AI judge) — not needed; any non-empty, non-sentinel content is acceptable

## Risks

### Risk 1: Phase 3 context does not already pass `p2-perplexity` content to the template
**Impact:** The template cannot render the prompt display or detect skipped/failed state without the content being in context
**Mitigation:** Read the existing Phase 3 view rendering code (`EpisodeWorkflowView` or equivalent) before building; add the required context keys if missing. The `compute_workflow_progress()` function already accepts `artifact_contents` — the view may already pass it.

### Risk 2: `prompt-perplexity` artifact may not exist
**Impact:** Copy-prompt panel renders empty or crashes
**Mitigation:** Render the prompt panel only if `perplexity_prompt_content` is non-empty; otherwise show a note "No research prompt available — the prompt artifact has not been generated yet."

## Race Conditions

### Race 1: User submits paste while workflow is mid-retry
**Location:** `PastePerplexityResearchView.post()`
**Trigger:** User clicks "Save research" while the auto-retry is simultaneously running and writing to `p2-perplexity`
**Data prerequisite:** `p2-perplexity` artifact content must be set before `step_question_discovery` reads it
**State prerequisite:** Workflow must not be in a `running` state mid-task
**Mitigation:** The paste endpoint overwrites the artifact content unconditionally. If a concurrent retry also writes, last-write-wins. Since this is a manual fallback for already-failed runs, the workflow is not actively running; the risk is negligible. Add `metadata["manually_pasted_at"]` timestamp for auditability.

## No-Gos (Out of Scope)

- Validating the pasted content is semantically correct Perplexity output
- Toast/flash message UI feedback on validation failure
- Supporting manual paste for any artifact other than `p2-perplexity`
- Versioning or diffing multiple paste attempts
- Perplexity API retry logic changes (handled in PR #228)

## Update System

No update system changes required — this is a purely internal Django view + template change with no new dependencies, config files, or deployment steps.

## Agent Integration

No agent integration required — this is a web UI change. The agent does not need to invoke the paste endpoint; it is a human-facing workflow recovery tool.

## Documentation

- [ ] Create `docs/features/perplexity-manual-paste.md` describing the manual paste fallback flow
- [ ] Update `docs/features/README.md` index table with the new entry

## Success Criteria

- [ ] When `p2-perplexity` is `skipped` or `failed`, Phase 3 shows the `prompt-perplexity` content in a read-only block with a "Copy prompt" button
- [ ] A textarea and "Save research" button are visible in the same state
- [ ] Submitting non-empty, non-sentinel content writes to `p2-perplexity` and re-enqueues Question Discovery
- [ ] Submitting empty or sentinel-prefixed content is rejected (no write, no enqueue)
- [ ] The existing "Retry" button remains available alongside the paste panel
- [ ] After a successful paste, Phase 3 reloads and the `p2-perplexity` substep shows as complete (or running)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation created at `docs/features/perplexity-manual-paste.md`

## Team Orchestration

### Team Members

- **Builder (perplexity-paste)**
  - Name: paste-builder
  - Role: Implement `PastePerplexityResearchView`, URL registration, and Phase 3 template changes
  - Agent Type: builder
  - Resume: true

- **Validator (perplexity-paste)**
  - Name: paste-validator
  - Role: Verify view logic, template rendering, and test coverage
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: paste-documentarian
  - Role: Create `docs/features/perplexity-manual-paste.md` and update index
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

builder, validator, documentarian (see PLAN_TEMPLATE for full list)

## Step by Step Tasks

### 1. Implement PastePerplexityResearchView and URL
- **Task ID**: build-view
- **Depends On**: none
- **Validates**: tests/integration/ (new test for PastePerplexityResearchView)
- **Assigned To**: paste-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `PastePerplexityResearchView` to `apps/podcast/workflow.py` (after `PasteResearchView`)
- Register URL `podcasts/<slug>/episodes/<episode_slug>/workflow/perplexity-paste/` in `apps/podcast/urls.py` with `name="paste_perplexity_research"`
- Validate content (non-empty, not starting with `[SKIPPED:` or `[FAILED:`), write to `p2-perplexity` artifact, set `metadata["manually_pasted"] = True`, re-enqueue `step_question_discovery`, resume workflow if paused
- Redirect to Phase 3 (HTMX-aware, same as `RetryResearchSourceView._redirect()`)

### 2. Update Phase 3 template and view context
- **Task ID**: build-template
- **Depends On**: none
- **Validates**: manual browser test of Phase 3 with skipped artifact
- **Assigned To**: paste-builder
- **Agent Type**: builder
- **Parallel**: true
- In the view that renders `_workflow_step_content.html`, pass `perplexity_status` and `perplexity_prompt_content` into context for Phase 3
- In `_workflow_step_content.html` under `{% if current_step == 3 %}`, add a conditional block: when `perplexity_status` is `skipped` or `failed`, render the prompt display (`<pre>` + copy button) and the paste form (`<textarea>` + submit button)

### 3. Write tests
- **Task ID**: build-tests
- **Depends On**: build-view
- **Validates**: tests/integration/test_perplexity_paste.py
- **Assigned To**: paste-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test valid paste: content written, question discovery enqueued, redirect to phase 3
- Test empty content: no write, no enqueue
- Test sentinel-prefixed content (`[SKIPPED:...`): no write, no enqueue
- Test missing `p2-perplexity` artifact: no crash, warning logged

### 4. Validate
- **Task ID**: validate-all
- **Depends On**: build-view, build-template, build-tests
- **Assigned To**: paste-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/ -x -q` and confirm pass
- Confirm `p2-perplexity` is correctly written on valid paste
- Confirm no regression to existing retry views

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-all
- **Assigned To**: paste-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/perplexity-manual-paste.md`
- Add entry to `docs/features/README.md`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| New view registered | `grep -r "paste_perplexity_research" apps/podcast/urls.py` | output contains paste_perplexity_research |
| Feature doc exists | `test -f docs/features/perplexity-manual-paste.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

None — the issue recon confirmed all key assumptions. Ready to build.
