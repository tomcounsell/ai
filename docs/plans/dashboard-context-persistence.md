---
status: In Progress
type: bug+enhancement
appetite: Medium
owner: Valor
created: 2026-03-26
tracking: https://github.com/tomcounsell/ai/issues/549
last_comment_id:
---

# Dashboard: Project Context, SDLC Stage Visibility, and Data Persistence

## Problem

The web dashboard at `/` has three visibility and persistence gaps:

1. **SDLC stage pills not rendering** -- The template renders stage pills from `s.stages`, but sessions that go through the SDLC pipeline show empty dashes. Issue #492 wired `start_stage()` into the pre_tool_use hook, so `stage_states` should now be populated on parent PM sessions. The rendering path (`_parse_stage_states` in `ui/data/sdlc.py`) looks correct for both JSON string and dict inputs. The likely remaining gap is that the UI table only shows the parent PM session's `stage_states`, but `_session_to_pipeline()` reads `session.stage_states` directly -- if the field is still None on some sessions (e.g., non-SDLC chat sessions, or sessions created before #492 was merged), the pills show as dashes. Need to verify the write path end-to-end and add a fallback that infers stage info from `history` entries when `stage_states` is empty.

2. **Project column lacks context** -- The project column shows a raw `project_key` string (e.g., "popoto") as the fourth column. It should be the first column, show the human-readable project name from `projects.json`, and include a metadata popover on hover showing Telegram chat, GitHub repo, working directory, tech stack, and machine info.

