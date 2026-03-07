# Episode Brief Editor on Workflow Step 1

**Status**: Planning
**Issue**: #129
**Branch**: `session/episode-brief-editor`

## Summary

Add inline editing for episode title and description on workflow step 1. The description is the single most critical input — it becomes the p1-brief artifact that seeds all 12 phases of research and production.

Currently, workflow step 1 is a monitoring dashboard showing phase status and a "Start Pipeline" button. Users cannot edit episode details from the workflow UI — they must go to Django admin or the episode creation form.

## User Journey Context

From `docs/plans/episode-editor-user-journey.md` Stage 3:

> The workflow page is a *monitoring dashboard*, not an *editor*. Before the user clicks "Start Pipeline," they need a place to:
> 1. Set the episode title
> 2. Write the episode description/topic (which seeds all the research)
> 3. Optionally configure depth, tone, or research focus

## Current Behavior

**Workflow Step 1 shows:**
- Phase status indicator (gray/amber/green dot)
- Phase description
- Progress bar
- Sub-steps checklist (artifact existence checks)
- "Start Pipeline" button (always enabled when workflow is pending)
- No editable fields

**Episode creation flow:**
1. User creates episode via `EpisodeCreateView` (form with title, description, tags)
2. Redirects to workflow step 1
3. User clicks "Start Pipeline" → `produce_episode` task enqueued
4. `setup_episode()` reads `Episode.description` to create `p1-brief` artifact

**Gap:** If the user wants to revise the description after landing on the workflow page (but before starting the pipeline), they must navigate away to Django admin or back to the creation form.

## Desired Behavior

**Workflow Step 1 should include:**
- **Inline editable title field** (text input with HTMX save)
- **Inline editable description field** (textarea with HTMX save)
- Optional: Tags field (for future enhancement)
- "Start Pipeline" button should be **disabled** when description is empty
- Changes should save via HTMX partial update (no full page reload)
- Show save feedback (success/error messages)

**User flow:**
1. User lands on workflow step 1 after episode creation
2. Sees title and description fields (pre-filled from creation form)
3. Can edit either field inline
4. Edits auto-save on blur or explicit save button
5. "Start Pipeline" button only enabled when description is non-empty
6. Clicks "Start Pipeline" → pipeline starts with the edited description

## Technical Design

### Template Changes

**File:** `apps/public/templates/podcast/_workflow_step_content.html`

Add a new section BEFORE the pipeline action button:

```html
<!-- Episode Brief Editor (Step 1 only) -->
{% if current_step == 1 %}
<div class="mb-6 px-4 py-4 border border-gray-200 bg-white">
  <h3 class="text-sm font-semibold text-gray-700 mb-3">Episode Details</h3>

  <!-- Title Field -->
  <div class="mb-4">
    <label for="episode-title" class="block text-xs font-medium text-gray-600 mb-1">Title</label>
    <input type="text"
           id="episode-title"
           name="title"
           value="{{ episode.title }}"
           class="input-brand w-full"
           hx-patch="{% url 'podcast:episode_update_field' slug=podcast.slug episode_slug=episode.slug %}"
           hx-trigger="blur changed delay:500ms"
           hx-vals='{"field": "title"}'
           hx-include="[name='title']"
           hx-target="#save-status"
           hx-swap="innerHTML">
  </div>

  <!-- Description Field -->
  <div class="mb-4">
    <label for="episode-description" class="block text-xs font-medium text-gray-600 mb-1">
      Description / Research Prompt
      <span class="text-red-500">*</span>
    </label>
    <textarea id="episode-description"
              name="description"
              rows="6"
              class="input-brand w-full"
              placeholder="Describe what this episode should cover. This becomes the research prompt for the AI pipeline."
              hx-patch="{% url 'podcast:episode_update_field' slug=podcast.slug episode_slug=episode.slug %}"
              hx-trigger="blur changed delay:500ms"
              hx-vals='{"field": "description"}'
              hx-include="[name='description']"
              hx-target="#save-status"
              hx-swap="innerHTML">{{ episode.description }}</textarea>
    <p class="text-xs text-gray-500 mt-1">This prompt seeds all research phases. Be specific about topics, themes, or questions to explore.</p>
  </div>

  <!-- Save Status -->
  <div id="save-status" class="text-sm"></div>
</div>
{% endif %}
```

### View Changes

**File:** `apps/podcast/workflow.py`

Add a new PATCH handler for field updates:

```python
def patch(self, request, slug: str, episode_slug: str, step: int, *args, **kwargs):
    """Handle HTMX field updates for episode title/description on step 1."""
    podcast, episode = self._load_context(request, slug, episode_slug, step)

    if step != 1:
        return HttpResponse("Field editing only available on step 1", status=400)

    field = request.POST.get("field")
    if field not in ["title", "description"]:
        return HttpResponse("Invalid field", status=400)

    value = request.POST.get(field, "").strip()

    # Validation
    if field == "title" and not value:
        return HttpResponse('<span class="text-red-600">Title cannot be empty</span>', status=400)

    if field == "description" and not value:
        return HttpResponse('<span class="text-red-600">Description cannot be empty</span>', status=400)

    # Save
    setattr(episode, field, value)
    episode.save(update_fields=[field])

    # Return success message
    return HttpResponse('<span class="text-green-600"><i class="fas fa-check-circle"></i> Saved</span>')
```

