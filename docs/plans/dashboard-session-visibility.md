---
status: Planning
type: bug
appetite: Medium
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/657
last_comment_id:
---

# Dashboard Session Visibility: Fix Broken Timestamps, Surface Hidden Fields, Add Parent/Child Hierarchy

## Problem

The dashboard's Agent Sessions table is the primary operational snapshot for monitoring what agent sessions are doing across all projects. Multiple data bugs and visibility gaps make it unreliable.

**Current behavior:**
1. Started and Duration columns always show "-" because `_safe_float()` does not handle `datetime.datetime` objects from Popoto fields
2. Completed sessions are invisible because the retention filter depends on `_safe_float()` which returns 0 for all timestamps, placing every session before the 48-hour cutoff
3. Stale "running" sessions show the same green badge as genuinely active ones -- no visual distinction for sessions stuck for hours
4. Name column shows raw truncated Telegram messages instead of `context_summary`
5. Parent/child sessions appear as unrelated flat rows despite `parent_agent_session_id` and `get_children()` existing on the model
6. Rich metadata (`context_summary`, `expectations`, `turn_count`, `tool_call_count`, `watchdog_unhealthy`, `priority`, `classification_type`) is populated at runtime but absent from `PipelineProgress` and templates
7. `config/reflections.yaml` has a stale `daily-maintenance` entry and only 2 enabled reflections

**Desired outcome:**
Timestamps and durations work, completed sessions appear, stale sessions are visually flagged, parent/child relationships are visible, and key session metadata (activity level, what it is working on, what it is waiting for) is surfaced without clicking into detail views.

## Prior Art

- **#549 / PR #554**: Dashboard: project context, SDLC stage pills, data persistence -- Established the current dashboard structure with project metadata popovers and stage dots. The `PipelineProgress` model and `_session_to_pipeline()` were created here.
- **#592**: Audit AgentSession model -- Cleaned up field names and duplicates. Solidified the `parent_agent_session_id` / `get_children()` API.
- **#608 / PR #616**: Rename job terminology to agent_session -- Template still references `s.job_id` which maps via property alias.
- **#648 / PR #652**: Rename SessionType.CHAT to PM + add TEAMMATE -- Touched `ui/data/sdlc.py` `_resolve_persona_display()` for new session types. Does not fix `_safe_float()` or add new fields. Recently merged.
- **#656**: Dashboard shows stale pipeline state -- Complementary issue about SDLC stage accuracy (artifact inference). Distinct from this issue which is about session metadata and data layer bugs.

## Data Flow

1. **Redis (Popoto)**: `AgentSession` records stored with `datetime.datetime` fields for `created_at`, `started_at`, `completed_at`, `updated_at`
2. **Data layer** (`ui/data/sdlc.py`): `get_all_sessions()` queries all `AgentSession` records, converts each via `_session_to_pipeline()` into `PipelineProgress` Pydantic models, then filters by retention cutoff and sorts
3. **Template** (`ui/templates/_partials/sessions_table.html`): Jinja2 renders each `PipelineProgress` as a table row, using `format_timestamp` and `format_duration` filters
4. **HTMX refresh**: `/_partials/sessions/` endpoint returns the table HTML every 5 seconds via HTMX polling
5. **JSON endpoint**: `/dashboard.json` serializes `PipelineProgress` models as JSON for programmatic consumption

The core bug is at step 2: `_safe_float()` checks `isinstance(val, int|float|str)` but `datetime.datetime` is none of those, so it returns `None`. This cascades: timestamps are `None` in `PipelineProgress`, so `format_timestamp` renders "-", `format_duration` renders "-", and the retention filter's `_best_timestamp()` returns 0 for every session (placing all completed sessions before the cutoff).

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 0
- Review rounds: 1

## Prerequisites

No prerequisites -- this work modifies existing dashboard code with no external dependencies.

## Solution

### Key Elements

