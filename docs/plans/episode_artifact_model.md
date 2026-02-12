---
status: Planning
type: feature
appetite: Medium
owner: Tom
created: 2026-02-12
tracking:
---

# Episode Artifacts & Publish Workflow

## Problem

The podcast system has a Django app with Episode records imported from RSS, but:
1. Episode records have empty `report_text`, `transcript`, `chapters`, `sources_text` fields
2. Research artifacts (p2-*, p3-briefing, content plans, prompts, review notes) have no home in the database
3. The 12-phase episode workflow produces files locally but has no path to get them into the production database
4. Episode numbers are assigned by import order rather than intentionally at creation time
5. The RSS feed is a static file in a GitHub repo rather than generated from the database

**Current behavior:**
38 published episodes exist on disk with ~700+ text artifacts. The workflow ends with a `git push` to a GitHub repo. A separate `import_podcast_feed` command reads the RSS feed to create bare Episode records. There is no connection between "produce an episode" and "Episode exists in the database."

**Desired outcome:**
A complete local-to-production publish pipeline. Episodes are created in the production database first (with a reserved number), the local workflow produces files as before, and a publish command pushes everything to the database. The RSS feed is generated dynamically from Django.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM

**Interactions:**
- PM check-ins: 1-2 (scope alignment on publish workflow and feed generation)
- Review rounds: 1

## Prerequisites

No prerequisites — the podcast app and Episode model already exist.

## Solution

### Key Elements

- **`EpisodeArtifact` model**: Generic document store for research, plans, prompts, logs — any text file tied to an episode
- **`start_episode` command**: Pulls a draft Episode from production DB, creates local working directory with pre-populated files
- **`publish_episode` command**: Reads local files, populates Episode fields + creates artifacts. Runs against local DB first (test), then production DB (real publish)
- **Dynamic RSS feed**: Django view generates feed.xml from Podcast + Episode querysets, cached at the URL
- **Backfill import**: One-time command to import existing episodes from the research repo

### Data Flow

```
PRODUCTION (Render DB)          LOCAL MACHINE                 GITHUB (research repo)
┌──────────────────┐            ┌──────────────────┐          ┌──────────────────┐
│                  │            │                  │          │ research.yuda.me │
│ Episode(draft)   │ start_    │ pending-episodes/│          │                  │
│  - number ✓      │─episode──►│  - p1-brief.md   │          │ (binary files    │
│  - slug ✓        │ (pulls)   │  - logs/         │          │  served here     │
│  - title ✓       │           │  - research/     │          │  until S3)       │
│  - description   │           │                  │ git push │                  │
│                  │           │ Phases 1-11      │────────►│ *.mp3, cover.png │
│                  │ publish_  │ (unchanged)      │ (archive)│                  │
│ Episode(pub'd)   │◄─episode─│                  │          │                  │
│  + report_text   │ (pushes)  │                  │          │                  │
│  + transcript    │           └──────────────────┘          └──────────────────┘
│  + chapters      │
│  + sources_text  │           Test publish targets local DB first.
│  + audio_url     │           Production publish: DATABASE_URL=<prod>
│  + Artifacts     │
│    - p2-*.md     │           ┌──────────────────┐
│    - p3-briefing │           │ Dynamic RSS Feed  │
│    - plans/logs  │──────────►│ /podcast/feed.xml │ (cached Django view)
│                  │           │ Serves from DB    │
└──────────────────┘           └──────────────────┘
```

### 1. EpisodeArtifact Model

```python
class EpisodeArtifact(Timestampable):
    episode = ForeignKey(Episode, CASCADE, related_name="artifacts")
    title = CharField(max_length=200)          # e.g. "research/p2-perplexity.md"
    content = TextField()                       # markdown/text content
    metadata = JSONField(default=dict, blank=True)  # optional structured data

    class Meta:
        ordering = ["title"]
        unique_together = [("episode", "title")]
```

