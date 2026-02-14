---
status: Planning
type: feature
appetite: Medium
owner: Tom
created: 2026-02-13
tracking:
---

# Episode Workflow Progress UI (View-Only)

## Problem

The podcast production workflow has 12 phases spanning research, synthesis, audio generation, and publishing. Currently the only way to see where an episode stands is to SSH into the dev machine and check files, or scroll through Claude Code's task list during production.

**Current behavior:**
Episode progress is invisible through the web UI. The Episode model tracks only three states (draft/in_progress/complete), which obscures the 12 distinct phases of work. Staff cannot see which research tools have run, whether cross-validation is done, or if audio has been transcribed — without manual file inspection.

**Desired outcome:**
A staff-only web UI at `/podcast/<slug>/<episode_slug>/edit/<step>/` that shows per-phase progress for any episode. Each of the 12 phases displays sub-step completion derived from existing database records (Episode fields + EpisodeArtifact records). HTMX navigation between steps avoids full page reloads. View-only for v1 — no mutations.

## Appetite

**Size:** Medium

**Team:** Solo dev + PM. 1 check-in to confirm the phase-to-DB mapping looks right, 1 review round on the UI.

**Interactions:**
- PM check-ins: 1 (validate phase completion logic matches actual workflow)
- Review rounds: 1 (UI layout and HTMX behavior)

## Prerequisites

No prerequisites — this work uses only existing models and the Django template system.

## Solution

### Key Elements

- **Workflow progress service**: Pure function that takes an Episode + its artifact titles and returns 12 Phase objects with sub-step completion status — derived entirely from existing DB data, no new fields
- **Staff-only view**: Single view class serving both full page loads and HTMX partial swaps, gated by `is_staff`
- **Page-per-step with HTMX nav**: Sidebar shows all 12 phases with status dots; clicking a phase swaps the main content via HTMX without reloading the sidebar or page chrome

### Flow

**Episode detail page** → Click "Workflow" link → **Step 1 (Setup)** → Click sidebar phases or Next button → **Step N** (HTMX swap, URL updates) → **Step 12 (Commit & Push)**

Direct URL access (e.g., `/podcast/yudame-research/ep15-sleep/edit/7/`) renders the full page at that step.

### Technical Approach

1. **Phase completion is derived from existing data** — no new model fields or migrations. Each phase maps to a combination of Episode fields and EpisodeArtifact title lookups:

   | Phase | Name | DB Evidence |
   |-------|------|-------------|
   | 1 | Setup | Episode exists, status != "draft" |
   | 2 | Perplexity Research | Artifact `research/p2-perplexity.md` |
   | 3 | Question Discovery | Artifact `research/question-discovery.md` or digest |
   | 4 | Targeted Research | Any p2-grok/chatgpt/gemini/claude/manual artifact |
   | 5 | Cross-Validation | Artifact `research/cross-validation.md` |
   | 6 | Master Briefing | Artifact `research/p3-briefing.md` |
   | 7 | Synthesis | `Episode.report_text` populated |
   | 8 | Episode Planning | Artifact matching `content_plan.md` |
   | 9 | Audio Generation | `audio_url` + `audio_file_size_bytes > 0` |
   | 10 | Audio Processing | `transcript` + `chapters` both populated |
   | 11 | Publishing | `cover_image_url` set + `description` populated |
   | 12 | Commit & Push | `published_at` is set (determines feed.xml appearance) |

2. **Two DB queries total**: Episode with `select_related("podcast")` + artifact titles as flat `values_list`

3. **View uses `MainContentView`** (imported from `apps.public.views.helpers.main_content_view`) with `LoginRequiredMixin` + `UserPassesTestMixin` for staff access. On HTMX requests, returns only the step content partial; on full page loads, returns the complete page with sidebar.

4. **HTMX navigation**: Sidebar links use `hx-get`, `hx-target="#workflow-step-content"`, `hx-swap="innerHTML"`, `hx-push-url="true"`. Prev/Next buttons use the same pattern.

### Files to Create

| File | Purpose |
|------|---------|
| `apps/podcast/services/__init__.py` | Empty init for services package |
| `apps/podcast/services/workflow_progress.py` | `SubStep`/`Phase` dataclasses + `compute_workflow_progress()` |
| `apps/podcast/workflow.py` | `EpisodeWorkflowView` (staff-only, MainContentView) |
| `apps/public/templates/podcast/episode_workflow.html` | Full page: breadcrumb, progress bar, sidebar + content area |
| `apps/public/templates/podcast/_workflow_step_content.html` | HTMX partial: phase header, sub-step checklist, prev/next nav |
| `apps/podcast/tests/test_workflow_progress.py` | Unit tests for phase completion logic |
| `apps/podcast/tests/test_workflow_views.py` | View tests (auth, 404, HTMX vs full page) |

### Files to Modify

| File | Change |
|------|--------|
| `apps/podcast/urls.py` | Add URL pattern before `episode_detail` |

## Rabbit Holes

