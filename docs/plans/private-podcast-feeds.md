# Private Podcast Feeds

## Overview

A system for publishing private podcast feeds to clients, enabling secure distribution of audio content through authenticated RSS feeds. Clients receive unique feed URLs that work with any podcast player while maintaining access control.

## Core Features

### 1. Podcast Management
- Create and manage podcasts (shows) with metadata
- Upload and manage episodes with audio files
- Episode details: title, description, show notes, publish date
- Draft/published workflow for episodes
- Episode ordering and numbering

### 2. Private Feed Generation
- Generate RSS 2.0 feeds with iTunes/podcast namespace extensions
- Per-client unique feed URLs with authentication tokens
- Feed customization (artwork, categories, language)
- Enclosure support for audio files with proper MIME types

### 3. Client Access Control
- Invite clients to specific podcasts
- Token-based feed authentication (URL token approach for podcast app compatibility)
- Revoke/regenerate access tokens
- Track feed access per client

### 4. Audio File Handling
- Upload audio files (MP3, M4A) to Cloudflare R2
- Generate signed URLs for secure audio delivery (24h expiration)
- Store duration and file size metadata
- Support for large file uploads
- Zero egress fees (R2 advantage over S3)

---

## Data Model

### Core Models

```
apps/podcast/models/

Podcast (show)
├── id (UUID primary key)
├── title (CharField)
├── description (TextField)
├── author (CharField)
├── language (CharField, default='en')
├── category (CharField) - iTunes category
├── artwork (FK to Upload, nullable)
├── website_url (URLField, nullable)
├── owner_email (EmailField)
├── is_explicit (BooleanField, default=False)
├── slug (SlugField, unique)
├── [Timestampable: created_at, updated_at]
├── [Authorable: author_user FK]

Episode
├── id (UUID primary key)
├── podcast (FK to Podcast)
├── title (CharField)
├── description (TextField) - plain text summary
├── content (TextField) - HTML show notes
├── episode_number (PositiveIntegerField) - sequential per podcast, starting at 1
├── episode_type (CharField: full, trailer, bonus)
├── audio_file (FK to Upload)
├── duration_seconds (PositiveIntegerField)
├── file_size_bytes (PositiveIntegerField)
├── guid (UUIDField, unique) - RSS guid
├── [Timestampable: created_at, updated_at]
├── [Publishable: published_at, unpublished_at]

PodcastSubscription (client access)
├── id (UUID primary key)
├── podcast (FK to Podcast)
├── subscriber_email (EmailField)
├── subscriber_name (CharField)
├── access_token (CharField, unique, indexed) - URL-safe token
├── is_active (BooleanField, default=True)
├── feed_url (computed property)
├── last_accessed_at (DateTimeField, nullable)
├── access_count (PositiveIntegerField, default=0)
├── [Timestampable: created_at, updated_at]
├── [Expirable: expires_at]

FeedAccessLog
├── id (BigAutoField)
├── subscription (FK to PodcastSubscription)
├── accessed_at (DateTimeField, auto_now_add)
├── user_agent (CharField)
├── ip_address (GenericIPAddressField)
```

### Behaviors Used
- **Timestampable**: All models get created_at, updated_at
- **Authorable**: Podcast gets author_user FK for ownership
- **Publishable**: Episode gets publish/unpublish workflow
- **Expirable**: PodcastSubscription can have expiration dates

---

## API Endpoints

### Admin/Management (authenticated, staff)

```python
# apps/podcast/urls.py - included under /podcasts/

# Podcast CRUD
path("", PodcastListView.as_view(), name="podcast-list")
path("create/", PodcastCreateView.as_view(), name="podcast-create")
path("<slug:slug>/", PodcastDetailView.as_view(), name="podcast-detail")
path("<slug:slug>/edit/", PodcastUpdateView.as_view(), name="podcast-edit")
path("<slug:slug>/delete/", PodcastDeleteView.as_view(), name="podcast-delete")

# Episode CRUD
path("<slug:slug>/episodes/", EpisodeListView.as_view(), name="episode-list")
path("<slug:slug>/episodes/create/", EpisodeCreateView.as_view(), name="episode-create")
path("<slug:slug>/episodes/<uuid:episode_id>/", EpisodeDetailView.as_view(), name="episode-detail")
path("<slug:slug>/episodes/<uuid:episode_id>/edit/", EpisodeUpdateView.as_view(), name="episode-edit")
path("<slug:slug>/episodes/<uuid:episode_id>/delete/", EpisodeDeleteView.as_view(), name="episode-delete")
path("<slug:slug>/episodes/<uuid:episode_id>/publish/", EpisodePublishView.as_view(), name="episode-publish")

# Subscription management
path("<slug:slug>/subscribers/", SubscriberListView.as_view(), name="subscriber-list")
path("<slug:slug>/subscribers/invite/", SubscriberInviteView.as_view(), name="subscriber-invite")
path("<slug:slug>/subscribers/<uuid:sub_id>/revoke/", SubscriberRevokeView.as_view(), name="subscriber-revoke")
path("<slug:slug>/subscribers/<uuid:sub_id>/regenerate/", SubscriberRegenerateView.as_view(), name="subscriber-regenerate")
```

