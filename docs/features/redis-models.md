# Redis Model Relationships

Popoto models stored in Redis form the persistent state layer of the system. This document maps the relationships between models and their field ownership.

## Model Relationship Map

```
TelegramMessage ──────── AgentSession
  msg_id                   job_id
  agent_session_id ──────> job_id
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
3. **Job worker resolves TelegramMessage** via `telegram_message_key` to get enrichment parameters
4. **Job worker sets back-reference**: `TelegramMessage.agent_session_id = AgentSession.job_id`

This bidirectional link enables:
- Looking up which session processed a given message
- Looking up which message triggered a given session
- Reading enrichment metadata from its canonical location (TelegramMessage)

### Fallback Path

For sessions created before the migration (no `telegram_message_key`), the job worker falls back to reading enrichment fields directly from AgentSession. These fields are retained on AgentSession for backward compatibility with pre-existing records.

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
| `job_id` | AgentSession primary key (AutoKeyField) | `session.id` property alias available |
| `session_id` | Telegram-derived session identifier | Format: `tg_{project}_{chat_id}_{msg_id}` |
| `telegram_message_id` | Telegram message ID (integer) | Renamed from `message_id` for clarity |
| `telegram_message_key` | Popoto key to TelegramMessage | Renamed from `trigger_message_id` for clarity |
| `claude_session_uuid` | Claude Code transcript UUID | Used for continuation sessions |