- **Tracking progress in the model** — Adding `current_phase` or `workflow_state` fields sounds useful but creates a second source of truth. Deriving from existing data is simpler and always accurate.
- **Real-time updates** — Polling or WebSocket to show live progress during production. Unnecessary for v1; staff can refresh the page.
- **Local filesystem integration** — Checking `pending-episodes/` on disk would show pre-publish progress but only works on the dev machine. DB-only is the right boundary for a web UI.
- **Edit/action capabilities** — Tempting to add "run Phase 2" buttons, but that's a separate feature. View-only keeps this focused.

## Risks

### Risk 1: Artifact title mismatches
**Impact:** Phase completion shows wrong status if artifact titles in the DB don't match the expected strings (e.g., `content_plan.md` vs `plans/content-plan.md`).
**Mitigation:** Check multiple title variants per phase. The `publish_episode.py` command's `create_artifacts()` function is the canonical source of artifact titles — the service matches against those exact patterns.

### Risk 2: Stale data for in-progress episodes
**Impact:** Until `publish_episode` runs, most fields are empty. An "in_progress" episode will show almost everything incomplete.
**Mitigation:** This is expected and accurate for v1. The UI shows what the DB knows. A future v2 could optionally check local files.

## No-Gos (Out of Scope)

- Edit/mutation capabilities (triggering phases, uploading files)
- Local filesystem checks (pending-episodes/ directory)
- New model fields or migrations
- Real-time progress updates (WebSocket/polling)
- Non-staff access or public-facing views

## Update System

No update system changes required — this is a web UI feature internal to the Django app.

## Agent Integration

No agent integration required — this is a Django view for human staff use.

## Documentation

### Inline Documentation
- [ ] Docstring on `compute_workflow_progress()` explaining the phase-to-DB mapping
- [ ] Docstring on `EpisodeWorkflowView` explaining HTMX partial vs full page behavior

No external documentation needed — this is an internal staff tool.

## Success Criteria

- [ ] `/podcast/<slug>/<episode>/edit/1/` through `/edit/12/` all render for staff users
- [ ] Anonymous users redirected to login; non-staff users get 403
- [ ] Step 0 and step 13 return 404
- [ ] HTMX navigation between steps swaps content without full page reload
- [ ] Direct URL access renders the full page at the correct step
- [ ] Phase status dots accurately reflect DB state (green/orange/gray)
- [ ] Sub-steps show meaningful detail (word counts, file sizes, artifact presence)
- [ ] All tests pass

## Team Orchestration

### Team Members

- **Builder (service + view)**
  - Name: workflow-builder
  - Role: Implement workflow_progress.py service, workflow.py view, templates, URL config
  - Agent Type: builder
  - Resume: true

- **Validator (tests + QA)**
  - Name: workflow-validator
  - Role: Write tests, verify auth gating, HTMX behavior, phase accuracy
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create workflow progress service
- **Task ID**: build-service
- **Depends On**: none
- **Assigned To**: workflow-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `apps/podcast/services/__init__.py`
- Create `apps/podcast/services/workflow_progress.py` with `SubStep`, `Phase` dataclasses and `compute_workflow_progress()` function
- Phase completion logic maps to Episode fields + artifact title lookups per the technical approach table

### 2. Write service unit tests
- **Task ID**: test-service
- **Depends On**: build-service
- **Assigned To**: workflow-validator
- **Agent Type**: validator
- **Parallel**: false
- Create `apps/podcast/tests/test_workflow_progress.py`
- Test each phase returns correct status for known inputs
- Test in_progress detection (some sub-steps complete)
- Test progress_fraction calculation
- Run: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_workflow_progress.py -v`

### 3. Create templates and view
- **Task ID**: build-view
- **Depends On**: build-service
- **Assigned To**: workflow-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `apps/public/templates/podcast/episode_workflow.html` (full page with sidebar)
- Create `apps/public/templates/podcast/_workflow_step_content.html` (HTMX partial)
- Create `apps/podcast/workflow.py` with `EpisodeWorkflowView`
- Add URL pattern to `apps/podcast/urls.py`

### 4. Write view integration tests
- **Task ID**: test-view
- **Depends On**: build-view
- **Assigned To**: workflow-validator
- **Agent Type**: validator
- **Parallel**: false
- Create `apps/podcast/tests/test_workflow_views.py`
- Test anonymous → login redirect
- Test non-staff → 403
- Test staff → 200
- Test invalid step → 404
- Test HTMX request returns partial
- Test full page request returns complete HTML
- Run: `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_workflow_views.py -v`

### 5. Final validation
- **Task ID**: validate-all
- **Depends On**: test-service, test-view
- **Assigned To**: workflow-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all podcast tests
- Manual QA in browser

## Validation Commands

- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_workflow_progress.py -v` — service logic tests
- `DJANGO_SETTINGS_MODULE=settings pytest apps/podcast/tests/test_workflow_views.py -v` — view integration tests
- Manual: login as staff, navigate to `/podcast/<slug>/<episode>/edit/1/`, click through all 12 steps, verify HTMX swaps

---

## Resolved Questions

1. **Phase 3 (Question Discovery)** — OK if bare; may rarely have a DB artifact. We'll check for `research/question-discovery.md` or digest but accept it will often show incomplete. Can be enriched later.

2. **Phase 11 vs 12 distinction** — Phase 11 (Publishing) checks that publishing assets are ready (`cover_image_url` + `description`). Phase 12 (Commit & Push) checks `published_at` is set, which determines when the episode appears in feed.xml.
