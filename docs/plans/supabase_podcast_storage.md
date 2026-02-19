---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-02-18
tracking: https://github.com/yudame/cuttlefish/issues/85
---

# Supabase Podcast Storage: Public/Private Bucket Support

## Problem

The storage service currently assumes a single Supabase bucket (`SUPABASE_BUCKET_NAME`). Podcasts can be public or private (`Podcast.is_public`), but all files go to the same bucket with public URLs. Private podcasts need their audio and cover art stored in a private Supabase bucket with signed (expiring) URLs, while public podcasts use a public bucket with permanent URLs.

Additionally, local development machines need Supabase credentials configured so `store_file()` works locally (prerequisite for the local audio worker, issue #84).

**Current behavior:**
- Single `SUPABASE_BUCKET_NAME` env var — no distinction between public/private
- `store_file()` always returns a public URL via `get_public_url()`
- Private podcast audio is stored with public URLs (accessible to anyone with the link)
- Local dev falls back to `LocalFileStorage` — can't test Supabase uploads

**Desired outcome:**
- Two Supabase buckets: public and private
- `store_file()` accepts a `public` parameter to select the correct bucket
- Private files get signed URLs with 24-hour TTL
- Podcasts route to the correct bucket based on `Podcast.is_public`
- Podcast visibility is immutable after creation (enforced at DB level)
- Private podcast RSS feeds require `SUPABASE_USER_ACCESS_TOKEN` for access
- `.env.example` documents all required Supabase env vars
- Local dev can upload to Supabase when credentials are present

## Appetite

**Size:** Medium

**Team:** Solo dev + PM. One review round.

**Interactions:**
- PM check-ins: 0 (all questions resolved)
- Review rounds: 1

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| `SUPABASE_PROJECT_URL` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('SUPABASE_PROJECT_URL')"` | Supabase project URL (`https://oqtandccoymkstwjafzr.supabase.co`) |
| `SUPABASE_SERVICE_ROLE_KEY` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('SUPABASE_SERVICE_ROLE_KEY')"` | Service role auth for uploads |
| `SUPABASE_PUBLIC_BUCKET_NAME` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('SUPABASE_PUBLIC_BUCKET_NAME')"` | Public storage bucket |
| `SUPABASE_PRIVATE_BUCKET_NAME` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('SUPABASE_PRIVATE_BUCKET_NAME')"` | Private storage bucket |
| `SUPABASE_USER_ACCESS_TOKEN` | `python -c "from dotenv import dotenv_values; assert dotenv_values('.env.local').get('SUPABASE_USER_ACCESS_TOKEN')"` | Feed authentication for private podcasts |

## Solution

### Key Elements

- **Dual-bucket configuration**: `SUPABASE_PUBLIC_BUCKET_NAME` and `SUPABASE_PRIVATE_BUCKET_NAME` replace the single `SUPABASE_BUCKET_NAME`
- **Bucket-aware storage API**: `store_file()` gains a `public=True` parameter; callers pass `public=podcast.is_public`
- **Signed URLs for private files**: Private bucket files return signed URLs via `create_signed_url()` with 24-hour TTL
- **Authenticated private feeds**: Private podcast RSS feeds require `SUPABASE_USER_ACCESS_TOKEN` as a query parameter or header; feed view generates fresh signed URLs on each request
- **Immutable visibility**: `Podcast.is_public` cannot change after creation (DB constraint). No bucket migration logic needed.

### Flow

**Upload (public podcast):**
Audio/cover art generated → `store_file(key, bytes, content_type, public=True)` → SupabaseStorage selects public bucket → `upload()` → `get_public_url()` → permanent URL stored in `Episode.audio_url`

**Upload (private podcast):**
Audio/cover art generated → `store_file(key, bytes, content_type, public=False)` → SupabaseStorage selects private bucket → `upload()` → signed URL NOT stored (generated on demand)

**Feed serving (public podcast):**
RSS feed request → `PodcastFeedView` → episodes with permanent `audio_url` → XML response (no auth required)

**Feed serving (private podcast):**
RSS feed request with `?token=SUPABASE_USER_ACCESS_TOKEN` → token validated → `get_file_url(key, public=False)` → fresh 24h signed URLs generated → XML response with signed URLs for audio and cover art

### Technical Approach

- **Settings layer**: Replace `SUPABASE_BUCKET_NAME` with `SUPABASE_PUBLIC_BUCKET_NAME` and `SUPABASE_PRIVATE_BUCKET_NAME` in `settings/base.py` and `settings/database.py`. Add `SUPABASE_USER_ACCESS_TOKEN`. Keep `SUPABASE_BUCKET_NAME` as fallback for public bucket (backwards compat).
- **SupabaseStorageManager**: Add `create_signed_url(path, expires_in)` method wrapping `storage_client.create_signed_url()`.
- **SupabaseStorage backend**: Maintain two `SupabaseStorageManager` instances (public + private). `store()` and `get_url()` accept `public` kwarg to select which manager. Private `get_url()` returns signed URL with 24h TTL.
- **Storage public API**: `store_file(key, content, content_type, public=True)` and `get_file_url(key, public=True)` — default to public for backwards compatibility.
- **Podcast services**: Pass `public=episode.podcast.is_public` to all `store_file()` / `get_file_url()` calls.
- **Feed views**: Private feeds require token auth (`SUPABASE_USER_ACCESS_TOKEN`). Generate fresh signed URLs at render time for private podcast episodes. Remove `cache_page` decorator for private feeds (signed URLs must be fresh).
- **Immutability constraint**: Add DB-level constraint or override `Podcast.save()` to prevent `is_public` from changing after initial creation.
- **Local dev**: When `STORAGE_BACKEND=local` (default), the `public` parameter is ignored — local storage always serves files directly. When `STORAGE_BACKEND=supabase`, dual-bucket support activates.

## Rabbit Holes

- **Per-episode visibility** — Don't add `is_public` to individual episodes. Visibility is at the podcast level only.
- **Automatic signed URL refresh** — Don't build a background job to refresh signed URLs in the DB. Generate them on-the-fly in feed views.
- **Supabase RLS policies** — Don't implement Row Level Security on the storage buckets. Service role key bypasses RLS, and we control access at the application layer.
- **CDN/caching for signed URLs** — Signed URLs can't be cached by CDNs. Don't optimize this now.
- **Bucket migration** — Podcasts cannot change visibility, so no migration logic between buckets.
- **OAuth/JWT for feed auth** — Simple token comparison is sufficient for private feeds. Don't build a full auth system.

## Risks

### Risk 1: Signed URL expiry in podcast players
**Impact:** If a podcast player caches the RSS feed for longer than 24 hours, audio downloads will fail with 403.
**Mitigation:** 24-hour TTL is generous. Podcast players typically re-fetch feeds every few hours. The feed view generates fresh signed URLs on every request.

### Risk 2: Breaking existing `store_file()` callers
**Impact:** Any code calling `store_file()` without the new `public` parameter could break.
**Mitigation:** Default `public=True` maintains backwards compatibility. All existing callers already target the public bucket behavior.

### Risk 3: Token leakage in feed URLs
**Impact:** The `SUPABASE_USER_ACCESS_TOKEN` in feed URL query params could be logged or shared.
**Mitigation:** This is standard practice for private podcast feeds (Apple Podcasts, Patreon all use token-in-URL). The token only grants feed access, not admin operations.

## No-Gos (Out of Scope)

- Per-episode public/private (visibility is podcast-level only)
- Changing podcast visibility after creation (immutable `is_public`)
- S3 backend dual-bucket support (Supabase only for now)
- Supabase bucket creation/management (buckets are pre-created manually)
- Full OAuth/JWT authentication for private feeds

## Update System

No update system changes required — this is a settings and service layer change. Local audio workers will need the new env vars configured, but that's documented in `.env.example`.

## Agent Integration

No agent integration required — this is a storage service internal change. The agent doesn't directly call `store_file()`.

## Documentation

### Feature Documentation
- [ ] Update `docs/plans/file-storage-service.md` to reference dual-bucket support
- [ ] Add Supabase storage section to deployment docs

### Inline Documentation
- [ ] Docstrings on new `public` parameter for `store_file()` and `get_file_url()`
- [ ] Comment explaining 24h signed URL TTL

## Success Criteria

- [ ] `store_file(key, bytes, type, public=True)` uploads to public bucket, returns permanent URL
- [ ] `store_file(key, bytes, type, public=False)` uploads to private bucket
- [ ] `get_file_url(key, public=False)` returns a fresh signed URL (24h TTL)
- [ ] Podcast audio service routes to correct bucket based on `Podcast.is_public`
- [ ] Podcast cover art routes to correct bucket based on `Podcast.is_public`
- [ ] Local audio worker uploads to correct bucket
- [ ] Public RSS feed serves permanent URLs (no auth required)
- [ ] Private RSS feed requires `SUPABASE_USER_ACCESS_TOKEN` and serves fresh signed URLs
- [ ] `Podcast.is_public` cannot be changed after creation (DB constraint)
- [ ] `.env.example` documents all Supabase env vars (PROJECT_URL, SERVICE_ROLE_KEY, PUBLIC_BUCKET_NAME, PRIVATE_BUCKET_NAME, USER_ACCESS_TOKEN)
- [ ] Old `SUPABASE_BUCKET_NAME` still works as fallback for public bucket
- [ ] Existing tests pass (backwards compatible)
- [ ] New tests cover dual-bucket routing, signed URL generation, and feed auth
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (storage-layer)**
  - Name: storage-builder
  - Role: Implement dual-bucket storage in settings, SupabaseStorageManager, SupabaseStorage backend, and public API
  - Agent Type: builder
  - Resume: true

- **Builder (podcast-integration)**
  - Name: podcast-builder
  - Role: Update podcast services, audio.py, local_audio_worker, feed views, and add immutability constraint
  - Agent Type: builder
  - Resume: true

- **Builder (config-and-env)**
  - Name: config-builder
  - Role: Update settings files and .env.example
  - Agent Type: builder
  - Resume: true

- **Validator (storage)**
  - Name: storage-validator
  - Role: Verify dual-bucket routing, signed URLs, and backwards compatibility
  - Agent Type: validator
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write tests for dual-bucket storage, signed URLs, feed auth, and immutability
  - Agent Type: test-engineer
  - Resume: true

- **Validator (final)**
  - Name: final-validator
  - Role: Run full test suite and verify all success criteria
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Update Settings and Environment Configuration
- **Task ID**: build-config
- **Depends On**: none
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: true
- Replace `SUPABASE_BUCKET_NAME` with `SUPABASE_PUBLIC_BUCKET_NAME` and `SUPABASE_PRIVATE_BUCKET_NAME` in `settings/base.py` and `settings/database.py`
- Add `SUPABASE_USER_ACCESS_TOKEN` to settings
- Keep `SUPABASE_BUCKET_NAME` as fallback: `SUPABASE_PUBLIC_BUCKET_NAME = os.environ.get("SUPABASE_PUBLIC_BUCKET_NAME", "") or os.environ.get("SUPABASE_BUCKET_NAME", "")`
- Update `.env.example` with all new env vars and comments (remove old `SUPABASE_BUCKET_NAME`)
- Update `settings/local.py` to enable `STORAGE_BACKEND = "supabase"` when Supabase env vars are present

### 2. Implement Dual-Bucket Storage Backend
- **Task ID**: build-storage
- **Depends On**: build-config
- **Assigned To**: storage-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `create_signed_url(path, expires_in)` method to `SupabaseStorageManager`
- Refactor `SupabaseStorage` to hold two `SupabaseStorageManager` instances (public + private)
- Add `public` parameter to `StorageBackend.store()`, `get_url()`, `get_content()`, `delete()` (default `True`)
- Update `store_file()`, `get_file_url()`, `get_file_content()`, `delete_file()` public API with `public` parameter
- Private `get_url()` calls `create_signed_url(expires_in=86400)` (24 hours)
- Update `check_config()` to validate both bucket names
- Ensure `LocalFileStorage` accepts and ignores `public` parameter

### 3. Update Podcast Services, Commands, and Feed Views
- **Task ID**: build-podcast
- **Depends On**: build-storage
- **Assigned To**: podcast-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `apps/podcast/services/audio.py` `generate_audio()` to pass `public=episode.podcast.is_public`
- Update `apps/podcast/services/publishing.py` `generate_cover_art()` to pass `public=episode.podcast.is_public`
- Update `apps/podcast/management/commands/local_audio_worker.py` to pass `public` based on podcast visibility (worker needs podcast `is_public` from API response)
- Update `apps/podcast/views/feed_views.py`:
  - Private feeds: validate `SUPABASE_USER_ACCESS_TOKEN` from query param `?token=`
  - Private feeds: generate fresh signed URLs for audio and cover art via `get_file_url(key, public=False)`
  - Private feeds: remove `cache_page` decorator (signed URLs must be fresh per request)
  - Public feeds: no changes to existing behavior
- Add immutability constraint on `Podcast.is_public` (override `save()` to prevent changes after first save, or add a DB trigger)

### 4. Validate Storage Layer
- **Task ID**: validate-storage
- **Depends On**: build-storage
- **Assigned To**: storage-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `store_file(public=True)` routes to public bucket
- Verify `store_file(public=False)` routes to private bucket
- Verify `get_file_url(public=False)` returns signed URL
- Verify backwards compatibility — callers without `public` param default to public bucket
- Verify `LocalFileStorage` ignores `public` parameter gracefully

### 5. Write Tests
- **Task ID**: build-tests
- **Depends On**: build-podcast
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Test dual-bucket routing in `SupabaseStorage`
- Test signed URL generation for private files (24h TTL)
- Test `store_file()` backwards compatibility (no `public` param)
- Test podcast audio service routes to correct bucket
- Test feed view generates signed URLs for private podcasts
- Test feed view requires token for private podcasts
- Test feed view rejects invalid tokens for private podcasts
- Test `Podcast.is_public` immutability after creation

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: config-builder
- **Agent Type**: documentarian
- **Parallel**: false
- Update `.env.example` comments
- Update `CLAUDE.md` if Supabase env vars section needs changes
- Add inline docstrings to modified functions

### 7. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite: `DJANGO_SETTINGS_MODULE=settings pytest`
- Verify all success criteria met
- Verify pre-commit hooks pass

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/common/tests/test_storage.py -v` - Storage backend tests
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` - Podcast integration tests
- `DJANGO_SETTINGS_MODULE=settings pytest --tb=short` - Full test suite
- `uv run pre-commit run --all-files` - Code quality checks
- `python -c "from apps.common.services.storage import store_file; help(store_file)"` - Verify API signature updated
