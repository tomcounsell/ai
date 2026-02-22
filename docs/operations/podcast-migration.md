# Podcast Episode Migration Guide

**One-time migration** to import ~49 existing podcast episodes from the research repository into the Cuttlefish production database.

## Overview

This migration backfills historical episodes across 3 podcasts:
- **Yudame Research** (public) - 5 series with ~35 episodes
- **Solomon Islands Telecom** (private) - 1 series with ~8 episodes
- **Stablecoin** (private) - 1 series with ~6 episodes

The `backfill_episodes` management command:
1. Creates 3 `Podcast` records
2. Creates ~49 `Episode` records with all metadata
3. Creates ~200+ `EpisodeArtifact` records from research files
4. Sets all episodes to `status='complete'` with appropriate publish dates

**Idempotent**: Safe to re-run. Uses `update_or_create` for all records.

## Prerequisites

### 1. Locate Source Repository

The old research repo must be accessible locally at a known path:

```bash
# Example paths (adjust to your actual location):
/Users/valorengels/src/research/podcast/episodes/
/Users/tomcounsell/src/research/podcast/episodes/
```

Expected directory structure:
```
research/podcast/episodes/
├── active-recovery/
│   ├── ep01-understanding-recovery/
│   ├── ep02-sleep-optimization/
│   └── ...
├── algorithms-for-life/
├── building-a-micro-school/
├── cardiovascular-health/
├── kindergarten-first-principles/
├── solomon-islands-telecom-series/
└── stablecoin-series/
```

### 2. Local Database Ready

```bash
# Ensure local Postgres is running
psql -l | grep cuttlefish

# If needed, create database
createdb cuttlefish

# Run migrations
uv run python manage.py migrate
```

### 3. Production Database Access (for Phase 3)

You'll need the production `DATABASE_URL` from Render:

```bash
# Get it from Render dashboard or environment variables
# Format: postgres://username:password@host:port/database?sslmode=require
```

**WARNING**: Direct production database access requires caution. Consider using Render's dashboard if you're not comfortable with `psql` commands.

---

## Phase 1: Local Backfill

Import episodes into your local database first to verify the migration.

### Step 1: Dry Run (Preview)

Preview what will be created without making any changes:

```bash
uv run python manage.py backfill_episodes \
    --source-dir /Users/valorengels/src/research/podcast/episodes/ \
    --dry-run --verbose
```

**Expected output:**
```
[DRY RUN] No changes will be made
Source directory: /Users/valorengels/src/research/podcast/episodes/

Would create podcast: Yudame Research (yudame-research)
Would create podcast: Solomon Islands Telecom (solomon-islands-telecom)
Would create podcast: Stablecoin (stablecoin)

--- Series: active-recovery -> yudame-research ---
  Episode: ep01-understanding-recovery -> "Understanding Recovery"
    Would create episode
    ARTIFACT (dry-run): research/perplexity.md
    ARTIFACT (dry-run): research/briefing.md
    ...

=== Backfill Summary ===
Podcasts: 3 created, 0 found
Episodes: 49 created, 0 updated
Artifacts: 215 created, 0 updated
```

### Step 2: Run for Real

Execute the migration:

```bash
uv run python manage.py backfill_episodes \
    --source-dir /Users/valorengels/src/research/podcast/episodes/ \
    --verbose
```

**Expected output:**
```
Source directory: /Users/valorengels/src/research/podcast/episodes/

Created podcast: Yudame Research (yudame-research)
Created podcast: Solomon Islands Telecom (solomon-islands-telecom)
Created podcast: Stablecoin (stablecoin)

--- Series: active-recovery -> yudame-research ---
  Episode: ep01-understanding-recovery -> "Understanding Recovery"
    Created episode #1
    Fields: report, transcript, audio_url, duration_seconds
    Artifacts: 8 created, 0 updated
  ...

=== Backfill Summary ===
Podcasts: 3 created, 0 found
Episodes: 49 created, 0 updated
Artifacts: 215 created, 0 updated
```

### Step 3: Verify Local Import

```bash
# Start Django shell
uv run python manage.py shell
```

