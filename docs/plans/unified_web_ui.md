---
status: Planning
type: feature
appetite: Large
owner: Valor
created: 2026-03-24
tracking: https://github.com/tomcounsell/ai/issues/477
last_comment_id:
---

# Unified Web UI: Infrastructure, Reflections Dashboard, SDLC Observer

## Problem

**Current behavior:**
Zero web UI exists. Understanding system state requires SSH and manual inspection across multiple data sources:
- **Reflections**: Read markdown files in `logs/reflections/`, query Redis models via Python one-liners, tail log files
- **SDLC pipelines**: Check Telegram threads, read plan docs, inspect git branches, grep logs
- **System health**: No unified view of any operational data

**Desired outcome:**
A single localhost web application at `localhost:8500` serving two dashboards on shared infrastructure:
1. **Reflections Dashboard** — Visibility into scheduled task execution, history, errors, and ignore patterns
2. **SDLC Observer** — Real-time view of active development pipelines, stage transitions, and outcomes

## Prior Art

- **Issue #413**: Reflections Dashboard (closed, superseded by #477) — had a plan at `docs/plans/reflections-dashboard.md` (status: Ready) using Flask. Now superseded.
- **Issue #460**: Web UI infrastructure (closed, superseded by #477) — scoped shared infrastructure only
- **Issue #461**: SDLC Observer UI (closed, superseded by #477) — scoped observer only
- **Issue #319**: Structured logging/telemetry — established Redis-backed state pattern for observability
- **PR #490**: Consolidated SDLC stage tracking — unified `stage_states` field, wired hooks for stage transitions

No prior web UI code has ever been merged. This is fully greenfield.

## Data Flow

### Reflections Dashboard
1. **Entry point**: Browser requests `localhost:8500/reflections/`
2. **FastAPI router** (`ui/routers/reflections.py`): Routes to async view function
3. **Data layer** (`ui/data/reflections.py`): Queries Redis via Popoto models (`Reflection`, `ReflectionRun`, `ReflectionIgnore`), reads `config/reflections.yaml` for registry
4. **Templates** (`ui/templates/reflections/`): Jinja2 renders HTML with data, HTMX attributes for drill-down
5. **HTMX partials**: Browser requests HTML fragments via `hx-get`, FastAPI returns fragments (no JSON API)
6. **Log files**: When user drills into a run, the data layer reads the log file path from the model and serves content inline

### SDLC Observer
1. **Entry point**: Browser requests `localhost:8500/sdlc/`
2. **FastAPI router** (`ui/routers/sdlc.py`): Routes to async view function
3. **Data layer** (`ui/data/sdlc.py`): Queries `AgentSession` model, deserializes `stage_states` via Pydantic models, reads `history` field
4. **Templates** (`ui/templates/sdlc/`): Renders pipeline cards with stage indicators
5. **HTMX polling**: `hx-trigger="every 5s"` refreshes active pipeline state without WebSockets

## Architectural Impact

- **New dependencies**: `fastapi`, `uvicorn[standard]`, `jinja2` (add to `pyproject.toml`)
- **New directory**: `ui/` — entirely new top-level package
- **New Pydantic models**: `StageState`, `PipelineProgress` serializers for existing `stage_states` JSON
- **Model extension**: `Reflection` model gets `run_history` ListField for historical runs; large logs stored as file paths
- **Interface changes**: None to existing code. Data layer reads existing models read-only.
- **Coupling**: Low — `ui/` depends on `models/` and `config/`, nothing depends on `ui/`
- **Reversibility**: High — delete `ui/` directory and remove deps to fully revert

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (infrastructure choices confirmed above, design review during build)
- Review rounds: 1-2 (code review, visual review of dashboards)

