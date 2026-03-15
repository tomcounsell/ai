---
status: Ready
type: feature
appetite: Medium
owner: Valor
created: 2026-03-15
tracking: https://github.com/tomcounsell/ai/issues/413
last_comment_id:
---

# Reflections Dashboard

## Problem

The reflections system runs 4+ scheduled tasks (health checks every 5min, orphan recovery every 30min, branch cleanup daily, full maintenance pipeline daily) — but there's no way to see what happened without SSH and manual inspection.

**Current behavior:**
To check if reflections ran successfully, you must: (1) read markdown files in `logs/reflections/`, (2) query Redis models via Python one-liners, (3) tail log files. There's no unified view and no way to browse history across reflection types.

**Desired outcome:**
A local web dashboard at `localhost:PORT` showing all registered reflections, their schedules, full run history with drill-down, and complete error logs — all in one place, no terminal required.

## Prior Art

- **Issue #319**: Add structured logging and telemetry for Observer Agent and stage transitions — closed, focused on logging not UI. Relevant because it established the pattern of Redis-backed state for observability.

No prior dashboard or web UI work exists in this repo. This is greenfield.

## Data Flow

1. **Entry point**: User opens `localhost:PORT` in browser
2. **Flask app** (`dashboard/app.py`): Receives HTTP request, routes to view function
3. **Data layer** (`dashboard/data.py`): Queries Redis via existing Popoto models (`Reflection`, `ReflectionRun`, `ReflectionIgnore`) and reads `config/reflections.yaml` for the registry
4. **Templates** (`dashboard/templates/`): Jinja2 renders HTML with data, HTMX attributes for interactive drill-down
5. **HTMX partials**: Browser requests partial HTML fragments (run detail, log viewer, step expansion) via `hx-get` — Flask returns HTML fragments, no JSON API needed
6. **Log files**: When user drills into a run, Flask reads the log file path from the Redis object and serves the file content inline

## Architectural Impact

- **New dependencies**: Flask + Jinja2 (Flask includes Jinja2). No other Python packages. HTMX loaded from CDN.
- **Interface changes**: None to existing systems. Dashboard is read-only.
- **Coupling**: Loosely coupled — reads from Redis and filesystem, never writes. If Redis is down, dashboard shows an error page.
- **Data ownership**: No change. Dashboard is a pure reader.
- **Reversibility**: Fully reversible — delete the `dashboard/` directory and it's gone.

## Appetite

**Size:** Medium

**Team:** Solo dev, PM review

**Interactions:**
- PM check-ins: 1 (scope alignment on UI layout)
- Review rounds: 1 (visual review of dashboard)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Redis running | `python -c "import redis; redis.Redis().ping()"` | Data source for reflection state |
| Flask installed | `python -c "import flask"` | Web framework |
| Popoto models accessible | `python -c "from models.reflection import Reflection"` | Data access |

Run all checks: `python scripts/check_prerequisites.py docs/plans/reflections-dashboard.md`

## Solution

### Key Elements

- **Flask app** (`dashboard/app.py`): Standalone web server, binds to localhost only, serves Jinja2 templates
- **Data access layer** (`dashboard/data.py`): Encapsulates all Redis queries and log file reads; provides clean data to templates
- **Templates** (`dashboard/templates/`): Base layout + pages for overview, run history, run detail, schedule, ignores
- **HTMX interactivity**: Modals for run detail, inline log viewer, expandable step sections — all via HTML fragment endpoints

### Flow

**Dashboard home** → Click reflection name → **Run history** (paginated) → Click specific run → **Run detail modal** (steps, findings, errors) → Click "View Log" → **Log viewer** (full text inline)

**Dashboard home** → Click "Schedule" → **Schedule view** (all reflections sorted by next-due)

**Dashboard home** → Click "Ignore Patterns" → **Ignore list** (active patterns with expiry)

### Technical Approach

- Flask app as standalone process (`python -m dashboard.app`), reads same Redis instance as bridge
- Jinja2 templates with a shared base layout (sidebar nav, status header)
- HTMX `hx-get` for lazy-loading run details and log content (keeps initial page load fast)
- Data sources: `Reflection` model (per-reflection last-run state), `ReflectionRun` model (daily-maintenance per-step detail, keyed by date), `ReflectionIgnore` model (suppressed patterns), markdown reports in `logs/reflections/`, and log files (`logs/reflections.log`, `logs/reflections_error.log`)
- Registry loader reuses `agent.reflection_scheduler.load_registry()` — no duplication
- Color-coded status badges: green=success, red=error, yellow=running, gray=pending/skipped
- Relative timestamps ("3 hours ago") computed in Jinja2 filters

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Dashboard renders a clear error page when Redis is unreachable (not a stack trace)
- [ ] Missing or unreadable log files show "Log file not found" message instead of 500 error
- [ ] Malformed `reflections.yaml` shows a warning banner, not a crash

### Empty/Invalid Input Handling
- [ ] Dashboard home works with zero reflections registered (empty state)
- [ ] Run history page works when a reflection has never run (no history)
- [ ] Log viewer handles empty log files gracefully

### Error State Rendering
- [ ] Failed runs display red status badges with the error message visible
- [ ] Step-level failures within daily-maintenance show per-step error indicators

## Rabbit Holes

- **Real-time WebSocket updates** — Tempting to add live-updating status. Not worth it. Page refresh or HTMX polling on a 30s interval is sufficient for a management dashboard.
- **CRUD operations** — Don't add the ability to create/edit/delete reflections, trigger manual runs, or manage ignores through the UI. Read-only only. Mutations go through CLI/config.
- **Authentication/authorization** — Local-only dashboard, no auth needed. If remote access is needed later, that's a separate issue.
- **Custom CSS framework** — Don't build or import a CSS framework. Use minimal inline styles or a single `<style>` block. The dashboard is functional, not pretty.
- **Log search/filtering** — Don't build a log search engine. Just display the raw log content. Ctrl+F in the browser is sufficient.

