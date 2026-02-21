# Podcast Privacy Settings

**Issue:** #96 — Podcast privacy setting
**Status:** Draft

## Problem

The current `Podcast.is_public` boolean only supports two states (public/private). Issue #96 requires three privacy levels: **Public**, **Unlisted**, **Restricted**. The current private feed auth uses a single shared `SUPABASE_USER_ACCESS_TOKEN` env var — not per-user tokens.

## Current State

| Component | Current Behavior |
|-----------|-----------------|
| `Podcast.is_public` | Boolean. `True` = public, `False` = private |
| `Podcast.save()` | Prevents changing `is_public` after creation (bucket mismatch guard) |
| Feed view | Public → cached, permanent URLs. Private → validates `?token=` against single `SUPABASE_USER_ACCESS_TOKEN` |
| Web views | `_get_accessible_podcast()` checks `is_public` or `owner == request.user` |
| List view | Shows public podcasts + user's own private ones |
| Audio storage | Public → public Supabase bucket. Private → private bucket with signed URLs |
| Admin | `is_public` in list display and filters |

## Target State (from issue #96)

Three privacy levels:

| Level | Behavior | Default? |
|-------|----------|----------|
| **Public** | Anyone can access. Listed on podcast index. Requires platform registration (human task). | No |
| **Unlisted** | Accessible via direct link. Not listed publicly. No auth required for feed. | **Yes** (new default) |
| **Restricted** | Per-user unique feed URLs with access tokens. Tracked usage. | No |

v2 (future): Restricted feeds get usage tracking and rate limiting.

## Implementation Plan

### Phase 1: Model — Replace `is_public` with `privacy` field

**File:** `apps/podcast/models/podcast.py`

```python
class Podcast(Timestampable):
    class Privacy(models.TextChoices):
        PUBLIC = "public", "Public"
        UNLISTED = "unlisted", "Unlisted"
        RESTRICTED = "restricted", "Restricted"

    # ... existing fields ...
    privacy = models.CharField(
        max_length=20,
        choices=Privacy.choices,
        default=Privacy.UNLISTED,
    )
```

- Remove `is_public` field
- Add `privacy` field with TextChoices
- Add convenience properties: `is_public`, `is_unlisted`, `is_restricted` (read-only, for backward compat)
- Update `save()` immutability guard: prevent changing privacy after creation (same bucket-routing concern)

