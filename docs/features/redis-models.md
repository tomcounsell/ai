# Redis Model Relationships

Popoto models stored in Redis form the persistent state layer of the system. This document maps the relationships between models and their field ownership.

## Model Relationship Map

```
TelegramMessage ──────── AgentSession
  msg_id                   id
  agent_session_id ──────> id
  msg_id <─────────────── telegram_message_key
  project_key              project_key
  chat_id                  chat_id
  message_id               telegram_message_id
  has_media                (deprecated: has_media)
  media_type               (deprecated: media_type)
  youtube_urls             (deprecated: youtube_urls)
  non_youtube_urls         (deprecated: non_youtube_urls)
  reply_to_msg_id          (deprecated: reply_to_msg_id)
  classification_type      (deprecated: classification_type)
  classification_confidence

Link                     Chat
  link_id                  chat_id (unique)
  project_key              project_key
  chat_id                  chat_name
                           chat_type

DeadLetter               ReflectionRun
  letter_id                date (unique)
  project_key              project_key
  chat_id

BridgeEvent              Memory
  event_id                 memory_id
  project_key              agent_id
  chat_id                  project_key
                           content
                           importance
                           source
                           relevance (DecayingSortedField)
                           confidence (ConfidenceField)
                           bloom (ExistenceFilter)
```

## Cross-References

### AgentSession <-> TelegramMessage

When a Telegram message triggers an agent session:

1. **Bridge stores TelegramMessage** with media, URL, and classification metadata
2. **Bridge enqueues AgentSession** with `telegram_message_key` pointing to the TelegramMessage's `msg_id`
3. **Session worker resolves TelegramMessage** via `telegram_message_key` to get enrichment parameters
4. **Session worker sets back-reference**: `TelegramMessage.agent_session_id = AgentSession.id`

This bidirectional link enables:
- Looking up which session processed a given message
- Looking up which message triggered a given session
- Reading enrichment metadata from its canonical location (TelegramMessage)

### Fallback Path

For sessions created before the migration (no `telegram_message_key`), the session worker falls back to reading enrichment fields directly from AgentSession. These fields are retained on AgentSession for backward compatibility with pre-existing records.

## project_key

All models carry a `project_key` field for direct project association. This replaces the implicit `chat_id -> project` lookup that previously required loading `~/Desktop/Valor/projects.json` at query time.

Models with project_key:
- **AgentSession** (existing)
- **BridgeEvent** (existing)
- **TelegramMessage** (added)
- **Link** (added)
- **DeadLetter** (added)
- **Chat** (added)
- **ReflectionRun** (added)
- **Memory** (added — subconscious memory records, partitioned by project_key)

## Field Ownership

Message metadata (media, URLs, classification) is owned by **TelegramMessage**, not AgentSession. The fields exist on both models for backward compatibility, but new code should always read from TelegramMessage via `telegram_message_key`.

| Field | Owner | Also On |
|-------|-------|-------------------|
| has_media | TelegramMessage | AgentSession |
| media_type | TelegramMessage | AgentSession |
| youtube_urls | TelegramMessage | AgentSession |
| non_youtube_urls | TelegramMessage | AgentSession |
| reply_to_msg_id | TelegramMessage | AgentSession |
| classification_type | TelegramMessage | AgentSession |
| classification_confidence | TelegramMessage | AgentSession |

## Migration

Run the one-time backfill script after deploying the code changes:

```bash
# Preview changes
python scripts/migrate_model_relationships.py --dry-run

# Run migration (last 90 days)
python scripts/migrate_model_relationships.py

# Custom time range
python scripts/migrate_model_relationships.py --max-age 30
```

The script:
1. Backfills `project_key` on all models using `chat_id -> project` mapping from `~/Desktop/Valor/projects.json`
2. Copies enrichment metadata from AgentSession to TelegramMessage
3. Sets `telegram_message_key` and `agent_session_id` cross-references

## Identity Fields