```python
from apps.podcast.models import Podcast, Episode, EpisodeArtifact

# Check podcast counts
Podcast.objects.count()
# Expected: 3

# Check episode counts
Episode.objects.count()
# Expected: ~49

# Check artifact counts
EpisodeArtifact.objects.count()
# Expected: ~215

# Verify a specific episode
ep = Episode.objects.filter(slug='ep01-understanding-recovery').first()
print(f"Title: {ep.title}")
print(f"Status: {ep.status}")
print(f"Audio: {ep.audio_url}")
print(f"Duration: {ep.duration_seconds}s")
print(f"Artifacts: {ep.artifacts.count()}")
```

### Step 4: Test RSS Feed Locally

```bash
# Start development server
uv run python manage.py runserver
```

Visit:
- `http://localhost:8000/podcast/` - Podcast listing
- `http://localhost:8000/podcast/yudame-research/` - Public RSS feed
- `http://localhost:8000/podcast/solomon-islands-telecom/` - Should require auth (private)

Verify:
- Episodes appear in feed
- Audio URLs are accessible
- Metadata is correct (title, description, publish date)

---

## Phase 2: Dump Local Database

Export the backfilled data for production import.

### Create Data Dump

```bash
# Export only podcast-related tables (data-only, no schema)
pg_dump -h localhost -U valorengels cuttlefish \
    --data-only \
    --table=podcast_podcast \
    --table=podcast_episode \
    --table=podcast_episodeartifact \
    --table=podcast_episodeworkflow \
    > podcast_migration.sql

# Verify the dump file
ls -lh podcast_migration.sql
# Should be several hundred KB

# Preview first 50 lines to verify it contains INSERT statements
head -n 50 podcast_migration.sql
```

**Expected content:**
```sql
--
-- PostgreSQL database dump
--

SET statement_timeout = 0;
...

COPY public.podcast_podcast (id, created_at, modified_at, title, slug, description, ...) FROM stdin;
1	2026-02-19 12:00:00+00	2026-02-19 12:00:00+00	Yudame Research	yudame-research	...
2	2026-02-19 12:00:00+00	2026-02-19 12:00:00+00	Solomon Islands Telecom	solomon-islands-telecom	...
...
```

---

## Phase 3: Restore to Production

**CAUTION**: This modifies the production database. Ensure you have a backup strategy.

### Option A: Direct psql Restore (Recommended if you have access)

```bash
# Set production DATABASE_URL (get from Render dashboard)
export PRODUCTION_DATABASE_URL="postgres://username:password@host:port/database?sslmode=require"

# Verify connection
psql $PRODUCTION_DATABASE_URL -c "SELECT COUNT(*) FROM podcast_podcast;"

# Import data
psql $PRODUCTION_DATABASE_URL < podcast_migration.sql

# Verify import
psql $PRODUCTION_DATABASE_URL -c "SELECT COUNT(*) FROM podcast_podcast;"
# Expected: 3

psql $PRODUCTION_DATABASE_URL -c "SELECT COUNT(*) FROM podcast_episode;"
# Expected: ~49
```

### Option B: Via Render Dashboard (If direct access blocked)

If your production database doesn't allow direct external connections:

1. Go to Render dashboard: `https://dashboard.render.com/d/dpg-{your-db-id}`
2. Click "Shell" tab to access database console
3. Copy-paste the SQL from `podcast_migration.sql` in chunks
4. Alternatively, use Render's "Restore from Backup" if you've created a backup

### Verify Production Import

```bash
# SSH to production web service or use Render shell
uv run python manage.py shell

# Run verification
from apps.podcast.models import Podcast, Episode
print(f"Podcasts: {Podcast.objects.count()}")
print(f"Episodes: {Episode.objects.count()}")

# Test RSS feed endpoint
curl https://ai.yuda.me/podcast/yudame-research/ | head -50
```

---

## Phase 4: Future Sync (Production → Local)

Create a reusable script to sync production podcast data back to local for development.

### Create Sync Script

