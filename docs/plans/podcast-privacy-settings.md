# Podcast Privacy Settings

**Issue:** #96 — Podcast privacy setting
**Status:** Draft (updated 2026-02-23)

## Problem

The current `Podcast.is_public` boolean only supports two states (public/private). Issue #96 requires three privacy levels: **Public**, **Unlisted**, **Restricted**. The current private feed auth uses a single shared `SUPABASE_USER_ACCESS_TOKEN` env var — not per-user tokens.

## Current State

| Component | Current Behavior |
|-----------|-----------------|
| `Podcast` model | Extends `Timestampable, Publishable`. Has `is_public` boolean, `owner` FK, `published_at`/`unpublished_at` from Publishable |
| `Podcast.is_public` | Boolean. `True` = public, `False` = private |
| `Podcast.is_published` | Property from `Publishable` mixin — checks `published_at` is set and not unpublished |
| `Podcast.save()` | Prevents changing `is_public` after creation (bucket mismatch guard) |
| Feed view | Public → cached, permanent URLs. Private → validates `?token=` against single `SUPABASE_USER_ACCESS_TOKEN` |
| Web views | `_get_accessible_podcast()` checks `is_public` AND `is_published`, or `owner == request.user` |
| `_podcast_published_filter()` | Returns Q filter for published podcasts (published_at in the past, not unpublished) |
| List view | Shows published public podcasts + user's own private ones, with `episode_count`/`latest_episode_at` annotations |
| Audio storage | Public → public Supabase bucket. Private → private bucket with signed URLs |
| Admin | `is_public` in `list_display` and `list_filter` |
| Latest migration | `0010_rename_topic_series_to_tags.py` |

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
class Podcast(Timestampable, Publishable):
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

    @property
    def is_public(self) -> bool:
        """Backward-compat property: True if privacy is PUBLIC."""
        return self.privacy == self.Privacy.PUBLIC

    @property
    def is_unlisted(self) -> bool:
        return self.privacy == self.Privacy.UNLISTED

    @property
    def is_restricted(self) -> bool:
        return self.privacy == self.Privacy.RESTRICTED

    @property
    def uses_private_bucket(self) -> bool:
        """Only restricted podcasts use the private Supabase bucket."""
        return self.is_restricted
```

- Remove `is_public` BooleanField
- Add `privacy` field with TextChoices
- Add read-only convenience properties (backward compat for `is_public`)
- Update `save()` immutability guard: prevent changing `privacy` after creation (same bucket-routing concern)
- Note: `Publishable` mixin provides `published_at`, `is_published`, `publish()`, `unpublish()` — these are **orthogonal** to privacy (a podcast can be published+restricted or unpublished+public)

**Migration strategy (migration `0011`):**
1. Add `privacy` CharField with default `"unlisted"` (non-nullable from the start)
2. Data migration: `is_public=True` → `privacy="public"`, `is_public=False` → `privacy="restricted"`
3. Remove `is_public` column
4. Note: Tom manages migrations — prepare the model change and migration plan, don't run `makemigrations`

### Phase 2: New model — `PodcastAccessToken`

**File:** `apps/podcast/models/access_token.py` (new)

```python
import secrets

from django.db import models

from apps.common.behaviors import Timestampable


class PodcastAccessToken(Timestampable):
    """Per-user access token for restricted podcast feeds."""

    podcast = models.ForeignKey(
        "podcast.Podcast",
        on_delete=models.CASCADE,
        related_name="access_tokens",
    )
    label = models.CharField(
        max_length=200,
        help_text="Who/what this token is for (e.g. 'Tom iPhone', 'Client A')",
    )
    token = models.CharField(max_length=64, unique=True, db_index=True)
    is_active = models.BooleanField(default=True)
    last_accessed_at = models.DateTimeField(null=True, blank=True)
    access_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.podcast} — {self.label}"

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(32)
        super().save(*args, **kwargs)

    def record_access(self):
        """Increment access count and update timestamp. Use F() to avoid race conditions."""
        from django.db.models import F
        from django.utils import timezone

        PodcastAccessToken.objects.filter(pk=self.pk).update(
            access_count=F("access_count") + 1,
            last_accessed_at=timezone.now(),
        )