- **`_safe_float()` datetime fix**: Add `datetime.datetime` isinstance check with `.timestamp()` conversion -- one-line fix that unblocks timestamps, durations, and retention filter
- **PipelineProgress enrichment**: Add new fields for parent/child hierarchy and session metadata
- **Staleness detection**: Compare `updated_at` to `now()` for running sessions, flag those >10 minutes stale
- **Template updates**: Show `context_summary`, `expectations`, activity metrics, staleness indicators, and parent/child grouping
- **Reflections config cleanup**: Remove disabled `daily-maintenance`, keep actual active reflections

### Flow

**Redis AgentSession** --> `_safe_float()` (now handles datetime) --> `_session_to_pipeline()` (populates new fields) --> `get_all_sessions()` (groups parent/child, flags stale) --> **Template** (renders enriched rows with hierarchy)

### Technical Approach

- Fix `_safe_float()` by adding `import datetime` and checking `isinstance(val, datetime.datetime)` before calling `val.timestamp()`
- Add fields to `PipelineProgress`: `parent_agent_session_id`, `children`, `context_summary`, `expectations`, `turn_count`, `tool_call_count`, `watchdog_unhealthy`, `priority`, `classification_type`, `is_stale`
- In `_session_to_pipeline()`, populate new fields from the `AgentSession` attributes
- In `get_all_sessions()`, after building the flat list, group children under parents by matching `parent_agent_session_id` and remove children from the top-level list
- Avoid N+1 queries: do NOT call `get_children()` per session. Instead, build a dict of `parent_id -> [children]` from the already-loaded flat session list
- Add `is_stale` computed property: for sessions with status "running"/"active", check if `updated_at` is >10 minutes ago
- Template: render child sessions as indented sub-rows beneath their parent with a visual connector line
- Template: show `context_summary` in the Name column (fallback to truncated `message_text`)
- Template: for dormant sessions, show `expectations` inline as a muted subtitle
- Template: add `turn_count`/`tool_call_count` as a small activity badge
- Template: add CSS class `stale` to status badge for stale sessions, with a different color or icon
- Update `dashboard.json` to include all new fields (additive only, no breaking changes)

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `_safe_float()` handles all edge cases without raising: `None`, empty string, non-numeric string, Popoto field objects, and now `datetime` objects
- [ ] `_session_to_pipeline()` handles sessions with missing/None values for all new fields without raising
- [ ] Parent/child grouping handles orphaned children (parent_id set but parent not in the current session list) gracefully

### Empty/Invalid Input Handling
- [ ] `_safe_float()` with a `datetime` that has no timezone info still works (calls `.timestamp()` which uses local time)
- [ ] `context_summary`, `expectations` as None/empty string render cleanly in template (fallback to "-" or message_text)
- [ ] `turn_count`/`tool_call_count` as 0 or None render without error

### Error State Rendering
- [ ] Stale sessions render with visual distinction (CSS class applied)
- [ ] Sessions with `watchdog_unhealthy` set show a warning indicator
- [ ] Orphaned child sessions (parent not visible) still render as top-level rows

## Test Impact

- [ ] `tests/unit/test_ui_sdlc_data.py::TestSafeFloat` -- UPDATE: add test cases for `datetime.datetime` input
- [ ] `tests/unit/test_ui_sdlc_data.py::TestPipelineProgress` -- UPDATE: add tests for new fields (`context_summary`, `turn_count`, `children`, `is_stale`, etc.)
- [ ] `tests/unit/test_ui_sdlc_data.py::TestRetentionFilter` -- UPDATE: verify retention filter works correctly when timestamps are `datetime` objects (the fix enables this path)
- [ ] `tests/unit/test_ui_sdlc_data.py::test_session_to_pipeline_uses_history_fallback` -- UPDATE: mock session needs new fields added to avoid attribute errors

## Rabbit Holes

- **Real-time WebSocket updates**: The 5-second HTMX polling works fine. Do not replace it with WebSocket push.
- **Session detail view redesign**: This issue is about the table view. Expanding the inline detail panel is a separate effort.
- **Reflections scheduler rewrite**: Only update the YAML config. Do not change the scheduler logic or create new reflection functions.
- **N+1 get_children() queries**: Do NOT call `get_children()` on each session. Build the parent-child mapping from the already-loaded flat list in `get_all_sessions()`.
- **Complex tree rendering**: Parent/child is only 2 levels deep (PM spawns Dev). Do not build a generic recursive tree renderer.

