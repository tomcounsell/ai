---
status: In Progress
type: feature
appetite: Small
owner: Tom
created: 2026-02-24
updated: 2026-02-24
tracking: https://github.com/yudame/cuttlefish/issues/101
branch: feature/issue-101-edit-podcast
---

# Edit Podcast Page with Cover Art Management

## Problem

Podcast owners have no web UI to edit their podcast settings. All 3 podcasts have empty `cover_image_url` fields, meaning RSS `<itunes:image>` is empty and podcast apps show no channel artwork. The only way to edit podcast metadata is through Django admin.

**Current behavior:**
- Podcast metadata (title, description, URLs) can only be edited via Django admin
- No cover art upload -- `cover_image_url` is empty for all 3 podcasts
- Episodes without their own cover get no fallback (`effective_cover_image_url` returns `None`)
- The `podcast-cover-art` skill can generate AI covers, but has no UI trigger

**Desired outcome:**
Podcast owners can edit metadata and upload/generate cover art from `/podcast/{slug}/edit/`. Cover images stored in Supabase appear in RSS feeds and podcast apps.

## Current State (as of 2026-02-24)

All implementation code was committed directly to main in commit `a4112e1` without going through SDLC (no feature branch, no PR, no passing tests). This plan documents the validation and cleanup needed.

### What exists and is correct

| Component | File | Status |
|-----------|------|--------|
| PodcastEditView | `apps/podcast/views/podcast_views.py` | Implemented, lines 176-230 |
| URL route | `apps/podcast/urls.py` | Wired at `<slug:slug>/edit/`, line 20 |
| Template | `apps/public/templates/podcast/podcast_edit.html` | Complete, 133 lines |
| Edit button on detail page | `apps/public/templates/podcast/podcast_detail.html` | Lines 22-27, owner-only |
| View export | `apps/podcast/views/__init__.py` | PodcastEditView in `__all__` |
| 10 view tests | `apps/podcast/tests/test_views.py` | Lines 707-835, PodcastEditViewTestCase |

### What is working correctly in the code

- `LoginRequiredMixin` enforces authentication (anonymous -> redirect to login)
- `get_queryset()` scopes to `owner=request.user` (non-owner -> 404)
- Form fields: title, description, author_name, author_email, language, website_url, spotify_url, apple_podcasts_url
- Cover image upload via `request.FILES["cover_image"]` (outside ModelForm)
- File size validation: 5MB max, returns `form_invalid()` with error message
- Privacy-aware Supabase bucket routing via `podcast.uses_private_bucket`
- Private podcasts store storage key; public podcasts store full URL
- Redirect to detail page on success with `messages.success()`
- Template uses brand.css classes (`input-brand`, `btn-brand`)
- Cover art section with current image preview or placeholder icon
- Categories field intentionally excluded (admin-only per plan)
- AI cover art generation intentionally deferred to v2

### Blocker: Database migrations not applied

All 48 podcast view tests fail with:
```
django.db.utils.ProgrammingError: column "published_at" of relation "podcast_podcast" does not exist
```

Three migrations are pending (not applied to local DB):

| Migration | What it does |
|-----------|-------------|
| 0009_add_topic_series_to_episode | Adds `published_at`, `edited_at`, `unpublished_at` to Podcast (Publishable mixin); adds `topic_series` to Episode |
| 0010_rename_topic_series_to_tags | Renames `topic_series` to `tags` on Episode |
| 0011_populate_podcast_metadata | Data migration for yudame-research podcast metadata |

These migrations are NOT specific to issue #101 -- they were created for other features (Publishable mixin on Podcast from issue #96, topic_series/tags rename from issue #103). The Podcast model already inherits from `Publishable` in code, but the DB schema is behind.

**Dependency:** Tom must run `uv run python manage.py migrate podcast` before ANY podcast tests can pass. This blocks the entire podcast test suite, not just the edit view tests.

### Missing test coverage

The existing 10 tests cover access control and metadata editing well, but do not test:
- Cover image upload (mocking `store_file()`)
- File size rejection (> 5MB)

These tests should be added on the feature branch.

## Appetite

**Size:** Small batch (validation + test gap fill, ~1 hour)

The implementation is done. Remaining work is:
1. Create feature branch from main
2. Add cover art upload tests (mocking Supabase `store_file`)
3. Add file size rejection test
4. Verify all tests pass after migrations are applied
5. Open PR for review
6. Close issue #101

## Prerequisites

**Hard blocker:** Migrations 0009-0011 must be applied before tests can run. This is Tom's responsibility per project rules.

