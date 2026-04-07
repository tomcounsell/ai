# Message Pipeline: Deferred Enrichment

## Problem

Synchronous enrichment in the Telegram event handler blocked message acknowledgment for seconds or minutes. Operations like YouTube transcription, link summarization, and media processing ran inline, delaying the bridge's ability to process subsequent messages and risking Telegram disconnects under load.

## Architecture: Enqueue Fast, Enrich Later

A two-stage pipeline separates fast message capture from slow enrichment work.

### Stage 1: Event Handler (Fast Path)

The event handler extracts only lightweight metadata and enqueues immediately. Target latency: < 10ms.

**Metadata extracted inline:**
- `has_media` / `media_type` — boolean flag and type string from the message object
- `urls` — parsed from message text
- `reply_to_msg_id` — for threading context
- `chat_id`, `sender_id`, `timestamp` — standard fields

No network calls, no file downloads, no API requests. The message is persisted to the job queue and the handler returns.

### Stage 2: Job Worker (Enrichment Path)

A background worker picks up enqueued messages and runs four independent enrichment operations:

| Operation | Description | Failure Mode |
|-----------|-------------|--------------|
| **Media processing** | Download and describe images/documents via Ollama | Skip on timeout; message delivered without media context |
| **YouTube transcription** | Fetch transcript for YouTube URLs | Skip on failure; URL included without transcript |
| **Link summarization** | Fetch and summarize non-YouTube URLs via Perplexity | Skip on failure; raw URL preserved |
| **Reply chain fetch** | Retrieve parent messages for threading context | Skip on failure; reply delivered without parent context |

Each operation is independent and fault-tolerant. A failure in one does not block or affect the others. The message is always delivered to the agent, with whatever enrichment succeeded.

## Zero-Loss Restarts

Two mechanisms prevent message loss during bridge restarts:

- **Catch-up replay**: `catch_up=True` on the Telethon client replays messages received while the bridge was offline. Messages missed during a restart are processed on reconnection.
- **Per-chat Redis dedup**: `bridge/dedup.py` tracks processed message IDs per chat in Redis with a TTL. When catch-up replays a message that was already enqueued before the restart, the dedup layer silently drops the duplicate.

## Key Files

| File | Purpose |
|------|---------|
| `bridge/telegram_bridge.py` | Event handler with fast-path metadata extraction |
| `bridge/enrichment.py` | Enrichment operations (media, YouTube, links, replies) |
| `bridge/dedup.py` | Per-chat Redis deduplication |
| `agent/job_queue.py` | Job queue for deferred processing |

## Related

- [Bridge Self-Healing](bridge-self-healing.md) — crash recovery and watchdog monitoring
- [YouTube Transcription](youtube-transcription.md) — details on transcript fetching
- [Link Content Summarization](link-summarization.md) — details on URL summarization
- [Image Vision Support](image-vision.md) — details on media description
