---
status: In Progress
type: feature
appetite: Small
owner: Valor
created: 2026-03-07
tracking: https://github.com/yudame/cuttlefish/issues/128
---

# Episode Creation Form with Title/Description/Tags

## Problem

When a user clicks "+ New Episode" on the podcast detail page, they currently get an "Untitled Episode" with no opportunity to set a title, description, or topic. The episode description seeds all downstream research (it becomes the `p1-brief` artifact), so the user **must** be able to write it before the pipeline starts.

**Current behavior:**
- Click "+ New Episode" → creates `Episode(title="Untitled Episode")` → redirects to workflow page
- No form fields for title, description, or tags
- User has no control over what the research pipeline investigates

**Desired outcome:**
- Click "+ New Episode" → lands on an **episode creation form** with:
  - **Title** (or working title)
  - **Description / topic idea** (becomes the research prompt / `p1-brief` seed)
  - **Tags** (optional, for categorization)
- Submit → creates episode with user-provided data → redirects to workflow step 1

**User journey context:**
This is **Stage 2** from `docs/plans/episode-editor-user-journey.md` — the critical entry point before automated research begins.

## Appetite

**Size:** Small (1-2 days)

**Team:** Solo dev. Frontend form + view update.

**Interactions:**
- Review rounds: 1

## Prerequisites

- Episode model already has `title`, `description`, and `tags` fields
- Route `/podcast/{slug}/new/` already exists as `EpisodeCreateView`
- Form styling patterns exist in `podcast_edit.html`

## Solution

### Key Elements

- **Django Form** — `EpisodeForm` with title (required), description (required), tags (optional)
- **Updated View** — `EpisodeCreateView` serves GET (render form) and POST (create episode)
- **New Template** — `episode_create.html` following existing patterns (HTMX, Tailwind v4, brand.css)
- **Redirect** — After successful creation, redirect to workflow step 1 with populated episode

### Technical Approach

1. **Create `apps/podcast/forms.py`:**

   ```python
   from django import forms
   from apps.podcast.models import Episode

   class EpisodeForm(forms.ModelForm):
       class Meta:
           model = Episode
           fields = ["title", "description", "tags"]
           widgets = {
               "title": forms.TextInput(attrs={"class": "input-brand w-full"}),
               "description": forms.Textarea(attrs={"class": "input-brand w-full", "rows": 6}),
               "tags": forms.TextInput(attrs={"class": "input-brand w-full", "placeholder": "e.g. AI, productivity, deep-dive"}),
           }
           labels = {
               "title": "Episode Title",
               "description": "Description / Topic",
               "tags": "Tags (comma-separated)",
           }
           help_texts = {
               "description": "This becomes the research prompt for the AI pipeline. Be specific about what you want the episode to cover.",
           }
   ```

2. **Update `EpisodeCreateView` in `apps/podcast/views/podcast_views.py`:**

   ```python
   from django.views.generic.edit import CreateView
   from apps.podcast.forms import EpisodeForm

   class EpisodeCreateView(LoginRequiredMixin, UserPassesTestMixin, CreateView):
       """Create a draft Episode with title, description, and tags before starting workflow."""
       model = Episode
       form_class = EpisodeForm
       template_name = "podcast/episode_create.html"

       def test_func(self) -> bool:
           return self.request.user.is_staff

       def get_context_data(self, **kwargs):
           context = super().get_context_data(**kwargs)
           podcast = get_object_or_404(Podcast, slug=self.kwargs["slug"])
           context["podcast"] = podcast
           return context

       def form_valid(self, form):
           podcast = get_object_or_404(Podcast, slug=self.kwargs["slug"])
           form.instance.podcast = podcast
           form.instance.slug = uuid4().hex[:12]
           form.instance.status = "draft"
           return super().form_valid(form)

       def get_success_url(self):
           return reverse(
               "podcast:episode_workflow",
               kwargs={
                   "slug": self.object.podcast.slug,
                   "episode_slug": self.object.slug,
                   "step": 1,
               },
           )
   ```

3. **Create `apps/public/templates/podcast/episode_create.html`:**

   Follow `podcast_edit.html` patterns:
   - Breadcrumb navigation
   - Form with CSRF token
   - Input fields using `input-brand` class
   - Error display for non-field errors and field-specific errors
   - Cancel button → back to podcast detail
   - Submit button → "Create Episode" (btn-brand)

4. **Form Field Requirements:**
   - **Title**: Required, max 200 chars
   - **Description**: Required (this is the research prompt)
   - **Tags**: Optional, comma-separated text input

### File Changes

**New files:**
- `apps/podcast/forms.py` — EpisodeForm
- `apps/public/templates/podcast/episode_create.html` — form template

**Modified files:**
- `apps/podcast/views/podcast_views.py` — Convert EpisodeCreateView from View to CreateView
- `apps/podcast/views/__init__.py` — Add EpisodeForm import (if exposed)

## Rabbit Holes

- **Don't add slug editing** — Auto-generated UUID slug is correct (user never sees it)
- **Don't add episode number editing** — Auto-incremented on save
- **Don't add file uploads** — Cover art and audio come later in the workflow
- **Don't add HTMX partial updates** — Simple form submission is sufficient
- **Don't change ownership model** — Keep staff-only for now (owner access is a separate issue)

## No-Gos

- No rich text editor for description (plain textarea is fine)
- No tag autocomplete (future enhancement)
- No episode duplication feature
- No draft saving without submission (user must fill form and submit)
- No preview of what the research prompt will look like

## Documentation

No documentation changes needed — this is an internal UI enhancement. The episode creation form is self-explanatory from the user journey doc.

## Update System

No update system changes required — this feature is purely internal to the Django app.

## Agent Integration

No agent integration required — this is a web UI feature for human users.

## Acceptance Criteria

- [ ] EpisodeForm created with title (required), description (required), tags (optional)
- [ ] EpisodeCreateView updated to serve GET (form) and POST (create)
- [ ] episode_create.html template follows podcast_edit.html styling patterns
- [ ] Form validates required fields and shows errors
- [ ] Successful submission creates Episode with user-provided data
- [ ] Episode.description is populated and ready for `setup_episode()` to create `p1-brief`
- [ ] Redirects to workflow step 1 after creation
- [ ] Existing tests pass
- [ ] Ruff format and check pass
- [ ] PR opened linking to issue #128