## Solution

The solution is already implemented. See "Current State" above for the full inventory.

### Implementation Details (reference)

**View** (`apps/podcast/views/podcast_views.py:176-230`):
- `PodcastEditView(LoginRequiredMixin, MainContentView, UpdateView)`
- `MAX_COVER_SIZE = 5 * 1024 * 1024` (5MB)
- `get_queryset()` -> `Podcast.objects.filter(owner=self.request.user)`
- `form_valid()` handles cover image upload before calling `super()`
- `_upload_cover()` uses `store_file()` with privacy-aware bucket routing

**URL** (`apps/podcast/urls.py:20`):
```python
path("<slug:slug>/edit/", PodcastEditView.as_view(), name="edit"),
```

**Template** (`apps/public/templates/podcast/podcast_edit.html`):
- Breadcrumb: Podcasts / {title} / Edit
- Cover art section with image preview or placeholder
- Metadata form with `input-brand` styling
- Platform links section (Spotify, Apple Podcasts)
- `enctype="multipart/form-data"` on form

## Rabbit Holes

- **AI cover art generation button**: Leave for v2 -- wrapping the CLI skill as an async HTMX action adds background task complexity.
- **Image cropping/resizing**: Accept any image for now, document recommended dimensions (3000x3000).
- **Categories JSON editor**: Exclude from edit form; admin-only.
- **Slug editing**: Changing slugs breaks feed URLs and storage paths.

## No-Gos (Out of Scope)

- AI cover art generation via web UI
- Image cropping/resizing in browser
- Categories JSON editor
- Slug editing
- Privacy/visibility toggle (handled by issue #96)
- Episode-level cover editing

## Risks

### Risk 1: File upload to wrong Supabase bucket
**Impact:** Cover stored in public bucket for a restricted podcast (or vice versa).
**Mitigation:** Already mitigated -- code uses `podcast.uses_private_bucket` for bucket routing.

### Risk 2: Large file uploads
**Impact:** User uploads a 50MB image, causing timeout or memory issues.
**Mitigation:** Already mitigated -- 5MB limit in `form_valid()`.

## Success Criteria

- [x] `/podcast/{slug}/edit/` accessible only to podcast owner (404 for others)
- [x] Form displays current values for all editable fields
- [x] Metadata changes (title, description, URLs) save correctly
- [x] Cover image upload stores file in Supabase and updates `cover_image_url`
- [x] Cover image appears in podcast detail page and RSS feed `<itunes:image>`
- [x] Edit button visible on detail page for owner only
- [x] Non-owner gets 404 (not 403) when accessing edit URL
- [x] File size validation rejects uploads > 5MB
- [ ] Tests pass (BLOCKED: migrations 0009-0011 not applied)
- [ ] Cover art upload tests added (with mocked `store_file`)
- [ ] File size rejection test added
- [ ] Pre-commit passes
- [ ] Feature branch PR opened and reviewed

## Step by Step Tasks

### 1. Apply pending migrations (Tom)
- **Task ID**: apply-migrations
- **Depends On**: none
- **Assigned To**: Tom (manual)
- **Parallel**: false
- Run `uv run python manage.py migrate podcast`
- This applies migrations 0009, 0010, 0011
- Unblocks the entire podcast test suite (48 tests)

### 2. Create feature branch and add missing tests
- **Task ID**: add-missing-tests
- **Depends On**: apply-migrations
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Create `feature/issue-101-edit-podcast` branch from main
- Add test for cover image upload with mocked `store_file()`:
  - POST with a small image file -> podcast.cover_image_url updated
  - Verify `store_file` called with correct args (storage key, bytes, content type, public flag)
- Add test for file size rejection:
  - POST with a file > 5MB -> form error, podcast unchanged
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py::PodcastEditViewTestCase -v`

### 3. Validate full test suite
- **Task ID**: validate-tests
- **Depends On**: add-missing-tests
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py -v`
- Run `uv run pre-commit run --all-files`
- All 48+ view tests must pass

### 4. Open PR and close issue
- **Task ID**: open-pr
- **Depends On**: validate-tests
- **Assigned To**: builder
- **Agent Type**: builder
- **Parallel**: false
- Push `feature/issue-101-edit-podcast` to remote
- Open PR against main with summary of changes
- After merge, close issue #101

## Validation Commands

```bash
# After migrations are applied:
DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py::PodcastEditViewTestCase -v
DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py -v
uv run pre-commit run --all-files
```
