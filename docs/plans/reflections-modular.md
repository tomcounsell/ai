---
status: Ready
type: chore
appetite: Medium
owner: Valor
created: 2026-04-17
tracking: https://github.com/tomcounsell/ai/issues/1028
last_comment_id: 4377084180
revision_applied: true
---

# Reflections Modularization: One File per Reflection, Grouped Dashboard

## Problem

The reflections system has 33 recurring jobs declared in `config/reflections.yaml`, dispatched by `agent/reflection_scheduler.py`. After PR #967 extracted the 3,086-line monolith, their definitions still live in bundle modules totaling ~3,250 LOC (`reflections/auditing.py` 715L, `reflections/maintenance.py` 470L, `agent/sustainability.py` 626L, etc.). The dashboard renders them as a flat list that dominates the `localhost:8500` home view.

**Current behavior:**
- Bundle modules force a reader to scroll past 5 unrelated reflections to understand one. Cadence rationale, failure modes, and cross-references aren't consistently documented per reflection.
- A failing test in `reflections/auditing.py` doesn't identify which of its 6 reflections broke without digging.
- The dashboard "Reflections" section on `localhost:8500` is a flat table with no grouping, no collapse, no summary. It owns ~70% of the viewport.
- 9 reflections live outside `reflections/` (in `agent/sustainability.py`, `agent/agent_session_queue.py`, `scripts/`) — the callable registry is the only thing that makes them cohere as a subsystem.
- `config/reflections.yaml:58` still references `ReflectionRunner` (deleted in PR #967).
- The dashboard's group taxonomy is defined twice: as `REFLECTION_GROUPS` constants in `ui/data/reflections.py` AND implicitly via bundle file membership. The Python constants are also stale (e.g. references `docs-auditor` while YAML has `documentation-audit` and `feature-docs-audit`). Single source of truth is needed.

**Desired outcome:**
- Every reflection is a self-contained file at `reflections/{group}/{reflection_name}.py` with a module docstring covering purpose, cadence rationale, failure modes, and related reflections.
- Shared helpers live in one file: `reflections/utilities.py` — only genuinely shared logic (≥3 callers or non-trivial).
- Model properties and methods stay on their Popoto models; reflection files orchestrate, they don't own model logic.
- Dashboard renders 4 collapsible groups (matching the existing dashboard taxonomy: `agents`, `housekeeping`, `audits`, `memory`); default collapsed, each summary shows enabled/total, soonest next_due, and error indicator.
- `agent/sustainability.py` deleted; stale YAML comment removed.
- YAML `group:` field becomes the single source of truth for group membership; `REFLECTION_GROUPS` constants in `ui/data/reflections.py` deleted.

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

**Notes:** The dashboard-domination problem is now more acute — 9/33 reflections are disabled, yet the dashboard renders all rows regardless of enabled state. Collapsible grouping is more valuable post-drift, not less. Any `enabled: false` reflection has a comment explaining why; preserve that comment verbatim when adding the `group:` field during YAML cutover (task 6).

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
- **4 group directories** under `reflections/`: `agents/`, `housekeeping/`, `audits/`, `memory/`. Each with an `__init__.py` (empty — no package-level re-exports). These match the existing dashboard taxonomy in `ui/data/reflections.py` (`GROUP_AGENTS`, `GROUP_HOUSEKEEPING`, `GROUP_AUDITS`, `GROUP_MEMORY`).
- **One file per reflection** — every reflection in `config/reflections.yaml` (33 currently) gets a file at `reflections/{group}/{name}.py`. Each is a self-contained teaching artifact: module docstring → imports → `run(...)` public entry → private helpers. **Includes agent-type reflections** (`system-health-digest`, `sentry-issue-triage`, plus any others with `execution_type: agent`): these get placeholder files whose docstring documents purpose/cadence/failure modes and explicitly points at the YAML `command:` as the executable source-of-truth.
- **Relocated callables** — the 4 `agent/sustainability.py` reflections move into `reflections/agents/`. The 3 `agent/agent_session_queue.py` callables get thin wrappers in `reflections/agents/` and `reflections/housekeeping/` (keeping the queue functions where ops scripts call them). `scripts/popoto_index_cleanup.py` and `scripts/memory_consolidation.py` keep thin shims in their original locations (per Tom: existing CLI invocations may exist; safer to leave shims than risk breaking them); the reflection logic moves into `reflections/housekeeping/redis_index_cleanup.py` and `reflections/memory/memory_dedup.py` respectively.
- **YAML migration** — `config/reflections.yaml` updates every `callable:` path and adds an explicit `group:` field per entry (one of `agents`, `housekeeping`, `audits`, `memory`). Stale `ReflectionRunner` comment at line 58 removed.
- **`agent/sustainability.py` fully deleted** — all 4 reflections move out; recon will re-confirm no external non-test callers; if `send_hibernation_notification` or `sustainability_digest` exports have surviving callers, they relocate to a non-reflection module (e.g. `agent/notifications.py`) before deletion.
- **Dashboard grouping** — `ui/data/reflections.py` returns grouped structure; the `REFLECTION_GROUPS` Python mapping is deleted (YAML `group:` field is the new source of truth); `ui/templates/reflections/_partials/status_grid.html` renders `<details>` per group.

### Flow

**Phase A (skeleton):** Create `reflections/utilities.py` and the 4 group directories with empty `__init__.py`.
**Phase B (migrate, parallel per group):** For each group, create individual reflection files by extracting from the source bundle (or moving from `agent/sustainability.py` / wrapping from `agent/agent_session_queue.py`). Includes agent-type placeholder files.
**Phase C (cutover):** Update `config/reflections.yaml` all at once (new paths + `group:` fields), then delete the 4 bundle modules + `reflections/utils.py` + `agent/sustainability.py` + stale comment.
**Phase D (dashboard):** Update `ui/data/reflections.py` to read `group:` from YAML (delete `REFLECTION_GROUPS` constants), update Jinja template to render collapsible `<details>`.
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
- **Wrap** when the function is called by non-reflection code paths: the 3 `agent/agent_session_queue.py` callables stay in-place (they're also called by ops scripts); the corresponding `reflections/agents/*.py` or `reflections/housekeeping/*.py` file imports and delegates. Same pattern for `scripts/popoto_index_cleanup.py` and `scripts/memory_consolidation.py` per Tom's resolution: shims stay at original paths, reflection logic moves into the new files.

**YAML grouping field:**
```yaml
- name: circuit-health-gate
  group: agents          # NEW: one of agents | housekeeping | audits | memory
  description: "..."
  interval: 60
  priority: high
  execution_type: function
  callable: "reflections.agents.circuit_health_gate.run"
  enabled: true
```

**Group assignment heuristic:** The dashboard's existing `REFLECTION_GROUPS` constant in `ui/data/reflections.py` is the starting point. During YAML cutover, each reflection's `group:` matches that mapping where present. New reflections not in the mapping (e.g. `documentation-audit`, `feature-docs-audit`, `knowledge-reindex`, `embedding-orphan-sweep`) inherit by intuitive fit: anything ending in `-audit` → `audits`; anything memory/embedding-related → `memory`; anything sweeping/cleaning → `housekeeping`. The single source of truth shifts from the Python constant to the YAML field, and the constant gets deleted in Phase D.

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
  "name": "agents",
  "label": "Agents",
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
- [ ] Empty group (e.g., if `memory/` ships with only 1 reflection after disabling others): group still renders with `0/1` display.

## Test Impact

- [ ] `tests/unit/test_reflections_package.py` — UPDATE: 26 tests import from `reflections.maintenance`, `reflections.auditing`, etc. Imports change to `reflections.maintenance.tech_debt_scan`, `reflections.auditing.documentation_audit`, etc. Test logic stays.
- [ ] `tests/unit/test_sustainability.py` — UPDATE: 23 tests import from `agent.sustainability`. Move to `tests/unit/reflections/test_agents_self_healing.py` (or per-reflection files) with new import paths pointing at `reflections.agents.{circuit_health_gate,session_recovery_drip,session_count_throttle,failure_loop_detector}`.
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: 47 tests. Any that mock/assert specific callable paths need path updates. Most are generic scheduler tests; minimal impact.
- [ ] `tests/unit/test_ui_reflections_data.py` — UPDATE + EXTEND: 12 existing tests stay; add new tests for `get_grouped_reflections()` covering empty groups, all-disabled groups, mixed-status groups, error aggregation.
- [ ] `tests/integration/test_reflections_redis.py` — UPDATE: 13 tests. Imports change; Redis persistence tests unaffected.
- [ ] `tests/unit/test_utc.py` — VERIFY: comment `4277729942` (2026-04-20 UTC hotfix, commit `9e3a64f5`) rewired three relocated callables (`session_recovery_drip`, `session_count_throttle`, `failure_loop_detector`, plus `memory_decay_prune`, `memory_quality_audit`, `behavioral_learning` if still extant) to import `to_unix_ts` from `bridge.utc`. After the file split, confirm test_utc.py still exercises these callables via their new import paths, or that the test references nothing path-specific. UPDATE imports only if needed.
- [ ] `ui/templates/index.html` and the session-list partial (per PR #1282 / issue #1269) — VERIFY UNTOUCHED: the template restructure scope is `ui/templates/reflections/_partials/status_grid.html` only. Session row liveness chips, ghost badge, and lifecycle glyphs added by #1282 must not regress. No code changes expected; add a smoke test or visual check during BUILD that loads `localhost:8500` and confirms session rows still render their Liveness chips alongside the new collapsible reflection groups.

No tests to DELETE — all existing coverage is relevant. No REPLACE needed — behavior is preserved, only import paths change.

**Upstream-change notices incorporated:**
- Comment `4277729942` (2026-04-20): UTC hotfix `9e3a64f5` rewired `.timestamp()` calls in three sustainability callables, two memory_management callables, and `behavioral_learning` to use `bridge.utc.to_unix_ts()`. **Action for BUILD:** when relocating these files to `reflections/agents/`, `reflections/memory/`, and `reflections/pipelines/`, preserve the `from bridge.utc import to_unix_ts` imports and naive-as-UTC normalization. Do NOT regress to bare `.timestamp()`. The "behavior parity" mandate in Rabbit Holes already covers this; this is the explicit reminder.
- Comment `4377084180` (2026-05-05): PR #1282 / issue #1269 expanded the dashboard session detail modal (Liveness section, freshness chips, ghost badge, new glyphs). **Action for BUILD:** the collapsible `<details>` grouping pattern this plan establishes for reflections must NOT bleed into the session list. Scope of template restructure is reflections-only — touch only `ui/templates/reflections/_partials/status_grid.html` and `ui/data/reflections.py`. Different files than session-list, but they share the dashboard refresh path and HTMX cadence, so verify visually during BUILD.

## Rabbit Holes

- **Don't rewrite any reflection's logic.** Behavior parity is the mandate; this is a move+document refactor, not a rewrite. If a reflection is buggy, file a separate issue.
- **Don't normalize docstring style across all Popoto models.** Model behavior stays on models, but this plan is not a doc pass on `models/`. Scope creep.
- **Don't replace HTMX with a client framework.** The `<details>`-outside-swap-target approach works with the existing stack. Don't add Idiomorph, Alpine.js, or Lit just to solve open-state preservation.
- **Don't auto-derive groups from callable module paths in code.** The user asked for explicit `group:` fields in YAML — stick to that. Auto-derivation is implicit magic.
- **Don't collapse `reflections/session_intelligence.py`, `daily_report.py` further.** These are already single-file and already match the one-file-per-reflection principle; they just move into the appropriate group directory (`agents/`, `audits/` respectively per dashboard taxonomy). (`behavioral_learning.py` was deleted in #1362; no longer applies.)
- **Don't introduce a new group taxonomy.** Per Tom: use the existing 4 dashboard groups (`agents`, `housekeeping`, `audits`, `memory`) verbatim. Do not propose `core/`, `self_healing/`, `tasks/`, `pipelines/`, or any other directory name.
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
- [ ] Each reflection file (33 currently) has a module docstring covering purpose, cadence rationale, failure modes, related reflections, and cross-references (see Technical Approach for the standard shape). Agent-type files use the placeholder docstring pattern pointing at the YAML `command:`.
- [ ] `reflections/utilities.py` has a module docstring listing each public helper, its purpose, and its callers.

### CLAUDE.md
- [ ] Update the CLAUDE.md reference to reflection locations if any currently point at bundle modules. (Quick scan: no direct references to bundle module file paths; likely no change needed — verify during Phase E.)

## Success Criteria

- [ ] `reflections/{group}/{name}.py` exists for every reflection in `config/reflections.yaml` (33 currently), each with a single `run()` public entry and a module docstring matching the standard shape. Agent-type reflections get placeholder files whose docstring points at YAML `command:` as executable source-of-truth.
- [ ] `reflections/utilities.py` exists with `load_local_projects` + `run_llm_reflection`; `reflections/utils.py` deleted
- [ ] All single-file bundles deleted: `reflections/maintenance.py`, `reflections/auditing.py`, `reflections/memory_management.py`, `reflections/task_management.py`, `reflections/session_intelligence.py`, `reflections/daily_report.py` (`reflections/behavioral_learning.py` already deleted in #1362)
- [ ] `agent/sustainability.py` **fully deleted** (per Tom). Any non-reflection helpers with surviving callers (`send_hibernation_notification`, `sustainability_digest`) relocated to `agent/notifications.py` first.
- [ ] `scripts/popoto_index_cleanup.py` and `scripts/memory_consolidation.py` retained as thin import-and-delegate shims (per Tom: existing CLI invocations may exist; safer to leave shims).
- [ ] `config/reflections.yaml`: every entry has a `group:` field with value in `{agents, housekeeping, audits, memory}`, every `callable:` points at a resolvable path, stale `ReflectionRunner` comment at line 58 removed
- [ ] `REFLECTION_GROUPS`, `GROUP_DESCRIPTIONS`, and `GROUP_DISPLAY_ORDER` constants deleted from `ui/data/reflections.py` (YAML `group:` field is now the single source of truth)
- [ ] Dashboard at `localhost:8500` renders 4 collapsible groups (`Agents`, `Housekeeping`, `Audits`, `Memory`), default collapsed, with enabled/total count + soonest next_due + error indicator per group
- [ ] HTMX auto-refresh preserves `<details>` open/closed state (verified manually)
- [ ] All ~120 reflection-related tests pass after import-path updates (counts: 26 + 23 + 47 + 12 + 13 = 121)
- [ ] New test: `test_all_callables_resolve` iterates `config/reflections.yaml` and asserts `_resolve_callable()` succeeds for every entry
- [ ] New test: `test_every_yaml_entry_has_group` iterates `config/reflections.yaml` and asserts every entry has a `group:` field with valid value
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

- **Builder (per group — 4 parallel)**
  - Name: `group-builder-{group}` (4 instances: `agents`, `housekeeping`, `audits`, `memory`)
  - Role: Migrate all reflections for one group — extract/move/wrap to `reflections/{group}/{name}.py`, write standardized docstrings, keep behavior identical, including agent-type placeholder files where applicable
  - Agent Type: builder
  - Resume: true

- **Builder (YAML cutover)**
  - Name: `yaml-cutover-builder`
  - Role: Update `config/reflections.yaml` paths + add `group:` fields per entry (one of `agents`, `housekeeping`, `audits`, `memory`); delete the 4 bundle modules + `reflections/utils.py` + `agent/sustainability.py` + stale comment line 58
  - Agent Type: builder
  - Resume: true

- **Builder (dashboard)**
  - Name: `dashboard-builder`
  - Role: Add `get_grouped_reflections()` to `ui/data/reflections.py` (reading `group:` from YAML); delete `REFLECTION_GROUPS`/`GROUP_DESCRIPTIONS`/`GROUP_DISPLAY_ORDER` constants; restructure `ui/templates/reflections/_partials/status_grid.html` for collapsible groups; add route for per-group partial refresh
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
- **Validates**: file existence — `reflections/utilities.py`, `reflections/{agents,housekeeping,audits,memory}/__init__.py`
- **Assigned To**: scaffold-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/utilities.py` with `load_local_projects` and `run_llm_reflection` copied verbatim from `reflections/utils.py` (keep other helpers in `utils.py` for now — they'll be inlined during group migrations).
- Create 4 group directories under `reflections/` (`agents/`, `housekeeping/`, `audits/`, `memory/`) with empty `__init__.py`.

### 2. Migrate `agents` group
- **Task ID**: build-agents
- **Depends On**: build-scaffold
- **Validates**: existing `tests/unit/test_sustainability.py` still passes via new paths; `tests/unit/test_reflections_package.py` agent-related tests pass
- **Assigned To**: group-builder-agents
- **Agent Type**: builder
- **Parallel**: true (with 3-5)
- Wrap (thin shim, source stays put): `agent.agent_session_queue._agent_session_health_check` → `reflections/agents/session_liveness_check.py`; `cleanup_corrupted_agent_sessions` → `reflections/agents/agent_session_cleanup.py`
- Move from `agent/sustainability.py`: `circuit_health_gate`, `session_recovery_drip`, `session_count_throttle`, `failure_loop_detector` → per-file `reflections/agents/*.py`. Carry internal helpers (`_compute_fingerprint`, `_file_github_issue`, `_send_telegram`) with whichever reflection uses them; duplicate if shared by ≥2.
- Move: `reflections/session_intelligence.py` → `reflections/agents/session_intelligence.py`
- Placeholder file (agent-type, no Python callable): `reflections/agents/system_health_digest.py` — module docstring documents purpose/cadence/failure modes and points at YAML `command:` as executable source-of-truth
- Placeholder file (agent-type, if applicable): `reflections/agents/pm_audio_briefing.py` — same pattern; verify `execution_type` during build before treating as placeholder
- Each file gets a standardized docstring per Technical Approach
- **Preserve sync signatures** — per commit `65fcfcc5`, several reflections were converted from async to sync; do not reintroduce `async def run()`
- **Preserve `to_unix_ts` imports** — the 3 sustainability reflections (`session_recovery_drip`, `session_count_throttle`, `failure_loop_detector`) use `from bridge.utc import to_unix_ts` per the naive-datetime hotfix (commit `9e3a64f5`); preserve verbatim

### 3. Migrate `housekeeping` group
- **Task ID**: build-housekeeping
- **Depends On**: build-scaffold
- **Validates**: `tests/unit/test_reflections_package.py` housekeeping tests pass with new imports
- **Assigned To**: group-builder-housekeeping
- **Agent Type**: builder
- **Parallel**: true (with 2, 4-5)
- Wrap (thin shim, source stays put): `agent.agent_session_queue.cleanup_stale_branches_all_projects` → `reflections/housekeeping/stale_branch_cleanup.py`
- Move logic, keep CLI shim: `scripts/popoto_index_cleanup.py:run_cleanup` → `reflections/housekeeping/redis_index_cleanup.py`. Per Tom: leave the shim at `scripts/popoto_index_cleanup.py` (may be invoked manually as CLI). The shim becomes a thin import-and-delegate wrapper.
- Split `reflections/maintenance.py` into per-file units in `reflections/housekeeping/`: `tech_debt_scan.py`, `redis_ttl_cleanup.py`, `redis_quality_audit.py`, `merged_branch_cleanup.py`, `disk_space_check.py`, `analytics_rollup.py` — **note** `tech_debt_scan` and `redis_quality_audit` will move to `audits/` per group-membership rules; only the cleanup/check/rollup ones land in `housekeeping/`
- Move: `do-docs-branch-sweeper` callable (locate via `config/reflections.yaml` → its current source module) into `reflections/housekeeping/do_docs_branch_sweeper.py`
- Inline single-use helpers (`extract_structured_errors`, regex constants) into their owning files

### 4. Migrate `audits` group
- **Task ID**: build-audits
- **Depends On**: build-scaffold
- **Validates**: `tests/unit/test_reflections_package.py` audit tests pass with new imports
- **Assigned To**: group-builder-audits
- **Agent Type**: builder
- **Parallel**: true (with 2-3, 5)
- Split `reflections/auditing.py` (715L, 6 reflections) into per-file units under `reflections/audits/`: `daily_log_review.py`, `documentation_audit.py`, `feature_docs_audit.py`, `skills_audit.py`, `hooks_audit.py`, `pr_review_audit.py`
- Move from `maintenance.py` (per group-fit): `tech_debt_scan.py`, `redis_quality_audit.py` → `reflections/audits/`
- Split `reflections/task_management.py` into `reflections/audits/task_backlog_check.py` and `reflections/audits/principal_staleness.py` (these were in the prior `tasks/` group; per dashboard taxonomy they belong in `audits/`)
- Move: `reflections/daily_report.py` → `reflections/audits/daily_report_and_notify.py` (rename to match registry `name:`)
- Placeholder file (agent-type, currently disabled): `reflections/audits/sentry_issue_triage.py` — module docstring + pointer to YAML `command:`
- Inline single-use helpers into their owning files

### 5. Migrate `memory` group
- **Task ID**: build-memory
- **Depends On**: build-scaffold
- **Validates**: memory management tests pass with new imports
- **Assigned To**: group-builder-memory
- **Agent Type**: builder
- **Parallel**: true (with 2-4)
- Split `reflections/memory_management.py` into `reflections/memory/memory_decay_prune.py`, `reflections/memory/memory_quality_audit.py`. Preserve `to_unix_ts` import per commit `9e3a64f5`.
- Move logic, keep CLI shim: `scripts/memory_consolidation.py:run_consolidation` → `reflections/memory/memory_dedup.py`. Per Tom: leave the shim at `scripts/memory_consolidation.py` (may be invoked manually as CLI). The shim becomes a thin import-and-delegate wrapper.
- Move (verify existence first): `knowledge-reindex` callable → `reflections/memory/knowledge_reindex.py`
- Move (verify existence first): `embedding-orphan-sweep` callable → `reflections/memory/embedding_orphan_sweep.py`

### 6. YAML cutover + delete old bundles
- **Task ID**: build-yaml-cutover
- **Depends On**: build-agents, build-housekeeping, build-audits, build-memory
- **Validates**: `test_all_callables_resolve` (new test added in task 9); scheduler startup doesn't ImportError
- **Assigned To**: yaml-cutover-builder
- **Agent Type**: builder
- **Parallel**: false
- Update every `callable:` in `config/reflections.yaml` to the new path (e.g. `reflections.agents.circuit_health_gate.run`)
- Add `group:` field to every entry, value one of `agents | housekeeping | audits | memory`. Group assignment per the heuristic in Technical Approach (existing `REFLECTION_GROUPS` mapping as starting point; new entries by intuitive fit)
- Delete stale `ReflectionRunner` comment at line 58
- Delete bundles: `reflections/maintenance.py`, `reflections/auditing.py`, `reflections/memory_management.py`, `reflections/task_management.py`, `reflections/utils.py`, `reflections/session_intelligence.py`, `reflections/daily_report.py` (`reflections/behavioral_learning.py` already deleted in #1362)
- **Fully delete `agent/sustainability.py`** (per Tom). If `send_hibernation_notification` or `sustainability_digest` exports have surviving non-test callers (re-grep before deletion), relocate them to `agent/notifications.py` first; otherwise delete the file outright. Do not leave a stub.
- Run `python -m ruff check .` and `python -m ruff format .` to clean import reshuffling

### 7. Dashboard grouping
- **Task ID**: build-dashboard
- **Depends On**: build-yaml-cutover
- **Validates**: `tests/unit/test_ui_reflections_data.py` passes with new grouping tests; manual browser check
- **Assigned To**: dashboard-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `get_grouped_reflections()` to `ui/data/reflections.py` reading `group:` from each YAML entry (no longer from `REFLECTION_GROUPS` constant)
- **Delete the `REFLECTION_GROUPS`, `GROUP_DESCRIPTIONS`, and `GROUP_DISPLAY_ORDER` constants** in `ui/data/reflections.py` — YAML `group:` field is now the single source of truth. Display labels (`"Agents"`, `"Housekeeping"`, `"Audits"`, `"Memory"`) become a small constant mapping for title-case rendering only.
- Restructure `ui/templates/reflections/_partials/status_grid.html`: outer `<details>` per group (NOT in HTMX swap target), inner rows swap via a new `/reflections/_partials/group/{group}/` route
- Add the per-group partial route in `ui/app.py` (follows existing pattern around line 295)
- Summary row format: `{label}  {enabled_count}/{total_count} enabled  next: {ts}  [error dot if any child failed]`
- Default state: all `<details>` collapsed

### 8. Manual UI validation
- **Task ID**: validate-dashboard
- **Depends On**: build-dashboard
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Start `python -m ui.app`; open `http://localhost:8500`
- Verify: 4 group rows render (`Agents`, `Housekeeping`, `Audits`, `Memory`), all collapsed
- Expand one group, wait 15 seconds, confirm it stays expanded through the HTMX refresh
- Confirm error dot renders when any child reflection has `last_status: error`
- Screenshot before/after

### 9. Test updates + new resolution test
- **Task ID**: build-tests
- **Depends On**: build-yaml-cutover, build-dashboard
- **Validates**: `pytest tests/unit/ tests/integration/test_reflections_redis.py -q`
- **Assigned To**: test-engineer
- **Agent Type**: test-engineer
- **Parallel**: false
- Update all import paths across `tests/unit/test_reflections_package.py`, `tests/unit/test_sustainability.py`, `tests/unit/test_reflection_scheduler.py`, `tests/unit/test_ui_reflections_data.py`, `tests/unit/test_utc.py` (if it imports from sustainability), `tests/integration/test_reflections_redis.py`
- Add `test_all_callables_resolve` in `test_reflection_scheduler.py`: iterate `config/reflections.yaml`, call `_resolve_callable()` on each entry, assert no ImportError / AttributeError
- Add `test_every_yaml_entry_has_group` in `test_reflection_scheduler.py`: iterate `config/reflections.yaml`, assert every entry has a `group:` field with value in `{agents, housekeeping, audits, memory}`
- Extend `test_ui_reflections_data.py` with tests for `get_grouped_reflections()`: empty registry, single-group registry, mixed-status groups, error aggregation

### 10. Documentation
- **Task ID**: document-feature
- **Depends On**: build-tests
- **Assigned To**: docs-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` to describe the `reflections/{group}/{name}.py` layout; point readers at individual files as source-of-truth
- Grep `docs/` and `CLAUDE.md` for references to `reflections/maintenance.py`, `agent/sustainability.py`, etc., and replace with new paths or remove
- Confirm `docs/features/README.md` index is accurate

### 11. Final validation
- **Task ID**: validate-all
- **Depends On**: validate-dashboard, build-tests, document-feature
- **Assigned To**: lead-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all Verification table commands
- Confirm every Success Criteria checkbox is met
- Generate final report: line count delta (expect ~3,250 → ~similar or slightly more due to docstrings, but distributed across 33 files), file count delta (9 → 33+ including `__init__.py`), list of files deleted

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit tests/integration/test_reflections_redis.py -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| All callables resolve | `pytest tests/unit/test_reflection_scheduler.py::test_all_callables_resolve -q` | exit code 0 |
| Every YAML entry has group | `pytest tests/unit/test_reflection_scheduler.py::test_every_yaml_entry_has_group -q` | exit code 0 |
| No bundle modules left | `test ! -f reflections/maintenance.py && test ! -f reflections/auditing.py && test ! -f reflections/memory_management.py && test ! -f reflections/task_management.py && test ! -f reflections/utils.py && test ! -f reflections/session_intelligence.py && test ! -f reflections/daily_report.py` (`behavioral_learning.py` already deleted in #1362) | exit code 0 |
| `agent/sustainability.py` deleted | `test ! -f agent/sustainability.py` | exit code 0 |
| All reflection files exist | `find reflections -mindepth 2 -maxdepth 2 -name '*.py' ! -name '__init__.py' \| wc -l` | output ≥ 33 |
| Stale comment removed | `grep -n 'ReflectionRunner' config/reflections.yaml` | exit code 1 |
| `REFLECTION_GROUPS` constant gone | `grep -n 'REFLECTION_GROUPS' ui/data/reflections.py` | exit code 1 |
| Dashboard renders 4 groups | `curl -s localhost:8500/ \| grep -c '<details class="reflection-group"'` | output ≥ 4 |
| Shims preserved | `test -f scripts/popoto_index_cleanup.py && test -f scripts/memory_consolidation.py` | exit code 0 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->
| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|

---

## Open Questions

All 4 open questions resolved by Tom on 2026-05-04:

1. **Agent-type reflections — do they get files?** ✅ **Resolved: yes — separate files (option A).** Plan reflects this throughout: agent-type reflections (`system-health-digest`, `sentry-issue-triage`, plus any others) get placeholder files whose docstring documents purpose/cadence/failure modes and explicitly points at the YAML `command:` as the executable source-of-truth.

2. **`scripts/memory_consolidation.py` and `scripts/popoto_index_cleanup.py` — move or wrap?** ✅ **Resolved: keep a thin shim.** Both scripts retained at their current paths as thin import-and-delegate wrappers. Reflection logic moves into `reflections/memory/memory_dedup.py` and `reflections/housekeeping/redis_index_cleanup.py` respectively.

3. **`agent/sustainability.py` file — fully delete, or leave a stub?** ✅ **Resolved: delete after refactored out.** All 4 reflection callables relocate; non-reflection helpers (`send_hibernation_notification`, `sustainability_digest`) move to `agent/notifications.py` if they have surviving callers (re-grep during build), then the file is deleted. No stub.

4. **Group labels for display** ✅ **Resolved: use the existing dashboard group labels.** The dashboard already defines 4 groups in `ui/data/reflections.py`: `agents`, `housekeeping`, `audits`, `memory`. The plan adopts these as the directory layout (`reflections/agents/`, `reflections/housekeeping/`, `reflections/audits/`, `reflections/memory/`) and as the `group:` field values in YAML. The `REFLECTION_GROUPS` Python constants are deleted; YAML becomes the single source of truth. This collapses the previously proposed 7 groups (Core, Self-Healing, Maintenance, Auditing, Memory, Tasks, Pipelines) into the 4 already-shipped dashboard groups.
