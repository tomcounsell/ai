---
status: Ready
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
A "New Episode" button on the podcast detail page (visible only to staff) creates a bare draft Episode with a UUID slug and redirects to the episode workflow view at step 1, where title and details get filled in later.

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

- **Staff-only button**: A "New Episode" button on `podcast_detail.html`, rendered as a POST form (CSRF-protected), conditionally shown when `request.user.is_staff`
- **Create Episode view**: A new `EpisodeCreateView` in `podcast_views.py` — staff-only, POST-only. Creates a bare draft Episode with a UUID slug and auto-assigned episode number, then redirects to workflow step 1.
- **No form template needed**: The button itself is the entire UI — one click creates and redirects.

### Flow

**Podcast detail page** → Click "New Episode" (POST) → Draft Episode created with UUID slug → **Redirect to episode workflow view** (step 1)

### Technical Approach

- **View**: `EpisodeCreateView(LoginRequiredMixin, UserPassesTestMixin, View)` — same auth pattern as `EpisodeWorkflowView`. POST-only; GET redirects back to podcast detail.
- **Episode creation**: `Episode.objects.create(podcast=podcast, title="", slug=uuid4().hex[:12], status="draft")`. Episode number auto-assigned by `Episode.save()`. Title and description left empty — filled in later through the workflow or admin.
- **Slug**: Short UUID hex (`uuid4().hex[:12]`) — unique enough, valid as a Django slug, and gets replaced when the title is finalized.
- **Redirect**: After creation, redirect to `podcast:episode_workflow` at step 1.
- **Button style**: Match the existing RSS Feed button (inline-flex, font-mono, outlined border style).
- **URL**: `<slug:slug>/new/` placed before the `<slug:episode_slug>/` catch-all in `urls.py`.

## Rabbit Holes

- **Building a full form page** — No form needed; just create a bare episode and let the workflow handle details.
- **Auto-starting the pipeline on create** — Out of scope; the workflow view already has a "Start Pipeline" button.
- **Title/slug refinement UX** — Future concern; for now the UUID slug is functional.

## Risks

### Risk 1: URL slug collision with "new"
**Impact:** If an episode has slug "new", the `<slug>/new/` route would shadow it.
**Mitigation:** Place the `new/` route before `<slug:episode_slug>/` in `urls.py`. Django matches top-to-bottom.

## No-Gos (Out of Scope)

- Auto-starting the production pipeline after creation
- Editing existing episodes from the frontend
- Title/slug editing UI
- Bulk episode creation

## Update System

No update system changes required — this is a web UI feature only.

## Agent Integration

No agent integration required — this is a staff-facing web button.

## Documentation

### Inline Documentation
- [ ] Docstring on `EpisodeCreateView`

No external documentation changes needed — this is a minor staff-facing UI addition.

## Success Criteria

- [ ] "New Episode" button visible on `/podcast/{slug}/` for staff users only
- [ ] Button not visible to anonymous or non-staff users
- [ ] Clicking button creates a draft Episode with UUID slug and auto-assigned episode number
- [ ] Redirects to workflow step 1 after creation
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (create-episode)**
  - Name: episode-builder
  - Role: Implement view, URL, button, and tests
  - Agent Type: builder
  - Resume: true

- **Validator (create-episode)**
  - Name: episode-validator
  - Role: Verify implementation meets criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Implement EpisodeCreateView and wire up URL + button
- **Task ID**: build-all
- **Depends On**: none
- **Assigned To**: episode-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `EpisodeCreateView` to `apps/podcast/views/podcast_views.py` — `LoginRequiredMixin` + `UserPassesTestMixin` (is_staff), POST creates Episode with UUID slug, redirects to workflow step 1, GET redirects to podcast detail
- Add URL `path("<slug:slug>/new/", ...)` to `apps/podcast/urls.py` BEFORE the `<slug:episode_slug>/` route
- Update `apps/podcast/views/__init__.py` exports
- Add staff-only POST form button to `apps/public/templates/podcast/podcast_detail.html` matching RSS button style

### 2. Write tests
- **Task ID**: build-tests
- **Depends On**: build-all
- **Assigned To**: episode-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `EpisodeCreateViewTestCase` to `apps/podcast/tests/test_views.py`
- Test: anonymous user POST gets redirected (302 to login)
- Test: non-staff user POST gets 403
- Test: staff POST creates episode and redirects to workflow
- Test: button visible to staff on detail page
- Test: button hidden from non-staff on detail page

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: episode-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py -v`
- Verify all success criteria met

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py -v` - all view tests pass
- `uv run pre-commit run --all-files` - code quality checks pass