```

- Token auto-generated via `secrets.token_urlsafe(32)` on first save
- `label` is a human-readable identifier — no user FK needed (tokens shared externally)
- `record_access()` uses F() expression to avoid race conditions on concurrent feed requests
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

Add `_serve_unlisted_feed()`:
```python
def _serve_unlisted_feed(self, request, podcast) -> HttpResponse:
    """Unlisted: same as public (cached, permanent URLs) but not indexed."""
    # Reuse public feed logic — the only difference is list visibility
    cache_key = f"podcast_feed_{podcast.slug}"
    cached_xml = cache.get(cache_key)
    if cached_xml is None:
        episodes = list(self._published_episodes(podcast))
        context = self._build_feed_context(request, podcast, episodes)
        cached_xml = render_to_string("podcast/feed.xml", context)
        cache.set(cache_key, cached_xml, 300)
    response = HttpResponse(cached_xml, content_type="application/rss+xml; charset=utf-8")
    response["Cache-Control"] = "public, max-age=300"
    return response
```

Rename `_serve_private_feed()` to `_serve_restricted_feed()`:
```python
def _serve_restricted_feed(self, request, podcast) -> HttpResponse:
    """Restricted: validate per-podcast token, generate signed URLs."""
    is_owner = (
        request.user.is_authenticated
        and podcast.owner
        and request.user == podcast.owner
    )
    if not is_owner:
        token_str = request.GET.get("token", "")
        if not token_str:
            return HttpResponseForbidden("Missing access token.")

        # Check per-podcast tokens first
        access_token = PodcastAccessToken.objects.filter(
            podcast=podcast, token=token_str, is_active=True
        ).first()

        if access_token:
            access_token.record_access()
        else:
            # Fallback: shared env token (backward compat, remove in v2)
            expected = getattr(settings, "SUPABASE_USER_ACCESS_TOKEN", "")
            if not expected or token_str != expected:
                return HttpResponseForbidden("Invalid access token.")

    episodes = list(self._published_episodes(podcast))
    for episode in episodes:
        if episode.audio_url:
            episode.audio_url = get_file_url(episode.audio_url, public=False)
        if episode.cover_image_url:
            episode.cover_image_url = get_file_url(episode.cover_image_url, public=False)

    context = self._build_feed_context(request, podcast, episodes)
    xml = render_to_string("podcast/feed.xml", context)
    response = HttpResponse(xml, content_type="application/rss+xml; charset=utf-8")
    response["Cache-Control"] = "no-store"
    return response
```

| Privacy | Feed behavior |
|---------|--------------|
| Public | Cached (5 min). Permanent audio URLs from public bucket. |
| Unlisted | Cached (5 min). Permanent audio URLs from public bucket. Same as public technically, but not listed on index. |
| Restricted | No cache. Validates `?token=` against `PodcastAccessToken` (fallback to shared env token). Signed audio URLs. Records access. |

**Key decision:** Unlisted podcasts store audio in the **public** bucket (permanent URLs, no signing overhead). Only restricted podcasts use the private bucket. This means changing the default for new podcasts from `is_public=False` to `privacy="unlisted"` also changes the storage bucket — which is fine for *new* podcasts, but existing private ones stay restricted.

### Phase 4: Web views — Update access logic

**File:** `apps/podcast/views/podcast_views.py`

Update `_get_accessible_podcast()` — must preserve the existing `is_published` check from `Publishable`:
```python
def _get_accessible_podcast(request, slug):
    """Get podcast if accessible to this user, else 404.

    Access rules:
    - Owner always has access (published or not)
    - Staff always has access
    - Public + published → accessible to everyone
    - Unlisted + published → accessible to everyone (via direct link)
    - Restricted → owner/staff only on web UI (feed uses tokens)
    - Unpublished → owner/staff only
    """
    podcast = get_object_or_404(Podcast, slug=slug)
    is_owner = request.user.is_authenticated and podcast.owner == request.user
    is_staff = request.user.is_authenticated and request.user.is_staff
    if is_owner or is_staff:
        return podcast
    # Must be published for anonymous/regular users
    if not podcast.is_published:
        raise Http404
    if podcast.privacy in (Podcast.Privacy.PUBLIC, Podcast.Privacy.UNLISTED):
        return podcast
    # Restricted: owner/staff only (already checked above)
    raise Http404
```

Update `PodcastListView.get()` — preserve existing annotations:
```python
def get(self, request, *args, **kwargs):
    # Public published podcasts visible to everyone
    podcasts = Podcast.objects.filter(
        _podcast_published_filter(),
        privacy=Podcast.Privacy.PUBLIC,
    )
    if request.user.is_authenticated:
        # Owner sees their own podcasts regardless of privacy/published state
        user_owned = Podcast.objects.filter(owner=request.user).exclude(
            privacy=Podcast.Privacy.PUBLIC
        )
        podcasts = (podcasts | user_owned).distinct()

    # Preserve existing annotations
    published_episode_filter = Q(episodes__published_at__isnull=False) & (
        Q(episodes__unpublished_at__isnull=True)
        | Q(episodes__unpublished_at__lt=F("episodes__published_at"))
    )
    podcasts = podcasts.annotate(
        episode_count=Count("episodes", filter=published_episode_filter),
        latest_episode_at=Max("episodes__published_at", filter=published_episode_filter),
    )
    self.context["podcasts"] = podcasts
    return self.render(request)
