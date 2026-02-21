#!/bin/bash
# Sync production podcast data to local database
# Usage: PRODUCTION_DATABASE_URL="postgres://..." ./scripts/sync_db_from_prod.sh
#
# Uses psql \COPY pipes instead of pg_dump to avoid version mismatch errors
# (e.g. local pg_dump 15 vs production PostgreSQL 17).

set -e

# Ensure PostgreSQL tools are on PATH
for PG_DIR in /opt/homebrew/opt/postgresql@*/bin /opt/homebrew/Cellar/postgresql@*/*/bin /usr/local/opt/postgresql@*/bin; do
    if [ -d "$PG_DIR" ]; then
        export PATH="$PG_DIR:$PATH"
        break
    fi
done

if ! command -v psql &>/dev/null; then
    echo "ERROR: psql not found. Install PostgreSQL: brew install postgresql@15"
    exit 1
fi

# Colors for output
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
NC='\033[0m' # No Color

echo -e "${YELLOW}=== Syncing Production Podcast Data to Local ===${NC}"
echo ""

# Check for PRODUCTION_DATABASE_URL
if [ -z "$PRODUCTION_DATABASE_URL" ]; then
    # Try loading from .env.prod
    if [ -f ".env.prod" ]; then
        PRODUCTION_DATABASE_URL=$(grep "^DATABASE_URL=" .env.prod | cut -d= -f2-)
    fi
fi

if [ -z "$PRODUCTION_DATABASE_URL" ]; then
    echo -e "${RED}ERROR: PRODUCTION_DATABASE_URL not set${NC}"
    echo ""
    echo "Options:"
    echo "  export PRODUCTION_DATABASE_URL='postgres://...'"
    echo "  # or place DATABASE_URL in .env.prod (auto-detected)"
    exit 1
fi

LOCAL_DATABASE_URL=${DATABASE_URL:-"postgres://$(whoami)@localhost:5432/cuttlefish"}
echo "Local database: $LOCAL_DATABASE_URL"
echo "Production:     ${PRODUCTION_DATABASE_URL%%@*}@***"
echo ""

# Tables to sync (order matters: parents before children)
TABLES=(
    "podcast_podcast"
    "podcast_podcastconfig"
    "podcast_episode"
    "podcast_episodeartifact"
    "podcast_episodeworkflow"
)

# Backup local data
BACKUP_FILE="/tmp/local_podcast_backup_$(date +%Y%m%d_%H%M%S).sql"
echo -e "${YELLOW}Backing up local data to $BACKUP_FILE${NC}"
pg_dump "$LOCAL_DATABASE_URL" \
    --data-only \
    --table=podcast_podcast \
    --table=podcast_podcastconfig \
    --table=podcast_episode \
    --table=podcast_episodeartifact \
    --table=podcast_episodeworkflow \
    > "$BACKUP_FILE" 2>/dev/null || echo "  (no existing data to backup)"

# Clear local podcast data (reverse order: children before parents)
echo -e "${YELLOW}Clearing local podcast data...${NC}"
psql "$LOCAL_DATABASE_URL" -q << 'SQL'
TRUNCATE TABLE podcast_episodeworkflow CASCADE;
TRUNCATE TABLE podcast_episodeartifact CASCADE;
TRUNCATE TABLE podcast_podcastconfig CASCADE;
TRUNCATE TABLE podcast_episode CASCADE;
TRUNCATE TABLE podcast_podcast CASCADE;
SQL

# Sync each table using \COPY pipes (works across PostgreSQL versions)
echo -e "${YELLOW}Copying production data to local...${NC}"
for TABLE in "${TABLES[@]}"; do
    printf "  %-30s" "$TABLE"
    RESULT=$(psql "$PRODUCTION_DATABASE_URL" -c "\COPY (SELECT * FROM $TABLE) TO STDOUT" \
        | psql "$LOCAL_DATABASE_URL" -c "\COPY $TABLE FROM STDIN" 2>&1)
    echo "$RESULT"
done

# Reset sequences so new inserts get correct IDs
echo -e "${YELLOW}Resetting sequences...${NC}"
psql "$LOCAL_DATABASE_URL" -q << 'SQL'
SELECT setval('podcast_podcast_id_seq', COALESCE((SELECT MAX(id) FROM podcast_podcast), 1));
SELECT setval('podcast_podcastconfig_id_seq', COALESCE((SELECT MAX(id) FROM podcast_podcastconfig), 1));
SELECT setval('podcast_episode_id_seq', COALESCE((SELECT MAX(id) FROM podcast_episode), 1));
SELECT setval('podcast_episodeartifact_id_seq', COALESCE((SELECT MAX(id) FROM podcast_episodeartifact), 1));
SELECT setval('podcast_episodeworkflow_id_seq', COALESCE((SELECT MAX(id) FROM podcast_episodeworkflow), 1));
SQL

# Verify
PODCAST_COUNT=$(psql "$LOCAL_DATABASE_URL" -t -c "SELECT COUNT(*) FROM podcast_podcast;" | xargs)
EPISODE_COUNT=$(psql "$LOCAL_DATABASE_URL" -t -c "SELECT COUNT(*) FROM podcast_episode;" | xargs)
ARTIFACT_COUNT=$(psql "$LOCAL_DATABASE_URL" -t -c "SELECT COUNT(*) FROM podcast_episodeartifact;" | xargs)

echo ""
echo -e "${GREEN}Sync complete!${NC}"
echo "  Podcasts:  $PODCAST_COUNT"
echo "  Episodes:  $EPISODE_COUNT"
echo "  Artifacts: $ARTIFACT_COUNT"
echo ""
echo "  Backup:    $BACKUP_FILE"
echo ""
echo -e "${GREEN}Local database now matches production.${NC}"
