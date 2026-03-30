---
status: Planning
type: feature
appetite: Medium
owner: Tom
created: 2026-02-11
tracking: https://github.com/yudame/cuttlefish/issues/35
---

# Django Podcast App — MVP

## Problem

The podcast production system was migrated from a static site repo (`research`) into `cuttlefish/apps/podcast/`, but it's just a collection of CLI tools — no Django integration. Meanwhile, the live podcast is served from GitHub Pages at `research.bwforce.ai` as static HTML/XML files.

**Current behavior:**
- 33+ episodes across 7 topic areas served from static GitHub Pages
- One monolithic RSS feed (`feed.xml`) manually updated by CLI tools
- No database — episode metadata lives in XML and static HTML files
- No admin UI for managing episodes
- Episode pages are static HTML with no audio player
- No support for private/subscriber-only feeds

**Desired outcome:**
- Django models store all podcast and episode data
- Admin UI for managing podcasts and episodes
- Dynamic RSS feed generation from the database (multi-feed architecture)
- Minimal public episode/podcast pages replacing the static site
- Existing 33+ episodes imported from the static site's `feed.xml`
- Foundation in place for private feeds (future work)

## Appetite

**Size:** Medium

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 1-2 (model design confirmation, data import review)
- Review rounds: 1 (before merging)

The models are straightforward, the admin is mostly auto-generated, RSS generation is well-defined by spec, and data import is mechanical parsing of a known XML format.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| PostgreSQL running | `pg_isready` | Database |
| Cuttlefish DB exists | `psql -d cuttlefish -c "SELECT 1"` | Database |
| Static feed accessible | `curl -s https://research.bwforce.ai/podcast/feed.xml \| head -5` | Data import source |

## Solution

### Key Elements

- **Podcast model**: Represents a feed (e.g., "Yudame Research Podcast" public feed, or a future private feed). Each podcast has its own RSS feed URL.
- **Episode model**: Belongs to a Podcast. Stores title, description, audio URL, duration, cover art URL, publication date, episode number, and show notes HTML.
- **RSS feed view**: Generates valid `feed.xml` from database for each Podcast. Includes iTunes/Podcasting 2.0 namespace tags.
- **Public views**: Minimal podcast listing page and episode detail page (title, description, companion links, audio download).
- **Admin**: Register models with Unfold admin. Basic list/filter/search. Inline episode management.
- **Data import command**: Management command that parses `research.bwforce.ai/podcast/feed.xml` and creates Podcast + Episode records.

### Data Model

```
Podcast (Timestampable)
├── title                   CharField(200)
├── slug                    SlugField(unique)
├── description             TextField
├── author_name             CharField(200)
├── author_email            EmailField
├── cover_image_url         URLField (external URL for now, R2 later)
├── language                CharField(10) default="en"
├── is_public               BooleanField default=True
├── categories              JSONField (iTunes categories)
├── website_url             URLField (blank)
└── episodes →

Episode (Timestampable)
├── podcast                 FK → Podcast
├── title                   CharField(200)  — includes series prefix e.g. "Cardiovascular Health: Lifestyle Foundations"
├── slug                    SlugField
├── episode_number          PositiveIntegerField
├── description             TextField (plain text summary)
├── show_notes_html         TextField (rich HTML show notes)
├── audio_url               URLField (points to static site or R2)
├── audio_duration_seconds  PositiveIntegerField
├── audio_file_size_bytes   BigIntegerField (for RSS enclosure)
├── cover_image_url         URLField (blank, falls back to podcast cover)
├── published_at            DateTimeField (nullable, null = draft)
├── is_explicit             BooleanField default=False
├── transcript_url          URLField (blank)
├── chapters_url            URLField (blank)
├── companion_resources     JSONField (flexible dict of companion URLs — summary, checklist, frameworks, etc.)
├── report_text             TextField (blank — full report content stored in DB)
├── sources_text            TextField (blank — full sources/citations stored in DB)
├── ordering: episode_number (ascending) — re-releases update published_at but keep their position
└── unique_together: (podcast, episode_number)
    unique_together: (podcast, slug)

Note: Report and sources are served at deterministic URLs via routing
(e.g. /podcast/{slug}/{episode-slug}/report/, /podcast/{slug}/{episode-slug}/sources/)
— no need to store URLs for these.
```

### Flow

**Admin user** → Django admin → Create/edit Podcast and Episodes → **RSS feed auto-generated** at `/podcast/{slug}/feed.xml`

**Listener** → `/podcast/` → See podcast listing → Click podcast → See episode list → Click episode → Episode detail page with download link

**Data import** → `python manage.py import_podcast_feed` → Parses static feed.xml → Creates Podcast + Episodes in DB

**CLI workflow** → Existing tools create episode in `pending-episodes/` → (future) publish script imports into Django

### Technical Approach

