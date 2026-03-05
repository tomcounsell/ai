# Local Audio Worker

The local audio worker offloads audio generation from the production server to a local machine. NotebookLM requires browser cookies for authentication, which cannot run on Render's headless environment. The worker polls the production API for episodes waiting on audio, generates audio locally via `notebooklm-py`, uploads to storage, and calls back to resume the workflow.

## Architecture

```
Task Pipeline (production)
    |
    v
step_audio_generation
    |
    v
pause_for_human(blocked_on="audio_generation")
    |
    v
GET /api/podcast/pending-audio/  <--- local_audio_worker polls
    |
    v
notebooklm-py (local machine)
    |
    v
store_file() --> storage (Supabase)
    |
    v
POST /api/podcast/episodes/<id>/audio-callback/
    |
    v
resume_workflow() + advance_step("Audio Generation")
    |
    v
step_transcribe_audio.enqueue()  (production resumes)
```

**Key design decisions:**
- The production task pipeline pauses instead of calling the NotebookLM API directly
- Authentication uses a shared `LOCAL_WORKER_API_KEY` via Bearer token
- The worker uploads audio to the same storage backend used by production
- The callback endpoint resumes the workflow and enqueues transcription automatically

## API Endpoints

Both endpoints require `Authorization: Bearer <LOCAL_WORKER_API_KEY>` and return JSON.

### `GET /api/podcast/pending-audio/`

Lists episodes whose workflow is paused waiting for audio generation.

**Request:**
```bash
curl -H "Authorization: Bearer $LOCAL_WORKER_API_KEY" \
  https://ai.yuda.me/api/podcast/pending-audio/
```

**Response (200):**
```json
{
  "episodes": [
    {
      "id": 42,
      "title": "Episode Title",
      "slug": "episode-slug",
      "podcast_slug": "podcast-slug",
      "sources": {
        "report.md": "...",
        "sources.md": "...",
        "briefing.md": "...",
        "content_plan.md": "...",
        "brief.md": "..."
      }
    }
  ]
}
```

The `sources` dict contains all content the worker needs to generate audio: the synthesis report, cited sources, master briefing, content plan, and episode brief. These map to `Episode.report_text`, `Episode.sources_text`, and the `p3-briefing`, `content-plan`, and `p1-brief` artifacts.

**Error responses:**
| Status | Meaning |
|--------|---------|
| 401 | Missing or invalid Bearer token |
| 405 | Wrong HTTP method (only GET allowed) |
| 503 | `LOCAL_WORKER_API_KEY` not configured on server |

### `POST /api/podcast/episodes/<id>/audio-callback/`

Receives completed audio from the worker and resumes the production workflow.

**Request:**
```bash
curl -X POST \
  -H "Authorization: Bearer $LOCAL_WORKER_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"audio_url": "https://storage.example.com/audio.mp3", "audio_file_size_bytes": 12345678}' \
  https://ai.yuda.me/api/podcast/episodes/42/audio-callback/
```

**Request body:**
| Field | Type | Required | Description |
|-------|------|----------|-------------|
| `audio_url` | string | yes | Public URL of the uploaded audio file |
| `audio_file_size_bytes` | integer | no | File size in bytes |

**Response (200):**
```json
{"status": "ok", "message": "Audio received, transcription enqueued"}
```

**Side effects:**
1. Updates `Episode.audio_url` and `Episode.audio_file_size_bytes`
2. Calls `workflow.resume_workflow()` to clear the paused state
3. Calls `workflow.advance_step()` to move past "Audio Generation"
4. Enqueues `step_transcribe_audio` to continue the pipeline

**Error responses:**
| Status | Meaning |
|--------|---------|
| 400 | Invalid JSON body or missing `audio_url` |
| 401 | Missing or invalid Bearer token |
| 404 | Episode or workflow not found |
| 405 | Wrong HTTP method (only POST allowed) |
| 409 | Workflow not paused for `audio_generation` |

## Management Command

```bash
uv run python manage.py local_audio_worker --base-url https://ai.yuda.me
```

### Flags

| Flag | Default | Description |
|------|---------|-------------|
| `--base-url` | _(required)_ | Production server URL |
| `--interval` | `5` | Poll interval in seconds |
| `--max-concurrent` | `3` | Maximum concurrent audio generation jobs |

### Examples

```bash
# Standard usage against production
uv run python manage.py local_audio_worker --base-url https://ai.yuda.me

# Slower polling, single job at a time
uv run python manage.py local_audio_worker --base-url https://ai.yuda.me --interval 30 --max-concurrent 1

# Local development (against local Django server)
uv run python manage.py local_audio_worker --base-url http://localhost:8000
```

The worker runs until interrupted with `Ctrl+C` (SIGINT) or SIGTERM. It uses a `ThreadPoolExecutor` for concurrent audio jobs and tracks in-progress episode IDs to avoid double-processing.

