---
status: Planning
type: feature
appetite: Small
owner: Tom
created: 2026-02-24
tracking: https://github.com/yudame/cuttlefish/issues/101
---

# Edit Podcast Page with Cover Art Management

## Problem

Podcast owners have no web UI to edit their podcast settings. All 3 podcasts have empty `cover_image_url` fields, meaning RSS `<itunes:image>` is empty and podcast apps show no channel artwork. The only way to edit podcast metadata is through Django admin.

**Current behavior:**
- Podcast metadata (title, description, URLs) can only be edited via Django admin
- No cover art upload — `cover_image_url` is empty for all 3 podcasts
- Episodes without their own cover get no fallback (`effective_cover_image_url` returns `None`)
- The `podcast-cover-art` skill can generate AI covers, but has no UI trigger

**Desired outcome:**
Podcast owners can edit metadata and upload/generate cover art from `/podcast/{slug}/edit/`. Cover images stored in Supabase appear in RSS feeds and podcast apps.

## Appetite

**Size:** Small batch (1-2 days implementation)

**Team:** Solo dev + PM (Tom reviews visual output)

**Interactions:**
- PM check-in: 1 (visual review of edit page)
- Review rounds: 1 (PR review before merge)

## Prerequisites

No hard prerequisites. Note that issue #96 will rename `is_public` to `privacy` — the edit page should use `podcast.is_public` for bucket routing, which will become a backward-compat property after #96. No conflict.

## Solution

### Key Elements

- **PodcastEditView**: `UpdateView` with owner-only access, following the `TeamUpdateView` pattern
- **Cover art upload**: File input accepting PNG/JPG, uploaded to Supabase via `store_file()`
- **Cover art generation**: HTMX button that triggers AI generation via `podcast-cover-art` pipeline (v2 scope — see Rabbit Holes)
- **Edit button on detail page**: Visible to owner, links to edit page

### Flow

**Owner visits podcast detail** → sees "Edit" button → clicks → **Edit page** with form fields + cover art section → uploads image or edits metadata → **POST** → file stored in Supabase → `cover_image_url` updated → redirect to detail page with success message

### Technical Approach

**View** (`apps/podcast/views/podcast_views.py`):

```python
class PodcastEditView(LoginRequiredMixin, MainContentView, UpdateView):
    model = Podcast
    template_name = "podcast/podcast_edit.html"
    fields = [
        "title", "description", "author_name", "author_email",
        "language", "website_url", "spotify_url", "apple_podcasts_url",
    ]

    def get_queryset(self):
        return Podcast.objects.filter(owner=self.request.user)

    def get_context_data(self, **kwargs):
        context = super().get_context_data(**kwargs)
        context["title"] = f"Edit: {self.object.title}"
        return context

    def form_valid(self, form):
        # Handle cover image upload (separate from ModelForm fields)
        cover_file = self.request.FILES.get("cover_image")
        if cover_file:
            self._upload_cover(form.instance, cover_file)

        response = super().form_valid(form)
        messages.success(self.request, "Podcast updated.")
        return response

    def _upload_cover(self, podcast, cover_file):
        storage_key = f"podcast/{podcast.slug}/cover.png"
        image_bytes = cover_file.read()
        content_type = cover_file.content_type or "image/png"
        url = store_file(storage_key, image_bytes, content_type, public=podcast.is_public)
        podcast.cover_image_url = url if podcast.is_public else storage_key

    def get_success_url(self):
        return reverse("podcast:detail", kwargs={"slug": self.object.slug})
```

**Key decisions:**
- Cover image handled via `request.FILES` outside the ModelForm (avoids adding `ImageField` to model — keep `URLField` as-is)
- `enctype="multipart/form-data"` on the form template
- `get_queryset()` scopes to owner's podcasts (returns 404 for non-owners automatically)
- For private podcasts, store the storage key in `cover_image_url` (signed URLs generated on-demand in feed view, matching the pattern in `publishing.py` line 68-72)

**URL** (`apps/podcast/urls.py`):
```python
path("<slug:slug>/edit/", PodcastEditView.as_view(), name="edit"),
```
Place before the `<slug:slug>/<slug:episode_slug>/` pattern to avoid slug collision.

**Template** (`apps/public/templates/podcast/podcast_edit.html`):
- Extends `base_template`
- Breadcrumb: Podcasts / {title} / Edit
- Two sections: metadata form + cover art upload
- Cover art section shows current image (or placeholder) with file input
- Uses `input-brand`, `btn-brand` classes from brand.css
- `<form method="post" enctype="multipart/form-data">`

**Detail page edit button** (`podcast_detail.html`):
- Add edit link next to RSS/New Episode buttons, visible when `is_owner` is true

**View registration** (`apps/podcast/views/__init__.py`):
- Export `PodcastEditView`

