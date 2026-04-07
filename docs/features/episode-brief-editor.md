# Episode Brief Editor

> **Business context:** See [Podcasting](~/work-vault/Cuttlefish/Podcasting.md) in the work vault for product overview and the role of the episode brief in the production pipeline.

Inline editing for episode title and description on workflow step 1.

## Purpose

The episode description is the most critical input — it becomes the `p1-brief` artifact that seeds all 12 phases of research and production. This feature allows users to refine the description before starting the pipeline, without navigating away from the workflow UI.

## Location

**Workflow Step 1**: `/podcast/{slug}/{episode_slug}/edit/1/`

The editor appears above the "Start Pipeline" button, below the phase header.

## Fields

| Field | Required | Auto-save Trigger | Validation |
|-------|----------|-------------------|------------|
| Title | Yes | Blur + 500ms debounce | Non-empty |
| Description | Yes | Blur + 500ms debounce | Non-empty |

## Behavior

1. **Auto-save**: Changes save automatically on blur (when user clicks away) with 500ms debounce
2. **Feedback**: Green checkmark appears after successful save
3. **Validation**: Red error message if field is empty
4. **Button state**: "Start Pipeline" button disabled until description is non-empty

## Technical Details

- **HTMX**: Uses `hx-patch` for partial updates (no page reload)
- **Endpoint**: `PATCH /podcast/{slug}/{episode_slug}/edit/1/update/`
- **Scope**: Only available on step 1 (before pipeline starts)

## Why Description is Critical

The description becomes the `p1-brief` artifact in `setup_episode()`:

```python
brief = EpisodeArtifact.objects.create(
    episode=episode,
    title="p1-brief",
    content=episode.description,  # ← Seeds all research
    artifact_type="text",
)
```

This brief is used by:
- Phase 2: Perplexity research (initial broad research)
- Phase 3: Question discovery (identifies research questions)
- Phase 4: Targeted research (GPT + Gemini deep dives)
- Phase 5: Cross-validation (fact-checking)
- Phase 6: Master briefing (synthesis)

A vague or empty description leads to unfocused research. A specific, detailed description produces high-quality output.

## Example Descriptions

**❌ Weak:**
> "An episode about AI"

**✅ Strong:**
> "Explore the alignment problem in AI: Why is it hard to specify human values in code? Cover inner alignment (mesa-optimizers), outer alignment (reward hacking), and recent approaches like RLHF, debate, and recursive reward modeling. Include perspectives from Stuart Russell, Paul Christiano, and Eliezer Yudkowsky."

## Implementation Details

### Template Changes

Added episode brief editor section in `apps/public/templates/podcast/_workflow_step_content.html`:
- Title input field with HTMX auto-save
- Description textarea with HTMX auto-save
- Save status indicator

### View Changes

Added `patch()` method to `EpisodeWorkflowView` in `apps/podcast/workflow.py`:
- Parses PATCH data from request body using `QueryDict`
- Validates field name (title or description only)
- Validates non-empty values
- Saves to database using `update_fields` for efficiency
- Returns HTML status message for HTMX to inject

### Button State Logic

Updated `_compute_button_state()` function to:
- Check if `episode.description.strip()` is non-empty
- Disable "Start Pipeline" button when description is empty
- Show blocked reason: "Episode description is required"

### URL Configuration

Added new route in `apps/podcast/urls.py`:
```python
path(
    "<slug:slug>/<slug:episode_slug>/edit/<int:step>/update/",
    EpisodeWorkflowView.as_view(),
    name="episode_brief_update",
)
```

## Testing

### Unit Tests (23 tests in `test_workflow_views.py`)

New tests added:
1. `test_patch_updates_title` - Verifies title can be updated via PATCH
2. `test_patch_updates_description` - Verifies description can be updated
3. `test_patch_rejects_empty_title` - Validates empty title rejection
4. `test_patch_rejects_empty_description` - Validates empty description rejection
5. `test_patch_only_works_on_step_1` - Ensures editing restricted to step 1
6. `test_patch_rejects_invalid_field` - Validates field name checking
7. `test_button_disabled_when_description_empty` - Verifies button state when no description
8. `test_button_enabled_when_description_exists` - Verifies button enabled with description

All tests pass successfully.

## Usage Flow

1. User creates episode via `/podcast/{slug}/new/` form
2. Redirects to workflow step 1 (`/podcast/{slug}/{episode_slug}/edit/1/`)
3. User sees pre-filled title and description fields
4. User edits either field → changes auto-save on blur
5. Green checkmark appears after successful save
6. If description is empty, "Start Pipeline" button is disabled with warning message
7. Once description is non-empty, button becomes enabled
8. User clicks "Start Pipeline" → `produce_episode` task enqueued with updated description

## Edge Cases Handled

1. **Empty description on page load** → Button disabled, blocked reason shown
2. **User clears description** → Validation error, save rejected
3. **Step 2+ access** → PATCH returns 400 "Field editing only available on step 1"
4. **Invalid field name** → PATCH returns 400 "Invalid field"
5. **Unicode/emoji in fields** → Handled natively by Django TextField/CharField
6. **Very long descriptions** → No length limit (LLM can handle ~8k tokens)

## Future Enhancements

- Tags inline editing
- Research depth/tone controls (e.g., "technical deep-dive" vs "beginner-friendly")
- Description templates (interview, explainer, debate formats)
- Character count/token estimate for description
- Preview of p1-brief artifact before starting pipeline
- Real-time collaboration (multiple users editing same episode)
