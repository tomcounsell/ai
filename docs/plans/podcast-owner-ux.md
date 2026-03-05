---
status: In Progress
type: feature
appetite: Small
owner: Tom
created: 2026-03-05
tracking: https://github.com/yudame/cuttlefish/issues/117
branch: feature/podcast-owner-ux
---

# Podcast Owner/Staff Draft Episode UX

## Problem

Podcast owners and staff have no way to access unpublished (draft) episodes from the website. The podcast detail page only shows published episodes, and navigating directly to a draft episode URL returns a 404.

**Current behavior:**
- Podcast detail page only lists published episodes
- Draft episodes are invisible in the web UI
- Navigating directly to `/podcast/{slug}/{draft-slug}/` returns 404
- No edit shortcuts from the public-facing episode pages

**Desired outcome:**
Podcast owners and staff see a "Drafts" section on the podcast detail page with edit links to the workflow view, and can access draft episode detail pages directly. Edit buttons appear on episode pages for authorized users.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies.

## Solution

### Key Elements

- **Drafts section on podcast detail**: Owner/staff see draft episodes above the published list with dashed-border cards and "Edit" buttons linking to the workflow view
- **Episode detail access for drafts**: Owner/staff can view draft episodes directly (no longer 404)
- **Edit button on episode detail**: Owner/staff see an "Edit" button next to the episode title linking to the workflow view

### Flow

**Podcast detail page** (owner/staff) -> See "Drafts (N)" section -> Click "Edit" -> **Episode workflow step 1**

**Episode detail page** (owner/staff) -> See "Edit" button next to title -> Click "Edit" -> **Episode workflow step 1**

**Episode detail page** (anonymous/public) -> Draft slug -> **404** (unchanged)

### Technical Approach

- `PodcastDetailView`: Query drafts separately (`published_at__isnull=True`), pass as `drafts` context variable; only populated for owner/staff
- `EpisodeDetailView`: Branch on `is_owner or is_staff` to allow fetching any episode vs. only published ones
- Templates use `{% if drafts %}` and `{% if user.is_staff or user == podcast.owner %}` guards
- `is_owner` and `is_staff` computed once in each view, reused for context and query logic

### Files Modified

| File | Change |
|------|--------|
| `apps/podcast/views/podcast_views.py` | `PodcastDetailView` adds `drafts` queryset; `EpisodeDetailView` allows owner/staff access to unpublished episodes |
| `apps/public/templates/podcast/podcast_detail.html` | New "Drafts" section with dashed-border cards and edit links |
| `apps/public/templates/podcast/episode_detail.html` | Edit button next to episode title for owner/staff |
| `static/css/tailwind.css` | Supporting styles |

## Rabbit Holes

- **Inline episode editing**: Editing fields directly on the episode detail page is out of scope; we link to the existing workflow view
- **Draft ordering/sorting UI**: Drafts are ordered by `created_at` descending; no custom sort needed
- **Staff-level granularity**: We use Django's `is_staff` flag; no per-podcast staff roles

## Risks

### Risk 1: Information leakage through draft titles
**Impact:** Draft episode titles could be visible to non-authorized users if template guards are bypassed
**Mitigation:** Drafts queryset is only populated when `is_owner or is_staff`; the queryset is `Episode.objects.none()` otherwise. Template guards are defense-in-depth only.

## No-Gos (Out of Scope)

- Inline editing of episode fields from the detail page
- Per-podcast staff/collaborator roles
- Draft preview with a shareable link
- Extending owner/staff access to report and sources views for unpublished episodes (separate issue)

## Update System

No update system changes required -- this is a web UI feature in the Django app.

## Agent Integration

No agent integration required -- this is a web UI feature.

## Documentation

### Inline Documentation
- [x] View docstrings updated for `PodcastDetailView` and `EpisodeDetailView`
- [ ] Code comments on permission branching logic

## Success Criteria

- [ ] Owner sees "Drafts (N)" section on podcast detail page with edit links
- [ ] Staff sees "Drafts (N)" section on podcast detail page with edit links
- [ ] Anonymous users do NOT see drafts section
- [ ] Owner can access draft episode detail page (200, not 404)
- [ ] Staff can access draft episode detail page (200, not 404)
- [ ] Anonymous users get 404 for draft episode detail page
- [ ] Edit button appears on episode detail for owner/staff
- [ ] Edit button does NOT appear for anonymous/regular users
- [ ] Existing published episode access is unchanged
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (views-templates)**
  - Name: views-builder
  - Role: Implement view logic and template changes
  - Agent Type: builder
  - Resume: true

- **Validator (access-control)**
  - Name: access-validator
  - Role: Verify permission checks work correctly
  - Agent Type: test-engineer
  - Resume: true

## Step by Step Tasks

### 1. Implement view and template changes
- **Task ID**: build-views
- **Depends On**: none
- **Assigned To**: views-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `drafts` queryset to `PodcastDetailView` (owner/staff only)
- Allow owner/staff to access unpublished episodes in `EpisodeDetailView`
- Add drafts section to `podcast_detail.html`
- Add edit button to `episode_detail.html`

### 2. Write tests for new access patterns
- **Task ID**: test-access
- **Depends On**: build-views
- **Assigned To**: access-validator
- **Agent Type**: test-engineer
- **Parallel**: false
- Test: owner sees drafts on podcast detail page
- Test: staff sees drafts on podcast detail page
- Test: anonymous does NOT see drafts
- Test: owner can access draft episode detail (200)
- Test: staff can access draft episode detail (200)
- Test: anonymous gets 404 for draft episode detail
- Test: edit button visibility for owner/staff vs anonymous

### 3. Final Validation
- **Task ID**: validate-all
- **Depends On**: test-access
- **Assigned To**: access-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify all success criteria met

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_views.py -v` - Verify all view tests pass
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` - Verify all podcast tests pass
