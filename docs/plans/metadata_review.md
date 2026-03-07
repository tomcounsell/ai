# Metadata Review Before Publishing

**Status**: Planning
**Issue**: #131
**Branch**: `session/metadata-review`

## Summary

Add a metadata review and editing section to workflow step 11 (Publishing Assets) to allow users to review and edit AI-generated episode metadata before publishing. Currently, AI generates title, description, show notes, tags, and cover art in phases 10-11, but there's no way to review or modify this content before it goes live.

## User Journey Context

From `docs/plans/episode-editor-user-journey.md` Stage 7:

> After post-production (phases 10-11), the AI generates episode metadata: description, show notes, title refinements, and cover art. Currently there is no way to review or edit any of this AI-generated content before it goes live. The user needs a metadata review screen.

## Current Behavior

**Workflow Step 11 (Publishing Assets) shows:**
- Phase status indicator
- Progress bar
- Sub-steps checklist (cover art, metadata, companions)
- No editable fields or preview of generated content

**Metadata generation:**
- Phase 10 (Audio Processing): Whisper transcription, chapter generation
- Phase 11 (Publishing Assets): Cover art, metadata (title/description/show_notes), companion resources
- Phases go green when complete — no user review step
- Phase 12 (Publish) immediately publishes the episode

**Gap:** User cannot see or edit AI-generated metadata before publishing.

## Desired Behavior

**Workflow Step 11 should include a metadata review section:**
- **Episode title** (editable text input)
- **Episode description** (editable textarea)
- **Show notes** (editable textarea with markdown preview)
- **Tags** (editable comma-separated input)
- **Cover art** (read-only preview with URL)
- All fields should save via HTMX partial update (no full page reload)
- Show save feedback (success/error messages)

**User flow:**
1. User navigates to workflow step 11 after phases 10-11 complete
2. Sees all AI-generated metadata in editable fields
3. Can edit any field inline
4. Changes auto-save on blur
5. Proceeds to step 12 (Publish) with reviewed metadata

## Technical Design

### Template Changes

**File:** `apps/public/templates/podcast/_workflow_step_content.html`

Add a new section AFTER the pipeline action button (before navigation):

```html
<!-- Metadata Review (Step 11 only) -->
{% if current_step == 11 %}
<div class="mb-6 px-4 py-4 border border-gray-200 bg-white">
  <h3 class="text-sm font-semibold text-gray-700 mb-4">Episode Metadata Review</h3>

  <!-- Title Field -->
  <div class="mb-4">
    <label for="episode-title" class="block text-xs font-medium text-gray-600 mb-1">Title</label>
    <input type="text"
           id="episode-title"
           name="title"
           value="{{ episode.title }}"
           class="w-full px-3 py-2 border border-gray-300 text-sm font-mono"
           hx-patch="{% url 'podcast:episode_update_field' slug=podcast.slug episode_slug=episode.slug %}"
           hx-trigger="blur changed delay:500ms"
           hx-vals='{"field": "title"}'
           hx-include="[name='title']"
           hx-target="#save-status-metadata"
           hx-swap="innerHTML">
  </div>

  <!-- Description Field -->
  <div class="mb-4">
    <label for="episode-description" class="block text-xs font-medium text-gray-600 mb-1">Description</label>
    <textarea id="episode-description"
              name="description"
              rows="4"
              class="w-full px-3 py-2 border border-gray-300 text-sm font-mono"
              hx-patch="{% url 'podcast:episode_update_field' slug=podcast.slug episode_slug=episode.slug %}"
              hx-trigger="blur changed delay:500ms"
              hx-vals='{"field": "description"}'
              hx-include="[name='description']"
              hx-target="#save-status-metadata"
              hx-swap="innerHTML">{{ episode.description }}</textarea>
  </div>

  <!-- Show Notes Field -->
  <div class="mb-4">
    <label for="episode-show-notes" class="block text-xs font-medium text-gray-600 mb-1">Show Notes</label>
    <textarea id="episode-show-notes"
              name="show_notes"
              rows="8"
              class="w-full px-3 py-2 border border-gray-300 text-sm font-mono"
              hx-patch="{% url 'podcast:episode_update_field' slug=podcast.slug episode_slug=episode.slug %}"
              hx-trigger="blur changed delay:500ms"
              hx-vals='{"field": "show_notes"}'
              hx-include="[name='show_notes']"
              hx-target="#save-status-metadata"
              hx-swap="innerHTML">{{ episode.show_notes }}</textarea>
  </div>

  <!-- Tags Field -->
  <div class="mb-4">
    <label for="episode-tags" class="block text-xs font-medium text-gray-600 mb-1">
      Tags <span class="text-gray-400">(comma-separated)</span>
    </label>
    <input type="text"
           id="episode-tags"
           name="tags"
           value="{{ episode.tags }}"
           class="w-full px-3 py-2 border border-gray-300 text-sm font-mono"
           placeholder="ai, technology, research"
           hx-patch="{% url 'podcast:episode_update_field' slug=podcast.slug episode_slug=episode.slug %}"
           hx-trigger="blur changed delay:500ms"
           hx-vals='{"field": "tags"}'
           hx-include="[name='tags']"
           hx-target="#save-status-metadata"
           hx-swap="innerHTML">
  </div>

  <!-- Cover Art Preview -->
  {% if episode.cover_image_url %}
  <div class="mb-4">
    <label class="block text-xs font-medium text-gray-600 mb-1">Cover Art</label>
    <img src="{{ episode.cover_image_url }}"
         alt="Episode cover art"
         class="w-48 h-48 object-cover border border-gray-300">
  </div>
  {% endif %}

  <!-- Save Status -->
  <div id="save-status-metadata" class="text-xs text-gray-500"></div>
</div>
{% endif %}
```