- Follow established patterns from `apps/drugs/` (models, admin, views, urls, apps.py)
- Use `Timestampable` behavior mixin on both models
- RSS feed: Compile from DB content using a Django template with proper XML content type, cached. Replicates the existing feed.xml structure exactly.
- Public views: Use `MainContentView` pattern for pages, templates in `apps/public/templates/podcast/`
- Audio URLs: Store as URLField pointing to `research.bwforce.ai` for now (R2 migration later)
- Episode ordering: `episode_number` within a podcast (ascending in DB, most recent first in feeds). Re-releases update `published_at` to bust player caches but keep their episode number.
- Import command: Parse XML with `xml.etree.ElementTree`, extract iTunes namespace metadata

## Rabbit Holes

- **Don't build private feed auth yet** — The access control model (secret URLs, tokens, etc.) is a separate feature. Just add `is_public` boolean to Podcast for now.
- **Don't build an audio player** — MVP episode pages just link to the MP3. An embedded player is future work.
- **Don't integrate with the CLI tools** — The existing `update_feed.py` and other tools stay as-is. A "publish from pending-episodes to database" bridge is future work.
- **Don't migrate audio files to R2** — Audio stays on `research.bwforce.ai` for MVP. R2 is v2.
- **Don't build subscriber management** — No user accounts or subscription tracking in this MVP.
- **Don't parse show notes from static HTML pages** — Import what's in `feed.xml` only. The static episode HTML pages have additional content but it's not worth scraping for MVP.

## Risks

### Risk 1: Feed.xml parsing edge cases
**Impact:** Import might miss episodes or mangle metadata if the XML has unexpected structure.
**Mitigation:** The feed is well-structured (we generated it). Parse conservatively, log warnings for any episode that can't be fully parsed, and allow manual fixup in admin.

### Risk 2: RSS feed compatibility
**Impact:** Generated feeds might not validate with Apple/Spotify podcast validators.
**Mitigation:** Follow the existing `feed.xml` structure exactly. Reference `docs/RSS-specification.md`. Test with a feed validator before shipping.

### Risk 3: Episode numbering during import
**Impact:** Episodes from different "series" (cardiovascular, stablecoin, etc.) are currently in one feed with separate episode numbering. Flattening into a single podcast requires renumbering.
**Mitigation:** Import in chronological order (by `pubDate`), assign sequential episode numbers 1-N. Original series name is preserved as a title prefix (e.g. "Cardiovascular Health: Lifestyle Foundations"). **Decision made** — all previous numbers are replaced.

## No-Gos (Out of Scope)

- Private feed access control (secret URLs, tokens, subscriber management)
- Audio file hosting on R2/CDN
- Embedded audio player on episode pages
- Integration with CLI podcast production tools
- Publishing workflow from `pending-episodes/` to database
- Full show notes with chapters, timestamps, research citations
- Podcast analytics or download tracking
- Search/filtering on public pages
- SEO optimization (Open Graph tags, structured data)

## Update System

No update system changes required — this is a new Django app within the existing cuttlefish deployment.

## Agent Integration

No agent integration required — this is a Django web feature. Future MCP tools for podcast management are out of scope.

## Documentation

### Feature Documentation
- [ ] Update `CLAUDE.md` with podcast app in the architecture section
- [ ] Add podcast URL patterns to settings/urls.py

### Inline Documentation
- [ ] Model docstrings explaining fields and relationships
- [ ] Management command help text

## Success Criteria

- [ ] `apps/podcast/` is a proper Django app (apps.py, models.py, admin.py, views.py, urls.py)
- [ ] `apps.podcast` in `INSTALLED_APPS`
- [ ] Podcast and Episode models created with migrations ready (not run — Tom approves)
- [ ] Admin interface registers both models with list display, filters, and search
- [ ] `/podcast/{slug}/feed.xml` returns valid RSS with iTunes namespace for any public podcast
- [ ] `/podcast/` shows a listing of public podcasts
- [ ] `/podcast/{slug}/` shows episodes for a podcast
- [ ] `/podcast/{slug}/{episode-slug}/` shows an episode detail page
- [ ] `import_podcast_feed` management command successfully imports all episodes from research.bwforce.ai
- [ ] Imported episodes have correct titles, descriptions, audio URLs, durations, and dates
- [ ] All existing tests still pass
- [ ] New models have test coverage

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: models-builder
  - Role: Create Django models, app config, and migrations
  - Agent Type: database-architect
  - Resume: true

- **Builder (admin)**
  - Name: admin-builder
  - Role: Register models in Unfold admin with proper configuration
  - Agent Type: builder
  - Resume: true

- **Builder (feeds)**
  - Name: feeds-builder
  - Role: RSS feed generation view
  - Agent Type: builder
  - Resume: true

- **Builder (views)**
  - Name: views-builder
  - Role: Public-facing views and templates
  - Agent Type: builder
  - Resume: true

- **Builder (import)**
  - Name: import-builder
  - Role: Management command to import from static feed.xml
  - Agent Type: builder
  - Resume: true

