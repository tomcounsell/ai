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
| Name | `display_name` property | Fallback chain: `slug` > issue/PR title (GitHub lookup) > `context_summary` > `MESSAGE:`/`FROM:` extracted from system prompt > `type • project` |
| Persona | `session_type` / `session_mode` | dev (blue), Teammate (green), PM (purple). `classification_type` badge shown alongside |
| Status | `status` field | Color-coded badge. Stale sessions (running >10 min without update) show dashed border + "(stale)" label |
| SDLC Stages | `stage_states` | Dot indicators: completed (green), in-progress (blue), failed (red), ready (yellow) |
| Started | `started_at` or `created_at` | Formatted timestamp |
| Duration | Computed from start to completion or now | Formatted duration |
| Links/Activity | `turn_count`/`tool_call_count`, issue/PR URLs | Activity badge shows turns/tool-calls; issue and PR links (captured via PostToolUse hook or backfilled from session history) |

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

### Lifecycle Iconography

All 8 non-terminal lifecycle states render with distinct glyphs in the row template (see [Session Lifecycle](session-lifecycle.md) for state semantics):
`running`, `pending`, `dormant`, `active`, `waiting_for_children`, `paused`, `paused_circuit`, `superseded`. Terminal statuses (`completed`, `failed`, `killed`, `abandoned`, `cancelled`, `superseded`) collapse the row into the terminal-status presentation.

## Liveness Signals

The dashboard exposes session liveness as state-of-truth so operators can answer "is this session actually progressing right now, or is it claimed-running-but-dead (ghost)?" without leaving the dashboard.

### Row-level signals (non-terminal sessions only)

- **Freshness chip** — age since `last_evidence_at` rendered as a colored chip via the `freshness_age` Jinja filter:
  - green (`freshness-fresh`) for `<60s`
  - amber (`freshness-warm`) for `<600s`
  - red (`freshness-stale`) for `>=600s`
- **Ghost badge** — when `process_alive == False` (the harness PID returned `ProcessLookupError` from a non-blocking `os.kill(pid, 0)` probe), the row renders a dashed-red `GHOST` badge to mark sessions whose harness subprocess has died but whose record still claims `running`/`active`.

### Modal Liveness section

`session_modal_content.html` renders a `Liveness` sub-table between Timing and SDLC, gated by the `_has_liveness` macro. Rows include:

- **PID** — `harness_pid` with one of three chips: alive (probe returned True), `GHOST — process dead` (probe returned False), or unknown (probe returned None: PID is None or `<= 0`, or `PermissionError`/`OSError`)
- `current_tool_name`, `last_evidence_at`, `last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`, `last_tool_use_at`, `last_turn_at`
- `recovery_attempts`, `reprieve_count`
- `watchdog_unhealthy` reason (when set)

### Process-alive probe

`ui/data/sdlc._check_process_alive(pid)` is a non-blocking `os.kill(pid, 0)` with tri-state return: `True` (alive), `False` (`ProcessLookupError` — ghost), or `None` (PID is None or `<= 0` to dodge process-group semantics, or `PermissionError`/`OSError`). The probe is gated to non-terminal probe statuses (`running`, `active`, `paused`, `paused_circuit`) — terminal sessions never trigger a probe.

### PID lifecycle invariant

`AgentSession.harness_pid` follows a single-writer subprocess-scoped contract owned by `_execute_agent_session` in `agent/session_executor.py`:

- Set on subprocess spawn via the `_on_sdk_started(pid)` closure
- Cleared on `proc.communicate()` return via the paired `_on_sdk_finished()` closure (`agent/messenger.py::notify_sdk_finished`)
- Defensive idempotent clear in the session-exit `finally` block as backstop for abnormal termination (worker crash, `CancelledError` before `proc.communicate()`)

The `notify_sdk_finished` callback is threaded through all three `_run_harness_subprocess` call sites in `agent/sdk_client.py` (primary spawn + image-dim fallback + stale-UUID fallback). See [PM Session Liveness](pm-session-liveness.md) for the broader evidence-based liveness model.

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

**Liveness:** `harness_pid`, `last_heartbeat_at`, `last_sdk_heartbeat_at`, `last_stdout_at`, `recovery_attempts`, `reprieve_count`, `process_alive`. Existing fields used by the row freshness chip and modal Liveness section: `current_tool_name`, `last_tool_use_at`, `last_turn_at`, `last_evidence_at`

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
