# Podcast Owner Access for Episode Creation

**Status**: Implementation
**Issue**: #133
**Stage**: User Journey Stage 2 (Important gap)

## Problem

Episode creation is restricted to `is_staff` users only. Podcast owners who aren't Django staff can't create episodes on their own podcasts, blocking independent use of the platform.

## Solution

Allow podcast owners to create and manage episodes on podcasts they own.

Permission check: `user.is_staff OR user is podcast.owner`

## Changes Required

### 1. EpisodeCreateView (apps/podcast/views/podcast_views.py)
- Update `test_func()` to check `is_staff OR is_podcast_owner`
- Retrieve podcast in `test_func()` using `slug` from URL kwargs
- User must be authenticated + (staff OR owner of the podcast)

### 2. EpisodeWorkflowView (apps/podcast/workflow.py)
- Update `test_func()` to check `is_staff OR is_podcast_owner`
- Same logic: retrieve podcast and verify ownership

### 3. Template (apps/public/templates/podcast/podcast_detail.html)
- Line 28: Change `{% if request.user.is_staff %}` to `{% if is_owner %}`
- The "+ New Episode" button already shows for owners via the `Edit` button pattern (line 22)
- Reuse existing `is_owner` context variable set by PodcastDetailView

## Testing Strategy

- Unit tests: Non-staff owner can create episodes
- Unit tests: Non-staff non-owner cannot create episodes
- Unit tests: Staff can create episodes on any podcast
- Integration test: Owner sees "+ New Episode" button in UI
- Integration test: Non-owner doesn't see the button

## No-Gos

- Do NOT change podcast ownership model
- Do NOT allow non-owners to create episodes
- Do NOT create Django migrations (per project rules)
- Do NOT add permission groups or complex permission logic

## Risks

- None. This is a simple permission check expansion following existing patterns.

## Success Criteria

- [ ] Podcast owners can click "+ New Episode" on their podcasts
- [ ] EpisodeCreateView allows owners through
- [ ] EpisodeWorkflowView allows owners through
- [ ] Tests verify owner/non-owner/staff access matrix
- [ ] No security regressions (non-owners still blocked)