## Risks

### Risk 1: Large log files slow down the log viewer
**Impact:** A 50MB log file loaded inline will freeze the browser
**Mitigation:** Truncate log display to last 1000 lines by default, with a "Load full log" button. Show file size in the header.

## Race Conditions

No race conditions identified. The dashboard is a read-only consumer of Redis state written by the bridge process. No shared mutable state. Flask runs in a separate process from the bridge.

## No-Gos (Out of Scope)

- No write operations — dashboard is strictly read-only
- No real-time WebSocket/SSE streaming
- No authentication or remote access
- No custom CSS framework or design system
- No log search, filtering, or aggregation
- No ability to trigger manual reflection runs from the UI
- No ability to create/edit ignore patterns from the UI (use CLI)
- No mobile responsiveness — desktop-only is fine

## Update System

The update script (`scripts/remote-update.sh`) needs to:
- Install Flask if not already in venv: `pip install flask` (Flask includes Jinja2)
- No other new dependencies
- Dashboard is optional — it doesn't affect bridge operation if not running

## Agent Integration

No agent integration required. The dashboard is a standalone local web server for human use only. It is not exposed to the agent via MCP or any other mechanism.

## Documentation

- [ ] Create `docs/features/reflections-dashboard.md` describing the dashboard, how to start it, and available views
- [ ] Add entry to `docs/features/README.md` index table
- [ ] Add `python -m dashboard.app` to Quick Commands table in `CLAUDE.md`

## Success Criteria

- [ ] `python -m dashboard.app` starts a Flask server on localhost
- [ ] Dashboard home shows all reflections from `config/reflections.yaml` with status from Redis
- [ ] Each reflection displays: name, description, last run time, status (color-coded), next due, run count, duration
- [ ] Clicking a reflection shows its detail: for daily-maintenance, shows `ReflectionRun` entries (keyed by date) with per-step drill-down; for other reflections, shows `Reflection` model state
- [ ] Daily-maintenance detail shows step-level progress, findings, and results from `ReflectionRun.step_progress`
- [ ] Failed runs show full error message and log file contents inline
- [ ] Schedule view shows upcoming runs sorted by next-due time
- [ ] Ignore patterns page lists active entries with pattern, reason, and expiry
- [ ] No npm/node/JS framework dependencies — only HTMX from CDN
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (flask-app)**
  - Name: dashboard-builder
  - Role: Build Flask app, routes, templates, and data layer
  - Agent Type: builder
  - Resume: true

- **Designer (templates)**
  - Name: template-designer
  - Role: Create Jinja2 templates with HTMX interactivity and clean layout
  - Agent Type: designer
  - Resume: true

- **Validator (dashboard)**
  - Name: dashboard-validator
  - Role: Verify dashboard serves correctly and displays all required data
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: docs-writer
  - Role: Create feature documentation
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build Flask dashboard app
- **Task ID**: build-dashboard
- **Depends On**: none
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `dashboard/__init__.py`, `dashboard/app.py`, `dashboard/data.py`
- Implement routes: `/` (home), `/reflections/<name>` (reflection detail + run history for daily-maintenance via ReflectionRun), `/schedule` (schedule view), `/ignores` (ignore patterns), `/logs` (log viewer partial), `/reports/<date>` (markdown report viewer)
- Data layer reads from existing Popoto models (`Reflection`, `ReflectionRun`, `ReflectionIgnore`) and `config/reflections.yaml` via `load_registry()`
- Add `__main__.py` so `python -m dashboard` works
- Bind to `localhost:8500` by default, configurable via `--port`

### 2. Create templates with HTMX
- **Task ID**: build-templates
- **Depends On**: build-dashboard
- **Assigned To**: template-designer
- **Agent Type**: designer
- **Parallel**: false
- Create `dashboard/templates/base.html` with sidebar nav and minimal CSS
- Create page templates: `home.html`, `run_history.html`, `schedule.html`, `ignores.html`
- Create HTMX partial templates: `_run_detail.html`, `_log_viewer.html`, `_step_detail.html`
- HTMX loaded from CDN in base template
- Color-coded status badges, relative timestamps, collapsible sections
- Paginated run history (20 per page)

### 3. Validate dashboard
- **Task ID**: validate-dashboard
- **Depends On**: build-templates
- **Assigned To**: dashboard-validator
- **Agent Type**: validator
- **Parallel**: false
- Start dashboard and verify all routes return 200
- Verify reflections from `config/reflections.yaml` appear on home page
- Verify Redis data (Reflection, ReflectionRun, ReflectionIgnore) renders correctly
- Verify HTMX drill-down into run details works
- Verify log viewer displays log file content
- Verify empty states render gracefully

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-dashboard
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/reflections-dashboard.md`
- Add entry to `docs/features/README.md` index table
- Update CLAUDE.md Quick Commands table

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: dashboard-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all validation commands
- Verify all success criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Flask importable | `python -c "from dashboard.app import create_app"` | exit code 0 |
| Models importable | `python -c "from models.reflection import Reflection; from models.reflections import ReflectionRun, ReflectionIgnore"` | exit code 0 |
| Dashboard starts | `timeout 5 python -m dashboard.app --port 8599 2>/dev/null; test $? -eq 124` | exit code 0 |
| Feature docs exist | `test -f docs/features/reflections-dashboard.md` | exit code 0 |

---
