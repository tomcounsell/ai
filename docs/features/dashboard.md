# Dashboard

The web UI dashboard provides an operational snapshot of agent sessions across all projects.

**Start:** `python -m ui.app` (serves on `localhost:8500`)
**JSON API:** `curl -s localhost:8500/dashboard.json`

## Agent Sessions Table

The sessions table is the primary view, auto-refreshing every 5 seconds via HTMX polling.

### Columns

| Column | Source | Notes |
|--------|--------|-------|
| Project | `project_key` + `projects.json` lookup | Shows project name with metadata popover (repo, chat, stack, machine) |
| Name | `display_name` property | Fallback chain: `context_summary` > `slug` > truncated `message_text` |
| Persona | `session_type` / `session_mode` | Developer (blue), Teammate (green), Project Manager (purple). `classification_type` badge shown alongside |
| Status | `status` field | Color-coded badge. Stale sessions (running >10 min without update) show dashed border + "(stale)" label |
| SDLC Stages | `stage_states` | Dot indicators: completed (green), in-progress (blue), failed (red), ready (yellow) |
| Started | `started_at` or `created_at` | Formatted timestamp |
| Duration | Computed from start to completion or now | Formatted duration |
| Links/Activity | `turn_count`/`tool_call_count`, issue/PR URLs | Activity badge shows turns/tool-calls; issue and PR links |

### Parent/Child Hierarchy

Sessions spawned by a parent (e.g., PM spawning Dev) are grouped visually:
- Child rows appear indented beneath their parent with a connector line
- Grouping is built from the flat session list using `parent_agent_session_id` (no N+1 queries)
- Orphaned children (parent not in current list) appear as normal top-level rows
- Hierarchy is only 2 levels deep (PM > Dev)

### Staleness Detection

Sessions with status `running` or `active` whose `updated_at` is more than 10 minutes old are flagged as stale:
- Row gets reduced opacity
- Status badge shows dashed orange border and "(stale)" text
- `watchdog_unhealthy` sessions additionally show a warning "!" badge

### Priority and Classification

- Sessions with `priority` of "urgent" or "high" show a colored priority badge
- `classification_type` (e.g., "sdlc", "qa") appears as an outlined badge next to the persona

### Dormant Sessions

Sessions with status `dormant` show `expectations` as an italic subtitle in the Name column, indicating what the agent is waiting for from the human.

## Data Flow

1. **Redis (Popoto):** `AgentSession` records with `datetime.datetime` timestamp fields
2. **Data layer** (`ui/data/sdlc.py`): `_safe_float()` converts datetime objects to float timestamps via `.timestamp()`. `_session_to_pipeline()` maps all fields to `PipelineProgress` Pydantic models. `get_all_sessions()` groups children under parents
3. **Template** (`ui/templates/_partials/sessions_table.html`): Jinja2 macro renders each session row, with recursive rendering for child rows
4. **HTMX refresh:** `/_partials/sessions/` endpoint returns table HTML every 5 seconds

## PipelineProgress Model

The `PipelineProgress` Pydantic model is the serialization layer between Redis data and the UI/JSON API.

### Fields

**Core:** `agent_session_id`, `session_id`, `session_type`, `status`, `slug`, `message_text`, `project_key`, `project_name`, `project_metadata`, `branch_name`

**Timestamps:** `created_at`, `started_at`, `completed_at`, `updated_at` (all as float epoch seconds)

**Hierarchy:** `parent_agent_session_id`, `children` (list of nested `PipelineProgress`)

**Metadata:** `context_summary`, `expectations`, `turn_count`, `tool_call_count`, `watchdog_unhealthy`, `priority`, `classification_type`, `is_stale`

**SDLC:** `stages`, `current_stage`, `events`

**Links:** `issue_url`, `plan_url`, `pr_url`

## JSON API

`GET /dashboard.json` returns all fields above for each session, plus health, reflections, and machine info. The `children` array is recursively serialized. All fields are additive -- no breaking changes from prior versions.

## Retention

Inactive sessions are filtered by a configurable retention period (env var `DASHBOARD_RETENTION_HOURS`, default 48h). Active sessions always appear regardless of age.

## Related

- Issue: #657
- `ui/data/sdlc.py` -- Data layer
- `ui/templates/_partials/sessions_table.html` -- Template
- `ui/app.py` -- FastAPI routes including `/dashboard.json`
- `ui/static/style.css` -- Styles for badges, hierarchy, staleness
