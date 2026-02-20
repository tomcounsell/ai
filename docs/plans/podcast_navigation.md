---
status: Planning
type: feature
appetite: Medium
owner: Tom Counsell
created: 2026-02-18
tracking: https://github.com/tomcounsell/Cuttlefish/issues/1
---

# Podcast Navigation and Public/Private Browsing

## Problem

The podcast section exists at `/podcast/` but is completely invisible — no link in the header nav, footer, or user menu. There is no way to discover podcasts on the site without knowing the URL directly.

Additionally, the `Podcast` model has no owner relationship, so there's no concept of "my podcasts." Podcasts have an `is_public` flag but private podcasts are inaccessible to their owners through the frontend. And there are no fields for external platform links (Spotify, Apple Podcasts, etc.), which listeners need to subscribe.

**Current behavior:**
- `/podcast/` exists but is unreachable from any navigation element
- No owner FK on Podcast — no way to show "my podcasts" to a logged-in user
- No platform link fields (Spotify, Apple, etc.) on Podcast model
- Empty state shows a generic icon with no call-to-action
- All podcast views hard-filter to `is_public=True`, making private podcasts invisible even to their owners

**Desired outcome:**
- Podcast link visible in header nav, footer, and user menu
- Anyone can browse and view public podcasts
- Logged-in users can see their own private podcasts
- Each podcast displays external platform links (Spotify, Apple Podcasts, RSS)
- Empty states guide users toward next actions

## Appetite

**Size:** Medium

**Team:** Solo dev + PM. One check-in to align on the owner model approach and platform links design, one review round.

**Interactions:**
- PM check-ins: 1-2 (scope alignment on private podcast ownership model)
- Review rounds: 1

## Prerequisites

No prerequisites — this work uses existing Django infrastructure and templates.

## Solution

### Key Elements

- **Navigation links**: Add "Podcast" to header, footer, and user dropdown across all pages
- **Owner relationship**: Add `owner` FK on Podcast model so users can see their private podcasts
- **Platform links**: Add Spotify and Apple Podcasts URL fields to Podcast model (RSS is route-generated via `feed.xml`, not a stored field)
- **View logic**: Update PodcastListView and PodcastDetailView to show private podcasts to their owner
- **Feed access for owners**: Update PodcastFeedView to allow authenticated podcast owners to access private feeds without `?token=`
- **Template updates**: Platform links section on podcast detail and episode detail, improved empty states

### Flow

**Public visitor** → Clicks "Podcast" in nav → `/podcast/` shows public podcasts → Clicks podcast → Detail page with episodes + platform links (Spotify, Apple) + RSS feed link → Clicks episode → Episode detail with audio player + show notes

**Logged-in owner** → Clicks "Podcast" in nav → `/podcast/` shows public podcasts + their private podcasts (labeled) → Same detail flow, but can also access their own private podcasts → RSS feed link works directly (authenticated via session, no `?token=` needed)

### Technical Approach

- Add `owner` ForeignKey (nullable, to AUTH_USER_MODEL) on `Podcast` model
- Add `spotify_url`, `apple_podcasts_url` URLFields on `Podcast` model (Google Podcasts was shut down — excluded)
- Update `PodcastListView` to union public podcasts with the logged-in user's private podcasts
- Update `PodcastDetailView` / `EpisodeDetailView` to allow access if `is_public=True` OR `owner == request.user`
- Update `PodcastFeedView._serve_private_feed` to accept authenticated owner (`request.user == podcast.owner`) as an alternative to `?token=` — so the RSS link on podcast detail pages works for owners without requiring them to know the token
- Add "Podcast" link to `layout/nav/navbar.html` (desktop + mobile), `layout/footer.html`, and `layout/nav/account_menu.html`
- Add platform links section (Spotify, Apple, RSS feed link) to `podcast_detail.html` and `episode_detail.html`
- Improve empty state in `podcast_list.html` with context-appropriate CTAs

## Rabbit Holes

- **Team-scoped podcast ownership** — Don't build team-based permissions for podcasts. Owner is a single user. Team support can come later if needed.
- **Podcast creation UI** — Don't build a frontend form for creating podcasts. Admin-only for now.
- **Platform link auto-detection** — Don't try to auto-discover Spotify/Apple URLs from RSS. These are manually entered.
- **Google Podcasts** — Google Podcasts was shut down in 2024. Don't add a `google_podcasts_url` field or UI for it.
- **Subscriber/follower system** — Don't add the ability to "follow" or "subscribe" to podcasts within the app.

## Risks

### Risk 1: Migration on production with existing data
**Impact:** Adding `owner` FK with null=True is safe. Adding URL fields with blank=True is safe. No data loss risk.
**Mitigation:** All new fields are nullable/blankable. Migration is additive only.

### Risk 2: Private podcast URL guessing
**Impact:** Someone could guess a private podcast's slug and access it via direct URL.
**Mitigation:** Views will check `is_public=True OR owner == request.user`. Unauthenticated users or non-owners get 404 for private podcasts.

## No-Gos (Out of Scope)

- Podcast creation/editing UI (admin-only)
- Team-based podcast permissions
- Platform link auto-detection
- Subscriber/follower system
- Podcast search/filtering
- Docs page improvements
- Teams navigation

## Agent Integration

No agent integration required — this is a frontend/model change with no MCP or bridge involvement.

## Update System