### Public Feed (token-authenticated)

```python
# apps/api/urls.py - public feed endpoints

# RSS feed - token in URL for podcast app compatibility
path("feed/<str:access_token>/", PodcastFeedView.as_view(), name="podcast-feed")
path("feed/<str:access_token>/rss.xml", PodcastFeedView.as_view(), name="podcast-feed-xml")

# Audio file delivery (presigned URL redirect)
path("feed/<str:access_token>/episode/<uuid:guid>/audio", EpisodeAudioView.as_view(), name="episode-audio")
```

---

## Feed Generation

### RSS 2.0 with Podcast Extensions

```xml
<?xml version="1.0" encoding="UTF-8"?>
<rss version="2.0"
     xmlns:itunes="http://www.itunes.com/dtds/podcast-1.0.dtd"
     xmlns:podcast="https://podcastindex.org/namespace/1.0"
     xmlns:atom="http://www.w3.org/2005/Atom">
  <channel>
    <title>Podcast Title</title>
    <description>Podcast description</description>
    <link>https://example.com</link>
    <language>en</language>
    <atom:link href="https://cuttlefish.app/api/feed/{token}/" rel="self" type="application/rss+xml"/>

    <!-- iTunes tags -->
    <itunes:author>Author Name</itunes:author>
    <itunes:summary>Summary</itunes:summary>
    <itunes:image href="https://..."/>
    <itunes:explicit>false</itunes:explicit>
    <itunes:category text="Business"/>
    <itunes:owner>
      <itunes:name>Owner</itunes:name>
      <itunes:email>owner@example.com</itunes:email>
    </itunes:owner>

    <item>
      <title>Episode Title</title>
      <description>Episode description</description>
      <enclosure url="https://cuttlefish.app/api/feed/{token}/episode/{guid}/audio"
                 length="12345678"
                 type="audio/mpeg"/>
      <guid isPermaLink="false">{guid}</guid>
      <pubDate>Mon, 01 Jan 2024 12:00:00 +0000</pubDate>
      <itunes:duration>3600</itunes:duration>
      <itunes:episodeType>full</itunes:episodeType>
      <itunes:episode>1</itunes:episode>
    </item>
  </channel>
</rss>
```

### Feed Service

```python
# apps/podcast/services/feed_generator.py

class PodcastFeedGenerator:
    """Generate RSS feeds for podcasts."""

    def __init__(self, podcast: Podcast, subscription: PodcastSubscription):
        self.podcast = podcast
        self.subscription = subscription

    def generate(self) -> str:
        """Generate RSS XML string."""
        # Build channel metadata
        # Add published episodes
        # Return XML string
        pass

    def get_enclosure_url(self, episode: Episode) -> str:
        """Generate authenticated enclosure URL for episode."""
        return f"{settings.BASE_URL}/api/feed/{self.subscription.access_token}/episode/{episode.guid}/audio"
```

---

## UI Pages

### Templates Location
All templates in `apps/public/templates/podcast/`

### Page Structure

```
1. Podcast List (/podcasts/)
   - Grid/list of all podcasts
   - Quick stats: episode count, subscriber count
   - Create new podcast button

2. Podcast Detail (/podcasts/{slug}/)
   - Podcast metadata and artwork
   - Episode list with publish status
   - Subscriber count and recent activity
   - Quick actions: add episode, invite subscriber

3. Podcast Edit (/podcasts/{slug}/edit/)
   - Form for podcast metadata
   - Artwork upload
   - iTunes category selection

4. Episode List (/podcasts/{slug}/episodes/)
   - Table of episodes with status
   - Drag-to-reorder (HTMX)
   - Bulk actions: publish, unpublish

5. Episode Form (/podcasts/{slug}/episodes/create/ or /edit/)
   - Title, description, show notes (rich text)
   - Audio file upload with progress
   - Episode number, type
   - Publish immediately or save as draft

6. Subscriber List (/podcasts/{slug}/subscribers/)
   - Table of subscribers with access status
   - Feed URL (copyable)
   - Last accessed, access count
   - Actions: revoke, regenerate token

7. Subscriber Invite (/podcasts/{slug}/subscribers/invite/)
   - Email and name form
   - Optional: set expiration date
   - Send invite email checkbox
```

### HTMX Interactions

```python
# Partial views for HTMX

# Episode list row update after publish/unpublish
path("partials/episode-row/<uuid:episode_id>/", EpisodeRowPartial.as_view())

# Subscriber row after regenerate
path("partials/subscriber-row/<uuid:sub_id>/", SubscriberRowPartial.as_view())

# Audio upload progress
path("partials/upload-progress/<uuid:upload_id>/", UploadProgressPartial.as_view())
```

---

## Integration with Existing Apps

### apps/common
- Use `Upload` model for audio files and artwork
- Use behavior mixins (Timestampable, Authorable, Publishable, Expirable)
- Use factories pattern for tests

### apps/integration/cloudflare (NEW)
- R2 client for audio file storage and signed URL generation
- S3-compatible API (boto3 with custom endpoint)
- Configure R2 bucket for podcast audio: `podcasts/{podcast_id}/episodes/`
- Signed URLs use R2's native public URL format: `https://pub-{id}.r2.dev/...`