**Migration strategy:**
- Data migration: `is_public=True` → `privacy="public"`, `is_public=False` → `privacy="restricted"` (existing private podcasts have token-based auth, so they're restricted)
- Remove `is_public` column after data migration

### Phase 2: New model — `PodcastAccessToken`

**File:** `apps/podcast/models/access_token.py` (new)

```python
class PodcastAccessToken(Timestampable):
    """Per-user access token for restricted podcast feeds."""
    podcast = models.ForeignKey(Podcast, on_delete=models.CASCADE, related_name="access_tokens")
    label = models.CharField(max_length=200, help_text="Who/what this token is for")
    token = models.CharField(max_length=64, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    access_count = models.PositiveIntegerField(default=0)
```

- Token generated via `secrets.token_urlsafe(32)` on creation
- `label` is a human-readable identifier (e.g. "Tom's iPhone", "Client A")
- No user FK required — tokens are standalone, can be shared externally
- v2: Add rate limiting fields (`max_requests_per_hour`, etc.)

Register in `apps/podcast/models/__init__.py`.

### Phase 3: Feed view — Three-tier access logic

**File:** `apps/podcast/views/feed_views.py`

Update `PodcastFeedView.get()`:

```python
def get(self, request, slug):
    podcast = get_object_or_404(Podcast, slug=slug)

    if podcast.privacy == Podcast.Privacy.PUBLIC:
        return self._serve_public_feed(request, podcast)

    if podcast.privacy == Podcast.Privacy.UNLISTED:
        return self._serve_unlisted_feed(request, podcast)

    # Restricted
    return self._serve_restricted_feed(request, podcast)
```

| Privacy | Feed behavior |
|---------|--------------|
| Public | Cached (5 min). Permanent audio URLs from public bucket. |
| Unlisted | Cached (5 min). Permanent audio URLs from public bucket. Same as public feed technically, but not listed anywhere. |
| Restricted | No cache. Validates `?token=` against `PodcastAccessToken`. Signed audio URLs. Increments `access_count`, updates `last_accessed_at`. |

**Key decision:** Unlisted podcasts store audio in the **public** bucket (permanent URLs, no signing overhead). Only restricted podcasts use the private bucket. This means changing the default for new podcasts from `is_public=False` to `privacy="unlisted"` also changes the storage bucket — which is fine for *new* podcasts, but existing private ones stay restricted.

### Phase 4: Web views — Update access logic

**File:** `apps/podcast/views/podcast_views.py`

Update `_get_accessible_podcast()`:
```python
def _get_accessible_podcast(request, slug):
    podcast = get_object_or_404(Podcast, slug=slug)
    if podcast.privacy == Podcast.Privacy.PUBLIC:
        return podcast
    if podcast.privacy == Podcast.Privacy.UNLISTED:
        return podcast  # accessible via direct link
    # Restricted: owner or staff only on web UI
    if request.user.is_authenticated and (
        podcast.owner == request.user or request.user.is_staff
    ):
        return podcast
    raise Http404
```

Update `PodcastListView`:
```python
# Show public podcasts to everyone
# Show unlisted + restricted only to owner/staff
podcasts = Podcast.objects.filter(privacy=Podcast.Privacy.PUBLIC)
if request.user.is_authenticated:
    user_owned = Podcast.objects.filter(owner=request.user).exclude(privacy=Podcast.Privacy.PUBLIC)
    podcasts = (podcasts | user_owned).distinct()
```

### Phase 5: Admin updates

**File:** `apps/podcast/admin.py`

- Replace `is_public` with `privacy` in `list_display` and `list_filter`
- Add `PodcastAccessTokenAdmin` with inline on PodcastAdmin
- Show token management: create, revoke, view usage stats

**File:** `apps/podcast/admin.py` (additions)

```python
class PodcastAccessTokenInline(TabularInline):
    model = PodcastAccessToken
    fields = ["label", "token", "is_active", "last_accessed_at", "access_count"]
    readonly_fields = ["token", "last_accessed_at", "access_count"]
    extra = 0

@admin.register(PodcastAccessToken)
class PodcastAccessTokenAdmin(ModelAdmin):
    list_display = ["podcast", "label", "is_active", "access_count", "last_accessed_at"]
    list_filter = ["is_active", "podcast"]
```

### Phase 6: Audio storage routing

**File:** `apps/podcast/services/audio.py`

Update storage bucket selection:
```python
# Public and Unlisted → public bucket (permanent URLs)
# Restricted → private bucket (signed URLs)
is_private = episode.podcast.privacy == Podcast.Privacy.RESTRICTED
audio_url = store_file(storage_key, audio_bytes, "audio/mpeg", public=not is_private)
```

Update all other places that reference `is_public` for bucket routing:
- `apps/podcast/services/audio.py`
- `apps/podcast/services/publishing.py`
- `apps/podcast/management/commands/local_audio_worker.py`
- `apps/podcast/tools/setup_episode.py`
- `apps/podcast/tools/episode_config.py`
- `apps/podcast/tools/notebooklm_prompt.py`
- `apps/podcast/models/podcast_config.py` (`to_dict()`)
- `apps/podcast/management/commands/backfill_episodes.py`

### Phase 7: Update references across codebase

All files currently referencing `is_public`:

| File | Change |
|------|--------|
| `models/podcast.py` | Replace field, add properties |
| `views/feed_views.py` | Three-tier logic |
| `views/podcast_views.py` | Updated access checks |
| `admin.py` | Replace in display/filter |
| `services/audio.py` | Bucket routing |
| `services/publishing.py` | Bucket routing |
| `management/commands/local_audio_worker.py` | Bucket routing |
| `management/commands/backfill_episodes.py` | Map old values to new |
| `tools/setup_episode.py` | Config dict |
| `tools/episode_config.py` | Config dict |
| `tools/notebooklm_prompt.py` | Sponsor break logic |
| `models/podcast_config.py` | `to_dict()` |
| `tests/*` | Update all test fixtures |

### Phase 8: Tests

- Model tests: privacy field choices, default, immutability guard
- `PodcastAccessToken` tests: creation, token generation, uniqueness, deactivation
- Feed tests: public/unlisted/restricted access patterns, token validation, access counting
- View tests: list filtering by privacy, access control per level
- Audio storage tests: bucket routing per privacy level

## Files to Create

| File | Purpose |
|------|---------|
| `apps/podcast/models/access_token.py` | PodcastAccessToken model |
| `apps/podcast/migrations/0009_*.py` | Add privacy field, migrate data, remove is_public, add PodcastAccessToken |

## Files to Modify

| File | Change |
|------|--------|
| `apps/podcast/models/podcast.py` | Replace `is_public` with `privacy` + properties |
| `apps/podcast/models/__init__.py` | Export PodcastAccessToken |
| `apps/podcast/views/feed_views.py` | Three-tier feed logic, token lookup |
| `apps/podcast/views/podcast_views.py` | Updated access checks |
| `apps/podcast/admin.py` | Privacy field + token admin |
| `apps/podcast/services/audio.py` | Bucket routing |
| `apps/podcast/services/publishing.py` | Bucket routing |
| `apps/podcast/management/commands/local_audio_worker.py` | Bucket routing |
| `apps/podcast/management/commands/backfill_episodes.py` | Privacy mapping |
| `apps/podcast/tools/setup_episode.py` | Config |
| `apps/podcast/tools/episode_config.py` | Config |
| `apps/podcast/tools/notebooklm_prompt.py` | Sponsor logic |
| `apps/podcast/models/podcast_config.py` | to_dict() |
| All test files | Update fixtures and assertions |

## Out of Scope (v2)

- Per-token rate limiting
- Usage analytics dashboard
- Access log model (FeedAccessLog)
- IP allowlisting
- Token expiration dates
- Email invitations for restricted feed access

## Migration Data Mapping

| Current | New |
|---------|-----|
| `is_public=True` | `privacy="public"` |
| `is_public=False` | `privacy="restricted"` |
| New podcasts | `privacy="unlisted"` (new default) |

Existing private podcasts (SATSOL, Soul World Bank) become `restricted` — preserving their current token-based auth and private bucket storage. The shared `SUPABASE_USER_ACCESS_TOKEN` continues to work as a fallback until per-podcast tokens are created.