```

### Phase 5: Admin updates

**File:** `apps/podcast/admin.py`

- Replace `is_public` with `privacy` in `list_display` and `list_filter`
- Add `PodcastAccessTokenInline` on `PodcastAdmin`
- Register standalone `PodcastAccessTokenAdmin`

```python
from apps.podcast.models import PodcastAccessToken

class PodcastAccessTokenInline(admin.TabularInline):
    model = PodcastAccessToken
    fields = ["label", "token", "is_active", "last_accessed_at", "access_count"]
    readonly_fields = ["token", "last_accessed_at", "access_count"]
    extra = 0

class PodcastAdmin(admin.ModelAdmin):
    list_display = ["title", "slug", "privacy", "is_published", "episode_count"]
    list_filter = ["privacy"]
    inlines = [PodcastAccessTokenInline]
    # ...

@admin.register(PodcastAccessToken)
class PodcastAccessTokenAdmin(admin.ModelAdmin):
    list_display = ["podcast", "label", "is_active", "access_count", "last_accessed_at"]
    list_filter = ["is_active", "podcast"]
    readonly_fields = ["token", "last_accessed_at", "access_count"]
```

### Phase 6: Audio storage routing

**File:** `apps/podcast/services/audio.py`

Update storage bucket selection:
```python
# Public and Unlisted → public bucket (permanent URLs)
# Restricted → private bucket (signed URLs)
is_private = episode.podcast.uses_private_bucket
audio_url = store_file(storage_key, audio_bytes, "audio/mpeg", public=not is_private)
```

Update all other places that reference `is_public` for bucket routing:
- `apps/podcast/services/audio.py` — bucket selection
- `apps/podcast/services/publishing.py` — bucket selection
- `apps/podcast/management/commands/local_audio_worker.py` — bucket selection
- `apps/podcast/tools/setup_episode.py` — config dict
- `apps/podcast/tools/episode_config.py` — config dict (`DEFAULT_CONFIG`, `_load_from_db()`)
- `apps/podcast/tools/notebooklm_prompt.py` — sponsor break logic
- `apps/podcast/models/podcast_config.py` — `to_dict()`
- `apps/podcast/management/commands/backfill_episodes.py` — PODCAST_DEFINITIONS mapping

**Pattern for all bucket routing updates:**
```python
# BEFORE
is_public = episode.podcast.is_public
url = store_file(key, data, mime, public=is_public)

# AFTER
url = store_file(key, data, mime, public=not episode.podcast.uses_private_bucket)
```

**Pattern for config dict updates:**
```python
# BEFORE
"is_public": podcast.is_public,

