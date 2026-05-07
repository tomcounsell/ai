# Image Vision Support

**Status**: Implemented
**Last revised**: 2026-05-07 (sdlc-1297)

## Overview

When users send images to Valor via Telegram, the bridge downloads the file at intake and the worker runs Claude Haiku 4.5 vision over it to generate a detailed description. The description is prepended to the message text the agent sees.

This split — bridge does Telethon I/O, worker does AI — was made explicit by [sdlc-1297](https://github.com/tomcounsell/ai/issues/1297). Before that change the worker held a (always-`None`) Telegram client reference and silently dropped every photo. The current contract is described in [media-enrichment.md](media-enrichment.md).

## Features

### Supported Image Formats

Automatically processes images in these formats:
- PNG (`.png`)
- JPEG (`.jpg`, `.jpeg`)
- GIF (`.gif`)
- WebP (`.webp`)
- BMP (`.bmp`)

### Vision Model

Uses **Claude Haiku 4.5** vision (cloud API, via `bridge.media.describe_image`):
- Same model the agent uses elsewhere — consistent quality, no extra setup
- Strong descriptions for screenshots, photos, whiteboards, diagrams
- Authoritative: no separate vision runtime to install or maintain

### Media Storage

The bridge downloads incoming media to `data/media/` with timestamped filenames:
- Format: `{prefix}_{YYYYMMDD}_{HHMMSS}_{name}` (see `bridge/media.py::download_media`)
- The absolute path is persisted on `TelegramMessage.media_local_path` so the worker can read it later.

## Message Flow

```
User sends image (with or without caption)
    |
    v
Bridge handler:
  - persists TelegramMessage(has_media=True, media_type="photo")
  - awaits download_media(client, msg)  [10s timeout, cf. sdlc-1297]
  - updates TelegramMessage.media_local_path = absolute_path
  - enqueues AgentSession
    |
    v
Worker pops session, calls bridge.enrichment.enrich_message(...)
  - reads media_local_path off the persisted TelegramMessage
  - calls bridge.media.process_downloaded_media(path, "photo")
  - process_downloaded_media -> describe_image (Claude Haiku 4.5 vision)
    |
    v
Enriched text: "[User sent an image]
                Image description: A screenshot showing a terminal..."
    |
    v
Agent receives enriched text and can discuss the image directly.
```

## Edge Case Handling

| Case | Behavior |
|------|----------|
| Bridge download timeout (10s) | `media_download_error="timeout after 10s"`; worker logs WARNING and proceeds with bare caption. |
| Bridge download exception | `media_download_error=<exc>`; same as above. |
| Worker can't read the file | Logs `[enrichment] media file at {path} not readable` and proceeds with bare caption. |
| Vision model error | Logged in worker; falls back to `[User sent an image - saved to {filename}]`. |
| Corrupt/invalid images | `validate_media_file` rejects; worker tries text extraction or returns a corrupted-file marker. |
| No `TelegramMessage` record (manual / non-Telegram session) | Normal path — branch skipped silently, summary `media=skipped:no_record`. |

The enrichment-summary log line in the worker reports one of `media={no, yes, skipped:no_record, skipped:download_failed, skipped:no_path, skipped:file_unreadable, skipped:no_description, failed}` so log scrapers can distinguish each case.

## Implementation Files

- `bridge/telegram_bridge.py` — handler, runs `download_media` synchronously at intake (with timeout), persists `media_local_path`/`media_download_error` on the `TelegramMessage`.
- `bridge/media.py`
  - `download_media()` — Telethon RPC, bridge-only.
  - `describe_image()` — Claude Haiku 4.5 vision API.
  - `process_downloaded_media(path, media_type)` — pure AI half (vision/Whisper/extract). No Telethon dependency. Worker-callable.
  - `process_incoming_media(client, message)` — thin wrapper kept for callers that still hold a live Telethon client.
- `bridge/enrichment.py::enrich_message` — worker-side dispatch; reads `media_local_path` off the persisted `TelegramMessage`.
- `models/telegram.py::TelegramMessage` — fields `media_local_path`, `media_download_error` (both nullable, additive).
- `agent/session_executor.py` — passes the loaded `TelegramMessage` to `enrich_message`.

## Dependencies

### Python Packages

- `anthropic` — Claude Haiku 4.5 vision is invoked via the standard Anthropic SDK already in the project.

No separate local vision runtime is required.

### Storage

- Disk space under `data/media/` for downloaded media. The bridge does not currently clean these up; eviction policy is tracked separately.

## Configuration

Defaults live in code; nothing project-specific is required:

```python
# Bridge handler download timeout (sdlc-1297)
DOWNLOAD_TIMEOUT_SECONDS = 10.0  # asyncio.wait_for at intake

# Media storage
MEDIA_DIR = Path(__file__).parent.parent / "data" / "media"  # historical constant
# Live path used by download_media() is data/media/ at the repo root.
```

## Testing

- `tests/unit/test_enrichment_media.py` — covers happy path, download-failure path, file-unreadable path, no-`TelegramMessage` path, and the no-media path.
- `tests/integration/test_media_enrichment_pipeline.py` — drives a real `TelegramMessage` record through `enrich_message` end-to-end (with `process_downloaded_media` mocked so the test doesn't burn a vision API call).

Manual smoke test:

1. Send photo with no caption -> Valor should describe what's in it.
2. Send screenshot of code -> Valor can discuss the code.
3. Send image with caption -> Description and caption both reach the agent.
4. Send a photo while the network is flaky -> Bridge logs a `[media] download timeout` warning and the agent receives the bare caption.

## Example Interactions

**User sends screenshot of error message:**
```
[User sent an image]
Image description: A terminal window showing a Python traceback.
The error is a KeyError: 'user_id' occurring in line 45 of app.py
within the get_user() function. The traceback shows the call originated
from handle_request() in routes.py.

What's causing this error?
```

**User sends photo of whiteboard:**
```
[User sent an image]
Image description: A whiteboard with a system architecture diagram.
Shows three boxes labeled "Frontend", "API", and "Database" connected
by arrows. The Frontend connects to API via "REST", and API connects
to Database via "PostgreSQL". There's a note saying "Add Redis cache here?"

Can you help me think through this architecture?
```