| Field | Purpose | Notes |
|-------|---------|-------|
| `id` | AgentSession primary key (AutoKeyField) | `session.agent_session_id` backward-compat alias available |
| `session_id` | Telegram-derived session identifier | Format: `tg_{project}_{chat_id}_{msg_id}` |
| `telegram_message_id` | Telegram message ID (integer) | Renamed from `message_id` for clarity |
| `telegram_message_key` | Popoto key to TelegramMessage | Renamed from `trigger_message_id` for clarity |
| `claude_session_uuid` | Claude Code transcript UUID | Used for continuation sessions |

## Field Type Semantics: KeyField vs IndexedField

Popoto field types have different implications for how records behave on mutation:

- **KeyField**: Part of the Redis key. Changing a KeyField value changes the record's identity, creating a new record and orphaning the old one. Code that needs to change a KeyField value must use the **delete-and-recreate** pattern (delete old record, create new one with all fields copied).
- **IndexedField**: Maintains a secondary index for `.filter()` queries but is NOT part of the Redis key. Mutating an IndexedField and calling `.save()` updates the record in place and correctly updates the secondary index. No delete-and-recreate needed.
- **Field**: Plain data field with no indexing. Mutate and save freely.

### AgentSession Key Fields

| Field | Type | Mutable? | Notes |
|-------|------|----------|-------|
| `id` | AutoKeyField | Never | Primary key, auto-generated |
| `session_type` | KeyField | No | Set once at creation ("chat" or "dev") |
| `project_key` | KeyField | No | Set once at creation |
| `chat_id` | KeyField | No | Set once at creation |
| `parent_session_id` | KeyField | No | Set once at creation (child sessions only, renamed from `parent_chat_session_id`) |
| `role` | Field | No | Set once at creation ("pm", "dev", or null for legacy) |
| `parent_agent_session_id` | KeyField | No | Set once at creation (child sessions only) |
| `status` | IndexedField | Yes | Mutate and save directly; no delete-and-recreate |

### AgentSession Datetime Fields

All timestamp fields use Popoto `DatetimeField` or `SortedField(type=datetime)`:

| Field | Type | Notes |
|-------|------|-------|
| `created_at` | SortedField(type=datetime) | Partitioned by project_key |
| `started_at` | DatetimeField(null=True) | Set when worker picks up session |
| `updated_at` | DatetimeField(auto_now=True) | Renamed from `last_activity` |
| `completed_at` | DatetimeField(null=True) | Set on terminal status |
| `scheduled_at` | DatetimeField(null=True) | Renamed from `scheduled_after` |

Float timestamps are auto-converted to datetime via `__setattr__`. Note: Popoto `DatetimeField` returns naive datetimes from Redis (no timezone info). Code that compares with `time.time()` must assume UTC for naive datetimes.

### AgentSession Consolidated DictFields

| Field | Contains | Replaces |
|-------|----------|----------|
| `initial_telegram_message` | `sender_name`, `sender_id`, `message_text`, `telegram_message_id`, `chat_title` | Six separate fields |
| `extra_context` | `revival_context`, `classification_type`, `classification_confidence` | Three separate fields |

The `status` field was changed from KeyField to IndexedField (popoto >= 1.4.3) to eliminate the delete-and-recreate overhead on every lifecycle transition (pending -> running -> active -> completed). This removed the primary source of duplicate session records in the dashboard.

### Where Delete-and-Recreate Is Still Needed

With `status` as an IndexedField, the delete-and-recreate pattern is no longer needed for status transitions. All status transitions (session pickup, completion, failure, recovery, watchdog marking, nudge re-enqueue) use direct field mutation and `.save()`.

The delete-and-recreate pattern remains in `agent/agent_session_queue.py` only in the `_AGENT_SESSION_FIELDS` list, which defines the fields to copy if a record ever needs to be re-created for KeyField changes. In practice, no current code path changes a KeyField value after creation -- the `bridge/session_transcript.py` module guards against `chat_id` mutation by logging a warning and skipping the write if the value would change.