# AFTER
"privacy": podcast.privacy,
"uses_private_bucket": podcast.uses_private_bucket,
```

### Phase 7: Update all remaining `is_public` references

All files currently referencing `is_public` (19 files found via grep):

| File | Change |
|------|--------|
| `models/podcast.py` | Replace field with `privacy` + convenience properties |
| `views/feed_views.py` | Three-tier logic |
| `views/podcast_views.py` | Updated access checks (preserve `is_published` + annotations) |
| `admin.py` | Replace in display/filter, add token inline |
| `services/audio.py` | Bucket routing via `uses_private_bucket` |
| `services/publishing.py` | Bucket routing via `uses_private_bucket` |
| `management/commands/local_audio_worker.py` | Bucket routing |
| `management/commands/backfill_episodes.py` | Map `is_public` → `privacy` in PODCAST_DEFINITIONS |
| `tools/setup_episode.py` | Config dict |
| `tools/episode_config.py` | `DEFAULT_CONFIG` and `_load_from_db()` |
| `tools/notebooklm_prompt.py` | Sponsor break logic |
| `models/podcast_config.py` | `to_dict()` |
| `tests/test_models.py` | Update immutability tests (8 test methods) |
| `tests/test_views.py` | Update fixtures |
| `tests/test_feeds.py` | Update setup fixtures |
| `tests/test_workflow_views.py` | Update fixtures |
| `tests/test_import_command.py` | Update fixtures |

### Phase 8: Tests

**Update existing tests:**
- `test_models.py`: 8 immutability tests → change from `is_public` to `privacy` field
- `test_views.py`: Update podcast fixtures to use `privacy=` kwarg
- `test_feeds.py`: Update setup to use `privacy=`
- `test_workflow_views.py`: Update fixtures
- `test_import_command.py`: Update fixtures

**New tests to add:**
- Model tests: `Privacy` choices, default value, `is_public`/`is_unlisted`/`is_restricted` properties, `uses_private_bucket`, immutability guard for `privacy`
- `PodcastAccessToken` tests: auto-generation of token, uniqueness, `record_access()` increments count, deactivation
- Feed tests:
  - Public feed → 200, cached, permanent URLs
  - Unlisted feed → 200, cached, permanent URLs (no token needed)
  - Restricted feed without token → 403
  - Restricted feed with valid `PodcastAccessToken` → 200, signed URLs, access recorded
  - Restricted feed with shared env token (backward compat) → 200
  - Restricted feed with invalid token → 403
  - Restricted feed with deactivated token → 403
- View tests:
  - `PodcastListView` only shows public published + user's own
  - `_get_accessible_podcast` returns podcast for public/unlisted+published, 404 for restricted (non-owner), 404 for unpublished (non-owner)
- Audio storage tests: bucket routing per privacy level via `uses_private_bucket`

## Files to Create

| File | Purpose |
|------|---------|
| `apps/podcast/models/access_token.py` | PodcastAccessToken model |
| `apps/podcast/migrations/0011_*.py` | Add privacy field, migrate data, remove is_public, add PodcastAccessToken |

## Files to Modify

| File | Change |
|------|--------|
| `apps/podcast/models/podcast.py` | Replace `is_public` with `privacy` + `Privacy` enum + properties |
| `apps/podcast/models/__init__.py` | Export `PodcastAccessToken` |
| `apps/podcast/views/feed_views.py` | Three-tier feed logic + `PodcastAccessToken` lookup |
| `apps/podcast/views/podcast_views.py` | Updated access checks (preserve `is_published`, annotations) |
| `apps/podcast/admin.py` | `privacy` field + token inline + `PodcastAccessTokenAdmin` |
| `apps/podcast/services/audio.py` | Bucket routing via `uses_private_bucket` |
| `apps/podcast/services/publishing.py` | Bucket routing via `uses_private_bucket` |
| `apps/podcast/management/commands/local_audio_worker.py` | Bucket routing |
| `apps/podcast/management/commands/backfill_episodes.py` | Privacy mapping in PODCAST_DEFINITIONS |
| `apps/podcast/tools/setup_episode.py` | Config dict |
| `apps/podcast/tools/episode_config.py` | Config dict |
| `apps/podcast/tools/notebooklm_prompt.py` | Sponsor logic |
| `apps/podcast/models/podcast_config.py` | `to_dict()` |
| All test files (5 files) | Update fixtures and assertions |

## Out of Scope (v2)

- Per-token rate limiting (`max_requests_per_hour`)
- Usage analytics dashboard
- Access log model (`FeedAccessLog`)
- IP allowlisting
- Token expiration dates
- Email invitations for restricted feed access
- Remove shared `SUPABASE_USER_ACCESS_TOKEN` fallback (keep for backward compat in v1)

## Migration Data Mapping

| Current | New |
|---------|-----|
| `is_public=True` | `privacy="public"` |
| `is_public=False` | `privacy="restricted"` |
| New podcasts | `privacy="unlisted"` (new default) |

Existing private podcasts (SATSOL, Soul World Bank) become `restricted` — preserving their current token-based auth and private bucket storage. The shared `SUPABASE_USER_ACCESS_TOKEN` continues to work as a fallback until per-podcast tokens are created.

## Interaction with Publishable Mixin

`Podcast` now extends `Publishable`, which provides `published_at`, `unpublished_at`, `is_published`, `publish()`, `unpublish()`. These are **orthogonal** to privacy:

| | Published | Unpublished |
|---|---|---|
| **Public** | Visible on index, feed works | Only owner/staff see it |
| **Unlisted** | Feed works, not on index | Only owner/staff see it |
| **Restricted** | Feed works (with token), not on index | Only owner/staff see it |

Both dimensions must be checked in views:
- `_get_accessible_podcast()` checks both `is_published` and `privacy`
- `PodcastListView` uses `_podcast_published_filter()` combined with `privacy=PUBLIC`
- Feed views don't check `is_published` (feeds should work for published episodes within any podcast)
