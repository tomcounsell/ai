---
status: Planning
type: chore
appetite: Large
owner: Valor Engels
created: 2026-04-15
tracking: https://github.com/tomcounsell/ai/issues/748
last_comment_id: ""
revision_applied: true
---

# Reflections Monolith Deletion

## Problem

The reflections system is split across two execution paths: a modern declarative YAML scheduler
(`agent/reflection_scheduler.py`) that runs 13 reflections inside the worker process, and a
legacy 3086-line monolith (`scripts/reflections.py`) that runs 18 units once daily via
`com.valor.reflections` launchd. Both are live simultaneously — creating duplicated effort,
divergent state models, and a maintenance surface nobody can reason about holistically.

**Current behavior:**
- `com.valor.reflections` launchd fires `scripts/reflections.py` daily at 6 AM.
- 18 units run sequentially in one class (`ReflectionRunner`), persisting state via `ReflectionRun` Redis model.
- The YAML scheduler (`agent/reflection_scheduler.py`) independently runs 13 reflections inside the worker process with separate per-reflection `Reflection` state records.
- Some units overlap in name (e.g., `popoto-index-cleanup` appears in both paths).
- `ReflectionRun` model (`models/reflection_run.py`) exists only to serve the monolith's resumability; it is dead weight once the monolith is gone.
- 4 planned memory-management reflections (`memory-decay-prune`, `memory-quality-audit`, `knowledge-reindex`) have no implementation. `memory-dedup` was wired by PR #959.
- `config/reflections.yaml` lives in-repo. Moving it to the vault (like `projects.json`) would let private per-machine customizations (intervals, enabled flags) survive repo updates.

**Desired outcome:**
- One execution path: all recurring work is declared in `reflections.yaml` and executed by the YAML scheduler inside the worker.
- `scripts/reflections.py`, `scripts/install_reflections.sh`, and `com.valor.reflections.plist` deleted.
- `models/reflection_run.py` removed; `models/reflections.py` shim updated to export only `ReflectionIgnore` and `PRReviewAudit`.
- `config/reflections.yaml` moved to `~/Desktop/Valor/reflections.yaml` with env-var → vault → in-repo fallback.
- Three new memory-management reflections implemented and registered.
- Dashboard, tests, and docs fully updated.

## Freshness Check

**Baseline commit:** `190884f9b2f1b79ab9f937f104b447ddf7088291`
**Issue filed at:** 2026-04-06T10:08:24Z
**Disposition:** Minor drift

**File:line references re-verified:**
- `scripts/reflections.py` — 126200 bytes, 3086 lines, confirmed present. Issue claimed "126200 bytes / ~3100 lines". Holds (3086 ≈ 3100).
- `agent/reflection_scheduler.py` — 20514 bytes, confirmed. Issue claim holds.
- `models/reflection.py` — confirmed present with `ran_at`, `run_count`, `last_status`, `last_error`, `last_duration`, `run_history` fields.
- `models/reflection_run.py`, `models/reflection_ignore.py`, `models/pr_review_audit.py` — all confirmed present. Shim at `models/reflections.py` is 16 lines (issue said 17 — close enough).
- `config/reflections.yaml` — now has **13 entries** (issue said 12). `memory-dedup` was added by PR #959 after the issue was filed.

**Cited sibling issues/PRs re-checked:**
- #795 — CLOSED 2026-04-14 by PR #959. `memory-dedup` callable (`scripts.memory_consolidation.run_consolidation`) is now registered in `config/reflections.yaml`. This removes one item from "still to do".
- #728 (knowledge-reindex) — still OPEN.

