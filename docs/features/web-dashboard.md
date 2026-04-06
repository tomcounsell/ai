# Web Dashboard: Session Table, Project Context, and Data Persistence

The main dashboard at `localhost:8500/sdlc/` displays all agent sessions in a unified table with SDLC stage pills, project metadata popovers, and configurable data retention. This document covers the data flow, stage inference logic, project metadata resolution, and configuration.

## Data Flow

```
AgentSession (Popoto/Redis)
    |
    v
ui/data/sdlc.py
    _session_to_pipeline()         # converts AgentSession -> PipelineProgress
        _parse_stage_states()      # parses stage_states JSON -> StageState list (all sessions)
        _get_project_metadata()    # resolves project_key -> name + metadata
    |
    v
PipelineProgress (Pydantic model)
    |
    v
ui/routers/sdlc.py                # FastAPI route handler
    |
    v
ui/templates/_partials/sessions_table.html   # Jinja2 template
    |
    v
Browser (HTMX polling for live updates)
```

### Query Path

`get_all_sessions()` in `ui/data/sdlc.py` is the primary query function:

1. Calls `AgentSession.query.all()` to fetch all sessions from Redis via Popoto
2. Splits sessions into **active** (running/pending/in_progress/active/waiting_for_children) and **inactive** (everything else)
3. Filters inactive sessions by the retention cutoff (see Configuration below)
4. Uses a timestamp fallback chain for ordering and filtering: `completed_at -> updated_at -> started_at -> created_at`
5. Returns active sessions (always shown, no cap) followed by up to `limit` inactive sessions, sorted newest-first

## SDLC Stage Pills

Each session row displays a horizontal strip of SDLC stage indicators. The eight stages in pipeline order are: ISSUE, PLAN, CRITIQUE, BUILD, TEST, REVIEW, DOCS, MERGE.

### Primary Source: `stage_states`

The `AgentSession.stage_states` field (populated by the PipelineStateMachine since issue #492) is the authoritative source. `_parse_stage_states()` handles three input formats:

- **JSON string**: parsed with `json.loads()` first
- **Dict**: used directly
- **Nested dict**: `{"STAGE": {"status": "completed", ...}}` -- extracts the inner `status` value

Internal metadata keys like `_patch_cycle_count` and `_critique_cycle_count` are ignored because the parser only iterates over the known `SDLC_STAGES` list.

### Stored State Only

All sessions (with or without a slug) use `_parse_stage_states()` to read `AgentSession.stage_states` directly. Artifact inference was removed in PR #733 (issue #729) — the dashboard no longer checks plan files on disk, PR existence, or GitHub review state. `stage_states` is the single source of truth for the dashboard just as it is for the merge gate.

### CSS Rendering

The template maps `StageState` properties to CSS classes:

| Property | CSS Class | Visual |
|----------|-----------|--------|
| `is_done` (completed/skipped) | `completed` | Green |
| `is_active` (in_progress) | `in-progress` | Blue |
| `is_failed` (failed) | `failed` | Red |
| `is_ready` (ready) | `ready` | Distinct from pending |
| default (pending) | (no class) | Dim/muted |

## Project Metadata Popover

The Project column is the first column in the sessions table. It shows the human-readable project name resolved from `projects.json` instead of the raw `project_key`.

### Resolution Logic

`_get_project_metadata()` in `ui/data/sdlc.py`:

1. Calls `_load_project_configs()` which delegates to `bridge.routing.load_config()` to read `projects.json`
2. Results are cached at module level with a 60-second TTL to avoid re-reading on every request
3. Looks up the project by `project_key` and extracts:
   - `name` -- human-readable project name (falls back to `project_key`)
   - `telegram_chat` -- from `project.telegram.groups`
   - `github_repo` -- from `project.github_repo`
   - `working_dir` -- from `project.working_directory`
   - `tech_stack` -- from `project.context.tech_stack`
   - `machine` -- from `project.machine` or `project.context.machine`

### Popover UI

When metadata exists, hovering over the project name reveals a popover showing the resolved fields. The popover is pure CSS (no JavaScript framework) and displays key-value rows for each available metadata field. If `projects.json` is unavailable or the `project_key` is not found, the column falls back to displaying the raw `project_key` string with no popover.

## Configuration

| Variable | Default | Description |
|----------|---------|-------------|
| `DASHBOARD_RETENTION_HOURS` | `48` | How many hours of inactive sessions to show on the dashboard. Active sessions are always shown regardless of age. Set via environment variable. |
| `UI_PORT` | `8500` | Port for the FastAPI server (inherited from web-ui infrastructure). |

### Setting Retention

```bash
# Show last 7 days of sessions
DASHBOARD_RETENTION_HOURS=168 python -m ui.app

# Show only today's sessions
DASHBOARD_RETENTION_HOURS=24 python -m ui.app
```

## Persistence Across Restarts

Sessions are stored in Redis via Popoto and survive bridge restarts. The dashboard reads directly from Redis, so session data persists as long as Redis is running. Key design decisions for data persistence:

- **Timestamp fallback chain**: `get_all_sessions()` uses `completed_at or updated_at or started_at or created_at` so sessions with `updated_at=None` are not silently dropped from the retention filter
- **Active sessions always shown**: Sessions with active status bypass the retention cutoff entirely
- **Inactive session limit**: Up to 50 inactive sessions are returned per query (up from the original 16) to support reviewing past work

## Pydantic Models

### `PipelineProgress`

The central data model for dashboard display, containing:

- Session identity: `agent_session_id`, `session_id`, `session_type`, `status`, `slug`
- Project context: `project_key`, `project_name`, `project_metadata`
- Timestamps: `created_at`, `started_at`, `completed_at`, `updated_at`
- SDLC state: `stages` (list of `StageState`), `current_stage`, `events`
- Links: `issue_url`, `plan_url`, `pr_url`
- Computed properties: `duration`, `is_active`, `is_complete`, `display_name`

### `StageState`

Represents a single SDLC stage with `name` and `status` fields, plus boolean properties (`is_active`, `is_done`, `is_failed`, `is_ready`) used by the template for CSS class mapping.

### `PipelineEvent`

A history entry with `role`, `text`, and optional `timestamp`, parsed from the session's history list.

## Related

- [Web UI](web-ui.md) -- Infrastructure, directory structure, and how to add new dashboards
- [SDLC Observer](sdlc-observer.md) -- Pipeline tracking dashboard at `/sdlc/`
- [Pipeline State Machine](pipeline-state-machine.md) -- How `stage_states` gets populated
- [Agent Session Model](agent-session-model.md) -- The underlying Redis model