Design decisions:
- **No `artifact_type` field** — the workflow is still evolving. Title-based identification is enough. Query by pattern (e.g. `title__startswith="research/p2-"`) for any grouping need.
- **`unique_together` on (episode, title)** — makes publish idempotent.
- **`metadata` JSONField** — catch-all for structured data (chapter timestamps, keywords, etc.).

### 2. Episode Model Changes

Episode already has `Publishable` (gives `published_at`). Add a `status` field for the draft→published lifecycle:

```python
class Episode(Timestampable, Publishable, Expirable):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("in_progress", "In Progress"),
        ("published", "Published"),
    ]
    status = CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    # ... existing fields ...
```

- **draft**: Created via intake form or admin. Has number, slug, title, description (requirements). No audio or research yet.
- **in_progress**: `start_episode` has been run. Local workflow underway.
- **published**: `publish_episode` has been run. All fields populated, `published_at` set.

Make `audio_url` optional (blank=True) so draft episodes can exist without audio:

```python
audio_url = URLField(blank=True)  # currently required — change to optional
```

### 3. `start_episode` Management Command

Pulls a draft Episode from the database, creates local working directory.

```bash
# Connect to production to pull the draft episode
DATABASE_URL=<prod> uv run python manage.py start_episode --podcast algorithms-for-life --episode ep10-game-theory
```

What it does:
1. Looks up Episode by `(podcast_slug, episode_slug)` — must be status=draft
2. Creates `apps/podcast/pending-episodes/{podcast_slug}/{episode_slug}/`
3. Creates subdirectories: `research/`, `research/documents/`, `logs/`, `tmp/`
4. Pre-populates `research/p1-brief.md` from Episode description (the requirements from intake)
5. Creates `logs/prompts.md` with episode metadata (number, title, date)
6. Creates `sources.md` template
7. Updates Episode status to `in_progress`
8. Prints summary: episode number, title, local directory path

### 4. `publish_episode` Management Command

Reads local files, populates Episode + creates artifacts.

```bash
# Test publish against local DB
uv run python manage.py publish_episode apps/podcast/pending-episodes/algorithms-for-life/ep10-game-theory/

# Real publish against production
DATABASE_URL=<prod> uv run python manage.py publish_episode apps/podcast/pending-episodes/algorithms-for-life/ep10-game-theory/
```

What it does:
1. Identifies episode from directory path (`{podcast_slug}/{episode_slug}`)
2. Looks up Episode by `(podcast_slug, episode_slug)`
3. **Populates Episode fields** (not stored as artifacts):
   - `report.md` → `report_text`
   - `sources.md` → `sources_text`
   - `*_transcript.json` → `transcript` (extracted text)
   - `*_chapters.json` → `chapters` (JSON string)
   - Audio file metadata → `audio_url`, `audio_duration_seconds`, `audio_file_size_bytes`
   - Cover image → `cover_image_url`
   - `published_at` → set to now (or from `publish.md` if present)
4. **Creates EpisodeArtifact records** for everything else:
   - All `research/*.md` files
   - `content_plan.md`, `research-prompt.md`, `review-notes.md`, `publish.md`
   - `logs/*.md` files
   - `companion/*.md` files
   - Any other `.md` file
5. Updates Episode status to `published`
6. Reports summary: fields populated, artifacts created, any warnings

Flags:
- `--dry-run` — preview without writing
- `--verbose` — show each file being processed
- `--skip-status-check` — allow re-publishing (for updates/corrections)

### 5. Dynamic RSS Feed View

Django view that generates RSS XML from the database. Replaces the static `feed.xml`.

```python
# apps/podcast/views/feed.py
class PodcastFeedView(View):
    """Generate RSS feed XML for a podcast from database records."""

    def get(self, request, podcast_slug):
        podcast = get_object_or_404(Podcast, slug=podcast_slug, is_public=True)
        episodes = podcast.episodes.filter(status="published").order_by("-published_at")
        # Generate XML with iTunes/Podcasting 2.0 namespace support
        # Return with content_type="application/rss+xml"
```

