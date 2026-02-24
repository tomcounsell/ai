---
status: Planning
type: chore
appetite: Large
owner: Valor
created: 2026-02-24
tracking:
---

# Redis Migration: Consolidate All Persistence into Popoto

## Problem

The system uses a split persistence architecture: SQLite for durable message/link/chat storage (`~/.valor/telegram_history.db`), Redis/Popoto for real-time operational state (jobs, sessions, events), and flat JSON/JSONL files for session logs and crash history. This creates:

**Current behavior:**
- Dual-write pattern: every incoming message writes to SQLite then attempts a best-effort Redis mirror that silently swallows errors
- SQLite opens a new connection and runs 13 DDL statements on *every function call* (no caching)
- No WAL mode or busy_timeout — concurrent bridge writes + CLI reads cause `SQLITE_BUSY` errors
- Session content (full conversation transcripts with tool calls) is not persisted at all — only sparse JSON snapshots at lifecycle transitions
- Six dead 0-byte `.db` files in `data/` from earlier iterations
- Two separate CLIs (`valor-history`, `valor-telegram`) both query the same SQLite through different paths

**Desired outcome:**
- Single persistence layer: Redis (persistent, RDB snapshots) via Popoto for all structured data
- Session transcripts saved as `.txt` files on disk with metadata in Popoto for queryability
- 3-month TTL on all data with automated cleanup
- Eliminate SQLite entirely from the message/link/chat path
- Eliminate the dual-write pattern and silent error swallowing

## Appetite

**Size:** Large

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites — Redis is already running with RDB persistence enabled, Popoto is already installed and used for 5 models. Sufficient RAM available.

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `redis-cli ping` | Redis server accessible |
| Popoto installed | `python -c "import popoto; print(popoto.__version__)"` | ORM available |
| Persistent Redis | `redis-cli CONFIG GET save` | RDB snapshots configured |

## Solution

### Key Elements

- **Promoted Popoto models**: Upgrade `TelegramMessage` from mirror to source of truth; new `Link` and `Chat` models replace SQLite tables
- **Session transcript logging**: `SessionLog` Popoto model with metadata + `.txt` file path for full transcript content
- **Unified data layer**: Single `tools/telegram_history/__init__.py` module rewritten to use Popoto instead of SQLite
- **TTL cleanup**: Shared cleanup job deleting records older than 3 months across all models
- **Data migration script**: One-time script to import existing SQLite records into Redis

### Flow

**Message arrives** → bridge stores via `TelegramMessage.create()` → bridge registers chat via `Chat.create()` → links extracted via `Link.create()`

**Session starts** → `SessionLog.create()` with log_path → turns appended to `.txt` file → session ends → `SessionLog` updated with final metadata

**CLI query** → `valor-history search "query"` → Popoto `TelegramMessage.query.filter(chat_id=X)` → app-level text filtering → results

### Technical Approach

- **Search strategy**: Application-level filtering. Fetch messages by `chat_id` + `timestamp` range from SortedField (efficient ZRANGEBYSCORE), then filter content in Python. At current scale (1,352 messages, ~11 chats) this is fast. RediSearch can be added later as a separate enhancement if volume warrants it.
- **Aggregations**: Computed in Python over filtered results (COUNT, MIN, MAX on timestamp). Simple loops over small result sets.
- **Pagination**: Python list slicing on query results.
- **Chat name resolution**: `Chat` model with `chat_name` as KeyField for exact match; fall back to `query.all()` with case-insensitive Python filtering for partial match.
- **Link upsert**: Get-or-create pattern — `Link.query.filter(url=X, chat_id=Y)` then create or update.
- **Session transcripts**: Append-only `.txt` files at `logs/sessions/{session_id}/transcript.txt`. Each line is `[timestamp] role: content`. The `SessionLog` Popoto model stores metadata (session_id, project, status, turn_count, tool_call_count, log_path) for querying without reading the file.
- **TTL enforcement**: A `cleanup_expired()` classmethod on each model using the `BridgeEvent.cleanup_old()` pattern. SortedField on timestamp enables efficient range queries. Called from daydream or a periodic job. TTL = 90 days (3 months).
- **KeyField mutation**: Follow existing delete-and-recreate pattern (already proven in `RedisJob` status transitions).

## Rabbit Holes

