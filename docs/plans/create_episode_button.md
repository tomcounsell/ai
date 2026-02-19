---
status: Planning
type: feature
appetite: Small
owner: Tom
created: 2026-02-19
tracking: https://github.com/yudame/cuttlefish/issues/93
---

# Create New Episode Button on Podcast Detail Page

## Problem

Staff currently have to use Django admin or the management command `start_episode` to create new draft episodes. There's no way to create an episode directly from the podcast detail page, breaking the browsing-to-action flow.

**Current behavior:**
Staff visit `/podcast/{slug}/`, see the episode list, then must navigate to `/admin/podcast/episode/add/` or run a CLI command to create a new episode.

**Desired outcome:**
A "New Episode" button on the podcast detail page (visible only to staff) leads to a simple form that creates a draft Episode and redirects to the episode workflow view at step 1.

## Appetite

**Size:** Small

**Team:** Solo dev, no review.

**Interactions:**
- PM check-ins: 0
- Review rounds: 0

## Prerequisites

No prerequisites — this work has no external dependencies.

## Solution

### Key Elements

- **Staff-only button**: A "New Episode" button on `podcast_detail.html`, conditionally rendered when `request.user.is_staff`
- **Create Episode view**: A new `EpisodeCreateView` in `podcast_views.py` — staff-only, handles GET (form) and POST (create + redirect)
- **Create Episode template**: A minimal form template collecting title and description (slug auto-generated, episode_number auto-assigned by model `save()`)
- **URL route**: `<slug:slug>/new/` mounted before the `<slug:episode_slug>/` catch-all

### Flow

**Podcast detail page** → Click "New Episode" → **Create form** (title + description) → Submit → **Episode workflow view** (step 1, ready to start pipeline)

### Technical Approach

- **View**: `EpisodeCreateView(LoginRequiredMixin, UserPassesTestMixin, MainContentView)` — same auth pattern as `EpisodeWorkflowView`
- **Form handling**: Use a plain Django form (no ModelForm needed — only 2 fields). On POST, create the Episode with `status="draft"`, auto-slug from title via `django.utils.text.slugify`, auto-increment `episode_number` (already handled by `Episode.save()`)
- **Redirect**: After creation, redirect to `podcast:episode_workflow` at step 1
- **Template**: Reuse the existing breadcrumb pattern + brand button styles
- **URL ordering**: Place `<slug:slug>/new/` before `<slug:slug>/<slug:episode_slug>/` in `urls.py` to avoid "new" being captured as an episode slug

## Rabbit Holes

- **Full ModelForm with every field** — Only title and description are needed at creation time; everything else gets populated by the workflow pipeline
- **HTMX form submission** — Standard form POST + redirect is fine for a creation action
- **Auto-starting the pipeline on create** — Out of scope; the workflow view already has a "Start Pipeline" button

## Risks

### Risk 1: URL slug collision
**Impact:** If a podcast has an episode with slug "new", the new URL `<slug>/new/` would shadow it
**Mitigation:** Place the `new/` route before the `<slug:episode_slug>/` route in `urls.py`. Django matches top-to-bottom, so `new/` wins. Extremely unlikely anyone names an episode "new".

### Risk 2: Duplicate slug creation
**Impact:** Creating two episodes with the same title would produce duplicate slugs, violating the unique constraint
**Mitigation:** Append episode number or handle `IntegrityError` with a validation message

## No-Gos (Out of Scope)

- Auto-starting the production pipeline after creation
- Editing existing episodes from the frontend
- Topic/description AI generation
- Bulk episode creation

## Update System

No update system changes required — this is a web UI feature only.

## Agent Integration

No agent integration required — this is a staff-facing web form.

## Documentation

### Inline Documentation
- [ ] Docstring on `EpisodeCreateView`

No external documentation changes needed — this is a minor staff-facing UI addition.

## Success Criteria

- [ ] "New Episode" button visible on `/podcast/{slug}/` for staff users only
- [ ] Button not visible to anonymous or non-staff users
- [ ] Form at `/podcast/{slug}/new/` collects title and description
- [ ] Slug auto-generated from title
- [ ] Episode number auto-assigned
- [ ] On submit, creates draft Episode and redirects to workflow step 1
- [ ] Duplicate slug handled gracefully
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (create-episode)**
  - Name: episode-builder
  - Role: Implement view, template, URL, and button
  - Agent Type: builder
  - Resume: true

- **Validator (create-episode)**
  - Name: episode-validator
  - Role: Verify implementation meets criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add URL route
- **Task ID**: build-url
- **Depends On**: none
- **Assigned To**: episode-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `path("<slug:slug>/new/", EpisodeCreateView.as_view(), name="episode_create")` to `apps/podcast/urls.py`
- Place BEFORE the `<slug:episode_slug>/` route
- Add `EpisodeCreateView` to imports in `apps/podcast/views/__init__.py`

### 2. Implement EpisodeCreateView
- **Task ID**: build-view
- **Depends On**: none
- **Assigned To**: episode-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `EpisodeCreateView` to `apps/podcast/views/podcast_views.py`
- `LoginRequiredMixin` + `UserPassesTestMixin` (test_func: `is_staff`)
- GET: render form template with podcast context
- POST: validate title + description, create Episode (draft, auto-slug, auto-number), redirect to workflow step 1
- Handle `IntegrityError` on duplicate slug by appending episode number

### 3. Create form template
- **Task ID**: build-template
- **Depends On**: none
- **Assigned To**: episode-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `apps/public/templates/podcast/episode_create.html`
- Breadcrumb: Podcasts / {podcast.title} / New Episode
- Simple form with title input, description textarea, submit button using `btn-brand` class
- CSRF token included

### 4. Add button to podcast detail template
- **Task ID**: build-button
- **Depends On**: none
- **Assigned To**: episode-builder
- **Agent Type**: builder
- **Parallel**: true
- Add "New Episode" button to `podcast_detail.html` header, next to RSS Feed button
- Wrap in `{% if request.user.is_staff %}` conditional
- Use existing button styling pattern (match RSS button style or use `btn-brand`)

### 5. Write tests
- **Task ID**: build-tests
- **Depends On**: build-view
- **Assigned To**: episode-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `EpisodeCreateViewTestCase` to `apps/podcast/tests/test_views.py`
- Test: anonymous user gets redirected (302)
- Test: non-staff user gets 403
- Test: staff user sees form (200)
- Test: staff POST creates episode and redirects
- Test: button visible to staff on detail page
- Test: button hidden from non-staff on detail page

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: episode-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py -v`
- Verify all success criteria met
- Check button visibility for staff vs non-staff

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py -v` - all view tests pass
- `uv run pre-commit run --all-files` - code quality checks pass

---

## Open Questions

1. Should the "New Episode" button use the brand accent style (`btn-brand-accent`) to stand out, or match the RSS button's outlined style for visual consistency?
2. After creating the episode, should we redirect to the workflow view (step 1) or to the episode detail page?
3. Should the form also allow setting a custom slug, or always auto-generate from title?