- URL: `/podcast/<podcast_slug>/feed.xml`
- Cached (5-10 min cache, invalidated on publish)
- Includes all iTunes namespace elements, Podcasting 2.0 tags (chapters, transcript)
- Show notes generated from `Episode.show_notes` or `Episode.report_text`

### 6. Backfill Import Command

One-time command to import existing 38 episodes from the research repo. Reuses the same logic as `publish_episode` but handles legacy naming.

```bash
uv run python manage.py backfill_episodes --source-dir /path/to/research/podcast/episodes/ --dry-run
```

What it does:
1. Walks series → episode directories
2. Matches to existing Episode records by `(podcast_slug, episode_slug)`
3. For each matched episode, runs the same file→field and file→artifact logic as `publish_episode`
4. Handles legacy naming normalization (see table below)
5. Reports: matched, unmatched, artifacts created

### File-to-Storage Mapping

Files that populate **Episode fields** (not stored as artifacts):

| File | Episode field |
|---|---|
| `report.md` | `report_text` |
| `sources.md` (episode root) | `sources_text` |
| `*_transcript.json` | `transcript` (extracted text) |
| `*_chapters.json` | `chapters` (JSON string) |
| `*.mp3` metadata | `audio_duration_seconds`, `audio_file_size_bytes` |

Files that become **EpisodeArtifact** records:

| File pattern | Title in DB |
|---|---|
| `research/p1-brief.md` | `research/p1-brief.md` |
| `research/p2-perplexity.md` | `research/p2-perplexity.md` |
| `research/p2-chatgpt.md` | `research/p2-chatgpt.md` |
| `research/p2-claude.md` | `research/p2-claude.md` |
| `research/p2-gemini.md` | `research/p2-gemini.md` |
| `research/p2-grok.md` | `research/p2-grok.md` |
| `research/p2-gpt-researcher.md` | `research/p2-gpt-researcher.md` |
| `research/p3-briefing.md` | `research/p3-briefing.md` |
| `research/sources.md` | `research/sources.md` |
| `content_plan.md` | `content_plan.md` |
| `research-prompt.md` | `research-prompt.md` |
| `review-notes.md` | `review-notes.md` |
| `publish.md` | `publish.md` |
| `logs/prompts.md` | `logs/prompts.md` |
| `logs/metadata.md` | `logs/metadata.md` |
| `logs/quality_scorecard.md` | `logs/quality_scorecard.md` |
| `companion/*.md` | `companion/{filename}.md` |
| Any other `.md` file | relative path from episode root |

Files **skipped**:

| File | Reason |
|---|---|
| `*.mp3` | Served from GitHub Pages; URL stored in `audio_url` |
| `cover.png` | Served from GitHub Pages; URL stored in `cover_image_url` |
| `report.html`, `transcript.html` | Derivable from markdown |
| `*_chapters.txt` | Redundant with JSON version |
| `tmp/*` | Temporary working files |

### Legacy Naming Normalization (backfill only)

| Old pattern | Normalized title |
|---|---|
| `research-results.md` (at root) | `research/research-results.md` |
| `research-briefing.md` (at root) | `research/research-briefing.md` |
| `research-perplexity.md` (at root) | `research/research-perplexity.md` |
| `research-gemini.md` (at root) | `research/research-gemini.md` |
| `cross-validation.md` (at root) | `research/cross-validation.md` |
| `perplexity_phase1_raw.md` (at root) | `research/perplexity_phase1_raw.md` |
| `prompts.md` (at root) | `logs/prompts.md` |
| `metadata.md` (at root) | `logs/metadata.md` |

### Technical Approach

- `apps/podcast/models/episode_artifact.py` — new model
- `apps/podcast/models/episode.py` — add `status` field, make `audio_url` optional
- `apps/podcast/admin.py` — artifact inline on Episode, status in list display
- `apps/podcast/views/feed.py` — dynamic RSS feed view
- `apps/podcast/urls.py` — feed URL route
- `apps/podcast/management/commands/start_episode.py`
- `apps/podcast/management/commands/publish_episode.py`
- `apps/podcast/management/commands/backfill_episodes.py`
- Migrations for EpisodeArtifact + Episode status field + audio_url blank=True