- **RediSearch/FTS**: Don't install redis-stack or add FTS5 as part of this migration. App-level search is sufficient at current scale. This is a separate future enhancement.
- **Async Popoto everywhere**: The bridge uses async but not all Popoto calls need to be async. Don't refactor synchronous CLI paths to async — use `query.filter()` (sync) where it works.
- **Migrating knowledge.db**: The knowledge/embeddings database is a different concern (vector search). Out of scope.
- **Connection pooling**: Popoto manages its own Redis connection. Don't add a custom pooling layer.

## Risks

### Risk 1: Data loss during migration
**Impact:** Loss of 1,352 messages and 56 links of historical data
**Mitigation:** Migration script runs as additive (writes to Redis while SQLite remains untouched). Verify counts match before removing SQLite code. Keep SQLite backup for 30 days post-migration.

### Risk 2: Search performance at scale
**Impact:** App-level text filtering becomes slow as message volume grows (O(n) per search)
**Mitigation:** SortedField time-range filtering narrows the working set first. At current growth rate (~1,352 messages over ~1 month), it would take years to reach 100K. Monitor and add RediSearch when needed.

### Risk 3: Redis memory growth
**Impact:** Unbounded growth fills available RAM
**Mitigation:** 3-month TTL with automated cleanup. At ~5KB per message, 3 months of data at 50 messages/day = ~22MB. Well within available RAM.

### Risk 4: Bridge restart during migration
**Impact:** Messages could be written to old SQLite path if code is partially deployed
**Mitigation:** Migration is atomic per commit — switch all writes in one commit, restart bridge immediately after.

## No-Gos (Out of Scope)

- RediSearch / full-text search indexing (future enhancement)
- Migrating `knowledge.db` (embeddings/vector search — different concern)
- Changing the CLI interface or command structure (same commands, different backend)
- Migrating flat-file crash history or daydream state (low value, low urgency)
- Adding new query capabilities beyond what SQLite currently provides

## Update System

- No update script changes required — Redis is already a dependency on all machines
- The `~/.valor/telegram_history.db` path will become unused after migration; the update script does not reference it
- No new dependencies to propagate (Popoto already installed everywhere)
- Migration script should be run once on each machine after code update: `python scripts/migrate_sqlite_to_redis.py`

## Agent Integration

- The `valor-history` MCP tool (`tools/telegram_history/__init__.py`) is the primary interface — it gets rewritten internally but its function signatures and return types remain identical
- No changes to `.mcp.json` — the same tool module is registered, just backed by Redis instead of SQLite
- The bridge (`bridge/telegram_bridge.py`) calls `store_message()`, `register_chat()`, `store_link()` — same function names, same arguments
- `bridge/context.py` calls `get_recent_messages()`, `get_link_by_url()`, `store_link()` — same interface
- Integration test: verify `valor-history search "test"` returns results from Redis after migration
- Integration test: verify bridge stores and retrieves messages end-to-end through Popoto

## Documentation