No update system changes required — this is a web application feature, not a bridge/tool change.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/podcast-services.md` with new model fields and view behavior

### Inline Documentation
- [ ] Docstrings on updated view methods explaining public/private logic

## Success Criteria

- [ ] "Podcast" link appears in header nav (desktop + mobile), footer, and user menu
- [ ] `/podcast/` shows all public podcasts to unauthenticated visitors
- [ ] `/podcast/` shows public podcasts + user's private podcasts to authenticated users
- [ ] Private podcasts display a visual indicator (e.g., lock icon or "Private" label)
- [ ] Private podcast detail pages return 404 for non-owners
- [ ] Podcast detail page shows Spotify and Apple Podcasts links when their URL fields are populated
- [ ] Podcast detail page always shows an RSS feed link (route-generated via `{% url 'podcast:feed' slug=podcast.slug %}`)
- [ ] Authenticated podcast owners can access their private podcast's RSS feed without `?token=` (session-based auth in `PodcastFeedView`)
- [ ] Episode detail page shows podcast platform links
- [ ] Empty state on `/podcast/` shows helpful context (not just an icon)
- [ ] Admin can set owner, spotify_url, apple_podcasts_url on Podcast
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (models + views)**
  - Name: podcast-builder
  - Role: Model changes, view logic, migrations
  - Agent Type: builder
  - Resume: true

- **Builder (templates + nav)**
  - Name: nav-builder
  - Role: Navigation templates, podcast templates, platform links UI
  - Agent Type: designer
  - Resume: true

- **Validator**
  - Name: podcast-validator
  - Role: Verify all navigation links work, public/private access control, platform links display
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Add model fields and migration
- **Task ID**: build-models
- **Depends On**: none
- **Assigned To**: podcast-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `owner` FK (nullable, to AUTH_USER_MODEL) on `Podcast` model
- Add `spotify_url`, `apple_podcasts_url` URLFields (blank=True) on `Podcast` model
- Generate and apply migration
- Update `PodcastAdmin` to include new fields in list_display and fieldsets

### 2. Update view logic for public/private access
- **Task ID**: build-views
- **Depends On**: build-models
- **Assigned To**: podcast-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `PodcastListView` to show `is_public=True` union with `owner=request.user` (if authenticated)
- Update `PodcastDetailView` to allow access if `is_public=True` OR `owner == request.user`
- Update `EpisodeDetailView`, `EpisodeReportView`, `EpisodeSourcesView` with same logic
- Pass `is_owner` flag to template context for private podcast indicators

### 3. Update private feed access for owners
- **Task ID**: build-feed-access
- **Depends On**: build-models
- **Assigned To**: podcast-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `PodcastFeedView._serve_private_feed` in `apps/podcast/views/feed_views.py` to allow access when `request.user.is_authenticated and request.user == podcast.owner` (in addition to the existing `?token=` path)
- Ensure unauthenticated non-token requests still get 403
- The RSS link on `podcast_detail.html` already points to the feed route without `?token=` — this change makes that link work for owners

### 4. Add navigation links
- **Task ID**: build-nav
- **Depends On**: none
- **Assigned To**: nav-builder
- **Agent Type**: designer
- **Parallel**: true
- Add "Podcast" link to desktop nav in `layout/nav/navbar.html` (between CTO Tools and Team)
- Add "Podcast" link to mobile menu in `layout/nav/navbar.html`
- Add "Podcast" link to footer in `layout/footer.html` (MCP Servers section)
- Add "My Podcasts" to user dropdown in `layout/nav/account_menu.html` (authenticated users)
- **Note**: `components/layout/navbar.html` and `components/layout/footer.html` are boilerplate — do NOT edit those. Only the `layout/` templates are included by `base.html`.

### 5. Update podcast templates with platform links and empty states
- **Task ID**: build-templates
- **Depends On**: build-models
- **Assigned To**: nav-builder
- **Agent Type**: designer
- **Parallel**: false
- Add platform links section to `podcast_detail.html` (Spotify, Apple icons/buttons when URLs populated; RSS feed link always shown via route)
- Add platform links to `episode_detail.html` (above or below audio player)
- Add private podcast indicator to `podcast_list.html` (lock icon + "Private" label)
- Improve empty state in `podcast_list.html` with descriptive text
- Show different empty state for authenticated users ("No podcasts yet" vs "Create one in admin")

### 6. Write/update tests
- **Task ID**: build-tests
- **Depends On**: build-views, build-feed-access
- **Assigned To**: podcast-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test public podcast visibility for anonymous users
- Test private podcast visibility for owner vs non-owner vs anonymous
- Test 404 for private podcast accessed by non-owner
- Test platform links display when URLs are populated vs empty
- Test navigation links appear in rendered templates
- Test owner can access private feed without `?token=` (authenticated session)
- Test non-owner still gets 403 on private feed without token

### 7. Validate everything
- **Task ID**: validate-all
- **Depends On**: build-nav, build-templates, build-tests
- **Assigned To**: podcast-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all navigation links render correctly
- Verify public/private access control works
- Verify platform links display correctly
- Verify private feed access works for owners
- Run full test suite
- Check all success criteria

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` - Run podcast tests
- `uv run python manage.py makemigrations --check` - Verify no missing migrations
- `uv run python manage.py check` - Django system checks pass

---

## Open Questions

1. **Owner model**: Should `owner` be a FK to User directly, or should we use the `Authorable` mixin (which provides `author` FK + `is_author_anonymous` + `authored_at`)? Direct FK is simpler and more explicit for this use case.

2. **Platform links on Episode vs Podcast**: Spotify/Apple links are typically per-podcast (not per-episode). Should we only put them on the Podcast model, or also allow episode-specific override URLs? Recommend: Podcast-level only.

3. **Private podcast empty state**: When an authenticated user has no private podcasts and there are no public podcasts, should we show a link to the admin to create one, or just a generic "No podcasts yet" message?
