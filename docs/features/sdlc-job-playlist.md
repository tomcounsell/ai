# SDLC Job Playlist

Sequential SDLC issue processing via a Redis-backed playlist with Observer-driven auto-progression.

## Overview

The playlist feature allows agents to enqueue multiple GitHub issues for sequential SDLC processing. When one issue's pipeline completes, the Observer hook automatically pops the next issue from the playlist and schedules it. Failed jobs get one retry (requeued to the end of the playlist).

## Usage

### Enqueue a playlist

```bash
python -m tools.job_scheduler playlist --issues 440 445 397
```

This validates all issues via `gh issue view`, creates a Redis list, and immediately schedules the first issue. The remaining issues are processed sequentially as each completes.

### Check playlist status

```bash
python -m tools.job_scheduler playlist-status
```

Shows the current playlist contents, length, and retry counts per issue.

## Architecture

### Data Flow

1. Agent invokes `job_scheduler.py playlist --issues 440 445 397`
2. Tool validates issues, populates Redis list `playlist:{project_key}`, schedules first issue
3. Observer steers the SDLC pipeline for the current issue
4. On job completion, `_playlist_hook` in `agent/job_queue.py` pops the next issue and schedules it
5. On job failure, the failed issue is requeued to the end (max 1 retry), then the next issue is popped
6. When the playlist is empty, a summary is logged

### Redis Data Structures

- **`playlist:{project_key}`**: Redis list holding ordered issue numbers (FIFO)
- **`playlist_retries:{project_key}`**: Redis hash tracking retry counts per issue number

### Observer Playlist Hook

Located in `agent/job_queue.py::_complete_job()`. After a job is deleted from Redis, if it was an SDLC job (`classification_type == "sdlc"`), the hook:

1. On failure: attempts to requeue the failed issue (max 1 retry)
2. Pops the next issue from the playlist
3. Guards against scheduling the same issue that just completed (loop prevention)
4. Schedules the next issue via subprocess call to `job_scheduler.py schedule`
5. If playlist is empty, delivers a summary

### Persona Gate

The `_check_persona_permission()` function in `tools/job_scheduler.py` enforces persona-based restrictions:

- **developer**: Full access to all operations
- **project-manager**: Full access to all operations
- **teammate**: Blocked from `schedule` and `playlist` operations; can use `status`, `push`, `bump`, `pop`, `cancel`, `playlist-status`

The persona is read from `os.environ.get("PERSONA", "developer")`. The default is intentionally permissive.

### Summarizer Evidence Hardening

The summarizer evidence pattern for "scheduled/queued" now requires a job ID artifact (e.g., `job-abc123`). The bare word "scheduled" is no longer accepted as evidence of real action.

Pattern: `r"\b(?:scheduled|queued)\b.*\bjob[_-]?[a-f0-9]{6,}\b"`

## Relevant Files

| File | Purpose |
|------|---------|
| `tools/job_scheduler.py` | Playlist subcommand, persona gate, Redis playlist operations |
| `agent/job_queue.py` | Observer playlist hook (`_playlist_hook`, `_deliver_playlist_summary`) |
| `bridge/summarizer.py` | Hardened evidence pattern for scheduled/queued |
| `tests/unit/test_sdlc_playlist.py` | Playlist and Observer hook tests |
| `tests/unit/test_job_scheduler_persona.py` | Persona gate tests |
| `~/Desktop/Valor/personas/*.md` | Soul files with job scheduler documentation |

## Failure Handling

- **Failed jobs**: Requeued to end of playlist (max 1 retry per issue)
- **Invalid issues**: Skipped during playlist creation with structured error reporting
- **Empty playlist**: Pop returns None cleanly, summary logged
- **Same-issue guard**: If the next issue matches the just-completed issue, it is skipped to prevent loops
- **Rate limiting**: Existing `MAX_SCHEDULED_PER_HOUR = 30` safety net applies

## Tracking

- **Issue**: [#450](https://github.com/tomcounsell/ai/issues/450)