```bash
# Create scripts directory if needed
mkdir -p /Users/valorengels/src/cuttlefish/scripts

# Create sync script
cat > /Users/valorengels/src/cuttlefish/scripts/sync_db_from_prod.sh << 'EOF'
#!/bin/bash
# Sync production podcast data to local database
# Usage: ./scripts/sync_db_from_prod.sh

set -e

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Syncing Production Podcast Data to Local ===${NC}"

# Check for PRODUCTION_DATABASE_URL
if [ -z "$PRODUCTION_DATABASE_URL" ]; then
    echo "ERROR: PRODUCTION_DATABASE_URL not set"
    echo "Get it from Render dashboard and export it:"
    echo "  export PRODUCTION_DATABASE_URL='postgres://...'"
    exit 1
fi

# Confirm local database
LOCAL_DATABASE_URL=${DATABASE_URL:-"postgres://valorengels@localhost:5432/cuttlefish"}
echo "Local database: $LOCAL_DATABASE_URL"

# Create backup of local data (optional)
BACKUP_FILE="/tmp/local_podcast_backup_$(date +%Y%m%d_%H%M%S).sql"
echo -e "${YELLOW}Creating local backup: $BACKUP_FILE${NC}"
pg_dump $LOCAL_DATABASE_URL \
    --data-only \
    --table=podcast_podcast \
    --table=podcast_episode \
    --table=podcast_episodeartifact \
    --table=podcast_episodeworkflow \
    > $BACKUP_FILE 2>/dev/null || echo "No existing data to backup"

# Dump production data
TEMP_FILE="/tmp/prod_podcast_data.sql"
echo -e "${YELLOW}Dumping production database...${NC}"
pg_dump $PRODUCTION_DATABASE_URL \
    --data-only \
    --table=podcast_podcast \
    --table=podcast_episode \
    --table=podcast_episodeartifact \
    --table=podcast_episodeworkflow \
    > $TEMP_FILE

# Clear local podcast data (to avoid conflicts)
echo -e "${YELLOW}Clearing local podcast data...${NC}"
psql $LOCAL_DATABASE_URL << 'SQL'
TRUNCATE TABLE podcast_episodeartifact CASCADE;
TRUNCATE TABLE podcast_episodeworkflow CASCADE;
TRUNCATE TABLE podcast_episode CASCADE;
TRUNCATE TABLE podcast_podcast CASCADE;
SQL

# Restore production data to local
echo -e "${YELLOW}Restoring production data to local...${NC}"
psql $LOCAL_DATABASE_URL < $TEMP_FILE

# Verify
PODCAST_COUNT=$(psql $LOCAL_DATABASE_URL -t -c "SELECT COUNT(*) FROM podcast_podcast;")
EPISODE_COUNT=$(psql $LOCAL_DATABASE_URL -t -c "SELECT COUNT(*) FROM podcast_episode;")

echo -e "${GREEN}Sync complete!${NC}"
echo "  Podcasts: $PODCAST_COUNT"
echo "  Episodes: $EPISODE_COUNT"
echo ""
echo "Local backup saved to: $BACKUP_FILE"

# Clean up temp file
rm $TEMP_FILE
EOF

# Make executable
chmod +x /Users/valorengels/src/cuttlefish/scripts/sync_db_from_prod.sh
```

### Usage

```bash
# Set production DATABASE_URL
export PRODUCTION_DATABASE_URL="postgres://username:password@host:port/database?sslmode=require"

# Run sync
./scripts/sync_db_from_prod.sh
```

**Expected output:**
```
=== Syncing Production Podcast Data to Local ===
Local database: postgres://valorengels@localhost:5432/cuttlefish
Creating local backup: /tmp/local_podcast_backup_20260219_163045.sql
Dumping production database...
Clearing local podcast data...
Restoring production data to local...
Sync complete!
  Podcasts: 3
  Episodes: 49

Local backup saved to: /tmp/local_podcast_backup_20260219_163045.sql
```

---

## Verification Checklist

After completing the migration, verify:

