---
status: Planning
type: chore
appetite: Large
owner: Valor
created: 2026-03-12
tracking: https://github.com/tomcounsell/ai/issues/295
last_comment_id:
---

# Strengthen Popoto Model Relationships and Naming

## Problem

Redis models are largely independent flat records with no formal relationships between them. As the system prepares for PM chat groups (where a project maps to multiple chats), the implicit chat_id-to-project assumption breaks down. Additionally, AgentSession has accumulated fields that belong on other models (message metadata, classification), and carries legacy field names from the old RedisJob queue system.

**Current behavior:**
- `project_key` is missing from TelegramMessage, Link, DeadLetter, Chat, and ReflectionRun -- all rely on `chat_id` as a proxy for project
- AgentSession stores message-specific fields (`has_media`, `media_type`, `youtube_urls`, `non_youtube_urls`, `reply_to_msg_id`, `classification_type`, `classification_confidence`) that describe the triggering Telegram message, not the session
- `job_id` is a legacy name from the old RedisJob queue system (it is the record's primary key)
- `session_id` stores the Claude Code session identifier but the name is ambiguous with the model name itself
- `last_transition_at` overlaps with `last_activity` and can be derived from the `history` list
- ReflectionRun is global, not scoped to a project

**Desired outcome:**
- All models carry `project_key` for direct project association
- Message metadata lives on TelegramMessage where it belongs
- AgentSession and TelegramMessage cross-reference each other
- AgentSession field names are clear and unambiguous
- Timestamp fields are rationalized (no redundant tracking)

## Prior Art

No prior issues found related to this work. This is the first systematic model relationship effort.

## Data Flow

### Current: Bridge -> AgentSession -> Enrichment

1. **Entry point**: Telegram event arrives at `bridge/telegram_bridge.py`
2. **Bridge extracts metadata**: `has_media`, `media_type`, `youtube_urls`, `non_youtube_urls`, `reply_to_msg_id` (lines ~654-672)
3. **Bridge creates TelegramMessage**: Only stores `msg_id`, `chat_id`, `message_id`, `direction`, `sender`, `content`, `timestamp`, `message_type`, `session_id` (line ~583)
4. **Bridge enqueues AgentSession**: All media/URL/classification metadata stored on the session (lines ~1158-1164)
5. **Job worker reads from AgentSession**: `_execute_job()` in `agent/job_queue.py` reads `job.has_media`, `job.youtube_urls`, etc. (line ~1402)
6. **Enrichment**: `bridge/enrichment.py:enrich_message()` receives these fields as parameters and processes them

### Target: Bridge -> TelegramMessage -> AgentSession -> Enrichment

1. **Entry point**: Same Telegram event
2. **Bridge extracts metadata**: Same extraction logic
3. **Bridge creates enriched TelegramMessage**: Stores all metadata on TelegramMessage (media, URLs, classification, reply_to)
4. **Bridge creates AgentSession**: Stores `trigger_message_id` referencing the TelegramMessage, no media/URL fields
5. **Job worker resolves TelegramMessage**: Loads the trigger message via `trigger_message_id` to get enrichment parameters
6. **Enrichment**: Same enrichment function, but parameters sourced from TelegramMessage instead of AgentSession

## Architectural Impact

- **Data ownership**: Message metadata ownership moves from AgentSession to TelegramMessage -- this is the correct domain model since these fields describe the message, not the session
- **New dependencies**: AgentSession gains a reference to TelegramMessage (`trigger_message_id`); TelegramMessage gains a back-reference (`agent_session_id`)
- **Interface changes**: `enrich_message()` parameters unchanged -- only the source of those parameters shifts from AgentSession fields to TelegramMessage fields
- **Coupling**: Slightly increases coupling between AgentSession and TelegramMessage, but this reflects a real relationship that already exists implicitly
- **Reversibility**: High -- Popoto/Redis fields can be added/removed without migrations. Old fields can coexist with new ones during transition.

## Appetite

**Size:** Large

**Team:** Solo dev, code reviewer

**Interactions:**
- PM check-ins: 1-2 (scope alignment on migration strategy, confirm multi-chat-per-project requirements)
- Review rounds: 2+ (model changes touch many files, need careful review)

This is a large refactor touching 7 models, ~30 consuming files, and requiring a data backfill script. The migration must be backward-compatible (add new fields first, then cut over readers, then remove old fields).

## Prerequisites

No prerequisites -- this work has no external dependencies beyond the existing codebase and Redis.

## Solution

### Key Elements

- **Model field additions**: Add `project_key` to TelegramMessage, Link, DeadLetter, Chat, ReflectionRun
- **TelegramMessage enrichment**: Move media, URL, classification, and reply fields from AgentSession to TelegramMessage
- **Cross-references**: Add `trigger_message_id` on AgentSession and `agent_session_id` on TelegramMessage
- **AgentSession renames**: `job_id` -> `id`, `session_id` -> `claude_code_session_id`
- **Timestamp consolidation**: Remove `last_transition_at`, promote `started_at` to SortedField
- **Migration script**: One-time backfill of new fields from existing data

### Flow

**Phase 1 (additive)**: Add new fields with defaults to all models -> Push to production -> Verify no breakage

**Phase 2 (writers)**: Update bridge to write media/URL/classification to TelegramMessage -> Update enqueue to set `trigger_message_id` -> Push

**Phase 3 (readers)**: Update job worker to read from TelegramMessage via `trigger_message_id` -> Update enrichment call sites -> Push

**Phase 4 (cleanup)**: Remove deprecated fields from AgentSession -> Remove `last_transition_at` -> Apply renames -> Push

### Technical Approach

- **Popoto has no formal migrations**: Add new fields with `null=True` defaults so existing records remain valid
- **Renames require careful coordination**: `job_id` -> `id` is an `AutoKeyField` -- verify Popoto handles this rename correctly or keep `job_id` as an alias
- **Backfill script**: Query all AgentSessions, for each one with `has_media`/`youtube_urls`/etc., find the corresponding TelegramMessage by `chat_id` + `message_id` and copy fields over
- **`project_key` population**: For existing records, derive from `chat_id` using the `config/projects.json` mapping

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Verify `enrich_message()` still handles None/missing fields gracefully when reading from TelegramMessage
- [ ] Verify job worker handles missing `trigger_message_id` (for sessions created before migration)
- [ ] Check `bridge/telegram_bridge.py` exception handlers around TelegramMessage creation (lines ~573-583)

### Empty/Invalid Input Handling
- [ ] Test that sessions with `trigger_message_id=None` (pre-migration) still work through the enrichment pipeline
- [ ] Test that TelegramMessage with all-None media fields passes through enrichment without error
- [ ] Verify `project_key=None` on old records does not break queries or filters

### Error State Rendering
- [ ] Verify monitoring/session_status.py still displays session info correctly after field renames
- [ ] Verify reflections script handles `project_key` on ReflectionRun correctly

## Rabbit Holes

- **Full ORM relationship support in Popoto**: Popoto is a simple Redis ORM without JOIN-like operations. Do not try to build a relationship layer -- use explicit field references and application-level lookups instead.
- **Renaming Redis keys in-place**: Popoto's `AutoKeyField` generates Redis keys from the field name. Renaming `job_id` to `id` may change the Redis key format. If so, keep `job_id` as the actual field and add an `id` property alias -- do NOT attempt a Redis key migration.
- **Backfilling all historical data**: Only backfill records from the last 90 days (matching the cleanup TTL). Older records will be cleaned up naturally.
- **Multi-chat-per-project implementation**: This plan adds `project_key` to Chat to prepare for it, but actually implementing the multi-chat routing is a separate project.

## Risks

### Risk 1: AutoKeyField rename breaks Redis key generation
**Impact:** All existing AgentSession records become inaccessible if the Redis key prefix changes
**Mitigation:** Test rename behavior in a staging Redis instance first. If key format changes, use a property alias (`id` -> `job_id`) instead of renaming the field.

### Risk 2: Phased migration leaves inconsistent state during transition
**Impact:** During the transition window, some enrichment data lives on AgentSession and some on TelegramMessage, causing confusion
**Mitigation:** Phase 3 (reader cutover) includes a fallback: if `trigger_message_id` is None, read from AgentSession fields directly (backward-compatible path).

### Risk 3: High blast radius -- ~30 files reference `session_id` or `job_id`
**Impact:** Missed references cause runtime errors
**Mitigation:** Use grep-driven exhaustive search before each rename. Run full test suite after each phase. Phase 4 (cleanup/renames) is last and can be deferred if risky.

## Race Conditions

### Race 1: TelegramMessage created after AgentSession enqueue
**Location:** `bridge/telegram_bridge.py` lines ~573-1164
**Trigger:** Bridge creates TelegramMessage at line ~583 and enqueues AgentSession at line ~1158. If the job worker picks up the session before TelegramMessage is fully committed to Redis, `trigger_message_id` resolves to nothing.
**Data prerequisite:** TelegramMessage must exist in Redis before the job worker reads it.
**State prerequisite:** Redis MULTI/EXEC or ordering guarantee that TelegramMessage.create() completes before AgentSession.create().
**Mitigation:** TelegramMessage is already created ~500 lines before the enqueue call -- the gap is large enough that this is not a practical concern. Add a defensive None-check in the job worker when resolving `trigger_message_id`.

## No-Gos (Out of Scope)

- Multi-chat-per-project routing logic (separate project, this plan only adds the `project_key` field to Chat)
- Popoto ORM relationship/JOIN features (use application-level lookups)
- Redis key migration for existing records (use aliases if rename changes key format)
- Changing the enrichment function signature (keep the same parameters, just change where they are sourced from)
- BridgeEvent.session_id FK to AgentSession (low-value, BridgeEvent already has `project_key` and `chat_id`)

## Update System

No update system changes required -- this is an internal model refactor. The update script (`scripts/remote-update.sh`) pulls code and restarts the bridge, which is sufficient. No new dependencies, config files, or migration steps are needed for the update process itself. The one-time backfill script should be run manually on each machine after the code is deployed.

## Agent Integration

No agent integration required -- this is a bridge-internal model change. The agent interacts with the system through Claude Code sessions, not directly through Popoto models. MCP servers and `.mcp.json` do not need changes. The bridge (`bridge/telegram_bridge.py`) will be updated to write to the new field locations, but that is part of the core implementation, not agent integration.

## Documentation

- [ ] Update `docs/features/redis-models.md` (if it exists) or create it to document the model relationship map
- [ ] Add entry to `docs/features/README.md` index table for model relationships
- [ ] Update inline docstrings on all modified model classes
- [ ] Document the migration script usage in a comment header within the script itself

## Success Criteria

- [ ] All 7 models have `project_key` field (TelegramMessage, Link, DeadLetter, Chat, ReflectionRun -- AgentSession and BridgeEvent already have it)
- [ ] TelegramMessage carries `has_media`, `media_type`, `youtube_urls`, `non_youtube_urls`, `reply_to_msg_id`, `classification_type`, `classification_confidence`, `agent_session_id`
- [ ] AgentSession carries `trigger_message_id` referencing TelegramMessage
- [ ] AgentSession no longer stores message-specific fields (removed in Phase 4)
- [ ] `last_transition_at` removed from AgentSession, `started_at` promoted to SortedField
- [ ] `job_id` renamed to `id` (or aliased) and `session_id` renamed to `claude_code_session_id`
- [ ] Job worker reads enrichment params from TelegramMessage via `trigger_message_id` with fallback to AgentSession
- [ ] One-time backfill script exists and runs successfully
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (models)**
  - Name: model-builder
  - Role: Add fields to all 7 models, create cross-references
  - Agent Type: builder
  - Resume: true

- **Builder (bridge-writers)**
  - Name: bridge-writer-builder
  - Role: Update bridge to write media/URL/classification to TelegramMessage, set trigger_message_id
  - Agent Type: builder
  - Resume: true

- **Builder (readers)**
  - Name: reader-builder
  - Role: Update job worker and enrichment to read from TelegramMessage, add fallback path
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Remove deprecated fields, apply renames, consolidate timestamps
  - Agent Type: builder
  - Resume: true

- **Builder (migration)**
  - Name: migration-builder
  - Role: Write and test the one-time backfill script
  - Agent Type: builder
  - Resume: true

- **Validator (models)**
  - Name: model-validator
  - Role: Verify all models have correct fields, cross-references resolve
  - Agent Type: validator
  - Resume: true

- **Validator (integration)**
  - Name: integration-validator
  - Role: Run full test suite, verify enrichment pipeline end-to-end
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Update model documentation and feature docs
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Add fields to models (Phase 1 - additive)
- **Task ID**: build-models
- **Depends On**: none
- **Assigned To**: model-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `project_key = KeyField(null=True)` to TelegramMessage, Link, DeadLetter, Chat, ReflectionRun
- Add `has_media`, `media_type`, `youtube_urls`, `non_youtube_urls`, `reply_to_msg_id`, `classification_type`, `classification_confidence`, `agent_session_id` to TelegramMessage
- Add `trigger_message_id = Field(null=True)` to AgentSession
- Promote `started_at` to `SortedField(type=float, partition_by="project_key", null=True)` on AgentSession

### 2. Validate model changes
- **Task ID**: validate-models
- **Depends On**: build-models
- **Assigned To**: model-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all models instantiate without error
- Verify existing tests still pass
- Verify Popoto accepts the new field definitions

### 3. Update bridge writers (Phase 2)
- **Task ID**: build-bridge-writers
- **Depends On**: validate-models
- **Assigned To**: bridge-writer-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `bridge/telegram_bridge.py` TelegramMessage creation (~line 583) to include media, URL, classification, reply fields
- Update `bridge/telegram_bridge.py` enqueue call (~line 1158) to set `trigger_message_id`
- Set `project_key` on TelegramMessage, Link, DeadLetter, Chat at creation time (derive from existing chat_id -> project mapping)
- Set `project_key` on ReflectionRun in `scripts/reflections.py`

### 4. Update readers (Phase 3)
- **Task ID**: build-readers
- **Depends On**: build-bridge-writers
- **Assigned To**: reader-builder
- **Agent Type**: builder
- **Parallel**: true
- Update `agent/job_queue.py` `_execute_job()` to load TelegramMessage by `trigger_message_id` and pass fields to `enrich_message()`
- Add fallback: if `trigger_message_id` is None, use AgentSession fields directly (backward compat)
- Update any other readers of these fields (`bridge/context.py`, `bridge/routing.py`, `tools/session_tags.py`, etc.)

### 5. Validate bridge and reader changes
- **Task ID**: validate-integration
- **Depends On**: build-readers
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full test suite
- Verify enrichment pipeline works end-to-end
- Verify backward compatibility with sessions that lack `trigger_message_id`

### 6. Write migration script
- **Task ID**: build-migration
- **Depends On**: validate-integration
- **Assigned To**: migration-builder
- **Agent Type**: builder
- **Parallel**: false
- Write `scripts/migrate_model_relationships.py` to backfill `project_key` and copy media/URL fields from AgentSession to TelegramMessage
- Support `--dry-run` flag
- Only process records from last 90 days

### 7. Cleanup deprecated fields (Phase 4)
- **Task ID**: build-cleanup
- **Depends On**: build-migration
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Remove `has_media`, `media_type`, `youtube_urls`, `non_youtube_urls`, `reply_to_msg_id`, `classification_type`, `classification_confidence` from AgentSession
- Remove `last_transition_at` from AgentSession (update `log_lifecycle_transition()` to not set it)
- Rename `job_id` -> `id` and `session_id` -> `claude_code_session_id` (or add aliases if rename breaks Redis keys)
- Update all ~30 consuming files to use new field names
- Grep-verify no stale references remain

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: build-cleanup
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create/update `docs/features/redis-models.md` with the model relationship map
- Add entry to `docs/features/README.md` index table
- Update model class docstrings

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: integration-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met (including documentation)
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No stale xfails | `grep -rn 'xfail' tests/ \| grep -v '# open bug'` | exit code 1 |
| project_key on TelegramMessage | `grep -n 'project_key' models/telegram.py` | output contains project_key |
| project_key on Chat | `grep -n 'project_key' models/chat.py` | output contains project_key |
| project_key on Link | `grep -n 'project_key' models/link.py` | output contains project_key |
| project_key on DeadLetter | `grep -n 'project_key' models/dead_letter.py` | output contains project_key |
| project_key on ReflectionRun | `grep -n 'project_key' models/reflections.py` | output contains project_key |
| trigger_message_id on AgentSession | `grep -n 'trigger_message_id' models/agent_session.py` | output contains trigger_message_id |
| No has_media on AgentSession | `grep -c 'has_media' models/agent_session.py` | output contains 0 |
| Migration script exists | `test -f scripts/migrate_model_relationships.py` | exit code 0 |

---

## Open Questions

1. **AutoKeyField rename safety**: Has anyone tested renaming a Popoto `AutoKeyField` (e.g., `job_id` -> `id`)? If it changes the Redis key prefix, all existing records become inaccessible. Should we test this in isolation first, or just use a property alias from the start?

2. **Multi-chat-per-project timeline**: The issue mentions preparing for PM chat groups. Is this actively planned, or is adding `project_key` to Chat sufficient preparation for now? This affects whether we need to add any routing logic or just the field.

3. **Phase 4 timing**: The cleanup phase (removing deprecated fields, applying renames) is the riskiest step. Should it be deferred to a separate issue/PR to keep the initial PR's blast radius smaller?

4. **Backfill scope**: The issue mentions a migration script. Should the backfill be mandatory before Phase 3 (reader cutover), or is the fallback path (read from AgentSession if trigger_message_id is None) sufficient to allow phased rollout without a backfill?