### View Changes

**File:** `apps/podcast/views/episode_update.py` (NEW)

Create a new view to handle PATCH requests for field updates:

```python
from django.contrib.auth.mixins import LoginRequiredMixin, UserPassesTestMixin
from django.http import HttpResponse
from django.shortcuts import get_object_or_404
from django.views import View

from apps.podcast.models import Episode, Podcast


class EpisodeUpdateFieldView(LoginRequiredMixin, UserPassesTestMixin, View):
    """Handle inline field updates for episode metadata via HTMX PATCH requests."""

    def test_func(self) -> bool:
        return self.request.user.is_staff

    def patch(self, request, slug: str, episode_slug: str):
        podcast = get_object_or_404(Podcast, slug=slug)
        episode = get_object_or_404(Episode, podcast=podcast, slug=episode_slug)

        field = request.POST.get("field")
        value = request.POST.get(field, "")

        # Whitelist editable fields
        allowed_fields = ["title", "description", "show_notes", "tags"]
        if field not in allowed_fields:
            return HttpResponse("Invalid field", status=400)

        setattr(episode, field, value)
        episode.save(update_fields=[field])

        return HttpResponse(
            f'<span class="text-green-600"><i class="fas fa-check-circle"></i> Saved</span>',
            status=200,
        )
```

### URL Configuration

**File:** `apps/podcast/urls.py`

Add a new URL pattern:

```python
path(
    "<slug:slug>/episodes/<slug:episode_slug>/update-field/",
    EpisodeUpdateFieldView.as_view(),
    name="episode_update_field",
),
```

## Tasks

- [ ] Create `apps/podcast/views/episode_update.py` with `EpisodeUpdateFieldView`
- [ ] Add URL pattern for `episode_update_field` in `apps/podcast/urls.py`
- [ ] Update `_workflow_step_content.html` to include metadata review section for step 11
- [ ] Test inline editing for title, description, show_notes, tags
- [ ] Verify cover art preview displays correctly
- [ ] Test HTMX save feedback messages
- [ ] Verify changes persist across page navigation

## No-Gos

- No migration changes (model fields already exist)
- No cover art upload/regeneration (read-only preview only)
- No chapter marker editing (future enhancement)
- No markdown preview for show notes (plain textarea only for MVP)

## Update System

No update system changes required — this is a UI-only feature.

## Agent Integration

No agent integration required — this is a staff-only web UI feature.

## Documentation

- [ ] Update `docs/plans/episode-editor-user-journey.md` to mark Stage 7 as implemented
- [ ] Add entry to `docs/features/README.md` index table for metadata review feature
