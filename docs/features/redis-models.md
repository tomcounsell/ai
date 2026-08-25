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

TeammateMetrics (singleton, key="global")
  teammate_classified_count (IntField)
  teammate_low_confidence_count (IntField)
  work_classified_count (IntField)
  teammate_response_times (ListField, max_length=1000)
  work_response_times (ListField, max_length=1000)
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

For sessions without a `telegram_message_key`, the session worker falls back to reading enrichment fields directly from AgentSession. These fields are retained on AgentSession for compatibility with pre-existing records.

## project_key

All models carry a `project_key` field for direct project association. This makes the project association explicit on each record rather than derived from `chat_id`.

Models with project_key:
- **AgentSession**
- **BridgeEvent**
- **TelegramMessage**
- **Link**
- **DeadLetter**
- **Chat**
- **ReflectionRun**
- **Memory** — subconscious memory records, partitioned by project_key

## Field Ownership

Message metadata (media, URLs, classification) is owned by **TelegramMessage**, not AgentSession. The fields exist on both models, but new code reads from TelegramMessage via `telegram_message_key`.

| Field | Owner | Also On |
|-------|-------|-------------------|
| has_media | TelegramMessage | AgentSession |
| media_type | TelegramMessage | AgentSession |
| youtube_urls | TelegramMessage | AgentSession |
| non_youtube_urls | TelegramMessage | AgentSession |
| reply_to_msg_id | TelegramMessage | AgentSession |
| classification_type | TelegramMessage | AgentSession |
| classification_confidence | TelegramMessage | AgentSession |

## Identity Fields

| Field | Purpose | Notes |
|-------|---------|-------|
| `id` | AgentSession primary key (AutoKeyField) | `session.agent_session_id` alias available |
| `session_id` | Telegram-derived session identifier | Format: `tg_{project}_{chat_id}_{msg_id}` |
| `telegram_message_id` | Telegram message ID (integer) | |
| `telegram_message_key` | Popoto key to TelegramMessage | |
| `claude_session_uuid` | Claude Code transcript UUID | Used for continuation sessions |

## Boolean Field Storage: Typed vs Untyped

Popoto boolean fields round-trip through Redis differently depending on whether the field
declares `type=bool`:

- **Typed** `Field(type=bool, default=False)` round-trips as a **real Python `bool`**
  (`type(v).__name__ == "bool"`, values `True`/`False`). Reading it with a plain `bool(value)`
  is correct.
- **Untyped** `Field(default=False)` (no `type=` argument) round-trips through Redis as the
  **string** `"True"` / `"False"`. `bool("False")` is `True` in Python (any non-empty string is
  truthy), so a naive `bool(getattr(obj, "field", False))` read is silently wrong for the `False`
  case.

`TelegramMessage.has_media` and the reflection-model fields `auto_delete_after_run` /
`dead_letter_escalated` (`models/reflection.py`) are all **typed**. `AgentSession.requires_real_chrome`,
`AgentSession.user_facing_routed`, and `AgentSession.retain_for_resume` are **untyped**.

Every untyped-field read site goes through the canonical `_truthy()` helper
(`agent/session_pickup.py`) instead of a bare `bool()` call: `ui/data/sdlc.py`,
`ui/app.py` (`/dashboard.json` output), and the `cmd_release` fast-path in
`tools/valor_session.py`. `models/crash_signature.py` imports the canonical helper.

**When adding a new boolean Popoto field:** always declare `Field(type=bool, ...)` so it
round-trips as a real bool and needs no `_truthy()` wrapping at read sites. Only reach for
`_truthy()` when reading an existing *untyped* boolean field you can't safely re-type.

## Field Type Semantics: KeyField vs IndexedField

Popoto field types have different implications for how records behave on mutation:

- **KeyField**: Part of the Redis key. Changing a KeyField value changes the record's identity, creating a new record and orphaning the old one. Code that needs to change a KeyField value must use the **delete-and-recreate** pattern (delete old record, create new one with all fields copied).
- **IndexedField**: Maintains a secondary index for `.filter()` queries but is NOT part of the Redis key. Mutating an IndexedField and calling `.save()` updates the record in place and correctly updates the secondary index. No delete-and-recreate needed.
- **Field**: Plain data field with no indexing. Mutate and save freely.

### AgentSession Key Fields

| Field | Type | Mutable? | Notes |
|-------|------|----------|-------|
| `id` | AutoKeyField | Never | Primary key, auto-generated |
| `session_type` | KeyField | No | Set once at creation ("pm", "teammate", or "dev") |
| `project_key` | KeyField | No | Set once at creation |
| `chat_id` | KeyField | No | Set once at creation |
| `parent_agent_session_id` | KeyField | No | Canonical parent reference. Set once at creation (child sessions only). |
| `role` | Field | No | Set once at creation ("pm", "dev", or null for legacy) |
| `status` | IndexedField | Yes | Mutate and save directly; no delete-and-recreate |

### AgentSession Datetime Fields

All timestamp fields use Popoto `DatetimeField` or `SortedField(type=datetime)`:

| Field | Type | Notes |
|-------|------|-------|
| `created_at` | SortedField(type=datetime) | Partitioned by project_key |
| `started_at` | DatetimeField(null=True) | Set when worker picks up session |
| `updated_at` | DatetimeField(null=True) | Stamped explicitly via `utc_now()` in `AgentSession.save()` override |
| `completed_at` | DatetimeField(null=True) | Set on terminal status |
| `scheduled_at` | DatetimeField(null=True) | |

Timestamps are auto-converted to UTC-aware `datetime` via `AgentSession.__setattr__`: `int | float` values are treated as Unix timestamps; `str` values are parsed as ISO 8601 (falling back to `None` on failure); any other non-`datetime`, non-`None` type is reset to `None`. This guards against Popoto's `is_valid()` silently aborting `save()` when a field holds a corrupt value. Note: Popoto `DatetimeField` returns naive datetimes from Redis (no timezone info) for other models; `AgentSession` normalizes all datetime fields to UTC-aware on load.

### AgentSession Consolidated DictFields

| Field | Contains |
|-------|----------|
| `initial_telegram_message` | `sender_name`, `sender_id`, `message_text`, `telegram_message_id`, `chat_title` |
| `extra_context` | `revival_context`, `classification_type`, `classification_confidence` |

The `status` field is an IndexedField (popoto >= 1.4.3), which eliminates the delete-and-recreate overhead on every lifecycle transition (pending -> running -> active -> completed).

### Where Delete-and-Recreate Is Still Needed

With `status` as an IndexedField, all status transitions (session pickup, completion, failure, recovery, watchdog marking, nudge re-enqueue) use direct field mutation and `.save()`.

The delete-and-recreate pattern remains in `agent/agent_session_queue.py` only in `clone_agent_session_fields` / `continuation_agent_session_fields`, which build the field payload when a record needs re-creating for a KeyField change. In practice, no current code path changes a KeyField value after creation -- the `bridge/session_transcript.py` module guards against `chat_id` mutation by logging a warning and skipping the write if the value would change.