### URL Configuration

**File:** `apps/podcast/urls.py`

Add a new route for field updates:

```python
path(
    "<slug:slug>/<slug:episode_slug>/edit/<int:step>/update/",
    EpisodeWorkflowView.as_view(),
    name="episode_update_field",
),
```

### Button State Logic Update

**File:** `apps/podcast/workflow.py` (function `_compute_button_state`)

Update the "Start Pipeline" button state to check for empty description:

```python
if wf.status == "pending" and step == 1:
    disabled = not episode.description.strip()
    return {
        "show": True,
        "label": "Start Pipeline",
        "color": "green" if not disabled else "gray",
        "icon": "check",
        "disabled": disabled,
        "blocked_reason": "Episode description is required" if disabled else "",
        "error": "",
    }
```

## Testing Strategy

### Unit Tests

**File:** `apps/podcast/tests/test_workflow_views.py`

Add tests for:
1. PATCH request updates title successfully
2. PATCH request updates description successfully
3. PATCH request rejects empty title
4. PATCH request rejects empty description
5. PATCH request only works on step 1
6. "Start Pipeline" button disabled when description is empty
7. "Start Pipeline" button enabled when description exists

### Integration Tests

**File:** `apps/podcast/tests/test_workflow_integration.py` (new)

Browser-based tests using Playwright:
1. Navigate to workflow step 1
2. Edit title field, verify auto-save
3. Edit description field, verify auto-save
4. Clear description, verify "Start Pipeline" becomes disabled
5. Add description, verify "Start Pipeline" becomes enabled
6. Start pipeline with edited description, verify p1-brief contains new text

### Test Data Setup

Use existing `EpisodeWorkflowViewTestCase` fixtures:
- `setUpTestData()` creates podcast, episode, user
- Use factory pattern for episodes with/without descriptions

## Implementation Checklist

- [ ] Update `_workflow_step_content.html` template with editor UI
- [ ] Add `patch()` method to `EpisodeWorkflowView`
- [ ] Update `_compute_button_state()` to check description
- [ ] Add URL route for `episode_update_field`
- [ ] Write unit tests for PATCH handler
- [ ] Write integration tests for HTMX interactions
- [ ] Test button enable/disable behavior
- [ ] Verify p1-brief artifact uses updated description

## Edge Cases

1. **User edits description after starting pipeline** → Field editing only available on step 1 (PATCH returns 400 for other steps)
2. **Concurrent edits** → Last write wins (Django ORM behavior). Not a concern for single-user workflow.
3. **Very long descriptions** → No length limit enforced. LLM context windows can handle ~8k tokens.
4. **Empty description on existing episodes** → Button disabled, blocked_reason shown. User must add description.
5. **Unicode/emoji in fields** → Django CharField/TextField handle UTF-8 natively. No special handling needed.

## No-Gos

- ❌ **Do NOT add a separate "Save" button** — use auto-save on blur
- ❌ **Do NOT allow editing on steps 2-12** — brief is locked once pipeline starts
- ❌ **Do NOT create migrations** — models already have `title` and `description` fields
- ❌ **Do NOT add tags editing yet** — focus on title/description only (tags can be future enhancement)
- ❌ **Do NOT add visual editor** — plain textarea is sufficient for v1

## Update System

No update system changes required. This is a pure UI/template enhancement with no new dependencies, config files, or migration needs. Existing deployments will get the new editor UI automatically via template changes.

## Agent Integration

No agent integration changes required. This feature is web-only (Django views + templates). The agent doesn't directly interact with the workflow UI — it monitors episodes via the database models, which remain unchanged.

## Documentation

- [ ] Create `docs/features/episode-brief-editor.md` describing the feature
- [ ] Add screenshots showing:
  - Empty episode with disabled "Start Pipeline" button
  - Inline editing with auto-save feedback
  - Enabled "Start Pipeline" button after description added
- [ ] Add entry to `docs/features/README.md` index table

### Documentation Content

**File:** `docs/features/episode-brief-editor.md`

```markdown
# Episode Brief Editor

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

## Screenshots

*(Add screenshots showing the editor in action)*
```

## Success Criteria

✅ **Deliverable exists and works**
- Episode title and description are editable on workflow step 1
- Auto-save works on blur with visual feedback
- "Start Pipeline" button disabled when description is empty

✅ **Code quality standards met**
- Ruff and Black pass
- Unit tests cover PATCH handler logic
- Integration tests verify HTMX interactions

✅ **Changes committed and pushed**
- All changes on `session/episode-brief-editor` branch
- PR opened linking to #129

✅ **Original request fulfilled**
- User can edit episode brief before starting pipeline
- Description validation prevents empty research prompts
- Workflow UI is now an editor, not just a monitoring dashboard

## Future Enhancements

- Tags inline editing
- Research depth/tone controls (e.g., "technical deep-dive" vs "beginner-friendly overview")
- Description templates (e.g., "interview format", "explainer format", "debate format")
- Character count/token estimate for description
- Preview what the p1-brief artifact will look like before starting pipeline
