# Telegram Inbound Attachments — Steering Enrichment + Auto-Ingest

When a file (`.txt`, photo, document, voice note, etc.) arrives in a chat that already has
a live session, the bridge enriches the message with the file's content **before** routing
it to the steering queue, and copies the file into the work-vault for permanent indexing.
Both steps live inside a single helper (`_ack_steering_routed` in
`bridge/telegram_bridge.py`) so every steering call site benefits from the same logic — no
scattered patches.

Original issue: #1215.

## What the agent sees

The helper detects `message.media`, calls `process_incoming_media(client, message)` to
extract the description (document text, image description, voice transcription), and uses
that enriched string as the steering message. When the user provided a real caption
alongside the file, the helper composes `"{description}\n\n{caption}"` — the same pattern
`bridge/enrichment.py:enrich_message` uses on the new-session path.

**Defensive fallback.** If `process_incoming_media` raises or returns an empty
description, the original `--file attachment only--` sentinel is pushed unchanged so
delivery never silently drops.

## Reaction ordering

For media-bearing messages the eyes emoji (👀) fires **before** the download starts, so
the user gets immediate "I see you" feedback during the (potentially multi-second)
download window. Abort-keyword detection runs afterwards, against the enriched text.
Text-only steering preserves the original reaction-after-push order byte-identical, so the
hot path adds zero awaits for the common case.

## Auto-ingest into the work-vault

Every downloaded attachment is also copied into `~/work-vault/telegram-attachments/` with
a disambiguated filename of the form
`{YYYYMMDD_HHMMSS}_{sender}_{message_id}_{original_basename}`. The `KnowledgeWatcher`
(`bridge/knowledge_watcher.py`) monitors the vault recursively, debounces filesystem
events, and routes new files through `convert_to_sidecar` to produce a searchable `.md`
sidecar. Once the watcher's debounce window elapses, the file is discoverable via:

```bash
python -m tools.memory_search search "..."
```

The vault copy is scheduled as a fire-and-forget `asyncio.create_task` appended to the
module-level `_background_tasks` list, so its work never runs on the bridge's hot path;
the steering push proceeds without waiting on it. Every failure inside the task is caught
locally and logged at WARNING — including a failure to *create* the task, which must never
gate steering. See [Markitdown Ingestion](markitdown-ingestion.md) for the watcher
pipeline.

## Privacy note

Auto-ingest is unconditional — files sent into chats are treated as work artifacts and
stored permanently in the vault. There is no consent gate. If you send a private
screenshot expecting it to be transient, expect it to land in the searchable knowledge
base.

## Related

- [Mid-Session Steering](mid-session-steering.md) — the steering queue this path feeds
- [Markitdown Ingestion](markitdown-ingestion.md) — the vault watcher and sidecar converter
- [Bridge Module Architecture](bridge-module-architecture.md) — where the bridge's inbound handlers live
