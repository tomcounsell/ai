# Media Enrichment

**Status**: Implemented
**Last revised**: 2026-05-07 (sdlc-1297)
**Related**: [image-vision.md](image-vision.md), [bridge-worker-architecture.md](bridge-worker-architecture.md)

## Why this doc exists

Telegram media (photos, voice notes, audio, documents) needs to reach the agent as **enriched text** (image description, transcription, document content) — not as a bare caption. The bridge owns Telethon I/O; the worker owns AI work. Since those processes are separate, neither half can do both jobs by itself. This page documents how the two halves cooperate.

The current shape was settled in [issue #1297](https://github.com/tomcounsell/ai/issues/1297) after the bridge/worker split silently broke media enrichment for ~weeks: the worker held a `_telegram_client` reference that was always `None` in its process, so every photo silently came through as a bare caption.

## End-to-end flow

```
Telegram
   |
   v
bridge.telegram_bridge.handler():
   1. Persist TelegramMessage(has_media=True, media_type=...)
   2. await asyncio.wait_for(download_media(client, msg), timeout=10s)
        - On success: TelegramMessage.media_local_path = abs_path
        - On timeout/error: TelegramMessage.media_download_error = "..."
   3. dispatch_telegram_session(...)  -> AgentSession enqueued in Redis
   4. Log: [bridge] intake_duration_ms=<ms> has_media=<bool> ...
   |
   v  (Redis queue)
   |
worker (agent.session_executor._execute_agent_session):
   1. Load TelegramMessage by telegram_message_key
   2. await bridge.enrichment.enrich_message(
          message_text=..., telegram_message=tm, ...)
        - Reads tm.media_local_path
        - If readable: bridge.media.process_downloaded_media(path, type)
            -> describe_image / transcribe_voice / extract_document_text
        - Builds enriched_text
   3. Pass enriched_text to the Claude agent
```

The bridge and worker share the filesystem — `media_local_path` is an absolute path that the worker resolves directly. No Telethon RPC happens in the worker process.

## Persisted state on `TelegramMessage`

| Field | Type | Meaning |
|-------|------|---------|
| `has_media` | bool | Set true at intake when `message.media` is non-null. Pre-existing. |
| `media_type` | str \| None | `"photo" \| "voice" \| "audio" \| "document"` etc. Pre-existing. |
| `media_local_path` | str \| None | **(sdlc-1297)** Absolute filesystem path the bridge wrote at intake, or `None` if the download failed. |
| `media_download_error` | str \| None | **(sdlc-1297)** Reason the bridge-side download failed (e.g. `"timeout after 10s"`). Inspected by the worker. |

All three new fields are nullable additive Popoto fields; existing records read `None` and the worker treats that as a normal "no path → skip AI" branch.

## Worker-side dispatch summary

The worker's `enrich_message` emits a single summary log line per call; `media=` enumerates the disposition:

| Summary value | Trigger |
|---------------|---------|
| `media=no` | `has_media=False` on the record. |
| `media=yes` | AI enrichment succeeded; description prepended to text. |
| `media=skipped:no_record` | No `TelegramMessage` record was loaded (manual session, ad-hoc invocation). Normal path. |
| `media=skipped:download_failed` | `media_local_path is None and media_download_error is not None`. |
| `media=skipped:no_path` | `media_local_path is None` and no error recorded — usually a pre-migration record. |
| `media=skipped:file_unreadable` | The file at `media_local_path` is missing or not readable. |
| `media=skipped:no_description` | AI ran but produced empty output. |
| `media=failed` | AI processing raised; logged with traceback. |

Single-machine assumption: the bridge writes the file, the worker reads it. If the bridge and worker ever live on different hosts, the file-share contract becomes explicit; until then, `media_local_path` is the only contract that matters.

## Bridge intake telemetry

The handler emits

```
[bridge] intake_duration_ms=<int> has_media=<bool> has_reply=<bool> chat_id=<id>
```

at the end of every successful message intake (after enqueue). This is the observation point for the success criterion *p95 intake under 2s for media-bearing messages*.

`download_media` is wrapped in `asyncio.wait_for(..., timeout=10.0)`. On `TimeoutError`, the bridge persists `media_download_error="timeout after 10s"` and proceeds to enqueue. The worker reads that on its side and falls through to the bare caption.

## Reply-chain note

The reply-chain branch in `bridge/enrichment.py` still requires a Telethon client and is therefore **silently skipped in the worker** until a follow-up issue lands. Tracked as a companion to #1297. Reply-context enrichment that the bridge handler hydrates synchronously (the existing `REPLY_THREAD_CONTEXT_HEADER` block) is unaffected — it's already pre-baked into `session.message_text`.

## Implementation files

- `bridge/telegram_bridge.py` — handler, intake timing, bridge-side download, persistence.
- `bridge/media.py` — `download_media`, `process_incoming_media`, `process_downloaded_media`, `describe_image`, `transcribe_voice`, `extract_document_text`.
- `bridge/enrichment.py` — worker-side `enrich_message`.
- `models/telegram.py` — `TelegramMessage` field definitions.
- `agent/session_executor.py` — call site that passes the loaded `TelegramMessage` to `enrich_message`.

## Tests

- `tests/unit/test_enrichment_media.py` — happy path + four failure-mode branches.
- `tests/integration/test_media_enrichment_pipeline.py` — TelegramMessage round-trip through `enrich_message` (process_downloaded_media mocked).
- `tests/unit/test_youtube_transcription.py` — verifies the YouTube branch is unaffected by the signature change.