## Rabbit Holes

- **Don't build artifact type taxonomy** — title-based identification is enough for now
- **Don't store binary files in the database** — mp3/png stay on GitHub Pages (S3 migration is a separate issue)
- **Don't render HTML** — `report.html` and `transcript.html` are derivable from markdown
- **Don't build the intake form** — that's a separate feature; for now draft episodes are created via Django admin
- **Don't migrate the podcast domain** — the old feed on research.yuda.me stays live; Apple/Spotify point updates are a manual step later
- **Don't try to unify old and new naming** — just normalize to predictable paths in the backfill

## Risks

### Risk 1: Slug matching failures during backfill
**Impact:** Some episodes on disk won't match database records, leaving artifacts unimported.
**Mitigation:** Backfill command logs all unmatched episodes. Can be re-run after fixing slugs. `--verbose` flag shows attempted matches.

### Risk 2: Feed parity with static feed.xml
**Impact:** Dynamic feed could differ from the existing static feed, causing podcast player issues.
**Mitigation:** Generate dynamic feed, diff against the existing static feed.xml, and fix any discrepancies before switching.

### Risk 3: Production DB access from local machine
**Impact:** `start_episode` and `publish_episode` require network access to Render PostgreSQL.
**Mitigation:** Render provides external connection strings. Test connectivity before relying on workflow.

## No-Gos (Out of Scope)

- No intake web form (future feature — see linked issue)
- No S3 migration for binary files (future feature — see linked issue)
- No domain migration from research.yuda.me
- No full-text search across artifacts
- No artifact versioning
- No changes to the 12-phase workflow itself (agents still write files to disk)
- No API endpoints for artifacts

## Update System

No update system changes required — these are database model additions and management commands.

## Agent Integration

No agent integration required — the podcast workflow continues to produce files locally. The publish command is run manually after the workflow completes.

## Documentation

### Feature Documentation
No feature docs needed — management commands are self-documenting via `--help`.

### Inline Documentation
- [ ] Docstring on `EpisodeArtifact` model explaining purpose and title conventions
- [ ] Docstring on each management command explaining usage and flags
- [ ] Docstring on feed view explaining caching and namespace support

## Success Criteria

- [ ] `EpisodeArtifact` model exists with `episode`, `title`, `content`, `metadata` fields
- [ ] Episode has `status` field (draft/in_progress/published) and optional `audio_url`
- [ ] Migrations run cleanly
- [ ] Admin shows artifacts as inline on Episode, status in list display
- [ ] `start_episode` creates local directory from draft Episode in DB
- [ ] `publish_episode` populates Episode fields + creates artifacts from local files
- [ ] `publish_episode` is idempotent (re-running updates, doesn't duplicate)
- [ ] `backfill_episodes` imports existing episodes from research repo
- [ ] Dynamic RSS feed view generates valid XML matching existing feed structure
- [ ] Test publish on local DB works, then production publish works
- [ ] Inline documentation on all new code

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: model-builder
  - Role: Create EpisodeArtifact model, Episode status field, migrations, admin
  - Agent Type: database-architect
  - Resume: true

- **Builder (commands)**
  - Name: command-builder
  - Role: Create start_episode, publish_episode, backfill_episodes commands
  - Agent Type: builder
  - Resume: true

- **Builder (feed)**
  - Name: feed-builder
  - Role: Create dynamic RSS feed view with iTunes/Podcasting 2.0 support
  - Agent Type: builder
  - Resume: true

- **Validator**
  - Name: plan-validator
  - Role: Verify all components work together
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create models and migrations
- **Task ID**: build-models
- **Depends On**: none
- **Assigned To**: model-builder
- **Agent Type**: database-architect
- **Parallel**: true
- Create `apps/podcast/models/episode_artifact.py`
- Add `status` field and `audio_url` blank=True to Episode
- Register EpisodeArtifact in `__init__.py`
- Add ArtifactInline to Episode admin, status to list_display
- Generate migrations

### 2. Create publish_episode command
- **Task ID**: build-publish
- **Depends On**: build-models
- **Assigned To**: command-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/management/commands/publish_episode.py`
- Implement file→Episode field mapping and file→artifact creation
- Support `--dry-run`, `--verbose`, `--skip-status-check`
- Idempotent via `update_or_create`

### 3. Create start_episode command
- **Task ID**: build-start
- **Depends On**: build-models
- **Assigned To**: command-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-publish)
- Create `apps/podcast/management/commands/start_episode.py`
- Pull draft Episode from DB, create local directory, pre-populate files
- Update Episode status to in_progress

### 4. Create backfill_episodes command
- **Task ID**: build-backfill
- **Depends On**: build-publish
- **Assigned To**: command-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/podcast/management/commands/backfill_episodes.py`
- Reuse publish logic with legacy naming normalization
- Walk series→episode dirs, match by slug, import artifacts

### 5. Create dynamic RSS feed view
- **Task ID**: build-feed
- **Depends On**: build-models
- **Assigned To**: feed-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with commands)
- Create `apps/podcast/views/feed.py`
- Add URL route in `apps/podcast/urls.py`
- iTunes namespace, Podcasting 2.0 tags, chapter/transcript links
- Cache with appropriate TTL

