---
status: Ready
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
49 published episodes exist on disk across 7 series, with ~700+ text artifacts. The workflow ends with a `git push` to a GitHub repo. A separate `import_podcast_feed` command reads the RSS feed to create bare Episode records. There is no connection between "produce an episode" and "Episode exists in the database."

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
- **Episode lifecycle fields**: `status` (draft/in_progress/complete) tracks work; `published_at` controls visibility; `episode_number` auto-assigned
- **`start_episode` command**: Pulls a draft Episode from production DB, creates local working directory with pre-populated files
- **`publish_episode` command**: Reads local files, populates Episode fields + creates artifacts. Runs against local DB first (test), then production DB (real publish)
- **Deterministic renderers**: RSS feed and episode detail page both render rich show notes from Episode fields via a shared template tag — no separate update tool needed
- **Backfill import**: One-time command to import 49 existing episodes across 3 podcasts from the research repo

### Data Flow

```
PRODUCTION (Render DB)          LOCAL MACHINE                 GITHUB (research repo)
┌──────────────────┐            ┌──────────────────┐          ┌──────────────────┐
│                  │            │                  │          │ research.yuda.me │
│ Episode(draft)   │ start_    │ pending-episodes/│          │                  │
│  - number (auto) │─episode──►│  - p1-brief.md   │          │ (binary files    │
│  - slug ✓        │ (pulls)   │  - logs/         │          │  served here     │
│  - title ✓       │           │  - research/     │          │  until S3)       │
│  - description   │           │                  │ git push │                  │
│                  │           │ Phases 1-11      │────────►│ *.mp3, cover.png │
│                  │ publish_  │ (unchanged)      │ (archive)│                  │
│ Episode(complete)│◄─episode─│                  │          │                  │
│  + report_text   │ (pushes)  │                  │          │                  │
│  + transcript    │           └──────────────────┘          └──────────────────┘
│  + chapters      │
│  + sources_text  │           Test publish targets local DB first.
│  + audio_url     │           Production publish: DATABASE_URL=<prod>
│  + published_at  │
│  + Artifacts     │           ┌──────────────────────────────────────┐
│    - p2-*.md     │           │ Deterministic renderers (from DB)    │
│    - p3-briefing │──────────►│ /podcast/<slug>/feed.xml  (RSS+cache)│
│    - plans/logs  │           │ /podcast/<slug>/<ep>/     (HTML page)│
│                  │           │ Both use {% episode_show_notes %}    │
└──────────────────┘           └──────────────────────────────────────┘
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

Episode already has `Publishable` (gives `published_at`). Add a `status` field for the production lifecycle. The `status` field tracks **work completion**, while `published_at` controls **visibility** (when the episode goes live in the feed and on the site).

```python
class Episode(Timestampable, Publishable, Expirable):
    STATUS_CHOICES = [
        ("draft", "Draft"),
        ("in_progress", "In Progress"),
        ("complete", "Complete"),
    ]
    status = CharField(max_length=20, choices=STATUS_CHOICES, default="draft")
    # ... existing fields ...
```

- **draft**: Created via intake form or admin. Has number, slug, title, description (requirements). No audio or research yet.
- **in_progress**: `start_episode` has been run. Local workflow underway.
- **complete**: `publish_episode` has been run. All content fields and artifacts populated.

**Visibility is determined solely by `published_at`:**
- The `publish_episode` command sets `status="complete"` AND `published_at=now()`.
- The feed view and episode list filter on `published_at` (not status) — same as the existing `Publishable` pattern.
- A "staged" episode is one with `status="complete"` but `published_at` not yet set (future: scheduled publishing).
- Queries for live episodes: `filter(published_at__isnull=False).filter(Q(unpublished_at__isnull=True) | Q(unpublished_at__lt=F("published_at")))`  — unchanged from current behavior.

Make `audio_url` optional (blank=True) so draft episodes can exist without audio:

```python
audio_url = URLField(blank=True)  # currently required — change to optional
```

**Side effect:** The existing `import_podcast_feed` command uses `audio_url` as the idempotency key (skips episodes where `audio_url` already exists in the DB). With `blank=True`, draft episodes will have `audio_url=""`. The import command's dedup check needs a guard to skip blank values: `filter(audio_url=audio_url).exclude(audio_url="")`.

**Auto-assign episode numbers:** Override `save()` to auto-assign the next available `episode_number` for the podcast when not explicitly set:

```python
def save(self, *args, **kwargs):
    if self.episode_number is None:
        max_num = (
            Episode.objects.filter(podcast=self.podcast)
            .aggregate(max_num=models.Max("episode_number"))["max_num"]
            or 0
        )
        self.episode_number = max_num + 1
    super().save(*args, **kwargs)