Three layers that build sequentially: infrastructure -> reflections dashboard -> SDLC observer. Each has clear boundaries.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "import redis; redis.Redis().ping()"` | Popoto models need Redis |
| Port 8500 available | `lsof -i :8500 \| grep -v PID \|\| echo 'available'` | UI server port |

Run all checks: `python scripts/check_prerequisites.py docs/plans/unified_web_ui.md`

## Solution

### Key Elements

- **FastAPI app factory** (`ui/app.py`): Mounts sub-routers, configures Jinja2, serves static files, binds to localhost only
- **Shared base template**: Dark theme, top nav bar, HTMX from CDN, shared CSS
- **Reflections data layer**: Read-only queries against existing Popoto models + `config/reflections.yaml`
- **SDLC data layer**: Pydantic serializers over existing `AgentSession.stage_states` and `history` fields
- **HTMX interactivity**: Drill-down panels, polling for live updates, pagination — no custom JS

### Flow

**Root** (`/`) → Dashboard index listing available dashboards →

**Reflections** (`/reflections/`) → Overview grid of all reflections with status badges → Click reflection → Run history with drill-down → Click run → Step-level detail with log viewer

**SDLC** (`/sdlc/`) → Active pipeline cards with stage indicators → Click pipeline → Stage transition timeline with timestamps → Completed pipelines section below

### Technical Approach

- **Framework**: FastAPI + Jinja2 + uvicorn. Async-native, matches codebase patterns.
- **Frontend**: HTMX from CDN (`<script src="https://unpkg.com/htmx.org@2...">`) for all interactivity. Zero npm/node.
- **CSS**: Single `ui/static/style.css`. Dark theme, information-dense, monospace-friendly. Status badges (green/yellow/red). Terminal aesthetics.
- **Process**: `python -m ui.app` standalone process on `localhost:8500` (configurable via `UI_PORT` env var). Not exposed to network.
- **SDLC serialization**: Pydantic models (`StageState`, `PipelineProgress`) that deserialize the existing `stage_states` JSON dict and `history` list on `AgentSession`. No new Redis model — structure what's already there.
- **Reflection history**: Extend `Reflection` model with `run_history` ListField containing serialized run dicts (timestamp, status, duration, error). Large log content stays on disk; model stores file path only.

### Directory Structure

```
ui/
  __init__.py
  app.py              # FastAPI app factory, mounts sub-routers, entrypoint
  templates/
    base.html         # Shared layout: nav, HTMX script tag, CSS link
    index.html        # Root route: dashboard listing
    reflections/
      overview.html   # All reflections with status grid
      history.html    # Paginated run history for a reflection
      detail.html     # Single run detail with step drill-down
      schedule.html   # Upcoming runs by next-due
      ignores.html    # Active ignore patterns
      _partials/      # HTMX fragment templates
    sdlc/
      pipelines.html  # Active pipelines with stage indicators
      detail.html     # Single pipeline stage timeline
      completed.html  # Recent completions
      _partials/      # HTMX fragment templates
  static/
    style.css         # Dark theme, layout grid, badges, typography
  routers/
    __init__.py
    reflections.py    # Reflections dashboard routes
    sdlc.py           # SDLC observer routes
  data/
    __init__.py
    reflections.py    # Data access for reflection state
    sdlc.py           # Data access + Pydantic serializers for SDLC