## Risks

### Risk 1: Template changes break HTMX refresh
**Impact:** Sessions table stops auto-updating or renders broken HTML
**Mitigation:** Test the 5-second refresh cycle manually after changes. Keep template changes minimal and backward-compatible. New fields use conditional rendering.

### Risk 2: Parent/child grouping creates visual clutter
**Impact:** Dashboard becomes harder to scan with indented child rows
**Mitigation:** Child rows are visually subtle (smaller font, muted colors, indent). Collapse by default if the number of children exceeds 3.

## Race Conditions

No race conditions identified -- all dashboard operations are synchronous read-only queries against Redis. The HTMX refresh polls every 5 seconds and replaces the entire table HTML, so there is no stale state accumulation.

## No-Gos (Out of Scope)

- SDLC stage accuracy fixes (that is #656)
- Session detail view improvements
- Adding clickable links to parent/child sessions
- Pagination or infinite scroll for sessions
- Changing the HTMX refresh interval
- Creating new reflection functions or changing scheduler logic
- WebSocket-based real-time updates

## Update System

No update system changes required -- this is a dashboard-only change. The dashboard runs locally via `python -m ui.app` and does not require deployment steps or new dependencies.

## Agent Integration

No agent integration required -- this is a UI/dashboard change. No new MCP servers, no bridge changes, no new tools. The dashboard reads from existing `AgentSession` Popoto records that are already populated by the bridge and agent SDK.

## Documentation

- [ ] Update `docs/features/dashboard.md` (or create if missing) with the new session table fields and parent/child hierarchy behavior
- [ ] Add inline code comments on `_safe_float()` explaining the datetime handling
- [ ] Update docstrings for `PipelineProgress` model to document new fields

## Success Criteria

- [ ] `Started` and `Duration` columns show real data for all sessions (datetime-to-float conversion works)
- [ ] Completed and failed sessions appear in the dashboard (retention filter works correctly)
- [ ] Sessions with status "running" but `updated_at` >10 minutes ago are visually flagged as stale
- [ ] `context_summary` displayed in session table when available (falls back to truncated message)
- [ ] Dormant sessions show `expectations` inline
- [ ] `turn_count`/`tool_call_count` visible as activity metric
- [ ] Parent/child sessions are visually grouped (child sessions indented under parent)
- [ ] `dashboard.json` includes all new fields
- [ ] `priority` visually distinguished for urgent/high sessions
- [ ] `watchdog_unhealthy` flag renders a warning indicator on affected rows
- [ ] `config/reflections.yaml` reflects the actual active reflection set
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (data-layer)**
  - Name: data-layer-builder
  - Role: Fix `_safe_float()`, enrich `PipelineProgress` model, implement parent/child grouping in `get_all_sessions()`
  - Agent Type: builder
  - Resume: true

- **Builder (template)**
  - Name: template-builder
  - Role: Update sessions table template with new fields, staleness indicators, parent/child rendering
  - Agent Type: builder
  - Resume: true

- **Builder (config)**
  - Name: config-builder
  - Role: Update `config/reflections.yaml` and `dashboard.json` endpoint
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: test-engineer
  - Role: Write and update unit tests for all new behavior
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: final-validator
  - Role: Verify all success criteria, run full test suite, check template rendering
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Fix _safe_float() and enrich PipelineProgress model
- **Task ID**: build-data-layer
- **Depends On**: none
- **Validates**: tests/unit/test_ui_sdlc_data.py
- **Assigned To**: data-layer-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `import datetime` to `ui/data/sdlc.py`
- Add `isinstance(val, datetime.datetime)` check to `_safe_float()`, returning `val.timestamp()`
- Add new fields to `PipelineProgress`: `parent_agent_session_id: str | None`, `children: list[PipelineProgress] | None`, `context_summary: str | None`, `expectations: str | None`, `turn_count: int | None`, `tool_call_count: int | None`, `watchdog_unhealthy: str | None`, `priority: str | None`, `classification_type: str | None`, `is_stale: bool`
- Populate new fields in `_session_to_pipeline()` from AgentSession attributes
- Compute `is_stale` in `_session_to_pipeline()`: True if status is running/active and `updated_at` is >10 minutes ago
- In `get_all_sessions()`, after converting all sessions, build parent-child grouping from the flat list (no N+1 queries)
- Orphaned children (parent not in current list) remain as top-level rows
- Update `display_name` property to prefer `context_summary` over `slug` when available

### 2. Update sessions table template
- **Task ID**: build-template
- **Depends On**: build-data-layer
- **Validates**: manual visual inspection
- **Assigned To**: template-builder
- **Agent Type**: builder
- **Parallel**: false
- Show `context_summary` in Name column via updated `display_name` (fallback chain: context_summary -> slug -> truncated message_text)
- Add `expectations` as muted subtitle for dormant sessions
- Add `turn_count`/`tool_call_count` as small activity badge in a new column or inline
- Add CSS class `stale` to status badge when `is_stale` is True, render differently
- Add `watchdog_unhealthy` warning indicator icon/badge
- Add `priority` visual accent for urgent/high
- Render child sessions as indented sub-rows beneath parent with visual connector
- Add `classification_type` badge next to persona

### 3. Update dashboard.json endpoint and reflections config
- **Task ID**: build-config
- **Depends On**: build-data-layer
- **Validates**: curl -s localhost:8500/dashboard.json | python -m json.tool
- **Assigned To**: config-builder
- **Agent Type**: builder
- **Parallel**: false
- Add all new PipelineProgress fields to the `dashboard.json` serialization in `ui/app.py`
- Update `config/reflections.yaml`: remove disabled `daily-maintenance` entry, verify remaining entries match actual codebase

### 4. Write and update tests
- **Task ID**: build-tests
- **Depends On**: build-data-layer
- **Validates**: tests/unit/test_ui_sdlc_data.py
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Add `test_datetime_input` to `TestSafeFloat`: assert `_safe_float(datetime.datetime(2026, 1, 1))` returns correct float
- Add `test_datetime_with_timezone` to `TestSafeFloat`: assert `_safe_float(datetime.datetime(2026, 1, 1, tzinfo=datetime.UTC))` returns correct float
- Add tests for new PipelineProgress fields: `context_summary`, `expectations`, `turn_count`, `tool_call_count`, `is_stale`, `children`, `parent_agent_session_id`
- Add test for `display_name` property: verify `context_summary` preference in fallback chain
- Add test for parent/child grouping in `get_all_sessions()`: mock sessions with parent_agent_session_id set, verify children are nested and removed from top-level
- Add test for stale detection: session with status "running" and old `updated_at` should have `is_stale=True`
- Update existing `test_session_to_pipeline_uses_history_fallback` mock to include new attributes
- Add test for retention filter with datetime timestamps (the fix enables completed sessions to appear)

### 5. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: final-validator
- **Agent Type**: documentarian
- **Parallel**: false
- Update or create `docs/features/dashboard.md` with new session table fields
- Add inline docstring updates to `PipelineProgress` and `_safe_float()`

### 6. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: final-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_ui_sdlc_data.py -x -q`
- Run `python -m ruff check ui/ config/reflections.yaml`
- Run `python -m ruff format --check ui/`
- Verify all success criteria met

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_ui_sdlc_data.py -x -q` | exit code 0 |
| All tests pass | `pytest tests/ -x -q --timeout=120` | exit code 0 |
| Lint clean | `python -m ruff check ui/ tests/unit/test_ui_sdlc_data.py` | exit code 0 |
| Format clean | `python -m ruff format --check ui/ tests/unit/test_ui_sdlc_data.py` | exit code 0 |
| datetime handled | `python -c "from ui.data.sdlc import _safe_float; from datetime import datetime; assert _safe_float(datetime(2026,1,1)) is not None"` | exit code 0 |
| New fields exist | `python -c "from ui.data.sdlc import PipelineProgress; p = PipelineProgress(agent_session_id='x'); assert hasattr(p, 'context_summary')"` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

No open questions -- the issue is well-scoped with confirmed recon findings and all technical approaches are straightforward.