## Rabbit Holes

- **AI cover art generation button**: The `podcast-cover-art` skill is a CLI tool that operates on filesystem paths. Wrapping it as an async HTMX action adds complexity (background task, polling, preview). Leave for v2 — manual upload covers the immediate need.
- **Image cropping/resizing**: Podcast covers should be 1400x1400 or 3000x3000 per Apple spec. Don't add client-side cropping — accept any image for now, document recommended dimensions.
- **Categories JSON editor**: The `categories` field is a JSONField. Building a proper tag picker is a separate UX effort. Exclude from edit form; keep admin-only for now.
- **Slug editing**: Changing slugs breaks feed URLs and Supabase storage paths. Don't include `slug` in the form.

## Risks

### Risk 1: File upload to wrong Supabase bucket
**Impact:** Cover stored in public bucket for a restricted podcast (or vice versa).
**Mitigation:** Use `podcast.is_public` for bucket routing, matching the pattern in `services/audio.py`. After issue #96, this becomes `podcast.uses_private_bucket`.

### Risk 2: Large file uploads
**Impact:** User uploads a 50MB image, causing timeout or memory issues.
**Mitigation:** Add Django form validation limiting file size to 5MB. Podcast covers are typically 500KB-2MB.

## No-Gos (Out of Scope)

- AI cover art generation via web UI — separate effort (requires async task pipeline)
- Image cropping/resizing in browser
- Categories JSON editor
- Slug editing
- Privacy/visibility toggle (handled by issue #96)
- Episode-level cover editing (separate from podcast channel cover)

## Update System

No update system changes required.

## Agent Integration

No agent integration required — straightforward Django CRUD view.

## Documentation

### Feature Documentation
- [ ] No new docs needed — standard Django view following established patterns

### Inline Documentation
- [ ] Docstrings on `PodcastEditView` and `_upload_cover` method

## Success Criteria

- [ ] `/podcast/{slug}/edit/` accessible only to podcast owner (404 for others)
- [ ] Form displays current values for all editable fields
- [ ] Metadata changes (title, description, URLs) save correctly
- [ ] Cover image upload stores file in Supabase and updates `cover_image_url`
- [ ] Cover image appears in podcast detail page and RSS feed `<itunes:image>`
- [ ] Edit button visible on detail page for owner only
- [ ] Non-owner gets 404 (not 403) when accessing edit URL
- [ ] File size validation rejects uploads > 5MB
- [ ] Tests pass
- [ ] Pre-commit passes

## Team Members

- **Builder (edit-page)**
  - Name: edit-page-builder
  - Role: Create view, form handling, template, URL routing, cover upload
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Verify access control, upload flow, template rendering, test pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create PodcastEditView and URL route
- **Task ID**: build-view-and-url
- **Depends On**: none
- **Assigned To**: edit-page-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `PodcastEditView` to `apps/podcast/views/podcast_views.py` following `TeamUpdateView` pattern
- Handle cover image upload in `form_valid()` via `request.FILES`
- Add URL pattern `<slug:slug>/edit/` to `apps/podcast/urls.py` (before episode slug patterns)
- Export from `apps/podcast/views/__init__.py`
- Add file size validation (max 5MB) in `form_valid`
- Commit changes

### 2. Create edit template
- **Task ID**: build-template
- **Depends On**: build-view-and-url
- **Assigned To**: edit-page-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/public/templates/podcast/podcast_edit.html`
- Breadcrumb navigation: Podcasts / {title} / Edit
- Metadata section: form fields using `input-brand` classes
- Cover art section: current image preview + file upload input
- Use `enctype="multipart/form-data"`
- Submit button with `btn-brand`
- Commit changes

### 3. Add edit button to detail page
- **Task ID**: build-detail-button
- **Depends On**: build-view-and-url
- **Assigned To**: edit-page-builder
- **Agent Type**: builder
- **Parallel**: true
- Add "Edit" link/button to `podcast_detail.html` header area, visible when `is_owner`
- Style consistent with existing RSS/New Episode buttons
- Commit changes

### 4. Write tests
- **Task ID**: build-tests
- **Depends On**: build-template
- **Assigned To**: edit-page-builder
- **Agent Type**: builder
- **Parallel**: false
- Test owner can access edit page (200)
- Test non-owner gets 404
- Test anonymous user redirected to login
- Test form saves metadata changes
- Test cover image upload stores file and updates model
- Test file size > 5MB rejected
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v`
- Commit changes

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: build-tests, build-detail-button
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all success criteria
- Run full test suite
- Run `uv run pre-commit run --all-files`
- Report pass/fail

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` -- all pass
- `uv run pre-commit run --all-files` -- all pass
- `curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/podcast/satsol/edit/` -- 302 (redirect to login) for anonymous