### Feature Documentation
- [ ] Update `docs/features/telegram-history.md` to reflect Redis backend
- [ ] Create `docs/features/session-transcripts.md` describing the new session logging
- [ ] Update `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstrings on all new/modified Popoto models
- [ ] Migration script usage in header comments

## Success Criteria

- [ ] All `tools/telegram_history/__init__.py` functions work against Redis/Popoto (same signatures, same return shapes)
- [ ] `TelegramMessage` is the source of truth (no SQLite writes)
- [ ] New `Link` model replaces SQLite links table
- [ ] New `Chat` model replaces SQLite chats table
- [ ] `SessionLog` model created with transcript `.txt` file writing
- [ ] Session transcripts capture messages, tool calls, and tool results
- [ ] 3-month TTL cleanup implemented and tested
- [ ] Data migration script migrates all existing SQLite records to Redis
- [ ] Both CLIs (`valor-history`, `valor-telegram`) work unchanged against new backend
- [ ] Bridge stores messages, links, chats via Popoto (no dual-write)
- [ ] `bridge/context.py` reads from Popoto successfully
- [ ] Zero 0-byte dead `.db` files remain in `data/`
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: models-builder
  - Role: Create new Popoto models (Link, Chat, SessionLog) and update TelegramMessage
  - Agent Type: database-architect
  - Resume: true

- **Builder (data-layer)**
  - Name: data-layer-builder
  - Role: Rewrite `tools/telegram_history/__init__.py` to use Popoto, update bridge integration points
  - Agent Type: builder
  - Resume: true

- **Builder (session-logging)**
  - Name: session-log-builder
  - Role: Implement SessionLog model + transcript file writing, integrate with bridge/SDK
  - Agent Type: builder
  - Resume: true

- **Builder (migration)**
  - Name: migration-builder
  - Role: Write SQLite-to-Redis migration script and TTL cleanup job
  - Agent Type: builder
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Verify all CLIs, bridge, and context module work against new backend
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update feature docs and create session transcript docs
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

See plan template for full list.

## Step by Step Tasks

### 1. Create/Update Popoto Models
- **Task ID**: build-models
- **Depends On**: none
- **Assigned To**: models-builder
- **Agent Type**: database-architect
- **Parallel**: true
- Create `models/link.py` with `Link` Popoto model (fields: link_id AutoKey, url KeyField, chat_id KeyField, message_id Field, domain KeyField, sender KeyField, status KeyField, timestamp SortedField, final_url Field, title Field, description Field, tags ListField, notes Field, ai_summary Field)
- Create `models/chat.py` with `Chat` Popoto model (fields: chat_id UniqueKeyField, chat_name KeyField, chat_type KeyField, updated_at SortedField with auto_now)
- Create `models/session_log.py` with `SessionLog` Popoto model (fields: session_id UniqueKeyField, project_key KeyField, status KeyField, chat_id KeyField, sender KeyField, started_at SortedField partitioned by project_key, completed_at Field, turn_count IntField, tool_call_count IntField, log_path Field, summary Field, branch_name Field, work_item_slug Field)
- Update `models/__init__.py` to export new models
- Add `cleanup_expired(max_age_days=90)` classmethod to TelegramMessage, Link, Chat, and SessionLog

### 2. Rewrite Data Layer
- **Task ID**: build-data-layer
- **Depends On**: build-models
- **Assigned To**: data-layer-builder
- **Agent Type**: builder
- **Parallel**: false
- Rewrite `tools/telegram_history/__init__.py` — replace all SQLite code with Popoto queries
- `store_message()`: Use `TelegramMessage.create()` directly (remove dual-write)
- `search_history()`: `TelegramMessage.query.filter(chat_id=X)` with timestamp SortedField range, then Python content filtering and relevance scoring
- `get_recent_messages()`: Filter by chat_id + SortedField timestamp range, slice for limit
- `get_chat_stats()`: Compute COUNT/MIN/MAX in Python over `TelegramMessage.query.filter(chat_id=X)`
- `store_link()`: Get-or-create pattern with `Link.query.filter(url=X, chat_id=Y)`
- `search_links()`: Filter by KeyFields (domain, sender, status) + Python text filtering
- `list_links()`: `Link.query.filter()` with Python slicing for pagination
- `get_link_by_url()`: `Link.query.filter(url=X)` with timestamp age check
- `update_link()`: Delete-and-recreate pattern for KeyField-safe mutation
- `register_chat()`: `Chat` get-or-create with `chat_id` UniqueKeyField
- `list_chats()`: `Chat.query.all()` with message count computed via TelegramMessage queries
- `resolve_chat_id()`: Exact match via KeyField, then Python fallback for case-insensitive/partial
- `search_all_chats()`: `TelegramMessage.query.filter()` with timestamp range, Python content filtering
- Extract duplicated relevance scoring into a shared `_score_relevance(query, content, timestamp, max_age_days)` function
- Remove all `sqlite3` imports, `_get_db_connection()`, and `DEFAULT_DB_PATH`
- Keep all function signatures and return dict shapes identical

### 3. Implement Session Transcript Logging
- **Task ID**: build-session-logging
- **Depends On**: build-models
- **Assigned To**: session-log-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-data-layer)
- Create `bridge/session_transcript.py` with:
  - `start_transcript(session_id, project_key, chat_id, sender)` — creates SessionLog + opens .txt file
  - `append_turn(session_id, role, content, tool_name=None, tool_input=None)` — appends to .txt, increments counters in SessionLog
  - `complete_transcript(session_id, status="completed")` — finalizes SessionLog metadata
- Transcript file format: `logs/sessions/{session_id}/transcript.txt`, each entry as `[ISO timestamp] ROLE: content`
- Tool calls logged as: `[timestamp] TOOL_CALL: tool_name(input_summary)`
- Tool results logged as: `[timestamp] TOOL_RESULT: result_summary` (truncated to 2000 chars)
- Integrate with `agent/sdk_client.py` to capture turns as they happen
- Integrate with `agent/job_queue.py` to start/complete transcripts at job lifecycle boundaries
- Replace `bridge/session_logs.py` snapshot approach with transcript approach (keep old module as deprecated fallback for one release)

### 4. Update Bridge Integration Points
- **Task ID**: build-bridge-integration
- **Depends On**: build-data-layer
- **Assigned To**: data-layer-builder
- **Agent Type**: builder
- **Parallel**: false
- Update `bridge/telegram_bridge.py` lines 596-618: `store_message()` and `register_chat()` calls (same function names, no change needed if signatures preserved)
- Update `bridge/telegram_bridge.py` lines 964-970: Remove the 1000-char truncation on Valor's responses — store full content (Redis can handle 20KB per field)
- Update `bridge/telegram_bridge.py` lines 1094-1101: Same for job queue send callback
- Update `bridge/context.py` lines 276, 433, 504: Verify `get_recent_messages()`, `get_link_by_url()`, `store_link()` work against new backend
- Remove the try/except Redis mirror block from old `store_message()` (no longer needed — Redis IS the store)

### 5. Write Migration Script
- **Task ID**: build-migration
- **Depends On**: build-models
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-data-layer)
- Create `scripts/migrate_sqlite_to_redis.py`:
  - Read all rows from `~/.valor/telegram_history.db` messages table
  - Create `TelegramMessage` for each (skip if already exists by chat_id + message_id)
  - Read all rows from links table → create `Link` models
  - Read all rows from chats table → create `Chat` models
  - Print migration stats (counts per table, errors, skipped)
  - `--dry-run` flag to preview without writing
  - `--verify` flag to compare counts between SQLite and Redis after migration
- Add `cleanup_expired()` calls to `scripts/daydream.py` maintenance cycle (90-day TTL for all models)

### 6. Clean Up Legacy Files
- **Task ID**: build-cleanup
- **Depends On**: build-data-layer, build-migration
- **Assigned To**: data-layer-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete dead `.db` files: `data/messages.db`, `data/system.db`, `data/telegram_cache.db`, `data/telegram_history.db`, `data/valor_messages.db`, `data/valor.db`
- Update `monitoring/health.py` `check_database()` to check Redis connectivity instead of `data/valor.db`
- Remove `models/telegram.py` if fully superseded (TelegramMessage model moves or stays — consolidate)
- Update `tests/tools/test_telegram_history.py` to test against Redis (use test db isolation)

### 7. Validate Integration
- **Task ID**: validate-integration
- **Depends On**: build-data-layer, build-session-logging, build-bridge-integration, build-cleanup
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify `valor-history search "test"` returns results
- Verify `valor-history recent --group "Dev: Valor"` returns messages
- Verify `valor-history groups` lists all chats
- Verify `valor-history links` returns stored links
- Verify `valor-telegram read --chat "Dev: Valor"` works
- Verify bridge stores incoming message → retrievable via CLI
- Verify session transcript `.txt` file is written during a session
- Verify `SessionLog` model has correct metadata after session completes
- Verify 90-day cleanup deletes old records correctly
- Run full test suite: `pytest tests/`

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-integration
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/telegram-history.md` — Redis backend, no more SQLite
- Create `docs/features/session-transcripts.md` — transcript format, SessionLog model, where files live
- Update `docs/features/README.md` index table
- Add migration instructions to plan doc or separate `docs/migration/redis-migration.md`

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Validation Commands