- **Validator (all)**
  - Name: podcast-validator
  - Role: Verify all components work together
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create Django app scaffold
- **Task ID**: build-scaffold
- **Depends On**: none
- **Assigned To**: models-builder
- **Agent Type**: database-architect
- **Parallel**: true
- Create `apps/podcast/apps.py` with `PodcastConfig`
- Create `apps/podcast/__init__.py`
- Add `"apps.podcast"` to `PROJECT_APPS` in `settings/base.py`
- Add `path("podcast/", include("apps.podcast.urls", namespace="podcast"))` to `settings/urls.py`

### 2. Create models
- **Task ID**: build-models
- **Depends On**: build-scaffold
- **Assigned To**: models-builder
- **Agent Type**: database-architect
- **Parallel**: false
- Create `apps/podcast/models.py` with Podcast and Episode models
- Create migrations directory `apps/podcast/migrations/__init__.py`
- Generate migration file with `makemigrations` (do NOT run `migrate`)

### 3. Create admin
- **Task ID**: build-admin
- **Depends On**: build-models
- **Assigned To**: admin-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `apps/podcast/admin.py` with Unfold ModelAdmin for Podcast and Episode
- Episode inline on Podcast admin page
- List display, filters, search fields

### 4. Create RSS feed view
- **Task ID**: build-feeds
- **Depends On**: build-models
- **Assigned To**: feeds-builder
- **Agent Type**: builder
- **Parallel**: true (with build-admin)
- Create feed generation view that outputs valid RSS XML
- Include iTunes namespace tags (author, duration, image, categories, explicit)
- Include Podcasting 2.0 namespace (chapters, transcript)
- Wire up URL: `/podcast/{slug}/feed.xml`

### 5. Create public views and templates
- **Task ID**: build-views
- **Depends On**: build-models
- **Assigned To**: views-builder
- **Agent Type**: builder
- **Parallel**: true (with build-admin, build-feeds)
- Create `apps/podcast/views.py` with:
  - `PodcastListView` — list of public podcasts
  - `PodcastDetailView` — episodes for a podcast
  - `EpisodeDetailView` — single episode page
  - `EpisodeReportView` — serves report_text at deterministic URL
  - `EpisodeSourcesView` — serves sources_text at deterministic URL
- Create `apps/podcast/urls.py` with routes:
  - `/podcast/` → podcast list
  - `/podcast/{slug}/` → podcast detail (episode list)
  - `/podcast/{slug}/feed.xml` → RSS feed
  - `/podcast/{slug}/{episode-slug}/` → episode detail
  - `/podcast/{slug}/{episode-slug}/report/` → report content
  - `/podcast/{slug}/{episode-slug}/sources/` → sources content
- Create templates in `apps/public/templates/podcast/`:
  - `podcast_list.html`
  - `podcast_detail.html`
  - `episode_detail.html`

### 6. Create data import command
- **Task ID**: build-import
- **Depends On**: build-models
- **Assigned To**: import-builder
- **Agent Type**: builder
- **Parallel**: true (with others)
- Create `apps/podcast/management/commands/import_podcast_feed.py`
- Parse `feed.xml` from URL or local file
- Create Podcast from channel metadata
- Create Episodes from items, ordered chronologically by pubDate
- Assign sequential episode numbers 1-N (replaces any original numbering)
- Merge original series name as title prefix (e.g. "Cardiovascular Health: Lifestyle Foundations")
- Extract: title, description, audio URL, duration, file size, pub date, cover image
- Handle iTunes namespace for metadata
- Idempotent: skip episodes that already exist (match by audio_url)
- Log progress and any parse warnings

### 7. Write tests
- **Task ID**: build-tests
- **Depends On**: build-models, build-feeds, build-views
- **Assigned To**: models-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Create `apps/podcast/tests/__init__.py`
- Test model creation and constraints (unique_together, ordering)
- Test RSS feed output structure and content type
- Test public views return 200 with correct templates
- Test import command with a fixture XML snippet

### 8. Final validation
- **Task ID**: validate-all
- **Depends On**: build-admin, build-feeds, build-views, build-import, build-tests
- **Assigned To**: podcast-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all tests: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v`
- Verify admin loads without errors
- Verify feed.xml output matches expected RSS structure
- Verify all success criteria met
- Generate final report

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/ -v` — All podcast tests pass
- `uv run python manage.py check --deploy 2>&1 | grep -i error` — No deployment errors
- `uv run python manage.py makemigrations --check --dry-run` — No missing migrations
- `python -c "from apps.podcast.models import Podcast, Episode; print('Models import OK')"` — Models importable

---

## Resolved Questions

1. **Episode numbering strategy**: Strict chronological by pubDate, renumber 1-N. All previous episode numbers are replaced.

2. **Original series name preservation**: Series name merged as title prefix (e.g. "Cardiovascular Health: Lifestyle Foundations"). No separate series field needed.

3. **Feed URL structure**: `/podcast/{slug}/feed.xml` confirmed.

4. **Companion resources**: Single `companion_resources` JSONField for flexible companion URLs. Report and sources stored as TextFields in DB, served at deterministic URLs.

5. **Ordering**: By `episode_number`, not `published_at`. Re-releases bump `published_at` to bust player caches but keep their position.

6. **RSS generation**: Compiled from DB via Django template, cached. Replicates existing feed.xml structure.