### apps/public
- Add podcast URLs to main URL configuration
- Use MainContentView pattern for pages
- Use HTMXView pattern for partials
- Follow existing template structure

### apps/api
- Add feed endpoints for RSS delivery
- Add audio redirect endpoint

---

## Implementation Phases

### Phase 1: Core Models & Admin
- [ ] Create apps/podcast Django app
- [ ] Define Podcast, Episode, PodcastSubscription models
- [ ] Add Django admin for all models
- [ ] Create model factories for testing
- [ ] Write model tests

### Phase 2: Feed Generation
- [ ] Implement PodcastFeedGenerator service
- [ ] Create RSS feed view (token auth)
- [ ] Create audio redirect view (presigned URLs)
- [ ] Test feed with podcast players
- [ ] Add FeedAccessLog tracking

### Phase 3: Management UI
- [ ] Podcast CRUD views
- [ ] Episode CRUD views with audio upload
- [ ] Subscriber management views
- [ ] HTMX partials for dynamic updates

### Phase 4: Client Experience
- [ ] Invite email template
- [ ] Feed access landing page (instructions)
- [ ] Token regeneration workflow
- [ ] Expiration handling

### Phase 5: Polish
- [ ] Bulk episode operations
- [ ] Analytics dashboard (access stats)
- [ ] Import from external podcast hosts
- [ ] Episode scheduling (future publish dates)

---

## Technical Considerations

### Audio File Storage (Cloudflare R2)
- Store in R2 bucket with prefix: `podcasts/{podcast_id}/episodes/`
- Use R2 signed URLs with 24-hour expiration
- R2 public URL format: `https://pub-{account}.r2.dev/{path}?signature=...`
- Zero egress fees - ideal for bandwidth-heavy audio content
- Set Content-Disposition for proper download names
- Maximum file size: 500MB (configurable)
- S3-compatible API via boto3 with custom endpoint:
  ```python
  import os
  s3 = boto3.client('s3',
      endpoint_url=os.environ['CLOUDFLARE_S3_API'],
      aws_access_key_id=os.environ['CLOUDFLARE_R2_ACCESS_KEY'],
      aws_secret_access_key=os.environ['CLOUDFLARE_R2_SECRET_KEY']
  )
  ```

### Feed Authentication
- Token in URL (not HTTP auth) for podcast app compatibility
- Tokens: 32-character URL-safe random strings
- Rate limiting: 100 requests/hour per token
- Track User-Agent for analytics

### Performance
- Cache generated feeds (5-minute TTL)
- Invalidate cache on episode publish/unpublish
- Background task for sending invite emails
- Presigned URL caching (match S3 expiration)

### Security
- Validate audio file MIME types on upload
- Scan uploaded files for malware (future)
- Log all feed accesses with IP
- Allow IP allowlisting per subscription (future)

---

## Dependencies

### Python Packages (add via uv)
```bash
uv add feedgen           # RSS feed generation
uv add python-magic      # MIME type detection for uploads
uv add mutagen           # Audio metadata extraction (duration)
```

### Existing Dependencies Used
- Django (ORM, views, templates)
- boto3 (R2 via S3-compatible API)
- django-htmx (HTMX integration)

### Environment Variables (in .env.local)
```
CLOUDFLARE_R2_ACCOUNT_ID=xxx          # Already set
CLOUDFLARE_S3_API=xxx                 # S3-compatible endpoint URL
CLOUDFLARE_API_TOKEN=xxx              # R2 API token
CLOUDFLARE_R2_BUCKET_NAME=xxx         # Already set
CLOUDFLARE_R2_PUBLIC_URL=xxx          # Already set (e.g., https://pub-xxx.r2.dev)

# For boto3 S3-compatible access (uncomment/generate if needed):
# CLOUDFLARE_R2_ACCESS_KEY=xxx
# CLOUDFLARE_R2_SECRET_KEY=xxx
```

**Note:** If using boto3, you need to generate R2 API tokens from the Cloudflare dashboard
(R2 → Manage R2 API Tokens) which provides Access Key ID and Secret Access Key.

---

## Testing Strategy

### Unit Tests
```
apps/podcast/tests/
├── test_models/
│   ├── test_podcast.py
│   ├── test_episode.py
│   └── test_subscription.py
├── test_services/
│   └── test_feed_generator.py
├── test_views/
│   ├── test_podcast_views.py
│   ├── test_episode_views.py
│   └── test_feed_views.py
└── factories.py
```

### Integration Tests
- Feed generation with real XML parsing
- S3 presigned URL generation
- Audio file upload flow

### E2E Tests
- Full podcast creation flow
- Episode upload and publish
- Feed access from external client (mock podcast player)

---

## Future Enhancements (Out of Scope)

- Multiple audio formats per episode (automatic transcoding)
- Transcript generation (AI-powered)
- Chapter markers
- Video podcast support
- Public podcast hosting (non-private feeds)
- Podcast analytics dashboard
- Integration with podcast directories
- Embeddable web player
