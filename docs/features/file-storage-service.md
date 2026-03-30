# File Storage Service

## Overview

Unified API for storing and retrieving binary files with pluggable backends: local filesystem (development), Supabase (production default), and S3-compatible services.

The public interface lives in `apps/common/services/storage.py` and exposes four functions: `store_file()`, `get_file_url()`, `get_file_content()`, and `delete_file()`.

## Backends

| Backend | Setting Value | Use Case |
|---------|--------------|----------|
| `local` | `STORAGE_BACKEND=local` | Development (default) |
| `supabase` | `STORAGE_BACKEND=supabase` | Production (dual-bucket) |
| `s3` | `STORAGE_BACKEND=s3` | Alternative production |

The backend is selected via the `STORAGE_BACKEND` Django setting. If the chosen backend is missing required configuration, it falls back to `local` with a warning.

## Dual-Bucket Support (Supabase)

Public and private files are stored in separate Supabase buckets:

- **Public bucket** (`SUPABASE_PUBLIC_BUCKET_NAME`): Permanent public URLs via `get_public_url()`
- **Private bucket** (`SUPABASE_PRIVATE_BUCKET_NAME`): Signed URLs with 24-hour TTL via `create_signed_url()`

### Usage

```python
from apps.common.services.storage import store_file, get_file_url

# Public file (default) - permanent URL
url = store_file("podcast/my-show/ep1/audio.mp3", audio_bytes, "audio/mpeg")
url = get_file_url("podcast/my-show/ep1/audio.mp3")

# Private file - signed URL with 24h expiration
store_file("podcast/private-show/ep1/audio.mp3", audio_bytes, "audio/mpeg", public=False)
signed_url = get_file_url("podcast/private-show/ep1/audio.mp3", public=False)
```

### Podcast Integration

Podcast audio and cover art are routed to the correct bucket based on `Podcast.is_public`:

- Public podcasts: public bucket, permanent URLs stored in `Episode.audio_url`
- Private podcasts: private bucket, storage key stored in `Episode.audio_url`, signed URLs generated on-demand in feed views

### Private Feed Authentication

Private podcast RSS feeds require a `token` query parameter matching `SUPABASE_USER_ACCESS_TOKEN`:
```
https://app.bwforce.ai/podcast/private-show/feed.xml?token=<SUPABASE_USER_ACCESS_TOKEN>
```

The feed view (`PodcastFeedView`) validates the token, then generates fresh signed URLs for all episode audio and cover art before rendering the RSS XML. Private feeds set `Cache-Control: no-store` to prevent caching of signed URLs.

### Feed Caching

Public podcast feeds are cached in Django's cache for 5 minutes to reduce database load. The cache is automatically invalidated whenever an episode is saved (via Django signal in `apps/podcast/signals.py`), ensuring immediate feed updates when episodes are published or unpublished.

Private podcast feeds are never cached, as signed URLs must be generated fresh on each request.

### Environment Variables

| Variable | Required | Purpose |
|----------|----------|---------|
| `SUPABASE_PROJECT_URL` | Yes | Supabase project URL |
| `SUPABASE_SERVICE_ROLE_KEY` | Yes | Service role auth for uploads |
| `SUPABASE_PUBLIC_BUCKET_NAME` | Yes | Public storage bucket name |
| `SUPABASE_PRIVATE_BUCKET_NAME` | No | Private storage bucket name (omit to disable private storage) |
| `SUPABASE_USER_ACCESS_TOKEN` | No | Token for private feed access |

**Backwards compatibility:** `SUPABASE_BUCKET_NAME` is accepted as a fallback for `SUPABASE_PUBLIC_BUCKET_NAME`.

### Podcast Visibility

`Podcast.is_public` is immutable after creation. Attempting to change it raises `ValueError`. This prevents files from being stranded in the wrong bucket and protects existing feed URLs.

## Implementation Details

### Backend Classes

- `LocalFileStorage` -- Writes to `MEDIA_ROOT`, returns `MEDIA_URL`-prefixed paths. The `public` parameter is accepted but has no effect (all local files are accessible).
- `SupabaseStorage` -- Instantiates two `SupabaseStorageManager` instances (one per bucket). Routes operations via `_manager(public)`.
- `S3Storage` -- Uses boto3 client. Single-bucket only; the `public` parameter is accepted but has no effect.

### Signed URL Generation

`SupabaseStorageManager.create_signed_url(file_path, expires_in=86400)` calls Supabase's signed URL API. The default TTL is 24 hours (86400 seconds). Signed URLs are generated fresh on each private feed request.

### Configuration Check

`check_storage_config()` returns a dict with the current backend name, an `ok` boolean, and any `missing_keys`. This is used for health checks and diagnostics.

## Migration Guide

### Migrating from Single Bucket to Dual-Bucket

If you're upgrading from a deployment using the old `SUPABASE_BUCKET_NAME` setting:

**Step 1: Create the private bucket** (if using private podcasts)
- In Supabase dashboard, create a new bucket for private files
- Set bucket to "Private" (not "Public")
- Note the bucket name

**Step 2: Update environment variables**
```bash
# Old setup (still works as fallback)
SUPABASE_BUCKET_NAME=my-bucket

# New setup (recommended)
SUPABASE_PUBLIC_BUCKET_NAME=my-bucket  # Same as old SUPABASE_BUCKET_NAME
SUPABASE_PRIVATE_BUCKET_NAME=my-private-bucket  # New private bucket
SUPABASE_USER_ACCESS_TOKEN=your-secret-token  # For private feed auth
```

**Step 3: No file migration needed**
- Existing files in the old bucket remain accessible
- The old `SUPABASE_BUCKET_NAME` setting still works as a fallback for `SUPABASE_PUBLIC_BUCKET_NAME`
- New public podcasts continue using the same bucket
- Only new private podcasts use the private bucket

**Step 4: Verify configuration**
```python
from apps.common.services.storage import check_storage_config

status = check_storage_config()
print(status)  # Should show ok=True with no missing_keys
```

### Rolling Back

To roll back to single-bucket mode:
1. Remove `SUPABASE_PRIVATE_BUCKET_NAME` from environment
2. Keep `SUPABASE_PUBLIC_BUCKET_NAME` or use old `SUPABASE_BUCKET_NAME`
3. Any `public=False` storage calls will fall back to the public bucket