3. **Bridge restart clears dashboard data** -- Sessions are stored in Redis via Popoto, so they should survive restarts. But `get_all_sessions()` applies a 48-hour cutoff filter on `last_activity` / `completed_at` / `created_at`. If `last_activity` is not being updated during session lifecycle (e.g., stays None), sessions fall through the filter. Also, Popoto index invalidation after restart (see popoto repo #283) may cause `AgentSession.query.all()` to return an empty set until new records are written.

## Solution

### Sub-problem 1: SDLC stage pills

**Root cause investigation and fix:**

- [ ] Add logging to `_session_to_pipeline()` to trace when `stage_states` is None vs empty vs populated -- deploy temporarily to confirm the write path from #492 is working
- [ ] In `_parse_stage_states()`, handle the `_patch_cycle_count` / `_critique_cycle_count` metadata keys that PipelineStateMachine stores alongside stage data (currently these get filtered out correctly since only `SDLC_STAGES` keys are iterated, but verify)
- [ ] Add a fallback in `_session_to_pipeline()`: when `stage_states` is None but `history` contains `[stage]` entries, infer stage states from history for legacy/pre-#492 sessions
- [ ] Verify that `SDLC_STAGES` in `ui/data/sdlc.py` matches `DISPLAY_STAGES` in `bridge/pipeline_graph.py` -- currently both are `["ISSUE", "PLAN", "CRITIQUE", "BUILD", "TEST", "REVIEW", "DOCS", "MERGE"]`, which is correct

**Template rendering verification:**

- [ ] Confirm CSS classes `completed`, `in-progress`, `failed` in `sessions_table.html` line 44 map correctly to the status strings from `StageState` (the template uses `stage.is_done`, `stage.is_active`, `stage.is_failed` properties which check `completed/skipped`, `in_progress`, `failed` respectively -- these align)
- [ ] Add a `ready` CSS state so stages marked "ready" (next to run) get a visual indicator distinct from "pending"

### Sub-problem 2: Project column as first with metadata popover

**Data layer changes (`ui/data/sdlc.py`):**

- [ ] Add a `project_name` field to `PipelineProgress` (default to `project_key`)
- [ ] Add a `project_metadata` field to `PipelineProgress` (dict with chat name, GitHub repo, working dir, tech stack, machine)
- [ ] Create a `_load_project_configs()` helper that calls `bridge.routing.load_config()` once and caches the result (module-level cache with TTL to avoid re-reading on every request)
- [ ] In `_session_to_pipeline()`, look up the project config by `project_key` and populate `project_name` and `project_metadata`

**Template changes (`sessions_table.html`):**

- [ ] Move project column to be the first `<th>` / `<td>` in the table
- [ ] Replace `{{ s.project_key or '-' }}` with `{{ s.project_name or s.project_key or '-' }}`
- [ ] Add a popover container triggered on hover/click that displays `project_metadata` fields:
  - Telegram chat name and ID
  - GitHub repo URL
  - Working directory path
  - Tech stack (from `context.tech_stack` in projects.json)
  - Machine name (from top-level or context)
- [ ] Style the popover with existing CSS patterns (or add minimal CSS to `static/css/`)

### Sub-problem 3: Data persistence across restarts

**Fix `get_all_sessions()` time filter:**

- [ ] Change the inactive session cutoff from 48h to a configurable retention period (default 24h as per issue, but allow override via env var `DASHBOARD_RETENTION_HOURS`)
- [ ] Fix the timestamp fallback chain: use `completed_at or last_activity or started_at or created_at` (currently `last_activity` is first, but it is often None)
- [ ] Increase the default `limit` from 16 to 50 for inactive sessions -- 16 is too low for reviewing past work

**Fix `last_activity` population:**

- [ ] Audit where `last_activity` is set on `AgentSession` -- grep for assignments to ensure it is updated during session lifecycle (at minimum: on status transitions, on auto-continue ticks, on steering message receipt)
- [ ] If `last_activity` is not being reliably updated, add a `touch_activity()` helper method on `AgentSession` that sets `last_activity = time.time()` and saves, then wire it into the worker loop and lifecycle transition points

**Popoto index resilience:**

- [ ] Test `AgentSession.query.all()` behavior after a fresh Redis connection (simulate restart). If Popoto indexes are stale, the query returns empty even though keys exist
- [ ] Add a startup probe to `ui/app.py` that logs the session count on boot so operators can detect index staleness
- [ ] If Popoto index rebuild is needed, add a `rebuild_indexes()` call to the UI app startup (Popoto provides `Model.query.rebuild_index()` or similar)

## Success Criteria

1. SDLC stage pills render with correct status colors (completed=green, in-progress=blue, failed=red) for sessions that have populated `stage_states` via the pipeline state machine
2. Sessions with no `stage_states` but with `[stage]` history entries show inferred stage pills (graceful fallback for pre-#492 sessions)
3. Project column is the first column in the sessions table
4. Project column shows the human-readable name from `projects.json` instead of the raw `project_key`
5. Hovering/clicking the project name shows a popover with: Telegram chat name, GitHub repo, working directory, tech stack, machine
6. Completed sessions remain visible on the dashboard after bridge restart for at least 24 hours (configurable via `DASHBOARD_RETENTION_HOURS`)
7. `get_all_sessions()` correctly falls through timestamp fields (`completed_at -> last_activity -> started_at -> created_at`) so sessions with `last_activity=None` are not silently dropped
8. All existing unit tests in `test_ui_sdlc_data.py` continue to pass
9. New unit tests cover: history fallback, project metadata population, retention filter behavior

## No-Gos

- Do not add a database migration or new storage backend -- Popoto/Redis is the store
- Do not make projects.json loading async -- the sync path is fine for the dashboard
- Do not cache project configs indefinitely -- use a short TTL (60s) so changes propagate
- Do not change the AgentSession model schema -- only add derived fields to PipelineProgress
- Do not add JavaScript frameworks -- use vanilla JS for the popover interaction

## Update System

No update system changes required. This is a web UI change that deploys with the normal `git pull` and bridge restart cycle. The `projects.json` path resolution already handles multi-machine deployment via `_resolve_config_path()`.

## Agent Integration

No agent integration required. This is a UI-only change. The agent does not interact with the dashboard -- it reads/writes AgentSession records via Popoto, and the dashboard reads those same records. The only cross-boundary concern is ensuring `stage_states` is written correctly by the agent hooks, which was already addressed by issue #492.

## Failure Path Test Strategy

- **stage_states is None**: `_parse_stage_states` returns empty list, template shows dashes (graceful degradation)
- **projects.json not found**: `_load_project_configs()` returns empty dict, project column falls back to raw `project_key`
- **Popoto index stale after restart**: Startup probe logs warning, `get_all_sessions()` returns empty list with logged warning, UI shows "No agent sessions" empty state
- **project_key not in projects.json**: `project_name` defaults to `project_key`, `project_metadata` is empty dict, popover shows "No metadata available"

## Test Impact

- [ ] `tests/unit/test_ui_sdlc_data.py::TestStageStateParsing` -- UPDATE: add test for history-based fallback when stage_states is None
- [ ] `tests/unit/test_ui_sdlc_data.py::TestPipelineProgress` -- UPDATE: add tests for new `project_name` and `project_metadata` fields
- [ ] `tests/unit/test_ui_sdlc_data.py::TestSdlcQueryFunctions::test_get_all_sessions_returns_list` -- UPDATE: verify retention filter behavior with configurable hours

## Test Plan

### Unit tests (`tests/unit/test_ui_sdlc_data.py`)

- [ ] Test `_parse_stage_states` with real PipelineStateMachine JSON output (including `_patch_cycle_count`)
- [ ] Test `_session_to_pipeline` history fallback when `stage_states` is None but history has stage entries
- [ ] Test `PipelineProgress.project_name` defaults to `project_key` when config unavailable
- [ ] Test `PipelineProgress.project_metadata` populates correctly from mock project config
- [ ] Test `get_all_sessions` retention filter with various timestamp combinations
- [ ] Test `get_all_sessions` with `last_activity=None` sessions (should fall through to `created_at`)

### Integration tests

- [ ] Test that `_load_project_configs()` successfully loads from `projects.json` (real file read)
- [ ] Test round-trip: create AgentSession with `stage_states`, query via `get_all_sessions`, verify stages render

### Manual verification

- [ ] Start UI (`python -m ui.app`), verify stage pills render for sessions with populated `stage_states`
- [ ] Verify project column is first and shows human-readable names
- [ ] Hover project name, verify popover shows metadata
- [ ] Restart bridge, reload dashboard, verify completed sessions still visible

## Rabbit Holes

- **Real-time updates via WebSocket/SSE**: Out of scope. The dashboard already uses HTMX polling which is sufficient.
- **Popoto index rebuild automation**: If the Popoto index issue (#283) is systemic, that is a separate Popoto-level fix, not a dashboard fix. We add a diagnostic probe only.
- **Per-project dashboard pages**: The popover gives quick context; full project detail pages are a separate feature.
- **Historical stage_states backfill**: Sessions created before #492 will show dashes unless the history fallback covers them. Do not attempt to backfill Redis records.

## Documentation

- [ ] Update `docs/features/README.md` to add entry for dashboard improvements
- [ ] Create `docs/features/web-dashboard.md` documenting the dashboard's data flow, configuration (retention hours env var), and project metadata popover