- `redis-cli ping` — Redis is running
- `python -c "from models.link import Link; print(Link.query.count())"` — Link model works
- `python -c "from models.chat import Chat; print(Chat.query.count())"` — Chat model works
- `python -c "from models.telegram import TelegramMessage; print(TelegramMessage.query.count())"` — Messages accessible
- `python -c "from models.session_log import SessionLog; print(SessionLog.query.count())"` — SessionLog works
- `valor-history groups` — CLI reads from Redis
- `valor-history search "test"` — Search works
- `pytest tests/tools/test_telegram_history.py -v` — Unit tests pass
- `pytest tests/ -v` — Full suite passes
- `python scripts/migrate_sqlite_to_redis.py --verify` — Migration verified

---

## Open Questions

1. **Response truncation**: Currently Valor's responses are truncated to 1,000 chars before storage. Should we store the full response now that Redis can handle it (up to 20KB per field), or keep a cap? Recommendation: store full response, cap at 20KB.

2. **Session transcript retention**: Should transcript `.txt` files follow the same 3-month TTL as Redis data, or keep them longer on disk since they're cheap? Recommendation: same 3-month TTL for consistency.

3. **Existing `AgentSession` model**: The new `SessionLog` model overlaps significantly with `AgentSession`. Should we merge them into one model, or keep `AgentSession` for real-time status tracking and `SessionLog` for the transcript reference? Recommendation: merge — `SessionLog` subsumes `AgentSession` with the addition of `log_path` and transcript-specific fields.
