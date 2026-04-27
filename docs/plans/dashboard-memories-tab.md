---
status: Planning
type: feature
appetite: Small
owner: Valor Engels
created: 2026-04-27
tracking: https://github.com/tomcounsell/ai/issues/1181
last_comment_id:
---

# Dashboard `/memories` view — per-record memory inspector

## Problem

The Memory model carries dense per-record telemetry — `category`, `outcome_history`, `dismissal_count`, `last_outcome`, `superseded_by` — that is invisible from the dashboard. The only memory data on `localhost:8500` today is the four aggregate counters (`memory_recalls_today/_7d`, `memory_extractions_today/_7d`) in the analytics stats card. To see *which* memories are decaying, inspect outcome history, or trace supersession chains, the user has to drop into `python -m tools.memory_search inspect --id <id>` from a terminal.

**Current behavior:**
- Dashboard shows aggregate memory counts only (analytics stats card).
- Per-record inspection requires the CLI.
- No view of dismissal-decay candidates.
- No way to find supersession chains visually.

**Desired outcome:**
- A dashboard page listing Memory records for the current project, filterable by category, with each row showing: title (first line of content), category badge, importance, age, source, outcome summary (acted/dismissed counts), decay flag (when `dismissal_count >= DISMISSAL_DECAY_THRESHOLD - 1`), and supersession links.
- Superseded records hidden by default behind a toggle.
- No measurable hit to dashboard load time on a corpus of ≥500 memories.

## Freshness Check

**Baseline commit:** `180440d0`
**Issue filed at:** 2026-04-26T16:44:39Z
**Disposition:** Unchanged (with one factual correction, captured in Recon Summary)

**File:line references re-verified:**
- `models/memory.py:48-101` — Memory model fields confirmed. **Correction to issue body:** `superseded_by` and `superseded_by_rationale` are top-level `StringField`s on the model, not entries in the `metadata` dict. Plan reflects the correct shape.
- `config/memory_defaults.py:67-73` — decay constants intact (`DISMISSAL_DECAY_THRESHOLD=3`, `MAX_OUTCOME_HISTORY=10`).
- `agent/memory_extraction.py:607-694` — `observe_outcome()` and `compute_act_rate()` helpers present and reusable.
- `ui/data/reflections.py` — analog pattern intact: synchronous `Model.query.filter()` → dict.
- `ui/app.py:122-141` — index handler and `_partials` HTMX route registration unchanged.
- `ui/templates/_partials/analytics_stats.html` — concrete HTMX partial, stat-card styling reusable.

**Cited sibling issues/PRs re-checked:**
- PR #959 (memory consolidation reflection) — merged 2026-04-14. Supersession behavior is shipped: `superseded_by` is set by the dedup reflection, excluded from active recall, retained in Redis for audit.

**Commits on main since issue was filed (touching referenced files):** None on `models/memory.py`, `agent/memory_extraction.py`, `ui/data/`, `ui/app.py`, or `ui/templates/`.

**Active plans in `docs/plans/` overlapping this area:** None. Closest neighbors are `intentional_memory_saves.md`, `memory-project-key-isolation.md`, `claude-code-memory-integration.md` — all touch memory ingestion or scoping, not the dashboard view.

**Notes:** The issue body's "section-in-index" recommendation is revised to a dedicated route (rationale in Solution → Technical Approach).

## Prior Art

- **Issue #552** (closed 2026-03-26) — *Local Claude Code session observability and memory parity.* Wired Claude Code hooks into the memory system. Tangential — establishes the data corpus this view will surface.
- **Issue #748** (closed 2026-04-14) — *Finish reflections unification.* Set up the reflection scaffolding the memory-dedup reflection plugs into. Tangential — supplies the supersession field this view renders.
- **PR #959** (merged 2026-04-14) — *LLM-based semantic memory consolidation.* Introduces `superseded_by` / `superseded_by_rationale` and the dedup reflection. Direct upstream — defines the supersession data model the view consumes.
- **Issue #1038** (closed 2026-04-18) — *Popoto binary fields crash redis-py clients with `decode_responses=True`.* Resolved upstream; not a current blocker. Mentioned because the new data layer reads `Memory.query`, which depends on the same client config — the fix in #1038 is what makes this feature feasible without binary-encoding workarounds.
- **No prior attempts at a dashboard `/memories` view** — this is greenfield UI work.

## Research

No relevant external findings — proceeding with codebase context. The work is purely internal: existing FastAPI + Jinja2 + HTMX stack, existing Popoto query patterns, no new libraries or third-party patterns to evaluate.