```

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] FastAPI exception handlers: test that Popoto connection failures render a user-friendly error page, not a 500 traceback
- [ ] Data layer: test behavior when Redis is unreachable (graceful degradation, not crash)
- [ ] Log file reader: test when log file path in model points to a deleted/missing file

### Empty/Invalid Input Handling
- [ ] Reflections overview when no reflections are registered (empty `config/reflections.yaml`)
- [ ] SDLC observer when no AgentSessions have `stage_states` (empty state)
- [ ] Run history for a reflection with zero historical runs
- [ ] Malformed `stage_states` JSON on a session (graceful skip, not crash)

### Error State Rendering
- [ ] Failed reflection runs display error message and log content inline
- [ ] Failed SDLC stages show error details in the pipeline detail view
- [ ] Missing log files show "Log file not found" instead of blank or error

## Test Impact

No existing tests affected — this is a greenfield feature with no prior test coverage. The `ui/` package is entirely new and modifies no existing code. The only existing code change is adding `run_history` to the `Reflection` model, which is additive (new field, no existing behavior changed).

## Rabbit Holes

- **Authentication/authorization** — localhost-only, no auth needed. Do not build user management.
- **WebSockets for live updates** — HTMX polling every 5s is sufficient. WebSockets add complexity for negligible benefit at this scale.
- **JSON API endpoints** — The UI renders HTML server-side. No REST API layer needed.
- **Custom JavaScript** — HTMX handles all interactivity. Resist writing JS.
- **Responsive mobile design** — This is a developer tool viewed on a laptop. Desktop-only is fine.
- **Controlling/restarting systems from the UI** — Read-only. Action buttons are a separate feature.
- **CSS framework (Tailwind, Bootstrap)** — Single hand-written CSS file. The design is simple enough.

## Risks

### Risk 1: Popoto model queries in async context
**Impact:** FastAPI is async but Popoto models may use synchronous Redis calls, causing event loop blocking
**Mitigation:** Wrap Popoto queries in `asyncio.to_thread()` or use `def` (sync) route handlers — FastAPI handles sync routes in a threadpool automatically

### Risk 2: Large run_history ListField on Reflection model
**Impact:** If run_history grows unbounded, Redis memory increases and serialization slows
**Mitigation:** Cap `run_history` at last N entries (e.g., 100). Trim oldest on append. Log files on disk provide full history.

### Risk 3: HTMX CDN dependency
**Impact:** If CDN is unreachable, dashboard loses interactivity
**Mitigation:** Acceptable for a localhost dev tool. Can vendor the file later if needed.

## Race Conditions

No race conditions identified — the UI is read-only and single-process. It reads Popoto model state but never writes to shared models. The only write path is appending to `Reflection.run_history`, which happens in the reflection scheduler (not the UI).

## No-Gos (Out of Scope)

- Authentication or remote access
- Controlling/restarting systems from the UI (read-only)
- Telegram integration from the dashboard
- npm, Node.js, or any JS build tooling
- Database migrations (Popoto/Redis is schemaless)
- Custom JavaScript beyond HTMX
- Mobile-responsive design
- JSON REST API (HTML-only, HTMX-driven)

## Update System

New dependencies must be propagated to all machines:
- Add `fastapi`, `uvicorn[standard]`, `jinja2` to `pyproject.toml`
- The `/update` skill's dependency sync step (`pip install -e .` or equivalent) will pick these up automatically
- No new config files or env vars required (UI_PORT is optional, defaults to 8500)
- No migration steps — the `ui/` directory and extended model fields are purely additive
- Future: could add a launchd service for auto-start, but not in this scope

## Agent Integration

No agent integration required — this is a standalone localhost web server for human use. The agent does not need to invoke or interact with the dashboard. The data layer reads the same Redis models the agent writes to, but there's no MCP server, no bridge integration, and no tool wrapping needed.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/web-ui.md` describing the web UI infrastructure, how to start it, how to add new dashboards
- [ ] Create `docs/features/reflections-dashboard.md` documenting the reflections dashboard views and data sources
- [ ] Create `docs/features/sdlc-observer.md` documenting the SDLC observer views and data sources
- [ ] Add entries to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstrings on FastAPI app factory and router registration
- [ ] Docstrings on Pydantic serializer models for SDLC state

## Success Criteria

- [ ] `python -m ui.app` starts FastAPI server on `localhost:8500`
- [ ] Root route (`/`) lists both dashboards with descriptions
- [ ] Base template includes HTMX from CDN, shared dark-theme CSS, top nav bar
- [ ] **Reflections**: Overview shows all reflections from registry with live status from Redis
- [ ] **Reflections**: Each reflection shows name, last run, status (color-coded), next due, run count, duration
- [ ] **Reflections**: Run history view with paginated past runs per reflection
- [ ] **Reflections**: Run detail with step-level drill-down for daily-maintenance
- [ ] **Reflections**: Failed runs show error message and log content inline
- [ ] **Reflections**: Schedule view shows upcoming runs ordered by next-due
- [ ] **Reflections**: Ignore patterns page lists active entries with pattern, reason, expiry
- [ ] **SDLC**: Active pipelines view shows in-progress work items with current stage
- [ ] **SDLC**: Horizontal stage indicator with current stage highlighted
- [ ] **SDLC**: Pipeline detail with stage transition history and timestamps
- [ ] **SDLC**: Failed stages show error details inline
- [ ] **SDLC**: Recent completions with total duration and outcome
- [ ] **SDLC**: Auto-refreshes via HTMX polling
- [ ] **SDLC**: Links to GitHub issue, PR, and plan doc where available
- [ ] All dashboards are read-only
- [ ] No npm, node, or JS framework dependencies
- [ ] Localhost binding only — not exposed to network
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (infrastructure)**
  - Name: ui-infra-builder
  - Role: FastAPI app factory, base templates, CSS, navigation, process entrypoint
  - Agent Type: builder
  - Resume: true

