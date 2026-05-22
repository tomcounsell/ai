# Media Enrichment

**Status**: Implemented
**Last revised**: 2026-05-09 (sdlc-1344, follows sdlc-1322 / sdlc-1297)
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
   2. await _download_media_with_retry(client, msg, prefix=media_type)
        - Per-attempt timeout = compute_media_timeout(message.file.size)
          (5s + size_bytes/MB, floored at 10s, capped at 120s)
        - On TimeoutError: retry once with 2x leash (still capped at 120s)
        - On success: TelegramMessage.media_local_path = abs_path
        - On terminal timeout: media_download_error = "timeout after Xs (retried)"
        - On other error: media_download_error = "<ExceptionType>: <msg>"
   3. dispatch_telegram_session(...)  -> AgentSession enqueued in Redis
   4. Log: [bridge] intake_duration_ms=<ms> has_media=<bool> ...
        Plus per-attempt: [media] download attempt=N outcome=... size_bytes=... computed_timeout_s=...
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
| `media_download_error` | str \| None | **(sdlc-1297, refined sdlc-1322)** Reason the bridge-side download failed. Distinct strings: `"timeout after Xs (retried)"` (both attempts timed out, where X is the second-attempt budget); `"<ExceptionType>: <msg>"` for non-timeout failures (no retry). Inspected by the worker. |

All three new fields are nullable additive Popoto fields; existing records read `None` and the worker treats that as a normal "no path → skip AI" branch.

## Worker-side dispatch summary

The worker's `enrich_message` emits a single summary log line per call; `media=` enumerates the disposition:

| Summary value | Trigger |
|---------------|---------|
| `media=no` | `has_media=False` on the record. |
| `media=yes` | AI enrichment succeeded; description prepended to text. |
| `media=skipped:no_record` | No `TelegramMessage` record was loaded (manual session, ad-hoc invocation). Normal path. |
| `media=skipped:download_failed` | `media_local_path is None and media_download_error is not None`. |
| `media=skipped:no_path` | `media_local_path is None` and no error recorded — after the **(sdlc-1330)** orphan self-heal runs first. If exactly one file matches `*_{message_id}.*` under `MEDIA_DIR`, the worker adopts it, logs an `INFO` self-heal line, and proceeds to AI enrichment (i.e. summary becomes `media=yes`). Zero or multiple matches fall through to this summary — usually a pre-migration record or genuinely missing intake. |
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

`download_media` is invoked through `_download_media_with_retry` (in `bridge/telegram_bridge.py`), which uses a size-aware per-attempt timeout from `compute_media_timeout()` at `bridge/media.py:258`. The formula is `max(10.0, min(120.0, 5.0 + size_bytes/MB))` — so a 1MB photo gets the 10s baseline, a 10MB voice note gets ~15s, a 100MB document gets ~105s, and anything ≥ ~115MB is capped at 120s. `message.file.size` being absent (some thumbnails) falls back to the 10s baseline.

On `TimeoutError`, the helper retries **once** with a 2x leash (still capped at 120s). If the retry also times out, the bridge persists `media_download_error="timeout after Xs (retried)"` (X = the second-attempt budget) and proceeds to enqueue. The `(retried)` suffix lets downstream logs distinguish "we tried twice and the file is just too big" from a first-attempt-unlucky failure. Non-timeout exceptions are not retried — the error string is stored as `"<ExceptionType>: <msg>"`. The worker reads `media_download_error` on its side and falls through to the bare caption with summary `media=skipped:download_failed`.

## Reply-chain note

The reply-chain branch in `bridge/enrichment.py` still requires a Telethon client and is therefore **silently skipped in the worker** until a follow-up issue lands. Tracked as a companion to #1297. Reply-context enrichment that the bridge handler hydrates synchronously (the existing `REPLY_THREAD_CONTEXT_HEADER` block) is unaffected — it's already pre-baked into `session.message_text`.

## Implementation files

- `bridge/telegram_bridge.py` — handler, intake timing, bridge-side download, persistence. Hosts `_download_media_with_retry` (size-aware timeout + 1-retry wrapper around `download_media`).
- `bridge/media.py` — `download_media`, `compute_media_timeout` (size-aware timeout helper, line 258), `process_incoming_media`, `process_downloaded_media`, `describe_image`, `transcribe_voice`, `extract_document_text`.
- `bridge/enrichment.py` — worker-side `enrich_message`; hosts the **(sdlc-1330)** orphan-file self-heal that globs `MEDIA_DIR` for `*_{message_id}.*` when `has_media=True` but neither `media_local_path` nor `media_download_error` is set.
- `models/telegram.py` — `TelegramMessage` field definitions.
- `agent/session_executor.py` — call site that passes the loaded `TelegramMessage` to `enrich_message`.

## Tests

- `tests/unit/test_enrichment_media.py` — happy path + four failure-mode branches.
- `tests/unit/test_telegram_bridge_media_timeout.py` — `compute_media_timeout` table tests + `_download_media_with_retry` retry/success/no-retry paths (sdlc-1322).
- `tests/integration/test_media_enrichment_pipeline.py` — TelegramMessage round-trip through `enrich_message` (process_downloaded_media mocked); also covers the size-aware retry path: `test_bridge_retries_slow_download_once` (first-attempt timeout, second succeeds) and `test_bridge_gives_up_after_retry` (both attempts time out, persisted error carries `(retried)` suffix).
- `tests/unit/test_youtube_transcription.py` — verifies the YouTube branch is unaffected by the signature change.