**Commits on main since issue was filed (touching referenced files):**
- `b6481171` feat(memory): LLM-based semantic memory consolidation (memory-dedup reflection) (#959) — added `memory-dedup` to `config/reflections.yaml`. Removes the `memory-dedup` wiring task from scope; `memory-decay-prune`, `memory-quality-audit`, `knowledge-reindex` remain unimplemented.
- `f10a884d` fix: resolve multiple issues found during email bridge setup session — touched `scripts/reflections.py` (LinkedIn step removal). Irrelevant to scope.
- `195df5d0` refactor: reflections quality pass — scheduler, model split, field conventions (#933) — this is the bulk of prior Phase 3 work, already reflected in issue recon.

**Active plans in `docs/plans/` overlapping this area:**
- `docs/plans/reflections-quality-pass.md` — marked status `In Progress` or `Complete` (PR #933 merged). Does not conflict.
- Memory consolidation work (#795, PR #959) — now closed and merged. No conflict.

**Notes:** `memory-dedup` is already wired; the plan's Phase B scope is now 3 reflections, not 4. `docs_auditor.py` uses `ReflectionRun` as a singleton metadata store for `last_audit_date` — this must be migrated to a different storage key before `ReflectionRun` can be deleted.

## Prior Art

- **#361** — "Reflections as first-class objects" — shipped `Reflection` model + YAML scheduler. This is the foundation the current work builds on.
- **#538** — Reflection scheduler Popoto ListField bug — closed/fixed.
- **#569** — Reflection observability — closed, shipped resource guards.
- **#413** — Reflections Dashboard — closed, shipped.
- **#617/#860** — Popoto orphan cleanup reflection — closed, shipped `popoto-index-cleanup`.
- **#751** — Bridge/worker separation — closed, cleared dual-process conflict.
- **#773/PR #842** — Sustainable self-healing — closed, shipped 4 circuit-gated reflections.
- **#790** — Dashboard reflections redesign — closed.
- **#839** — Worker hibernation — closed, shipped `worker-health-gate`, `session-resume-drip`.
- **#933/PR #195** — Reflections quality pass — closed, shipped model split and scheduler unification.
- **#795/PR #959** — Memory consolidation reflection — closed, shipped `memory-dedup`.

No previous attempt to delete the monolith was made. All prior work assumed coexistence.

## Architectural Impact

- **New package**: `reflections/` — standalone callables, one file per monolith unit (or thematic grouping). Each function is importable by dotted path for the YAML scheduler.
- **Deleted files**: `scripts/reflections.py`, `scripts/install_reflections.sh`, `com.valor.reflections.plist`, `models/reflection_run.py`.
- **Modified files**: `models/reflections.py` shim (remove `ReflectionRun`), `models/__init__.py` (remove `ReflectionRun`), `scripts/docs_auditor.py` (migrate off `ReflectionRun`), `config/reflections.yaml` (add 18 new entries), `agent/reflection_scheduler.py` (add vault fallback path), `scripts/update/env_sync.py` (add `reflections.yaml` sync).
- **Coupling**: Reduces coupling by removing the launchd→monolith→ReflectionRun chain. All reflections now flow through one scheduler.
- **Data ownership**: `ReflectionRun` Redis keys will be orphaned after deletion. Need a one-time cleanup or TTL expiry.
- **Reversibility**: Each phase is independently reversible. The monolith is not deleted until all 18 units are verified running from YAML. Launchd plist removal is the final step.

## Appetite

**Size:** Large

**Team:** Solo dev + code reviewer

**Interactions:**
- PM check-ins: 2-3 (phase completion checkpoints)
- Review rounds: 2 (after Phase A, after Phase C)

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worker running | `./scripts/valor-service.sh worker-status` | Confirm scheduler is live before adding units |
| Redis accessible | `python -c "import redis; redis.Redis().ping()"` | Reflection state persistence |
| `~/Desktop/Valor/` exists | `test -d ~/Desktop/Valor && echo ok` | Vault target for config relocation |

## Solution

### Key Elements

- **`reflections/` package**: 15 standalone async functions + 3 pipeline callables (one per merged pipeline). Each takes no args, reads from Redis/filesystem, returns `dict`. Zero shared state with the monolith class.
- **Pipeline preservation**: `session_intelligence`, `behavioral_learning`, and `daily_report_and_notify` each become a single callable that runs their sub-steps internally — preserving ordering without `depends_on` complexity.
- **YAML entries**: 18 new entries in `config/reflections.yaml` with intervals from the issue's suggested table.
- **Vault config**: `config/reflections.yaml` becomes a symlink to `~/Desktop/Valor/reflections.yaml`. Resolution order: `REFLECTIONS_YAML` env var → `~/Desktop/Valor/reflections.yaml` → `config/reflections.yaml`.
- **`ReflectionRun` removal**: `scripts/docs_auditor.py` migrates its singleton metadata to a plain Redis key. All other `ReflectionRun` usages are in the monolith (deleted) or tests (updated/deleted).
- **Memory reflections**: 3 new callables: `memory-decay-prune` (delete below WF_MIN_THRESHOLD), `memory-quality-audit` (flag zero-access + dismissed), `knowledge-reindex` (re-index work-vault docs).

### Flow

```
Worker startup
  → ReflectionScheduler tick (60s)
  → loads config/reflections.yaml (vault → in-repo fallback)
  → checks each Reflection.next_due
  → enqueues due reflections
  → executes callable (e.g., reflections.session_intelligence.run())
  → updates Reflection state (ran_at, last_status, run_history)
```

### Technical Approach

**Phase A — Extract monolith units**

Create `reflections/` package with one module per thematic group. **All new YAML entries added in Phase A must have `enabled: false`** — they will be enabled in Phase C after the monolith is deleted. This eliminates the transition-window race where both the YAML scheduler and the launchd monolith could execute the same logical unit concurrently and write to the same Redis state.

| Module | Functions | Maps to monolith steps |
|--------|-----------|------------------------|
| `reflections/maintenance.py` | `run_legacy_code_scan`, `run_redis_ttl_cleanup`, `run_redis_data_quality`, `run_branch_plan_cleanup`, `run_disk_space_check`, `run_analytics_rollup` | `step_clean_legacy`, `step_redis_cleanup`, `step_redis_data_quality`, `step_branch_plan_cleanup`, `step_disk_space_check`, `step_analytics_rollup` |
| `reflections/auditing.py` | `run_log_review`, `run_documentation_audit`, `run_skills_audit`, `run_hooks_audit`, `run_feature_docs_audit`, `run_pr_review_audit` | `step_review_logs`, `step_audit_docs`, `step_skills_audit`, `step_hooks_audit`, `step_feature_docs_audit`, `step_pr_review_audit` |
| `reflections/task_management.py` | `run_task_management`, `run_principal_staleness` | `step_clean_tasks`, `step_principal_staleness` |
| `reflections/session_intelligence.py` | `run()` | Pipeline: `step_session_analysis` → `step_llm_reflection` → `step_auto_fix_bugs` |
| `reflections/behavioral_learning.py` | `run()` | Pipeline: `step_episode_cycle_close` → `step_pattern_crystallization` |
| `reflections/daily_report.py` | `run()` | Pipeline: `step_produce_report` → `step_create_github_issue` |
| `reflections/memory_management.py` | `run_memory_decay_prune`, `run_memory_quality_audit`, `run_knowledge_reindex` | New — no monolith equivalent |

Each function:
1. Inlines helper logic currently on `self` (no class state).
2. Reads `ReflectionIgnore` from Redis for ignore-pattern checks (unchanged API).
3. Returns `{"status": "ok"|"error", "findings": [...], "summary": str}`.
4. Has no dependency on `ReflectionRun`.

Shared helpers currently on `ReflectionRunner` that multiple units need (e.g., `load_local_projects`, `has_existing_github_work`, `run_llm_reflection`, `is_ignored`) move to `reflections/utils.py`.

**Phase B — Wire memory reflections**

- `memory-decay-prune`: Scan all `Memory` records. Delete (via `instance.delete()`) any with `importance < WF_MIN_THRESHOLD (0.15)` AND `access_count == 0` AND `created_at > 30 days old`. Cap at 50 deletions per run.
- `memory-quality-audit`: Flag memories with `access_count == 0` after 30 days. Flag chronically dismissed memories (dismissal count > 3). Log findings; do not auto-delete.
- `knowledge-reindex`: Call `tools/knowledge/indexer.py` to re-index `~/src/work-vault/` docs into `KnowledgeDocument` records. Idempotent — existing records with matching hash are skipped.

**Phase C — Delete monolith and relocate config**

1. Confirm all 18 units are in YAML and have `ran_at` records proving execution.
2. `config/reflections.yaml` → `~/Desktop/Valor/reflections.yaml` (copy file, then replace `config/reflections.yaml` with symlink).
3. Update `agent/reflection_scheduler.py` `REGISTRY_PATH` to use resolution order: env var → vault → in-repo.
4. Update `scripts/update/env_sync.py` to add `sync_reflections_yaml()` alongside existing `sync_projects_json()`.
5. Delete `scripts/reflections.py`, `scripts/install_reflections.sh`, `com.valor.reflections.plist`.
6. Migrate `scripts/docs_auditor.py` off `ReflectionRun` → plain Redis key `docs_auditor:last_audit_date`.
7. Delete `models/reflection_run.py`. Remove from `models/reflections.py` shim and `models/__init__.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Each extracted callable must handle `redis.exceptions.ConnectionError` gracefully — log warning, return `{"status": "error", "error": "redis unavailable"}`.
- [ ] Pipeline callables (`session_intelligence`, `behavioral_learning`, `daily_report`) must not swallow sub-step exceptions — let them propagate so the scheduler marks the reflection as `last_status=error`.
- [ ] `memory-decay-prune` must guard against `instance.delete()` failures (already-deleted records).

### Empty/Invalid Input Handling
- [ ] `run_knowledge_reindex` must handle missing `~/src/work-vault/` gracefully (directory may not exist on CI).
- [ ] `run_memory_quality_audit` must handle empty Memory queryset (no memories at all) without error.
- [ ] All new callables must return a valid dict even when called with no Redis data.

### Error State Rendering
- [ ] Scheduler surface: when a reflection callable raises, the scheduler catches and sets `last_status=error`, `last_error=traceback`. Verify this behavior is preserved for new callables.
- [ ] Dashboard must still show `error` status for failed reflections.

## Test Impact

- [ ] `tests/unit/test_reflections.py` — DELETE: tests `scripts.reflections.ReflectionRunner` and `run_llm_reflection` from the monolith. After extraction, the logic moves to `reflections/session_intelligence.py` and `reflections/utils.py`. Rewrite as `tests/unit/test_reflections_session_intelligence.py` targeting the new dotted paths.
- [ ] `tests/unit/test_reflections_preflight.py` — DELETE: patches `scripts.reflections.ReflectionRunner._load_state` and `scripts.reflections.load_local_projects`. Both are gone after monolith deletion. No replacement needed — preflight logic moves inside each callable.
- [ ] `tests/unit/test_reflections_multi_repo.py` — UPDATE: patches `scripts.reflections.load_local_projects` → update import to `reflections.utils.load_local_projects`.
- [ ] `tests/unit/test_reflections_scheduling.py` — likely unaffected (tests the YAML scheduler, not the monolith). Verify imports.
- [ ] `tests/unit/test_reflections_report.py` — VERIFY: test imports from `scripts.reflections_report` (a separate standalone module, not the monolith `scripts.reflections`). No changes expected — `scripts/reflections_report.py` is not deleted by this plan. Confirm imports still resolve after monolith deletion. (Implementation note: line 8 imports `from scripts.reflections_report import create_reflections_issue, ...` — this module is independent and survives intact.)
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: add tests for vault fallback path in `load_registry()`.
- [ ] Add `tests/unit/test_reflections_package.py` — new: smoke tests for each callable in the `reflections/` package (import, call with mocked Redis, assert dict return).

## Rabbit Holes

- **`depends_on` in YAML**: Do not add a `depends_on` field to the YAML schema to express pipeline ordering. Keep each pipeline (`session_intelligence`, `behavioral_learning`, `daily_report`) as a single callable that internally sequences its sub-steps. Introducing DAG scheduling is a separate effort.
- **Migrating `ReflectionRun` data**: Do not write a data migration script for existing `ReflectionRun` records. They are day-keyed (date strings). After 30 days they expire via TTL (the monolith calls `cleanup_expired(max_age_days=30)`). Just let them die.
- **Per-unit rate limiting**: Do not add per-unit Redis rate-limiting logic in Phase A. The YAML scheduler's `interval` field already prevents over-triggering.
- **`daily-report-and-notify` ordering guarantee**: The "must run after all other daily reflections" constraint from the issue (#748 acceptance criterion) cannot be mechanically enforced by the current scheduler (no DAG). Accept this limitation. The `daily-report-and-notify` callable simply aggregates whatever findings exist in Redis at run time — it does not need other units to have finished first. The Telegram notification at the end of that unit is the best-effort daily summary. **A follow-up GitHub issue must be created after this plan ships** to track DAG scheduling support so the unfulfilled AC is not silently dropped.
- **`behavioral_learning` / `models.cyclic_episode`**: The step currently guards against `ImportError` on `models.cyclic_episode`. Keep this guard in the extracted callable. Do not implement `CyclicEpisode` as part of this plan.

## Risks

### Risk 1: Shared helper extraction breaks a unit
**Impact:** An extracted callable fails silently at runtime because a helper was inlined incorrectly.
**Mitigation:** Extract unit-by-unit. Add a smoke test for each callable that patches Redis and asserts a valid dict is returned. Run the test suite after each unit before committing.

### Risk 2: `docs_auditor.py` loses its `last_audit_date` state
**Impact:** `DocAuditor` re-audits all docs on every run instead of skipping recently audited ones.
**Mitigation:** Before deleting `ReflectionRun`, migrate `_load_state` / `_record_audit_date` to a plain Redis key (`docs_auditor:last_audit_date`) using the project's standard Redis connection (`redis.Redis.from_url(settings.REDIS_URL)` from `config/settings.py`). The key is global (not per-project) — `docs_auditor.py` audits a single codebase and `ReflectionRun` was never project-scoped for this usage. Write a targeted test to confirm state persists across calls.

### Risk 3: Config relocation breaks fresh-clone setup
**Impact:** A fresh machine without `~/Desktop/Valor/reflections.yaml` fails to start the scheduler.
**Mitigation:** The resolution order ends with `config/reflections.yaml` (in-repo fallback). Keep a full valid YAML in-repo so fresh clones work without the vault. The vault symlink is created by the update script and is optional.

### Risk 4: Launchd plist still installed on live machines
**Impact:** After repo deletion of the plist, existing machines still have `com.valor.reflections` running and triggering the now-deleted script → launchd errors.
**Mitigation:** The `/update` skill must include an `unload_reflections_launchd()` step that sources `.env` to read `SERVICE_LABEL_PREFIX`, derives the label dynamically (`${SERVICE_LABEL_PREFIX:-com.valor}.reflections`), and unloads `~/Library/LaunchAgents/${LABEL}.plist` before deleting it. A hardcoded path will silently miss machines with a non-default prefix. Add this to `scripts/update/` and call it from `remote-update.sh`.

### Risk 5: `memory-decay-prune` deletes important memories
**Impact:** Memories with low importance but meaningful content are permanently deleted.
**Mitigation:** Cap at 50 deletions per run. First two weeks in `dry_run=True` mode (log proposed deletions, do not execute). Respect `importance >= 7.0` exemption (same rule as `memory-dedup`).

## Race Conditions

### Race 1: YAML scheduler fires a unit while the monolith is mid-run
**Location:** `agent/reflection_scheduler.py` + `com.valor.reflections` launchd
**Trigger:** During the transition period (Phase A complete, Phase C not yet started), both paths may run `popoto-index-cleanup` or `analytics-rollup` concurrently.
**Mitigation:** All new YAML entries added in Phase A **must use `enabled: false`** until the monolith is deleted in Phase C. This prevents any concurrent execution — the YAML scheduler won't execute disabled entries regardless of name differences. Enable entries in Phase C only after the monolith is confirmed deleted. The naming distinction (monolith uses `_`, YAML uses `-`) provides an additional guard but is not sufficient on its own because the distinct names bypass `is_reflection_running()` — meaning both could execute concurrently if both are enabled.

### Race 2: Config symlink creation and scheduler startup race
**Location:** `agent/reflection_scheduler.py:REGISTRY_PATH` vs `scripts/update/env_sync.py`
**Trigger:** Update script creates symlink after worker has already started and loaded the in-repo YAML.
**Mitigation:** Not a real issue — the scheduler reloads the YAML on each tick via `load_registry()`. The symlink only needs to exist by the next tick (60 seconds).

## No-Gos (Out of Scope)

- Implementing `CyclicEpisode` model or `behavioral_learning` sub-steps for real (guard remains).
- Adding DAG (`depends_on`) support to the YAML scheduler schema.
- Rewriting the `pr_review_audit` unit — it is the largest (340+ lines) and most complex. Extract as-is; refactor separately.
- Any changes to `ReflectionIgnore` or `PRReviewAudit` models.
- Implementing the `knowledge-reindex` reflection against a live `KnowledgeDocument` model if `#728` (knowledge wiki) is not yet merged. Use a stub that returns `{"status": "skipped", "reason": "KnowledgeDocument not available"}` until #728 ships.
- Moving the reflection scheduler tick interval (60s) or any other scheduler config into YAML.

## Update System

The `/update` skill and `scripts/remote-update.sh` need two additions:

1. **`sync_reflections_yaml()`** in `scripts/update/env_sync.py`: follows the same pattern as `sync_projects_json()` — checks for `~/Desktop/Valor/reflections.yaml`, creates `config/reflections.yaml` symlink if vault file exists, skips gracefully if not (in-repo fallback takes over).

2. **`unload_reflections_launchd()`** in `scripts/update/` or inline in `remote-update.sh`: must read `SERVICE_LABEL_PREFIX` from `.env` using the same pattern as `scripts/install_reflections.sh` lines 14-17 (`set -a; source "$PROJECT_DIR/.env"; set +a`), then derive the label dynamically: `LABEL="${SERVICE_LABEL_PREFIX:-com.valor}.reflections"` and unload `~/Library/LaunchAgents/${LABEL}.plist 2>/dev/null`, removing the file if it exists. Using a hardcoded `com.valor.reflections` path will silently fail on machines where `SERVICE_LABEL_PREFIX` was customized. This is idempotent (safe to run on machines that never installed the launchd service).

Both additions are called from `remote-update.sh` in the config sync phase.

## Agent Integration

No changes to MCP servers or `.mcp.json`. The reflections system runs autonomously inside the worker — it is not invoked by the agent via MCP tools.

The `python scripts/reflections.py` CLI command disappears after Phase C. No CLI replacement is needed — the YAML scheduler handles all scheduling. The `python -m tools.doctor` health check already validates the reflection scheduler via the worker health check.

If the agent needs to inspect reflection state, it already uses `curl -s localhost:8500/dashboard.json` which reads from the `Reflection` model. No changes needed there.

## Documentation

- [ ] Update `docs/features/reflections.md` — remove monolith references, update architecture section to show only the YAML scheduler path, add `reflections/` package to Key Files table.
- [ ] Add `reflections/` package entries to `docs/features/README.md` index.
- [ ] Update `CLAUDE.md` quick reference table — remove `python scripts/reflections.py` and `./scripts/install_reflections.sh` commands.
- [ ] Update `docs/deployment.md` if it references `com.valor.reflections` launchd setup.

## Success Criteria

- [ ] All 18 monolith units are callable by dotted path from the `reflections/` package.
- [ ] All 18 units are declared in `~/Desktop/Valor/reflections.yaml` (and the in-repo symlink).
- [ ] `memory-decay-prune`, `memory-quality-audit`, `knowledge-reindex` implemented and registered.
- [ ] `scripts/reflections.py` deleted.
- [ ] `com.valor.reflections.plist` and `scripts/install_reflections.sh` removed.
- [ ] `models/reflection_run.py` removed. `models/reflections.py` shim exports only `ReflectionIgnore` and `PRReviewAudit`.
- [ ] `scripts/docs_auditor.py` does not import from `models.reflection_run` or `models.reflections.ReflectionRun`.
- [ ] Scheduler resolution order verified: `REFLECTIONS_YAML` env var → `~/Desktop/Valor/reflections.yaml` → `config/reflections.yaml`.
- [ ] `/update` skill provisions `reflections.yaml` symlink and unloads old launchd service.
- [ ] Dashboard shows all registered reflections with status, last run, and run history.
- [ ] Tests pass (`pytest tests/unit/ -x -q`).
- [ ] Lint clean (`python -m ruff check .`).

## Team Orchestration

### Team Members

- **Builder (reflections-package)**
  - Name: package-builder
  - Role: Extract all 18 monolith units into the `reflections/` package; add smoke tests.
  - Agent Type: builder
  - Resume: true

- **Builder (memory-reflections)**
  - Name: memory-builder
  - Role: Implement `memory-decay-prune`, `memory-quality-audit`, `knowledge-reindex` callables and register them in YAML.
  - Agent Type: builder
  - Resume: true

- **Validator (package)**
  - Name: package-validator
  - Role: Verify each extracted callable imports cleanly, returns valid dict with mocked Redis, and existing scheduler tests still pass.
  - Agent Type: validator
  - Resume: true

- **Builder (monolith-deletion)**
  - Name: deletion-builder
  - Role: Delete monolith and launchd plist, migrate `docs_auditor.py`, remove `ReflectionRun`, relocate YAML config, update update script.
  - Agent Type: builder
  - Resume: true

- **Validator (deletion)**
  - Name: deletion-validator
  - Role: Verify no remaining imports of `scripts.reflections`, `models.reflection_run`, or `ReflectionRun`. Run full test suite.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: doc-writer
  - Role: Update `docs/features/reflections.md`, `CLAUDE.md`, `docs/features/README.md`.
  - Agent Type: documentarian
  - Resume: true

### Available Agent Types

Tier 1 — builder, validator, documentarian.

## Step by Step Tasks

### 1. Extract maintenance units
- **Task ID**: build-maintenance-units
- **Depends On**: none
- **Validates**: `tests/unit/test_reflections_package.py` (create)
- **Assigned To**: package-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `reflections/__init__.py`.
- Create `reflections/utils.py` with `load_local_projects`, `has_existing_github_work`, `is_ignored`, `run_llm_reflection` extracted from monolith helpers.
- Create `reflections/maintenance.py` with `run_legacy_code_scan`, `run_redis_ttl_cleanup`, `run_redis_data_quality`, `run_branch_plan_cleanup`, `run_disk_space_check`, `run_analytics_rollup`.
- Add YAML entries for all 6 units with intervals from the plan's solution table. **Set `enabled: false` on all new entries** — they will be enabled in Phase C after monolith deletion.
- Add smoke tests.

### 2. Extract auditing units
- **Task ID**: build-auditing-units
- **Depends On**: build-maintenance-units (needs `reflections/utils.py`)
- **Validates**: `tests/unit/test_reflections_package.py` (update)
- **Assigned To**: package-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/auditing.py` with `run_log_review`, `run_documentation_audit`, `run_skills_audit`, `run_hooks_audit`, `run_feature_docs_audit`, `run_pr_review_audit`.
- Add YAML entries for all 6 units. **Set `enabled: false` on all new entries.**
- Add smoke tests.

### 3. Extract task management units
- **Task ID**: build-task-mgmt-units
- **Depends On**: build-maintenance-units
- **Validates**: `tests/unit/test_reflections_package.py` (update)
- **Assigned To**: package-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `reflections/task_management.py` with `run_task_management`, `run_principal_staleness`.
- Add YAML entries. **Set `enabled: false` on all new entries.**
- Add smoke tests.

### 4. Extract pipeline units
- **Task ID**: build-pipeline-units
- **Depends On**: build-auditing-units, build-task-mgmt-units
- **Validates**: `tests/unit/test_reflections_package.py` (update)
- **Assigned To**: package-builder
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/session_intelligence.py` with `run()` calling `step_session_analysis`, `step_llm_reflection`, `step_auto_fix_bugs` logic (inlined from monolith, using `reflections/utils.py` helpers).
- Create `reflections/behavioral_learning.py` with `run()` and `ImportError` guard for `models.cyclic_episode`.
- Create `reflections/daily_report.py` with `run()`.
- Add YAML entries for all 3 pipelines. **Set `enabled: false` on all new entries.**
- Add smoke tests (with appropriate mocking for Anthropic API calls in session_intelligence).

### 5. Implement memory-management reflections
- **Task ID**: build-memory-reflections
- **Depends On**: none
- **Validates**: `tests/unit/test_reflections_memory.py` (create)
- **Assigned To**: memory-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `reflections/memory_management.py` with `run_memory_decay_prune` (dry_run default, cap 50), `run_memory_quality_audit` (flag zero-access + dismissed), `run_knowledge_reindex` (stub if `KnowledgeDocument` unavailable).
- Add 3 YAML entries.
- Write unit tests covering dry_run mode, cap enforcement, empty queryset handling, graceful stub when `KnowledgeDocument` missing.

### 6. Validate package completeness
- **Task ID**: validate-package
- **Depends On**: build-pipeline-units, build-memory-reflections
- **Assigned To**: package-validator
- **Agent Type**: validator
- **Parallel**: false
- Verify all 18 monolith step keys have a corresponding YAML entry with a callable that imports cleanly.
- Run `pytest tests/unit/test_reflections_package.py tests/unit/test_reflections_memory.py tests/unit/test_reflection_scheduler.py -x -q`.
- Confirm YAML has exactly 18 new entries beyond the original 13.
- Report pass/fail.

### 7. Delete monolith and relocate config
- **Task ID**: build-deletion
- **Depends On**: validate-package
- **Assigned To**: deletion-builder
- **Agent Type**: builder
- **Parallel**: false
- Migrate `scripts/docs_auditor.py` `_load_state` / `_record_audit_date` to plain Redis key `docs_auditor:last_audit_date` using `redis.Redis.from_url(settings.REDIS_URL)` from `config/settings.py`. Key is global (not per-project).
- Delete `scripts/reflections.py`, `scripts/install_reflections.sh`, `com.valor.reflections.plist`.
- Delete `models/reflection_run.py`. Update `models/reflections.py` shim and `models/__init__.py`.
- **Enable all previously-disabled YAML entries** (set `enabled: true` for all entries added in Phase A tasks 1-4). The monolith is now gone — the race condition no longer exists.
- Update `agent/reflection_scheduler.py` `load_registry()` to: check `os.environ.get("REFLECTIONS_YAML")` → `~/Desktop/Valor/reflections.yaml` → `config/reflections.yaml`.
- Add `sync_reflections_yaml()` to `scripts/update/env_sync.py`.
- Add `unload_reflections_launchd()` call to `scripts/remote-update.sh` (must source `.env` to read `SERVICE_LABEL_PREFIX`, derive label dynamically as `${SERVICE_LABEL_PREFIX:-com.valor}.reflections`).
- Copy `config/reflections.yaml` to `~/Desktop/Valor/reflections.yaml`, then replace `config/reflections.yaml` with a symlink.

### 8. Validate deletion
- **Task ID**: validate-deletion
- **Depends On**: build-deletion
- **Assigned To**: deletion-validator
- **Agent Type**: validator
- **Parallel**: false
- `grep -r "scripts.reflections\|models.reflection_run\|ReflectionRun" --include="*.py" . | grep -v __pycache__ | grep -v ".worktrees"` — expect zero results.
- `pytest tests/unit/ -x -q` — all pass.
- `python -m ruff check .` — lint clean.
- Confirm `config/reflections.yaml` is a symlink pointing to `~/Desktop/Valor/reflections.yaml`.
- Report pass/fail.

### 9. Documentation
- **Task ID**: document-feature
- **Depends On**: validate-deletion
- **Assigned To**: doc-writer
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md`: remove monolith/launchd section, update architecture, add `reflections/` package to Key Files.
- Update `CLAUDE.md` quick reference: remove `python scripts/reflections.py` and `./scripts/install_reflections.sh`.
- Update `docs/features/README.md` with `reflections/` package entries.
- Check `docs/deployment.md` for `com.valor.reflections` references.

### 10. Final validation
- **Task ID**: validate-all
- **Depends On**: document-feature
- **Assigned To**: deletion-validator
- **Agent Type**: validator
- **Parallel**: false
- Run full suite: `pytest tests/unit/ tests/integration/ -x -q`.
- Lint: `python -m ruff check .`.
- Format: `python -m ruff format --check .`.
- Verify dashboard shows all reflections.
- Generate final report confirming all success criteria met.

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/unit/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No monolith imports | `grep -r "scripts.reflections" --include="*.py" . \| grep -v __pycache__ \| grep -v .worktrees` | exit code 1 |
| No ReflectionRun imports | `grep -r "reflection_run\|ReflectionRun" --include="*.py" . \| grep -v __pycache__ \| grep -v .worktrees` | exit code 1 |
| Monolith deleted | `test ! -f scripts/reflections.py` | exit code 0 |
| Plist deleted | `test ! -f com.valor.reflections.plist` | exit code 0 |
| YAML is symlink | `test -L config/reflections.yaml` | exit code 0 |

## Critique Results

| Severity | Critic | Finding | Addressed By | Implementation Note |
|----------|--------|---------|--------------|---------------------|
| CONCERN | Skeptic | `test_reflections_report.py` disposition is wrong — imports from `scripts.reflections_report` (standalone module, not monolith) | Test Impact entry updated to VERIFY | `scripts/reflections_report.py` is not deleted; no migration needed |
| CONCERN | Operator | `unload_reflections_launchd()` must read `SERVICE_LABEL_PREFIX` from `.env` dynamically | Risk 4 and Update System updated | Pattern from `install_reflections.sh` lines 14-17: `LABEL="${SERVICE_LABEL_PREFIX:-com.valor}.reflections"` |
| CONCERN | Adversary | `docs_auditor.py` Redis migration leaves connection approach unspecified | Risk 2 and Task 7 updated | Use `redis.Redis.from_url(settings.REDIS_URL)`; key is global (not per-project) |
| CONCERN | Adversary, Operator | Transition window race: distinct names bypass `is_reflection_running()` guard | Phase A, Race 1, Tasks 1-4, Task 7 updated | New YAML entries use `enabled: false` in Phase A; enabled in Phase C after monolith deleted |
| NIT | Skeptic | Reference to non-existent `docs/plans/memory-consolidation-reflection.md` | Freshness Check section corrected | Referenced as closed issue/PR instead |
| NIT | Operator | Unfulfilled AC (daily-report ordering) not tracked after plan ships | Rabbit Holes note updated | Follow-up GitHub issue to be created after ship |

---

## Open Questions

1. **`knowledge-reindex` stub scope**: Should the stub callable (`{"status": "skipped", "reason": "KnowledgeDocument not available"}`) be registered in YAML with `enabled: false` until #728 merges, or enabled with the stub active? Recommendation: `enabled: true` with the stub — this way it appears in the dashboard and the scheduler tracks it. Confirm this preference before Phase B build.

2. **`memory-decay-prune` threshold**: The plan uses `WF_MIN_THRESHOLD (0.15)` as the importance floor, matching the existing `WriteFilterMixin`. Is this threshold still current? If `config/memory_defaults.py` has been updated since this was set, use the latest value.