- **Builder (reflections)**
  - Name: reflections-builder
  - Role: Reflections data layer, router, templates, model extension
  - Agent Type: builder
  - Resume: true

- **Builder (sdlc)**
  - Name: sdlc-builder
  - Role: SDLC data layer with Pydantic serializers, router, templates
  - Agent Type: builder
  - Resume: true

- **Designer (CSS/templates)**
  - Name: ui-designer
  - Role: Dark theme CSS, status badges, stage indicators, layout polish
  - Agent Type: designer
  - Resume: true

- **Validator (full)**
  - Name: ui-validator
  - Role: Verify all success criteria, test server startup, review all views
  - Agent Type: validator
  - Resume: true

- **Test Engineer**
  - Name: ui-test-engineer
  - Role: Write tests for data layers, route handlers, and template rendering
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: ui-documentarian
  - Role: Feature docs for web UI, reflections dashboard, SDLC observer
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Infrastructure: FastAPI app, base templates, CSS
- **Task ID**: build-infra
- **Depends On**: none
- **Validates**: `python -c "from ui.app import create_app; print('ok')"`, `pytest tests/unit/test_ui_app.py` (create)
- **Assigned To**: ui-infra-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `fastapi`, `uvicorn[standard]`, `jinja2` to `pyproject.toml` and install
- Create `ui/__init__.py`, `ui/app.py` with FastAPI app factory, Jinja2 configuration, static file mounting
- Create `ui/templates/base.html` with HTMX CDN script, CSS link, top nav bar, content block
- Create `ui/templates/index.html` listing mounted dashboards
- Create `ui/static/style.css` with dark theme, layout grid, status badges, typography, collapsible sections
- Create `ui/routers/__init__.py` (empty)
- Bind to `127.0.0.1:{UI_PORT}` (default 8500), add `__main__` entrypoint
- Create `ui/data/__init__.py` (empty)

### 2. Reflections: data layer and model extension
- **Task ID**: build-reflections-data
- **Depends On**: build-infra
- **Validates**: `pytest tests/unit/test_ui_reflections_data.py` (create)
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: false
- Extend `Reflection` model: add `run_history` ListField for historical run dicts (timestamp, status, duration, error, log_path)
- Update `Reflection.mark_completed()` to append to `run_history` (capped at 100 entries)
- Create `ui/data/reflections.py`: functions to query all reflections, get run history, get schedule, get ignore patterns, read log file content by path
- Load registry from `config/reflections.yaml` for descriptions and intervals

### 3. Reflections: router and templates
- **Task ID**: build-reflections-ui
- **Depends On**: build-reflections-data
- **Validates**: `pytest tests/unit/test_ui_reflections_routes.py` (create)
- **Assigned To**: reflections-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `ui/routers/reflections.py` with routes: overview, schedule, history, detail, ignores
- Create HTMX partial routes for drill-down panels (run detail, log viewer, step expansion)
- Create all templates in `ui/templates/reflections/`: overview, history, detail, schedule, ignores
- Create HTMX partial templates in `ui/templates/reflections/_partials/`
- Mount reflections router in `ui/app.py`