## Configuration

### Production server (Render)

Set `LOCAL_WORKER_API_KEY` as an environment variable on the web service. Generate a secure random key:

```bash
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

The setting is read from Django settings via `getattr(settings, "LOCAL_WORKER_API_KEY", "")`.

### Local worker machine

Add to `.env.local`:
```
LOCAL_WORKER_API_KEY=<same key as production>
```

Additional requirements:
- `notebooklm-py` installed and authenticated (see below)
- Storage backend configured (Supabase credentials for production uploads)

### NotebookLM Authentication

`notebooklm-py` requires browser cookies for NotebookLM access. There are two authentication methods:

**Method 1: Interactive login (local development)**

Run `notebooklm login` to open a browser, sign in to Google, and save cookies to `~/.notebooklm/storage_state.json`. This is the simplest approach for local machines.

**Method 2: Environment variable (headless/Render)**

Set `NOTEBOOKLM_AUTH_JSON` to the JSON content of the storage state file. This is required for environments without a browser (e.g., Render workers).

To set up:
1. Run `notebooklm login` on a local machine to generate `~/.notebooklm/storage_state.json`
2. Copy the file contents: `cat ~/.notebooklm/storage_state.json | pbcopy`
3. Set the `NOTEBOOKLM_AUTH_JSON` environment variable on Render (paste the JSON as the value)

The `notebooklm-py` client checks `NOTEBOOKLM_AUTH_JSON` first, falling back to the file-based storage state. Cookies may expire and require re-authentication (repeat the steps above).

## How It Works

1. **Pipeline pauses**: When the task pipeline reaches Phase 9 (Audio Generation), `step_audio_generation` calls `workflow.pause_for_human(episode_id, "audio_generation")` instead of generating audio directly. The workflow enters `paused_for_human` status with `blocked_on="audio_generation"`.

2. **Worker polls**: The `local_audio_worker` command polls `GET /api/podcast/pending-audio/` at a configurable interval. The endpoint queries for `EpisodeWorkflow` records with `status="paused_for_human"` and `blocked_on="audio_generation"`.

3. **Source files prepared**: The API returns episode source content (report, sources, briefing, content plan, brief). The worker writes these to a temporary directory as `.md` files.

4. **Audio generated**: The worker calls `notebooklm-py` to create a NotebookLM notebook, upload the source files, generate audio, and download the resulting MP3. If a `content_plan.md` source is present, the worker extracts the NotebookLM Guidance section and passes it as `instructions` to guide the two-host conversation. The notebook is cleaned up after download.

5. **Audio uploaded**: The MP3 is uploaded to storage via `store_file()` with the key `podcast/{podcast_slug}/{slug}/audio.mp3`.

6. **Callback sent**: The worker POSTs the audio URL and file size to `POST /api/podcast/episodes/<id>/audio-callback/`.

7. **Workflow resumes**: The callback endpoint updates the episode record, resumes the workflow, advances past "Audio Generation", and enqueues `step_transcribe_audio` to continue the pipeline.

## Troubleshooting

### "Worker API not configured" (503)

`LOCAL_WORKER_API_KEY` is not set on the production server. Add it as an environment variable on Render.

### "Unauthorized" (401)

The `LOCAL_WORKER_API_KEY` in `.env.local` does not match the value on the production server. Verify both sides use the same key.

### "LOCAL_WORKER_API_KEY not configured" (CommandError)

The local `.env.local` is missing the `LOCAL_WORKER_API_KEY` setting. Add it and ensure Django loads it into settings.

### "notebooklm-py not installed" (CommandError)

Install the package: `uv add notebooklm-py`. Then authenticate: `notebooklm login`.

### Audio generation hangs or times out

NotebookLM audio generation can take 5-30 minutes. The worker waits up to 30 minutes per episode (`timeout_minutes=30`). If the timeout is exceeded, the job fails and the episode remains paused for retry on the next poll cycle.

### Expired NotebookLM cookies

If `notebooklm-py` returns authentication errors, re-authenticate with `notebooklm login`. This refreshes the browser cookies stored in `~/.notebooklm/storage_state.json`.

### "Episode is not waiting for audio" (409)

The callback was sent for an episode whose workflow is not in the expected state. This can happen if the callback is sent twice (the first call advances the workflow, so the second finds it in a different state). This is safe to ignore.

## Source Files

| File | Purpose |
|------|---------|
| `apps/api/views/worker_views.py` | API endpoint views (`pending_audio`, `audio_callback`) |
| `apps/api/urls.py` | URL routing for both endpoints |
| `apps/podcast/management/commands/local_audio_worker.py` | Management command |
| `apps/podcast/tasks.py` | `step_audio_generation` task (pauses workflow) |
| `apps/api/tests/test_worker_views.py` | API endpoint tests |
| `apps/podcast/tests/test_local_audio_worker.py` | Management command tests |