### 6. Validate all components
- **Task ID**: validate-all
- **Depends On**: build-publish, build-start, build-backfill, build-feed
- **Assigned To**: plan-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify migrations apply cleanly
- Dry-run backfill against research repo
- Diff dynamic feed against existing static feed.xml
- Test start_episode → publish_episode round-trip on local DB
- Verify all success criteria met

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings uv run python manage.py makemigrations --check --dry-run` — no pending migrations
- `DJANGO_SETTINGS_MODULE=settings uv run python manage.py migrate` — migrations apply
- `DJANGO_SETTINGS_MODULE=settings uv run python manage.py backfill_episodes --source-dir /path/to/episodes --dry-run` — backfill preview
- `DJANGO_SETTINGS_MODULE=settings uv run python manage.py publish_episode /path/to/episode --dry-run` — publish preview
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` — all tests pass
- `curl http://localhost:8000/podcast/yudame-research/feed.xml` — dynamic feed renders

---

## Open Questions

None — all architectural decisions resolved.

### Decided

- **Agents stay file-based.** The podcast workflow continues writing files to disk in `pending-episodes/`. No changes to subagent skills.
- **Test publish first, then production.** `publish_episode` runs against whatever DB Django is configured with. Test locally, then `DATABASE_URL=<prod>` for real.
- **Direct DB connection for production.** No API endpoint needed — Render provides external PostgreSQL connection strings.
- **Slug is the stable identifier.** Matching between disk and DB is always by `(podcast_slug, episode_slug)`, never by episode number.
- **No duplication between Episode fields and artifacts.** `report.md`, `sources.md`, transcript, and chapters become Episode field values. All other files become artifacts. A file goes to one place, never both.
- **Episode numbers assigned on production at intake.** The intake form (future feature) creates a draft Episode with a reserved number. `start_episode` pulls it down. The local workflow never assigns episode numbers.
- **Binary files stay on GitHub Pages for now.** Audio and cover images continue to be served from `research.yuda.me`. URLs are stored in `audio_url` and `cover_image_url`. S3 migration is a separate issue.
- **Dynamic RSS feed from Django.** Feed is generated from the database, cached. The old static feed on research.yuda.me stays live until Apple/Spotify directory entries are manually updated.
- **Git push becomes archival.** The research repo push in Phase 12 continues for archiving binary files (mp3, cover.png) but is no longer the publishing mechanism.

### Deferred (separate issues)

- **Intake form**: Web form on production for creating draft episodes with reserved numbers and requirements
- **S3 migration**: Move mp3/cover.png from GitHub Pages to S3/cloud storage
- **Domain migration**: Update Apple Podcasts and Spotify to point to new feed URL