```

Change `episode_number` to `null=True, blank=True` so it can be omitted on creation. The `unique_together` constraint still ensures no duplicates after assignment. The intake form (future) will use this auto-assignment; manual override via admin remains possible.

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
5. Updates Episode `status` to `complete` and sets `published_at`
6. Reports summary: fields populated, artifacts created, any warnings

Flags:
- `--dry-run` — preview without writing
- `--verbose` — show each file being processed
- `--skip-status-check` — allow re-publishing (for updates/corrections)

### 5. Deterministic Renderers (Feed + Episode HTML)

Both the RSS feed and the episode detail page are **deterministic outputs from the database**. No separate "update" tool is needed — the views render directly from Episode fields.

#### RSS Feed View

`PodcastFeedView` already exists at `apps/podcast/views/feed_views.py` with the URL route at `/podcast/<slug>/feed.xml`. The template is at `apps/public/templates/podcast/feed.xml`. Changes needed:

1. **Add caching** — 5-10 min cache TTL
2. **Add `xmlns:content` namespace** and `<content:encoded>` element — renders rich HTML show notes inline in the feed (what `update_feed.py` used to generate for the static feed)
3. **Add Podcasting 2.0 chapter/transcript URLs** — `<podcast:chapters>` and `<podcast:transcript>` elements when data is available
4. **Keep existing `published_at` filter** — the existing Publishable-based filter is correct and is now the canonical visibility gate

#### Episode Detail Page

The existing `EpisodeDetailView` and template (`episode_detail.html`) already render from the database. Enhance the template to render the rich show notes content that `update_feed.py` used to generate:
- Overview section from `description`
- "What You'll Learn" bullets from `show_notes` or structured data in `companion_resources`
- Key timestamps from `chapters` JSON
- Sources from `sources_text`
- Full report link via `report_text`

This replaces the static `report.html` and `transcript.html` files — those are now derivable views, not stored artifacts.

#### Show Notes Renderer

Create a shared template include or template tag (`{% episode_show_notes episode %}`) that renders the rich HTML show notes from Episode fields. Used by both:
- The feed template (inside `<content:encoded>`)
- The episode detail template (on the page)

This replaces the `generate_content_encoded()` function from `update_feed.py` — same output, but driven by Django template rendering instead of a standalone script.

### 6. Backfill Import Command

One-time command to import existing 49 episodes from the research repo at `/Users/tomcounsell/src/research/podcast/episodes/`. Creates Podcast records as needed and imports all episodes with their artifacts.

```bash
uv run python manage.py backfill_episodes --source-dir /Users/tomcounsell/src/research/podcast/episodes/ --dry-run
```

**Podcast splitting:** The research repo has 7 series. Two become separate private podcasts; the rest go into the public "Yudame Research" podcast.

| Series directory | Podcast | is_public | Episodes |
|---|---|---|---|
| `active-recovery` | Yudame Research | true | 4 |
| `algorithms-for-life` | Yudame Research | true | 10 |
| `building-a-micro-school` | Yudame Research | true | 9 |
| `cardiovascular-health` | Yudame Research | true | 6 |
| `kindergarten-first-principles` | Yudame Research | true | 6 |
| `solomon-islands-telecom-series` | Solomon Islands Telecom | false | 6 |
| `stablecoin-series` | Stablecoin | false | 8 |

This mapping is hardcoded in the backfill command (it's a one-time migration, not a reusable abstraction).

What it does:
1. Creates/gets Podcast records (Yudame Research, Solomon Islands Telecom, Stablecoin)
2. Walks series → episode directories, assigns each to the correct Podcast
3. Creates Episode records with auto-assigned `episode_number` (in directory sort order within each podcast)
4. For each episode, runs the same file→field and file→artifact logic as `publish_episode`
5. Handles legacy naming normalization (see table below)
6. Sets `status="complete"` and `published_at` from file timestamps or `publish.md`
7. Reports: podcasts created, episodes imported, artifacts created, any warnings

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

**New files:**
- `apps/podcast/models/episode_artifact.py` — new model
- `apps/podcast/management/commands/start_episode.py`
- `apps/podcast/management/commands/publish_episode.py`
- `apps/podcast/management/commands/backfill_episodes.py`
- `apps/podcast/templatetags/podcast_tags.py` — add `episode_show_notes` inclusion tag (file exists, extend it)

**Modified files:**
- `apps/podcast/models/episode.py` — add `status` field, auto-assign `episode_number`, make `audio_url` optional
- `apps/podcast/models/__init__.py` — export `EpisodeArtifact`
- `apps/podcast/admin.py` — artifact inline on Episode, status in list_display
- `apps/podcast/views/feed_views.py` — add caching, `content:encoded` via show notes tag
- `apps/public/templates/podcast/feed.xml` — add `xmlns:content` namespace, `content:encoded`, chapter/transcript tags
- `apps/public/templates/podcast/episode_detail.html` — use `{% episode_show_notes %}` for rich content
- Migrations for EpisodeArtifact + Episode status/episode_number/audio_url changes

**Note:** `apps/podcast/views/feed_views.py` and `apps/podcast/urls.py` already exist — no new view file or URL route needed.

**Obsoleted by this work:**
- `apps/podcast/tools/update_feed.py` — writes to a static `feed.xml` file in the research repo. Its `generate_content_encoded()` logic is replaced by the `{% episode_show_notes %}` template tag. Don't delete it in this plan; flag for cleanup later.

## Rabbit Holes

- **Don't build artifact type taxonomy** — title-based identification is enough for now
- **Don't store binary files in the database** — mp3/png stay on GitHub Pages (S3 migration is a separate issue)
- **Don't store rendered HTML as files** — `report.html` and `transcript.html` are replaced by deterministic Django views; no static HTML artifacts needed
- **Don't build the intake form** — that's a separate feature; for now draft episodes are created via Django admin
- **Don't migrate the podcast domain** — the old feed on research.yuda.me stays live; Apple/Spotify point updates are a manual step later
- **Don't try to unify old and new naming** — just normalize to predictable paths in the backfill
- **Don't delete `apps/podcast/tools/update_feed.py` yet** — it's obsoleted by the dynamic feed but may be useful as reference during implementation; flag for cleanup after this ships

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
Management commands are self-documenting via `--help`. Update the CLAUDE.md Podcast section to add entries for the new commands and feed view to the existing tables.

### Inline Documentation
- [ ] Docstring on `EpisodeArtifact` model explaining purpose and title conventions
- [ ] Docstring on each management command explaining usage and flags
- [ ] Docstring on feed view explaining caching and namespace support

## Success Criteria

- [ ] `EpisodeArtifact` model exists with `episode`, `title`, `content`, `metadata` fields
- [ ] Episode has `status` field (draft/in_progress/complete), auto-assigned `episode_number`, and optional `audio_url`
- [ ] Migrations run cleanly
- [ ] Admin shows artifacts as inline on Episode, status in list_display
- [ ] `start_episode` creates local directory from draft Episode in DB
- [ ] `publish_episode` populates Episode fields + creates artifacts, sets `status="complete"` and `published_at`
- [ ] `publish_episode` is idempotent (re-running updates, doesn't duplicate)
- [ ] `backfill_episodes` imports 49 episodes into 3 podcasts (Yudame Research public, Solomon Islands private, Stablecoin private)
- [ ] RSS feed renders `content:encoded` with rich show notes via `{% episode_show_notes %}`
- [ ] Episode detail page renders the same rich show notes
- [ ] `import_podcast_feed` still works with blank `audio_url` on draft episodes
- [ ] Feed filters on `published_at` (Publishable pattern), not on status
- [ ] Test publish on local DB works, then production publish works
- [ ] Tests pass for new models, commands, and feed changes
- [ ] Inline documentation on all new code
- [ ] CLAUDE.md podcast tables updated with new commands

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

- **Builder (renderers)**
  - Name: feed-builder
  - Role: Build shared show notes template tag, enhance RSS feed with content:encoded and caching, enhance episode detail page
  - Agent Type: builder
  - Resume: true

- **Builder (tests)**
  - Name: test-builder
  - Role: Write tests for new models, commands, and feed changes
  - Agent Type: test-engineer
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
- Add `status` field (draft/in_progress/complete) to Episode
- Make `episode_number` nullable with auto-assign in `save()`
- Make `audio_url` blank=True
- Register EpisodeArtifact in `__init__.py`
- Add ArtifactInline to Episode admin, status to list_display
- Guard `import_podcast_feed` dedup logic against blank `audio_url`
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
- Hardcode series→podcast mapping (5 public series → Yudame Research, solomon-islands → private, stablecoin → private)
- Create Podcast records as needed, create Episode records with auto-assigned numbers (directory sort order)
- Reuse publish logic with legacy naming normalization
- Set `status="complete"` and `published_at` for all backfilled episodes

### 5. Build deterministic renderers (feed + episode HTML)
- **Task ID**: build-feed
- **Depends On**: build-models
- **Assigned To**: feed-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with commands)
- Create `{% episode_show_notes %}` inclusion tag in `apps/podcast/templatetags/podcast_tags.py` — renders rich HTML (overview, timestamps, sources, report link) from Episode fields
- Modify `apps/podcast/views/feed_views.py` — add caching
- Modify `apps/public/templates/podcast/feed.xml` — add `xmlns:content` namespace, `content:encoded` using show notes tag, `podcast:chapters`, `podcast:transcript`
- Modify `apps/public/templates/podcast/episode_detail.html` — use `{% episode_show_notes %}` for rich content section

### 6. Write tests
- **Task ID**: build-tests
- **Depends On**: build-publish, build-start, build-backfill, build-feed
- **Assigned To**: test-builder
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `EpisodeArtifact` model tests to `apps/podcast/tests/test_models.py` (creation, unique_together, status field)
- Add command tests to `apps/podcast/tests/test_commands.py` (start_episode, publish_episode, backfill_episodes — at least dry-run paths)
- Update `apps/podcast/tests/test_feeds.py` — verify status-based filtering, `content:encoded` presence
- Update `apps/podcast/tests/test_import_command.py` — verify blank `audio_url` guard

### 7. Validate all components
- **Task ID**: validate-all
- **Depends On**: build-tests
- **Assigned To**: plan-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify migrations apply cleanly
- Run full test suite
- Dry-run backfill against research repo
- Diff dynamic feed against existing static feed.xml
- Test start_episode → publish_episode round-trip on local DB
- Verify all success criteria met

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings uv run python manage.py makemigrations --check --dry-run` — no pending migrations
- `DJANGO_SETTINGS_MODULE=settings uv run python manage.py migrate` — migrations apply
- `DJANGO_SETTINGS_MODULE=settings uv run python manage.py backfill_episodes --source-dir /Users/tomcounsell/src/research/podcast/episodes/ --dry-run` — backfill preview
- `DJANGO_SETTINGS_MODULE=settings uv run python manage.py publish_episode /path/to/episode --dry-run` — publish preview
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/ -v` — all tests pass
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_models.py -k "artifact" -v` — artifact model tests
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_feeds.py -v` — feed tests including content:encoded
- `curl http://localhost:8000/podcast/yudame-research/feed.xml` — dynamic feed renders