### Database Level
- [ ] 3 `Podcast` records exist
- [ ] ~49 `Episode` records exist with `status='complete'`
- [ ] ~215 `EpisodeArtifact` records exist
- [ ] All episodes have `published_at` timestamps
- [ ] Episode numbers are sequential per podcast

### Application Level
- [ ] RSS feeds render at `/podcast/{podcast-slug}/`
- [ ] Audio URLs are accessible (Supabase storage)
- [ ] Episode pages load with metadata
- [ ] Private podcasts require authentication
- [ ] Public podcasts are accessible without auth

### Admin Interface
- [ ] Podcasts visible in `/staff/podcast/podcast/`
- [ ] Episodes visible in `/staff/podcast/episode/`
- [ ] Artifacts visible in episode detail pages
- [ ] Can edit episode metadata

### Feed Validation
```bash
# Test with podcast feed validator
curl https://ai.yuda.me/podcast/yudame-research/ | \
    xmllint --format - | \
    head -100

# Should show valid RSS 2.0 with <item> elements
```

---

## Rollback Plan

If the migration fails or produces unexpected results:

### Local Rollback

```bash
# Drop and recreate database
dropdb cuttlefish
createdb cuttlefish

# Re-run migrations
uv run python manage.py migrate

# Start over with backfill
uv run python manage.py backfill_episodes \
    --source-dir /path/to/research/podcast/episodes/
```

### Production Rollback

**Option 1: Re-import from backup**

If Render has automatic backups enabled:
1. Go to Render database dashboard
2. Click "Backups" tab
3. Select most recent backup before migration
4. Click "Restore"

**Option 2: Truncate and re-import**

```bash
# Clear podcast tables
psql $PRODUCTION_DATABASE_URL << 'SQL'
TRUNCATE TABLE podcast_episodeartifact CASCADE;
TRUNCATE TABLE podcast_episodeworkflow CASCADE;
TRUNCATE TABLE podcast_episode CASCADE;
TRUNCATE TABLE podcast_podcast CASCADE;
SQL

# Re-import corrected dump
psql $PRODUCTION_DATABASE_URL < podcast_migration_fixed.sql
```

---

## Troubleshooting

### Issue: "Source directory not found"

```bash
# Verify path exists
ls -la /Users/valorengels/src/research/podcast/episodes/

# If not, locate the research repo
find ~/src -name "podcast" -type d
```

### Issue: "Podcast not in cache" warning

The command expects series directories to match `SERIES_TO_PODCAST` mapping in `backfill_episodes.py`. If you have additional series:

1. Check the series name: `ls /path/to/research/podcast/episodes/`
2. Add mapping to `SERIES_TO_PODCAST` in the command file
3. Add podcast definition to `PODCAST_DEFINITIONS`

### Issue: Production database refuses connection

```bash
# Verify SSL mode is required
psql "$PRODUCTION_DATABASE_URL"

# If SSL issues, add sslmode parameter
psql "postgres://user:pass@host:port/db?sslmode=require"
```

### Issue: Duplicate key violations during restore

This happens if data already exists. Either:

1. **Truncate first** (see Rollback Plan above)
2. **Skip conflicts** by wrapping imports in `ON CONFLICT DO NOTHING`
3. **Use upsert** via Django ORM instead of raw SQL

---

## Post-Migration Tasks

After successful migration:

1. **Update episode metadata** if needed via admin interface
2. **Generate any missing cover art** using podcast tools
3. **Validate RSS feeds** with podcast directories (Apple, Spotify)
4. **Update Notion tracker** to mark migration complete
5. **Archive research repo** or mark episodes as "migrated"
6. **Document any custom changes** made during migration

---

## Related Documentation

- [`apps/podcast/management/commands/backfill_episodes.py`](../../apps/podcast/management/commands/backfill_episodes.py) - Migration command source
- [`docs/features/podcast-services.md`](../features/podcast-services.md) - Podcast service layer API
- [`docs/reference/podcast-workflow-diagram.md`](../reference/podcast-workflow-diagram.md) - Episode workflow diagrams
- [`CLAUDE.md`](../../CLAUDE.md#podcast-production-system) - Podcast production system overview