## Data Flow

1. **Entry point**: User navigates to `localhost:8500/memories` (or clicks a peek link from the index page).
2. **`ui/app.py` route handler** (`/memories`): renders `memories.html` with initial filter state from query params (`category`, `decay`, `show_superseded`). Calls `ui.data.memories.get_memories(...)` to fetch the initial data shape.
3. **`ui/data/memories.py`** (new): `get_memories(project_key, category=None, decay_only=False, include_superseded=False, limit=200)` calls `Memory.query.filter(project_key=project_key)` → list. Filters in Python: drops superseded (unless toggled), filters by category, filters to `dismissal_count >= DISMISSAL_DECAY_THRESHOLD - 1` if `decay_only`. Sorts by `relevance` (DecayingSortedField). Truncates to `limit` and reports `truncated_count`. For each record, derives `act_rate` via `compute_act_rate()`, decay flag, supersession link.
4. **Template render** (`ui/templates/memories.html`): groups by category (collapsible sections), renders each record using the existing stat-card / data-table styling. HTMX partial `/_partials/memories/` swaps the list on filter change.
5. **HTMX partial endpoint** (`/_partials/memories/`): same data layer call, returns `_partials/memories_list.html`. Refresh trigger: `every 30s` (memories don't churn fast).
6. **Detail view (optional, deferred)**: clicking a row could open a modal showing full outcome history. v1: link to a static text dump at `/memories/{memory_id}` rendered via `inspect()` reuse. **Out of scope for v1** — see No-Gos.

The flow is single-process, single-thread, synchronous. No async, no shared mutable state, no external services.

## Architectural Impact

- **New dependencies**: None. Reuses FastAPI, Jinja2, HTMX, Popoto (`Memory.query`), and existing helpers (`compute_act_rate()`, `inspect()`).
- **Interface changes**: Adds two routes (`GET /memories`, `GET /_partials/memories/`) and one new module (`ui/data/memories.py`). No changes to existing routes, templates, or models.
- **Coupling**: Adds a new dependency from `ui/data/` to `models/memory.py` and `agent/memory_extraction.py`. Symmetric to existing `ui/data/sdlc.py` → `models/agent_session.py` coupling.
- **Data ownership**: Read-only. The view never writes to Memory records.
- **Reversibility**: Trivially reversible. Deleting `ui/data/memories.py`, `ui/templates/memories.html`, `ui/templates/_partials/memories_list.html`, and the two routes in `ui/app.py` removes the feature with zero data migration.

## Appetite

**Size:** Small

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (confirm dedicated-route choice and read-only-only scope before implementation)
- Review rounds: 1 (visual review of rendered page, plus standard code review)

This work pastes-and-adapts from `ui/data/reflections.py` and `ui/templates/_partials/analytics_stats.html`. The pattern is well-established; the bottleneck is rendering decisions and not coding time.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| UI server starts | `python -m ui.app & PID=$!; sleep 2; curl -sf http://localhost:8500/ > /dev/null && kill $PID` | Verify the existing dashboard runs before extending it |
| Memory model importable | `.venv/bin/python -c "from models.memory import Memory; assert Memory.query"` | Verify the data source the view depends on is available |
| At least one memory record exists in the active project | `.venv/bin/python -m tools.memory_search inspect --stats` | Verify there's data to render (non-blocking — empty-state UI must also work) |

Run all checks: `python scripts/check_prerequisites.py docs/plans/dashboard-memories-tab.md`

## Solution

### Key Elements

- **`ui/data/memories.py`** (new): Synchronous data-access module mirroring `ui/data/reflections.py`. Exports `get_memories(...)` (list view) and `get_memory_detail(memory_id)` (single-record reuse of `tools.memory_search.inspect`).
- **`/memories` route** (new in `ui/app.py`): Dedicated HTML page rendering `memories.html`, taking `category`, `decay`, `show_superseded` query params for initial filter state.
- **`/_partials/memories/` route** (new in `ui/app.py`): HTMX partial returning the rendered list, swapped on filter change and on a 30s refresh interval.
- **`ui/templates/memories.html`** (new): Full-page template extending `base.html` with filter controls (category buttons, decay-only toggle, show-superseded toggle) and a list region wired to the HTMX partial.
- **`ui/templates/_partials/memories_list.html`** (new): The renderable list — records grouped by category, each row showing the per-record summary fields.
- **Index page peek link** (small edit to `ui/templates/index.html`): A one-line link in the existing layout pointing to `/memories`. Avoids surprising the user with a hidden route.

### Flow

`/` (dashboard) → click "Memories" link in nav region → `/memories` (full list, default filter: hide superseded, no decay filter, all categories) → click "Decay imminent" toggle → HTMX swap shows only decay candidates → click "Corrections" filter → HTMX swap shows decay-imminent corrections only → click a record → (deferred to v2) modal with full outcome history; v1 just shows everything inline.

### Technical Approach

- **Dedicated route over section-in-index.** Rationale: per-record HTML is dense (≥6 fields per row plus filter controls); memories don't refresh on the 5s/10s cadence the index page uses; hundreds of records would dominate the page. Section-in-index would force an awkward collapse-by-default. A dedicated route is cleaner and matches the pattern reflections-detail uses (`/reflections/{name}`).
- **Read-only for v1.** Mutation lives in `python -m tools.memory_search forget`. Adding mutation buttons would require CSRF tokens, confirmation modals, and audit. Defer to v2 if a clear UX win emerges.
- **Filter at the data layer, not the template.** `get_memories()` accepts `category`, `decay_only`, `include_superseded`. The template only iterates the filtered result. Keeps the template trivial and unit-testable in pure Python.
- **Supersession default: hidden.** `include_superseded=False` by default. Toggle in the UI sets the query param; HTMX swap re-fetches with `include_superseded=true`. Faded "merged into `mem_xyz`" badge when shown.
- **Pagination ceiling: top-N=200, sorted by `relevance` descending.** When the corpus exceeds 200 records after filtering, render a footer banner: `Showing 200 of N records — see python -m tools.memory_search for full inspection.` Avoids both pagination UI and unbounded payload size.
- **Reuse helpers.** `compute_act_rate()` from `agent/memory_extraction.py` for the act-rate %. `tools.memory_search.inspect(memory_id=…)` for the deferred detail route (v1 uses inline rendering only — `inspect()` is wired up but the modal/detail page is out of scope).
- **Project scoping.** Resolve `project_key` from `os.environ.get("VALOR_PROJECT_KEY", DEFAULT_PROJECT_KEY)`. The view is single-project (matches the dashboard's existing single-project assumption).
- **Decay flag rule.** A record is "decay-imminent" when `metadata.get("dismissal_count", 0) >= DISMISSAL_DECAY_THRESHOLD - 1` (i.e., `>= 2`). Read the threshold from `config.memory_defaults` so the rule tracks the constant if it's ever tuned.
- **Empty state.** No memories matching the filter → render a friendly hint pointing to the CLI, not a blank panel.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `ui/data/memories.py::get_memories` wraps the `Memory.query.filter` call in a `try/except` that logs `logger.warning(...)` and returns `[]` on failure (mirrors `analytics.py:62-79`). Test asserts an exception during the query produces a logged warning AND an empty list returned.
- [ ] `compute_act_rate` is already exception-safe (returns `None` on empty input). No new handler needed for that path.

### Empty/Invalid Input Handling
- [ ] `get_memories` with `project_key=""` falls back to `DEFAULT_PROJECT_KEY`. Tested.
- [ ] `get_memories` returning an empty list renders the empty-state UI (test the template path with zero records).
- [ ] `category` query param with an unknown value (e.g., `?category=bogus`) renders empty list with the empty-state hint, not a 500 error.
- [ ] Memory record with missing `metadata` dict (legacy data) renders without crashing — defaults to category="default", outcome history = [], `dismissal_count=0`.

### Error State Rendering
- [ ] `/memories` route with the data layer raising surfaces the existing `error.html` template (already wired via the global exception handler in `ui/app.py:394-399`). Test asserts a 500 page renders, not a traceback.
- [ ] HTMX partial endpoint returning empty list still renders valid HTML (no `null` literal sneaking into the swap).

## Test Impact

- [ ] `tests/integration/test_dashboard.py` (or equivalent existing UI smoke test) — UPDATE if it asserts the route table; add `/memories` and `/_partials/memories/` to expected routes.
- [ ] No existing tests assert on the analytics stats Memory group structure that would break.

If the existing `tests/integration/test_dashboard.py` does not exist or does not enumerate routes, no existing tests are affected — this is purely additive UI work with no prior coverage of `/memories` (which is a new path). The builder verifies on first build.

## Rabbit Holes

- **Inline mutation UI.** Tempting because the CLI exists and "it would be one button." Each mutation requires CSRF, confirmation, audit log entry, and integration tests. Out of scope.
- **Real-time updates.** Memories don't change at sub-minute resolution. A 30s HTMX refresh is more than enough. Don't reach for SSE/WebSockets.
- **Cross-project memory views.** The dashboard is single-project. Multi-project comes if/when a shared dashboard exists. Don't introduce project switchers here.
- **Bloom filter / BM25 internals.** This is a record viewer, not a debugger. Don't render bloom hit counts, RRF scores, or embedding similarity — the issue body explicitly excludes this.
- **Pagination UI.** Top-N=200 with a "see CLI" hint covers the realistic case. Don't build pagination, infinite scroll, or sortable column headers in v1.
- **Modal / detail page.** Tempting because the analog (`/reflections/{name}`) has one. Defer to v2. Inline summary fields cover the issue's acceptance criteria.

## Risks

### Risk 1: Slow page render on large corpora
**Impact:** A project with thousands of memories could make `Memory.query.filter(project_key=...).all()` slow enough to delay the page render past the 1s budget the rest of the dashboard meets.
**Mitigation:** Top-N=200 cap is enforced *before* per-record decoration (act-rate computation, decay flag, etc.). Sort by `relevance` (a `DecayingSortedField` already indexed in Redis as a sorted set) using `Memory.query` with a sort hint if Popoto exposes one; otherwise sort the materialized list. Benchmark on the active corpus during build (memory inspect --stats reports the count). If the materialized list is the bottleneck, switch to direct `relevance` zrange via the Popoto-exposed sorted-set helper (no raw Redis — see global rules) before declaring done.

### Risk 2: Stale data after the memory-dedup reflection runs
**Impact:** The 30s refresh window means a user could see records that were superseded seconds ago as still active.
**Mitigation:** Acceptable — supersession is a slow process (nightly reflection). 30s staleness is fine. The supersession badge shows the timestamp of when the reflection ran (via the `superseded_by_rationale`'s implicit creation time, derivable from the supersession target's `relevance` change), so the user can tell.

### Risk 3: Records with malformed `metadata`
**Impact:** Legacy or pre-`outcome_history` records may have `metadata = {}` or missing keys. A `meta["category"]` direct-access would `KeyError`.
**Mitigation:** All access uses `meta.get("category", "default")` defaults. Tested explicitly in failure-path tests.

## Race Conditions

No race conditions identified. The view is read-only, runs in a single FastAPI sync handler, and reads from Popoto (which serializes its own reads via Redis client). Concurrent writes by `observe_outcome()` or the dedup reflection during a render produce a consistent snapshot — at worst the view shows pre- or post-update state, never partial. No mutation, no shared mutable state on the dashboard side.

## No-Gos (Out of Scope)

- Inline mutation (delete/dismiss/edit). CLI handles this.
- Cross-project memory views.
- Bloom filter / BM25 / embedding internals (this is a record viewer, not a debugger).
- Pagination UI (top-N=200 + CLI hint covers v1).
- Modal / detail page for full outcome history (deferred to v2 — link to CLI for now).
- Sortable column headers (default sort by `relevance` desc; filter is enough for v1).
- Real-time push (HTMX 30s polling is sufficient).
- Editing the `superseded_by` field manually.

## Update System

No update system changes required — this feature is purely internal to the dashboard. No new dependencies, no config files to propagate, no migration. The next `/update` cycle picks up the new routes/templates from the git pull alone.

## Agent Integration

No agent integration required — this is a dashboard-internal feature. The agent already has access to the underlying memory data via the existing `python -m tools.memory_search` CLI, which is exposed through the bash tool. There is no need for an MCP server, `.mcp.json` change, or bridge import. The view is for human (Valor) inspection at `localhost:8500/memories`.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/subconscious-memory.md` with a new `## Dashboard view` section describing the `/memories` route, the filter controls, and the supersession-default behavior. Include a screenshot of the rendered page.
- [ ] Add a row to the `docs/features/README.md` index (if subconscious-memory is not already there with a link, add the dashboard subsection link).

### External Documentation Site
The repo doesn't use Sphinx/MkDocs. No external docs site to update.

### Inline Documentation
- [ ] Module docstring on `ui/data/memories.py` explaining the data-access pattern (mirror `ui/data/reflections.py:1-6`).
- [ ] Module docstring on `ui/templates/memories.html` is unnecessary; templates are self-evident.
- [ ] Comment in `ui/app.py` route handler noting that `/memories` is paired with `/_partials/memories/` for HTMX swap.

## Success Criteria

- [ ] `/memories` route renders a list of Memory records for the active project, grouped by category.
- [ ] Each row shows: title (first line of content, truncated to ~80 chars), category badge, importance (1 decimal), age (humanized), source, outcome summary (e.g., "acted ×3 / dismissed ×1, 75% act rate"), decay flag (visible iff `dismissal_count >= 2`), supersession indicator (visible iff record is superseded AND show-superseded toggle is on).
- [ ] Filter controls work: category buttons (correction / decision / pattern / surprise / all), decay-only toggle, show-superseded toggle. Filter state survives an HTMX swap.
- [ ] Decay-imminent records visually flagged (e.g., yellow badge "decay 2/3").
- [ ] Superseded records hidden by default; visible behind toggle with faded "merged into mem_xyz" link.
- [ ] Empty state when no memories match the filter — friendly message pointing to the CLI.
- [ ] Truncation banner when the filtered corpus exceeds 200 records.
- [ ] Page renders in <500ms on a corpus of 500 memories (measured via browser devtools).
- [ ] `python -m ruff check ui/` and `python -m ruff format --check ui/` pass.
- [ ] `pytest tests/` passes (no regressions).
- [ ] `docs/features/subconscious-memory.md` updated with the dashboard view section.
- [ ] PR opened with `Closes #1181` in the body.

## Team Orchestration

When this plan is executed, the lead agent orchestrates work using Task tools. The lead NEVER builds directly — they deploy team members and coordinate.

### Team Members

- **Builder (data-and-route)**
  - Name: `memories-route-builder`
  - Role: Implement `ui/data/memories.py`, the two routes in `ui/app.py`, and the route's unit tests.
  - Agent Type: builder
  - Resume: true

- **Builder (templates)**
  - Name: `memories-template-builder`
  - Role: Implement `ui/templates/memories.html`, `ui/templates/_partials/memories_list.html`, and the small index.html peek-link edit. Reuse styling from analytics_stats.html.
  - Agent Type: designer
  - Resume: true

- **Validator (frontend)**
  - Name: `memories-frontend-validator`
  - Role: Browser-test the rendered page on the local dashboard. Verify all filter combinations render correctly. Capture a screenshot for the docs.
  - Agent Type: frontend-tester
  - Resume: true

- **Validator (final)**
  - Name: `memories-final-validator`
  - Role: Run all success-criteria checks. Verify ruff/format/pytest pass. Confirm docs updated.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: `memories-documentarian`
  - Role: Update `docs/features/subconscious-memory.md` with the dashboard section + screenshot. Update `docs/features/README.md` if needed.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Build data layer
- **Task ID**: build-data-layer
- **Depends On**: none
- **Validates**: `tests/unit/test_ui_data_memories.py` (create) — assert filter logic, decay flag rule, missing-metadata handling, top-N truncation
- **Assigned To**: `memories-route-builder`
- **Agent Type**: builder
- **Parallel**: true
- Create `ui/data/memories.py` with `get_memories(project_key, category=None, decay_only=False, include_superseded=False, limit=200)` and `get_memory_detail(memory_id)` (the latter is a thin wrapper over `tools.memory_search.inspect`).
- Use `Memory.query.filter(project_key=project_key)` to fetch. Sort by `relevance` desc. Apply filters in Python.
- Compute per-record: `act_rate` via `compute_act_rate()`, `decay_imminent` boolean, `superseded_by` link.
- Wrap the query in `try/except Exception` returning `[]` on failure with a `logger.warning`.
- Read `DISMISSAL_DECAY_THRESHOLD` from `config.memory_defaults` (do not hard-code 2 or 3).

### 2. Build routes
- **Task ID**: build-routes
- **Depends On**: build-data-layer
- **Validates**: `tests/integration/test_dashboard_memories.py` (create) — assert `/memories` returns 200 with filter params; `/_partials/memories/` returns valid HTML fragment; query params propagate.
- **Assigned To**: `memories-route-builder`
- **Agent Type**: builder
- **Parallel**: false
- Add `GET /memories` route to `ui/app.py` rendering `memories.html` with initial filter state from query params.
- Add `GET /_partials/memories/` route returning the partial template.
- No middleware, no auth — same as the rest of the dashboard.

### 3. Build templates
- **Task ID**: build-templates
- **Depends On**: build-data-layer
- **Validates**: Visual confirmation (handled by frontend-tester in step 5)
- **Assigned To**: `memories-template-builder`
- **Agent Type**: designer
- **Parallel**: true
- Create `ui/templates/memories.html` extending `base.html` with: page header, filter control row, list region wired to HTMX partial.
- Create `ui/templates/_partials/memories_list.html` rendering the records grouped by category. Reuse `.stats-grid` / `.stat-card` / `.badge` / `.data-table` classes from existing templates.
- Add a single-line peek link from `ui/templates/index.html` to `/memories` in the existing nav region.
- Decay flag: yellow `.badge` with text "decay N/3" where N is `dismissal_count`. Supersession link: faded `.text-muted` "merged into `mem_xyz`".
- Empty state: hint paragraph linking to the CLI command `python -m tools.memory_search`.
- Truncation banner: footer line showing N truncated records.

### 4. Validate routes (data-layer + integration)
- **Task ID**: validate-routes
- **Depends On**: build-routes, build-templates
- **Assigned To**: `memories-final-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run `pytest tests/unit/test_ui_data_memories.py tests/integration/test_dashboard_memories.py` — must pass.
- Run `python -m ruff check ui/` and `python -m ruff format --check ui/` — must pass.
- Confirm no regressions: `pytest tests/unit/ -x -q`.

### 5. Frontend validation
- **Task ID**: validate-frontend
- **Depends On**: build-routes, build-templates
- **Assigned To**: `memories-frontend-validator`
- **Agent Type**: frontend-tester
- **Parallel**: false
- Start dashboard: `python -m ui.app & PID=$!` (kill on exit).
- Hit `localhost:8500/memories` in headless browser. Verify default render (no superseded, no decay filter).
- Verify filter combinations: each category, decay-only on/off, show-superseded on/off.
- Verify empty-state renders when filter yields zero records (`?category=correction&decay=true` may yield empty).
- Capture a screenshot and save to `docs/features/assets/dashboard-memories.png`.

### 6. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-routes, validate-frontend
- **Assigned To**: `memories-documentarian`
- **Agent Type**: documentarian
- **Parallel**: false
- Add a `## Dashboard view` section to `docs/features/subconscious-memory.md` describing the `/memories` route, filter controls, supersession default, and screenshot.
- Update `docs/features/README.md` index entry for subconscious-memory if a sub-link is missing.
- Reference the screenshot captured in step 5.

### 7. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-routes, validate-frontend, document-feature
- **Assigned To**: `memories-final-validator`
- **Agent Type**: validator
- **Parallel**: false
- Run all success-criteria checks.
- Verify the docs updated with a screenshot reference.
- Confirm `pytest tests/`, `ruff check`, `ruff format --check` all pass.
- Generate final report.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Route registered | `python -c "from ui.app import create_app; app=create_app(); paths=[r.path for r in app.routes]; assert '/memories' in paths and '/_partials/memories/' in paths"` | exit code 0 |
| Data layer importable | `.venv/bin/python -c "from ui.data.memories import get_memories, get_memory_detail"` | exit code 0 |
| Page renders | `python -m ui.app & PID=$!; sleep 2; curl -sf 'http://localhost:8500/memories' > /dev/null && kill $PID` | exit code 0 |
| Filter param works | `python -m ui.app & PID=$!; sleep 2; curl -sf 'http://localhost:8500/_partials/memories/?category=correction' > /dev/null && kill $PID` | exit code 0 |
| Docs updated | `grep -l 'Dashboard view' docs/features/subconscious-memory.md` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **Project-key resolution at the dashboard.** The view assumes a single active project resolved from `VALOR_PROJECT_KEY` (with `DEFAULT_PROJECT_KEY` as fallback). Is this correct, or should the dashboard show records across all known projects on this machine (analogous to how `machine_projects` is displayed today)? If multi-project, we need a project selector — bumps appetite slightly. **Recommend single-project for v1** to match existing dashboard scope; revisit when/if cross-project views are added elsewhere.

2. **Sort key when `relevance` is a Popoto sorted-set field.** The plan sorts by `relevance` desc after materializing the list. If the corpus is large and sort cost matters, we'd query the sorted set directly via Popoto's exposed helpers. Is there a documented Popoto API for "get top-N from a `DecayingSortedField` partition"? If not, accept the materialize-and-sort cost (acceptable for ≤1000 records).

3. **Supersession badge timestamp.** The plan shows "merged into `mem_xyz`" but doesn't show *when* the merge happened. The Memory model doesn't currently track a merge timestamp — the `superseded_by_rationale` is the only audit field. Acceptable to omit the timestamp for v1, or should we track merge time as a small additive field on the model? **Recommend: omit for v1** (additive model field is its own small migration; out of scope here).