---

## Open Questions

None — all questions resolved.

### Decided

- **Agents stay file-based.** The podcast workflow continues writing files to disk in `pending-episodes/`. No changes to subagent skills.
- **Test publish first, then production.** `publish_episode` runs against whatever DB Django is configured with. Test locally, then `DATABASE_URL=<prod>` for real.
- **Direct DB connection for production.** No API endpoint needed — Render provides external PostgreSQL connection strings.
- **Slug is the stable identifier.** Matching between disk and DB is always by `(podcast_slug, episode_slug)`, never by episode number.
- **No duplication between Episode fields and artifacts.** `report.md`, `sources.md`, transcript, and chapters become Episode field values. All other files become artifacts. A file goes to one place, never both.
- **Episode numbers auto-assigned.** Model `save()` assigns next available number for the podcast. Backfill imports in directory sort order. Intake form (future) relies on auto-assignment.
- **Status tracks work, `published_at` tracks visibility.** Status is draft → in_progress → complete. `published_at` (from Publishable mixin) is the sole gate for feed inclusion and site visibility. No "published" status — an episode can be complete but staged (not yet live).
- **Feed and episode page are deterministic renderers.** Both the RSS feed `content:encoded` and the episode detail HTML page render from the same Episode fields via a shared `{% episode_show_notes %}` template tag. No separate update tool needed. Replaces `update_feed.py` and static `report.html`/`transcript.html`.
- **Three podcasts from backfill.** "Yudame Research" (public, 35 episodes from 5 series), "Solomon Islands Telecom" (private, 6 episodes), "Stablecoin" (private, 8 episodes). Mapping hardcoded in backfill command.
- **Backfill source: `/Users/tomcounsell/src/research/podcast/episodes/`** — single checkout of the research repo containing all 49 episodes across 7 series directories.
- **Binary files stay on GitHub Pages for now.** Audio and cover images continue to be served from `research.yuda.me`. URLs are stored in `audio_url` and `cover_image_url`. S3 migration is a separate issue.
- **Dynamic RSS feed from Django.** Feed is generated from the database, cached. The old static feed on research.yuda.me stays live until Apple/Spotify directory entries are manually updated.
- **Git push becomes archival.** The research repo push in Phase 12 continues for archiving binary files (mp3, cover.png) but is no longer the publishing mechanism.

### Deferred (separate issues)

- **Intake form**: Web form on production for creating draft episodes with reserved numbers and requirements
- **S3 migration**: Move mp3/cover.png from GitHub Pages to S3/cloud storage
- **Domain migration**: Update Apple Podcasts and Spotify to point to new feed URL
- **Cleanup `update_feed.py`**: Remove `apps/podcast/tools/update_feed.py` once the dynamic feed is verified in production
