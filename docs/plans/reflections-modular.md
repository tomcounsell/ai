---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-17
tracking: https://github.com/tomcounsell/ai/issues/1028
last_comment_id:
---

# Reflections Modularization: One File per Reflection, Grouped Dashboard

## Problem

The reflections system has 31 recurring jobs declared in `config/reflections.yaml`, dispatched by `agent/reflection_scheduler.py`. After PR #967 extracted the 3,086-line monolith, their definitions still live in 9 bundle modules totaling ~3,250 LOC (`reflections/auditing.py` 715L, `reflections/maintenance.py` 470L, `agent/sustainability.py` 626L, etc.). The dashboard renders them as a 31-row flat list that dominates the `localhost:8500` home view.

**Current behavior:**
- Bundle modules force a reader to scroll past 5 unrelated reflections to understand one. Cadence rationale, failure modes, and cross-references aren't consistently documented per reflection.
- A failing test in `reflections/auditing.py` doesn't identify which of its 6 reflections broke without digging.
- The dashboard "Reflections" section on `localhost:8500` is a 31-row flat table with no grouping, no collapse, no summary. It owns ~70% of the viewport.
- 9 of the 31 reflections live outside `reflections/` (in `agent/sustainability.py`, `agent/agent_session_queue.py`, `scripts/`) — the callable registry is the only thing that makes them cohere as a subsystem.
- `config/reflections.yaml:58` still references `ReflectionRunner` (deleted in PR #967).

**Desired outcome:**
- Every reflection is a self-contained file at `reflections/{group}/{reflection_name}.py` with a module docstring covering purpose, cadence rationale, failure modes, and related reflections.
- Shared helpers live in one file: `reflections/utilities.py` — only genuinely shared logic (≥3 callers or non-trivial).
- Model properties and methods stay on their Popoto models; reflection files orchestrate, they don't own model logic.
- Dashboard renders 7 collapsible groups; default collapsed, each summary shows enabled/total, soonest next_due, and error indicator.
- `agent/sustainability.py` is empty of reflection callables; stale YAML comment removed.

## Freshness Check

**Baseline commit:** `4124385b4867e7448ebab743cd8839445b3d7bb3` (updated from original `405eedd0` after 5 commits landed on main during plan drafting)
**Issue filed at:** 2026-04-17T09:11:51Z
**Disposition:** Minor drift — claims hold; two commits (`65fcfcc5`, `377fbe2d`) touched reflection code in ways that sharpen the plan without changing its shape.

**File:line references re-verified:**
- `agent/reflection_scheduler.py:193-211` (`_resolve_callable`) — still holds
- `agent/reflection_scheduler.py:123-190` (`load_registry`) — still holds
- `agent/agent_session_queue.py:1571,4632,4732` (3 session-queue callables) — still present
- `agent/sustainability.py:42,118,263,318` (4 self-healing callables) — still present
- `config/reflections.yaml:58` (stale `ReflectionRunner` comment) — still present ✓
- `config/reflections.yaml` mode — **CHANGED**: symlink (`120000`) → regular file (`100644`) per commit `d47d5a81`. No content difference; removes a sync step during edits.

**Commits on main since issue was filed (touching referenced files):**
- `65fcfcc5` — "fix(reflections): stop async reflection functions from blocking the event loop". Dropped `async` from `run_skills_audit`, `run_pr_review_audit`, `run_legacy_code_scan`, `run_task_management`, `session_intelligence.run`. **Impact:** per-reflection files for these five MUST preserve the sync signature; migration must not reintroduce `async def run()`. Plan's "behavior parity" mandate already covers this; flagged explicitly here.
- `377fbe2d` — "fix(reflections): disable all reflections with external gh CLI calls or agent spawning". Newly disabled: `pr-review-audit`, `task-backlog-check`, `merged-branch-cleanup`, `session-intelligence`, `system-health-digest`, plus `daily-report-and-notify`. **Impact:** these reflections still get per-file migration; their `enabled: false` + disable-reason comment must be preserved verbatim in the new YAML entries. **Total disabled-by-default now: 9 (was 2 at issue-filing time):**
  - `stale-branch-cleanup`, `sentry-issue-triage` (pre-existing)
  - `system-health-digest`, `merged-branch-cleanup`, `pr-review-audit`, `task-backlog-check`, `session-intelligence`, `daily-report-and-notify` (new via `377fbe2d`)
- `0a2f27c6`, `680a2a18`, `4124385b` — unrelated to this plan (bridge/update/worker-watchdog).

**Cited sibling issues/PRs re-checked:** #748, #967, #933, #991, #926 — all still closed/merged; no change.

**Active plans in `docs/plans/` overlapping this area:** None. `docs/plans/reflections-quality-pass.md` remains closed-via-#933.

**Notes:** The dashboard-domination problem is now more acute — 9/31 reflections are disabled, yet the dashboard renders all 31 rows regardless of enabled state. Collapsible grouping is more valuable post-drift, not less. Any `enabled: false` reflection has a comment explaining why; preserve that comment verbatim when adding the `group:` field during YAML cutover (task 9).

## Prior Art

- **PR #967** (merged) — "feat(reflections): delete 3086-line monolith, extract reflections/ package (#748)". Precedent for cutting bundled callables into the `reflections/` package. Used a phased approach; succeeded.
- **PR #933** (merged) — "refactor: reflections quality pass — scheduler, model split, field conventions". Fixed scheduler/model placement and YAML field conventions. Completed; this plan builds on its YAML conventions.
- **PR #991** (merged) — "chore(reflections): merge overlapping health gates/drips, rename to {subject}-{verb} standard". Name standardization (closed #978). Informs this plan's naming: file names match the `{subject}-{verb}` reflection name with `_` substitution (e.g. `circuit-health-gate` → `circuit_health_gate.py`).
- **PR #572** (merged) — "Reflections Regroup: 19 steps to 14 units with string keys". Historical; pre-monolith era. Shows the team has iterated on grouping before.
- **PR #790** (merged) — "Dashboard UI fixes: status layout, reflections redesign". The last dashboard refresh; informs the template patterns we'll extend for the collapsible view.

No prior attempt at per-reflection files or collapsible dashboard grouping found. No failed prior fixes to analyze.

## Research

No relevant external findings — proceeding with codebase context and training data. The work is purely internal refactoring: Python package layout, HTMX `<details>` behavior, Jinja template restructure. All well-covered by training data and existing repo patterns.

## Data Flow

For the *runtime* path (scheduler → reflection), data flow is unchanged:

1. **Entry point**: `ReflectionScheduler._run_loop` (`agent/reflection_scheduler.py`)
2. **Registry load**: `load_registry()` reads `config/reflections.yaml`
3. **Resolve**: `_resolve_callable("reflections.core.session_liveness_check.run")` → `importlib.import_module` + `getattr`
4. **Execute**: `execute_function_reflection(entry)` → calls `run()`, persists `Reflection` model state in Redis
5. **Output**: Updated `Reflection.ran_at`, `last_status`, `last_duration`, `run_history`

For the *dashboard* path:

1. **Entry point**: HTTP GET `/` (or auto-refresh `/reflections/_partials/status-grid/` every 10s)
2. **Data access**: `ui/data/reflections.py:get_all_reflections()` merges `config/reflections.yaml` + `Reflection.get_all_states()` from Redis
3. **New step (this plan)**: Group reflections by `group:` YAML field, compute per-group summary (enabled count, soonest next_due, error flag)
4. **Render**: Collapsible `<details>` per group, inner rows per reflection
5. **Output**: HTML response

## Architectural Impact

- **New dependencies**: None. Pure Python + existing Jinja/HTMX patterns.
- **Interface changes**: `callable:` path in `config/reflections.yaml` for all 31 entries. Every entry gains a `group:` field.
- **Coupling**: Decreases. Bundled modules currently allow accidental coupling between unrelated reflections; per-file layout enforces isolation.
- **Data ownership**: Unchanged. Model properties stay on models; reflection files never grow into data stores.
- **Reversibility**: High. `git revert` of the PR restores the bundle layout. No data migration, no Redis schema changes.

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1-2 (scope alignment mid-migration, confirm dashboard grouping UX before cutover)
- Review rounds: 1 (code review before merge)

Coding time is substantial (31 new files, dashboard rework) but mechanical. The risk surface is thin — no behavior changes, only reorganization — so review and alignment overhead is low.

## Prerequisites

No prerequisites — this work has no external dependencies, no new env vars, no new services.

## Solution

### Key Elements

- **`reflections/utilities.py`** — shared helpers. Contents: `load_local_projects()` (5 callers) and `run_llm_reflection()` (wraps Anthropic SDK config). Replaces `reflections/utils.py`.
- **7 group directories** under `reflections/`: `core/`, `self_healing/`, `maintenance/`, `auditing/`, `memory/`, `tasks/`, `pipelines/`. Each with an `__init__.py` (empty — no package-level re-exports).
- **31 reflection files** — one per reflection. Each a self-contained teaching artifact: module docstring → imports → `run(...)` public entry → private helpers.
- **Relocated callables** — the 4 `agent/sustainability.py` reflections move into `reflections/self_healing/`. The 3 `agent/agent_session_queue.py` callables get thin wrappers in `reflections/core/` (keeping the queue functions where ops scripts call them). `scripts/popoto_index_cleanup.py` and `scripts/memory_consolidation.py` get thin wrappers or move, depending on whether they're used outside the scheduler.
- **YAML migration** — `config/reflections.yaml` updates every `callable:` path and adds an explicit `group:` field per entry. Stale `ReflectionRunner` comment at line 58 removed.
- **Dashboard grouping** — `ui/data/reflections.py` returns grouped structure; `ui/templates/reflections/_partials/status_grid.html` renders `<details>` per group.

### Flow

**Phase A (skeleton):** Create `reflections/utilities.py` and the 7 group directories with empty `__init__.py`.
**Phase B (migrate, parallel per group):** For each group, create individual reflection files by extracting from the source bundle (or moving from `agent/sustainability.py` / wrapping from `agent/agent_session_queue.py`).
**Phase C (cutover):** Update `config/reflections.yaml` all at once (new paths + `group:` fields), then delete the 4 bundle modules + `reflections/utils.py` + stale comment.
**Phase D (dashboard):** Update `ui/data/reflections.py` to group, update Jinja template to render collapsible `<details>`.
**Phase E (docs + verify):** Update `docs/features/reflections.md`, any `docs/features/README.md` entries, and `CLAUDE.md` references. Run full test suite + one scheduler tick to verify no callable resolution breaks.

### Technical Approach

**File shape (standard):**
```python
"""
reflections/{group}/{name}.py — {one-line purpose}

What it does:
    {paragraph: what side effects, what it reads, what it writes}

Cadence: {interval} ({why this interval — e.g., "matches log rotation window"})
Priority: {priority} ({why})

Failure modes:
    - {Failure 1 and how it's handled — e.g., "Redis unavailable → logs warning, returns empty result, scheduler retries next tick"}
    - {Failure 2}

Related reflections:
    - {other reflection name}: {how they interact — e.g., "runs before memory-decay-prune to ensure stats are current"}

See also:
    - config/reflections.yaml (declaration)
    - docs/features/{relevant_feature}.md
"""
```

**Callable contract:** every reflection file exports a single `run()` function (sync or async — scheduler handles both). Argument contract matches `_resolve_callable` → `execute_function_reflection` path; no function takes mandatory args beyond what the scheduler already provides.

**Wrappers vs. moves:**
- **Move** when the function is used *only* by the reflection scheduler: all 4 `agent/sustainability.py` callables, `scripts/memory_consolidation.py:run_consolidation`, `scripts/popoto_index_cleanup.py:run_cleanup`.
- **Wrap** when the function is called by non-reflection code paths: the 3 `agent/agent_session_queue.py` callables stay in-place (they're also called by ops scripts); `reflections/core/*.py` imports and delegates.

**YAML grouping field:**
```yaml
- name: circuit-health-gate
  group: self_healing    # NEW: explicit for dashboard rendering
  description: "..."
  interval: 60
  priority: high
  execution_type: function
  callable: "reflections.self_healing.circuit_health_gate.run"
  enabled: true
```

**Dashboard collapsible-rendering pattern:**

The current template wraps the entire grid in an HTMX swap target (`#status-grid`, `hx-swap="innerHTML"`, `hx-trigger="every 10s"`). If we put `<details open>` tags inside the swap target, the 10s refresh collapses any manually-opened group. Fix: **swap target moves down one level** — the outer `<details>` wrapper for each group stays outside the swap zone; only the inner rows swap. Or, simpler and equivalent: use `hx-swap="morph"` via Idiomorph — but that adds a dependency, so prefer the template restructure.

**Template sketch:**
```html
<section id="reflections">
  {% for group in grouped_reflections %}
  <details class="reflection-group" id="group-{{ group.name }}">
    <summary>
      <strong>{{ group.label }}</strong>
      <span>{{ group.enabled_count }}/{{ group.total_count }} enabled</span>
      <span>next: {{ format_ts(group.soonest_next_due) }}</span>
      {% if group.has_error %}<span class="error-dot">●</span>{% endif %}
    </summary>
    <div hx-get="/reflections/_partials/group/{{ group.name }}/"
         hx-trigger="every 10s"
         hx-swap="innerHTML">
      {% include "reflections/_partials/group_rows.html" %}
    </div>
  </details>
  {% endfor %}
</section>
```

**`ui/data/reflections.py` addition:** new function `get_grouped_reflections()` that returns a list of group dicts:
```python
{
  "name": "self_healing",
  "label": "Self-Healing",
  "reflections": [...],            # existing per-reflection dicts
  "enabled_count": 5,
  "total_count": 6,
  "soonest_next_due": <timestamp>,
  "has_error": False,
}
```
Group labels come from a constant mapping in `ui/data/reflections.py` (keeps display names decoupled from directory names).

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit `reflections/utils.py` and each source bundle for `except Exception: pass` blocks. Each must preserve the logger.warning pattern when moving. (Known sites: `utils.py` `load_local_projects` has permissive project loading; auditing modules swallow missing-file errors.)
- [ ] Preserve the existing "log and skip" pattern on per-project failures in migrated reflections.

### Empty/Invalid Input Handling
- [ ] Dashboard grouping function must handle: no reflections loaded (empty registry), all reflections in one group (no empty groups rendered), reflections with `group: null` (fall into `"ungrouped"` bucket with a visible warning).
- [ ] Scheduler: if `_resolve_callable` raises on a migrated path (typo in YAML), scheduler currently logs + skips. Verify behavior preserved.

### Error State Rendering
- [ ] Dashboard group summary with a failing child: red dot renders, group label unchanged. Test in `tests/unit/test_ui_reflections_data.py`.
- [ ] Empty group (e.g., if `tasks/` ships with only 1 reflection after disabling one): group still renders with `0/1` display.

## Test Impact

- [ ] `tests/unit/test_reflections_package.py` — UPDATE: 26 tests import from `reflections.maintenance`, `reflections.auditing`, etc. Imports change to `reflections.maintenance.tech_debt_scan`, `reflections.auditing.documentation_audit`, etc. Test logic stays.
- [ ] `tests/unit/test_sustainability.py` — UPDATE: 23 tests import from `agent.sustainability`. Move to `tests/unit/reflections/test_self_healing.py` (or per-reflection files) with new import paths.
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: 47 tests. Any that mock/assert specific callable paths need path updates. Most are generic scheduler tests; minimal impact.
- [ ] `tests/unit/test_ui_reflections_data.py` — UPDATE + EXTEND: 12 existing tests stay; add new tests for `get_grouped_reflections()` covering empty groups, all-disabled groups, mixed-status groups, error aggregation.
- [ ] `tests/integration/test_reflections_redis.py` — UPDATE: 13 tests. Imports change; Redis persistence tests unaffected.

No tests to DELETE — all existing coverage is relevant. No REPLACE needed — behavior is preserved, only import paths change.

## Rabbit Holes

- **Don't rewrite any reflection's logic.** Behavior parity is the mandate; this is a move+document refactor, not a rewrite. If a reflection is buggy, file a separate issue.
- **Don't normalize docstring style across all Popoto models.** Model behavior stays on models, but this plan is not a doc pass on `models/`. Scope creep.
- **Don't replace HTMX with a client framework.** The `<details>`-outside-swap-target approach works with the existing stack. Don't add Idiomorph, Alpine.js, or Lit just to solve open-state preservation.
- **Don't auto-derive groups from callable module paths in code.** The user asked for explicit `group:` fields in YAML — stick to that. Auto-derivation is implicit magic.
- **Don't collapse `reflections/session_intelligence.py`, `behavioral_learning.py`, `daily_report.py` further.** These are already single-file and already match the one-file-per-reflection principle; they just move into `reflections/pipelines/`.
- **Don't redesign the reflection details modal or run history pane.** The current drill-in view works; only the top-level grid changes.

## Risks

### Risk 1: Callable path typo breaks a reflection silently
**Impact:** A reflection stops running but no test catches it until production silence accumulates (e.g., Redis TTL cleanup not running leaves orphaned data for weeks).
**Mitigation:** (1) Add a scheduler startup check that verifies every `callable:` in the registry resolves successfully — fail loud on module import error. (2) Add a test that instantiates the scheduler and calls `load_registry()` + `_resolve_callable()` on every entry in `config/reflections.yaml`. Already partially present in `test_reflection_scheduler.py`; extend to assert 100% resolution.

### Risk 2: Dashboard `<details>` state lost on HTMX refresh
**Impact:** Every 10s the dashboard auto-refreshes. If `<details open>` is inside the swap target, the browser collapses the group mid-interaction — user clicks to expand, 0-10 seconds later it snaps shut.
**Mitigation:** Restructure the template so `<details>` lives *outside* the swap zone; only the inner rows of each group refresh. Alternative (not chosen): localStorage persistence via `htmx:afterSwap` listener — adds JS complexity without benefit.

### Risk 3: `agent/sustainability.py` has non-reflection exports we miss
**Impact:** Emptying the file breaks some unrelated import path.
**Mitigation:** Recon confirmed only `tests/unit/test_sustainability.py` imports from this module. Run full test suite after emptying to catch any imports we missed. Leave the file as a thin re-export shim during a transition commit if we want belt-and-suspenders.

### Risk 4: 31 PRs worth of change in one PR is hard to review
**Impact:** Reviewer fatigue; easy to miss a bad path migration.
**Mitigation:** Structure the PR as mechanical commits, one per phase: (A) utilities, (B) group scaffolding, (C) per-group moves (one commit per group, 7 commits), (D) YAML cutover + old-bundle deletion, (E) dashboard, (F) docs. Reviewer can step through commit-by-commit.

## Race Conditions

No race conditions identified. The scheduler is a single-threaded async loop; callable resolution is synchronous importlib; Redis `Reflection` state updates use existing `mark_started`/`mark_completed` patterns which were already race-audited in PR #933. Dashboard reads are read-only on Redis.

## No-Gos (Out of Scope)

- Changing any reflection's cadence or behavior.
- Introducing a plugin system or dynamic reflection loading.
- Reflection dependency graphs ("run X after Y") — current scheduler is interval-based; don't add DAG semantics.
- Per-reflection enable/disable toggles in the dashboard UI (still done via YAML).
- Renaming reflections (standardized in PR #991; any further renames are separate work).
- Migrating `docs/features/reflections.md` to a per-reflection doc page structure (file-level docstrings are the single source of truth now; `docs/features/reflections.md` stays as an architectural overview).

## Update System

No update system changes required — this refactor is purely internal. `scripts/remote-update.sh` does not reference reflection file paths. `config/reflections.yaml` is committed to the repo and picked up automatically on update.

## Agent Integration

No agent integration required — reflections are scheduled background jobs executed by the worker, not tools the agent invokes via Telegram. The scheduler is started from `worker/__main__.py`; no MCP changes, no `.mcp.json` updates.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` to describe the `reflections/{group}/{name}.py` layout and point readers at individual files as the source of truth. Remove any language that references the bundle modules.
- [ ] Verify `docs/features/README.md` index entry for reflections is still accurate.
- [ ] No new `docs/infra/` doc needed — no infra changes.

### Inline Documentation
- [ ] Each of the 31 reflection files has a module docstring covering purpose, cadence rationale, failure modes, related reflections, and cross-references (see Technical Approach for the standard shape).
- [ ] `reflections/utilities.py` has a module docstring listing each public helper, its purpose, and its callers.

### CLAUDE.md
- [ ] Update the CLAUDE.md reference to reflection locations if any currently point at bundle modules. (Quick scan: no direct references to bundle module file paths; likely no change needed — verify during Phase E.)

## Success Criteria

- [ ] `reflections/{group}/{name}.py` exists for all 31 reflections, each with a single `run()` public entry and a module docstring matching the standard shape
- [ ] `reflections/utilities.py` exists with `load_local_projects` + `run_llm_reflection`; `reflections/utils.py` deleted
- [ ] `reflections/maintenance.py`, `reflections/auditing.py`, `reflections/memory_management.py`, `reflections/task_management.py` all deleted
- [ ] `agent/sustainability.py` contains no reflection callables (file either deleted or reduced to non-reflection helpers with justification)
- [ ] `config/reflections.yaml`: every entry has a `group:` field, every `callable:` points at a resolvable path, stale `ReflectionRunner` comment at line 58 removed
- [ ] Dashboard at `localhost:8500` renders 7 collapsible groups, default collapsed, with enabled/total count + soonest next_due + error indicator per group
- [ ] HTMX auto-refresh preserves `<details>` open/closed state (verified manually)
- [ ] All 98 reflection-related tests pass after import-path updates
- [ ] New test: `test_all_callables_resolve` iterates `config/reflections.yaml` and asserts `_resolve_callable()` succeeds for every entry
- [ ] `/do-test` passes
- [ ] `/do-docs` passes
- [ ] `python -m ruff check .` and `python -m ruff format --check .` both clean
- [ ] Full scheduler tick against Redis shows each reflection fires once in `tests/integration/test_reflections_redis.py` smoke test
- [ ] `docs/features/reflections.md` updated; grep confirms no references to deleted bundle paths remain in `docs/`

## Team Orchestration

Single-dev refactor; team structure stays lean.

### Team Members

- **Builder (utilities + scaffolding)**
  - Name: `scaffold-builder`
  - Role: Create `reflections/utilities.py`, group directories, `__init__.py` files
  - Agent Type: builder
  - Resume: true

- **Builder (per group — 7 parallel)**
  - Name: `group-builder-{group}` (7 instances: `core`, `self_healing`, `maintenance`, `auditing`, `memory`, `tasks`, `pipelines`)
  - Role: Migrate all reflections for one group — extract/move/wrap to `reflections/{group}/{name}.py`, write standardized docstrings, keep behavior identical
  - Agent Type: builder
  - Resume: true

- **Builder (YAML cutover)**
  - Name: `yaml-cutover-builder`
  - Role: Update `config/reflections.yaml` paths + add `group:` fields; delete the 4 bundle modules + `reflections/utils.py` + stale comment line 58
  - Agent Type: builder
  - Resume: true

- **Builder (dashboard)**
  - Name: `dashboard-builder`
  - Role: Add `get_grouped_reflections()` to `ui/data/reflections.py`; restructure `ui/templates/reflections/_partials/status_grid.html` for collapsible groups; add route for per-group partial refresh
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: `test-engineer`
  - Role: Update test imports for moved modules; add `test_all_callables_resolve`; extend dashboard tests for grouping
  - Agent Type: test-engineer
  - Resume: true

- **Documentarian**
  - Name: `docs-writer`
  - Role: Update `docs/features/reflections.md`, audit `docs/` and `CLAUDE.md` for stale bundle-module references
  - Agent Type: documentarian
  - Resume: true

- **Validator (lead)**
  - Name: `lead-validator`
  - Role: Run full verification, smoke-test scheduler tick, confirm dashboard render in a real browser
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Scaffold utilities + group directories
- **Task ID**: build-scaffold
- **Depends On**: none
- **Validates**: file existence — `reflections/utilities.py`, `reflections/{core,self_healing,maintenance,auditing,memory,tasks,pipelines}/__init__.py`
- **Assigned To**: scaffold-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/utilities.py` with `load_local_projects` and `run_llm_reflection` copied verbatim from `reflections/utils.py` (keep other helpers in `utils.py` for now — they'll be inlined during group migrations).
- Create 7 group directories under `reflections/` with empty `__init__.py`.

### 2. Migrate `core` group
- **Task ID**: build-core
- **Depends On**: build-scaffold
- **Validates**: `tests/unit/test_reflections_package.py::test_core_callables` (add)
- **Assigned To**: group-builder-core
- **Agent Type**: builder
- **Parallel**: true (with 3-8)
- Create `reflections/core/session_liveness_check.py` → thin wrapper calling `agent.agent_session_queue._agent_session_health_check`
- Create `reflections/core/agent_session_cleanup.py` → wrapper for `cleanup_corrupted_agent_sessions`
- Create `reflections/core/stale_branch_cleanup.py` → wrapper for `cleanup_stale_branches_all_projects`
- Create `reflections/core/redis_index_cleanup.py` → wrapper for `scripts.popoto_index_cleanup.run_cleanup`
- Each file gets a standardized docstring per Technical Approach

### 3. Migrate `self_healing` group
- **Task ID**: build-self-healing
- **Depends On**: build-scaffold
- **Validates**: existing `tests/unit/test_sustainability.py` still passes via new paths
- **Assigned To**: group-builder-self_healing
- **Agent Type**: builder
- **Parallel**: true (with 2, 4-8)
- Move `circuit_health_gate`, `session_recovery_drip`, `session_count_throttle`, `failure_loop_detector` from `agent/sustainability.py` into per-file `reflections/self_healing/*.py`
- Carry internal helpers (`_compute_fingerprint`, `_file_github_issue`, `_send_telegram`) with whichever reflection uses them; duplicate if shared by ≥2
- Create `reflections/self_healing/system_health_digest.py` → `execution_type: agent` entries still declare their `command:` in YAML; the file exists for docs and any future function conversion (docstring + placeholder if execution_type stays `agent`)
- Create `reflections/self_healing/sentry_issue_triage.py` → same pattern (currently disabled, `execution_type: agent`)

### 4. Migrate `maintenance` group
- **Task ID**: build-maintenance
- **Depends On**: build-scaffold
- **Validates**: `tests/unit/test_reflections_package.py` maintenance tests pass with new imports
- **Assigned To**: group-builder-maintenance
- **Agent Type**: builder
- **Parallel**: true (with 2-3, 5-8)
- Split `reflections/maintenance.py` into 6 files: `tech_debt_scan.py`, `redis_ttl_cleanup.py`, `redis_quality_audit.py`, `merged_branch_cleanup.py`, `disk_space_check.py`, `analytics_rollup.py`
- Inline `extract_structured_errors` (single caller) into its owning file
- Each file imports `load_local_projects` from `reflections.utilities` as needed

### 5. Migrate `auditing` group
- **Task ID**: build-auditing
- **Depends On**: build-scaffold
- **Validates**: `tests/unit/test_reflections_package.py` auditing tests pass with new imports
- **Assigned To**: group-builder-auditing
- **Agent Type**: builder
- **Parallel**: true (with 2-4, 6-8)
- Split `reflections/auditing.py` (715L, 6 reflections) into per-file units
- Inline `extract_structured_errors` (single caller) if it lives here

### 6. Migrate `memory` group
- **Task ID**: build-memory
- **Depends On**: build-scaffold
- **Validates**: memory management tests pass with new imports
- **Assigned To**: group-builder-memory
- **Agent Type**: builder
- **Parallel**: true (with 2-5, 7-8)
- Split `reflections/memory_management.py` into `memory_decay_prune.py`, `memory_quality_audit.py`, `knowledge_reindex.py`
- Move `scripts/memory_consolidation.py:run_consolidation` → `reflections/memory/memory_dedup.py` (keeping a thin `scripts/memory_consolidation.py` shim if any CLI invokes it directly; else delete `scripts/memory_consolidation.py`)

### 7. Migrate `tasks` group
- **Task ID**: build-tasks
- **Depends On**: build-scaffold
- **Validates**: task management tests pass with new imports
- **Assigned To**: group-builder-tasks
- **Agent Type**: builder
- **Parallel**: true (with 2-6, 8)
- Split `reflections/task_management.py` (122L, 2 reflections) into `task_backlog_check.py` and `principal_staleness.py`

### 8. Migrate `pipelines` group
- **Task ID**: build-pipelines
- **Depends On**: build-scaffold
- **Validates**: pipeline tests pass with new imports
- **Assigned To**: group-builder-pipelines
- **Agent Type**: builder
- **Parallel**: true (with 2-7)
- Move `reflections/session_intelligence.py` → `reflections/pipelines/session_intelligence.py`
- Move `reflections/behavioral_learning.py` → `reflections/pipelines/behavioral_learning.py`
- Move `reflections/daily_report.py` → `reflections/pipelines/daily_report_and_notify.py` (rename to match registry `name:`)
- Update each file's module docstring to the standard shape if it's not already

### 9. YAML cutover + delete old bundles
- **Task ID**: build-yaml-cutover
- **Depends On**: build-core, build-self-healing, build-maintenance, build-auditing, build-memory, build-tasks, build-pipelines
- **Validates**: `test_all_callables_resolve` (new test added in task 12); scheduler startup doesn't ImportError
- **Assigned To**: yaml-cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Update every `callable:` in `config/reflections.yaml` to the new path
- Add `group:` field to every entry
- Delete stale `ReflectionRunner` comment at line 58
- Delete `reflections/maintenance.py`, `reflections/auditing.py`, `reflections/memory_management.py`, `reflections/task_management.py`, `reflections/utils.py`
- Empty (or delete) `agent/sustainability.py` — if any non-reflection helpers remain with legitimate callers, leave them with a `# NOT a reflection — used by X` comment
- Run `python -m ruff check .` and `python -m ruff format .` to clean import reshuffling

### 10. Dashboard grouping
- **Task ID**: build-dashboard
- **Depends On**: build-yaml-cutover
- **Validates**: `tests/unit/test_ui_reflections_data.py` passes with new grouping tests; manual browser check
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `get_grouped_reflections()` to `ui/data/reflections.py` with group-label mapping constant
- Restructure `ui/templates/reflections/_partials/status_grid.html`: outer `<details>` per group (NOT in HTMX swap target), inner rows swap via a new `/reflections/_partials/group/{group}/` route
- Add the per-group partial route in `ui/app.py` (follows existing pattern around line 295)
- Summary row format: `{label}  {enabled_count}/{total_count} enabled  next: {ts}  [error dot if any child failed]`
- Default state: all `<details>` collapsed

### 11. Manual UI validation
- **Task ID**: validate-dashboard
- **Depends On**: build-dashboard
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Start `python -m ui.app`; open `http://localhost:8500`
- Verify: 7 group rows render, all collapsed
- Expand one group, wait 15 seconds, confirm it stays expanded through the HTMX refresh
- Confirm error dot renders when any child reflection has `last_status: error`
- Screenshot before/after

### 12. Test updates + new resolution test
- **Task ID**: build-tests
- **Depends On**: build-yaml-cutover, build-dashboard
- **Validates**: `pytest tests/unit/ tests/integration/test_reflections_redis.py -q`
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update all import paths across `tests/unit/test_reflections_package.py`, `tests/unit/test_sustainability.py`, `tests/unit/test_reflection_scheduler.py`, `tests/unit/test_ui_reflections_data.py`, `tests/integration/test_reflections_redis.py`
- Add `test_all_callables_resolve` in `test_reflection_scheduler.py`: iterate `config/reflections.yaml`, call `_resolve_callable()` on each entry, assert no ImportError / AttributeError
- Extend `test_ui_reflections_data.py` with tests for `get_grouped_reflections()`: empty registry, single-group registry, mixed-status groups, error aggregation

### 13. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` to describe the `reflections/{group}/{name}.py` layout; point readers at individual files as source-of-truth
- Grep `docs/` and `CLAUDE.md` for references to `reflections/maintenance.py`, `agent/sustainability.py`, etc., and replace with new paths or remove
- Confirm `docs/features/README.md` index is accurate

### 14. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-dashboard, build-tests, document-feature
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification table commands
- Confirm every Success Criteria checkbox is met
- Generate final report: line count delta (expect ~3,250 → ~similar or slightly more due to docstrings, but distributed across 31 files), file count delta (9 → 33+ including `__init__.py`), list of files deleted

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit tests/integration/test_reflections_redis.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| All callables resolve | `pytest tests/unit/test_reflection_scheduler.py::test_all_callables_resolve -q` | exit code 0 |
| No bundle modules left | `test ! -f reflections/maintenance.py && test ! -f reflections/auditing.py && test ! -f reflections/memory_management.py && test ! -f reflections/task_management.py && test ! -f reflections/utils.py` | exit code 0 |
| 31 reflection files exist | `find reflections -mindepth 2 -maxdepth 2 -name '*.py' ! -name '__init__.py' \| wc -l` | output > 30 |
| Every YAML entry has group | `python -c "import yaml; d=yaml.safe_load(open('config/reflections.yaml')); [assert_group(r) for r in d['reflections']]"` | exit code 0 |
| Stale comment removed | `grep -n 'ReflectionRunner' config/reflections.yaml` | exit code 1 |
| Dashboard renders groups | `curl -s localhost:8500/ \| grep -c '<details class="reflection-group"'` | output > 6 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

1. **Agent-type reflections (`system-health-digest`, `sentry-issue-triage`) — do they get files?** These have `execution_type: agent` and no Python callable; they're prompts executed by a PM session. Options: (a) create placeholder files with docstrings pointing at the YAML `command:` as source-of-truth, for consistency and discoverability; (b) skip — only function-type reflections get files. I assumed (a) in the plan; confirm.

2. **`scripts/memory_consolidation.py` and `scripts/popoto_index_cleanup.py` — move or wrap?** Recon didn't find non-scheduler callers, but these live in `scripts/` suggesting they may have been run manually as CLIs historically. If either is still invoked from a shell command or docs, keep a thin shim. Confirm or let me verify during build.

3. **`agent/sustainability.py` file — fully delete, or leave a stub?** All 4 reflections move out; recon found no external non-test callers. Deleting is cleanest. But `send_hibernation_notification` and `sustainability_digest` exports exist — are these called by anything I missed? (Will re-grep during build to be sure.)

4. **Group labels for display** — proposed: Core, Self-Healing, Maintenance, Auditing, Memory, Tasks, Pipelines. Any preference on ordering (priority-first: Self-Healing → Core → ... vs. alphabetical vs. frequency-based)? I'll default to priority-first for the dashboard since most interesting groups should be at the top.