### 4. SDLC: Pydantic serializers and data layer
- **Task ID**: build-sdlc-data
- **Depends On**: build-infra
- **Validates**: `pytest tests/unit/test_ui_sdlc_data.py` (create)
- **Assigned To**: sdlc-builder
- **Agent Type**: builder
- **Parallel**: true (parallel with build-reflections-data)
- Create `ui/data/sdlc.py` with Pydantic models: `StageState`, `PipelineProgress`, `PipelineEvent`
- Deserialize `AgentSession.stage_states` JSON into typed Pydantic objects
- Parse `AgentSession.history` entries to extract stage transition events with timestamps
- Functions: get active pipelines, get pipeline detail, get recent completions
- Extract links (issue_url, pr_url, plan_url) from session

### 5. SDLC: router and templates
- **Task ID**: build-sdlc-ui
- **Depends On**: build-sdlc-data
- **Validates**: `pytest tests/unit/test_ui_sdlc_routes.py` (create)
- **Assigned To**: sdlc-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `ui/routers/sdlc.py` with routes: active pipelines, pipeline detail, completed
- Create HTMX partial routes for polling refresh
- Create all templates in `ui/templates/sdlc/`: pipelines, detail, completed
- Create horizontal stage indicator component (pure HTML/CSS)
- Create HTMX partial templates in `ui/templates/sdlc/_partials/`
- Mount SDLC router in `ui/app.py`

### 6. Design polish
- **Task ID**: design-polish
- **Depends On**: build-reflections-ui, build-sdlc-ui
- **Assigned To**: ui-designer
- **Agent Type**: designer
- **Parallel**: false
- Review and refine `ui/static/style.css`: consistent spacing, typography, color system
- Polish status badges: green (success), yellow (running/pending), red (error/failed)
- Polish horizontal stage indicator: clear visual progression, current stage highlight
- Ensure dark theme is cohesive across all views
- Test visual appearance of empty states, error states, and full-data states

### 7. Test suite
- **Task ID**: test-suite
- **Depends On**: build-reflections-ui, build-sdlc-ui
- **Assigned To**: ui-test-engineer
- **Agent Type**: test-engineer
- **Parallel**: true (parallel with design-polish)
- Write unit tests for data layers (reflections, sdlc)
- Write unit tests for Pydantic serializers (valid JSON, malformed JSON, empty states)
- Write route handler tests using FastAPI TestClient
- Write integration test: start server, request each major route, assert 200 + key content
- Test HTMX partial endpoints return valid HTML fragments

### 8. Documentation
- **Task ID**: document-feature
- **Depends On**: test-suite, design-polish
- **Assigned To**: ui-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/web-ui.md`
- Create `docs/features/reflections-dashboard.md`
- Create `docs/features/sdlc-observer.md`
- Add entries to `docs/features/README.md` index table

### 9. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: ui-validator
- **Agent Type**: validator
- **Parallel**: false
- Start server with `python -m ui.app`, verify all routes return 200
- Verify all success criteria checkboxes
- Verify localhost-only binding (not 0.0.0.0)
- Verify no npm/node dependencies introduced
- Run full test suite
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/test_ui_*.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check ui/` | exit code 0 |
| Format clean | `python -m ruff format --check ui/` | exit code 0 |
| Server starts | `timeout 5 python -m ui.app 2>&1 \| grep -q 'Uvicorn running'` | exit code 0 |
| Root route | `curl -s http://localhost:8500/ \| grep -q 'Reflections'` | exit code 0 |
| Reflections route | `curl -s http://localhost:8500/reflections/ \| grep -q 'reflections'` | exit code 0 |
| SDLC route | `curl -s http://localhost:8500/sdlc/ \| grep -q 'sdlc'` | exit code 0 |
| Localhost only | `python -c "from ui.app import create_app; app = create_app(); print('ok')"` | exit code 0 |
| No node deps | `test ! -f ui/package.json` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Reflection run_history cap**: I proposed capping at 100 entries per reflection. Is that sufficient, or do you want longer history (with disk-backed overflow)?

2. **SDLC timestamp extraction**: The `history` field on AgentSession contains entries like `[stage] BUILD completed`. These don't have explicit timestamps. Should we add timestamps to history entries going forward (minor instrumentation change), or derive approximate times from session metadata?

3. **Old reflections-dashboard plan**: `docs/plans/reflections-dashboard.md` (tracking #413) is now superseded. Should I mark it as Cancelled and archive it, or leave it for reference?
