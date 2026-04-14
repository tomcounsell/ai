---
status: Ready
type: chore
appetite: Large
owner: valorengels
created: 2026-04-14
tracking: https://github.com/tomcounsell/ai/issues/748
last_comment_id: ""
---

# Reflections Monolith Deletion

## Problem

The reflections system is partially unified. `agent/reflection_scheduler.py` runs 12 reflections inside the worker process on independent intervals, but a 126KB / ~3100-line monolith at `scripts/reflections.py` still runs 18 legacy maintenance units sequentially at 6 AM daily via `com.valor.reflections.plist`. The two systems write to different Redis models (`Reflection` vs `ReflectionRun`), share no state, and the dashboard only sees the yaml-scheduled half.

**Current behavior:**
- Launchd fires the monolith daily at 6 AM. All 18 units run back-to-back on one schedule even though `disk_space_check` should run every 6 hours and `principal_staleness` only needs weekly.
- The 18 units are invisible on the dashboard. Operators SSH into machines and tail logs to see what ran.
- `config/reflections.yaml` lives in-repo, so per-machine customization would fork the config file.
- Three memory-management reflections proposed in #748 (`memory-decay-prune`, `memory-quality-audit`, `knowledge-reindex`) don't exist — memory records accumulate without pruning and standalone worker machines never index the work vault. (`memory-dedup` ships separately via #795 / PR #959.)

**Desired outcome:**
- Every recurring maintenance unit is declared in one yaml registry, scheduled independently, and visible on the dashboard.
- The monolith script, its launchd plist, its install script, and the `ReflectionRun` model are deleted in the same PR that extracts the units. Single cutover, no parallel-run.
- `reflections.yaml` lives in the iCloud-synced vault alongside `projects.json` so machines can diverge without forking the repo.
- Three new memory-management reflections keep the Memory and KnowledgeDocument stores healthy on every machine.

## Freshness Check

**Baseline commit:** `248a073d` (2026-04-14)
**Issue filed at:** 2026-04-06T10:08:24Z
**Disposition:** Unchanged — issue body was rewritten 2026-04-14 against current main; recon is fresh.

**File:line references re-verified:**
- `scripts/reflections.py` — 25 `step_*` methods confirmed via grep. Independent units: `step_clean_legacy` (L837), `step_review_logs` (L890), `step_clean_tasks` (L985), `step_audit_docs` (L1057), `step_produce_report` (L1090), `step_create_github_issue` (L1385), `step_skills_audit` (L1439), `step_hooks_audit` (L1483), `step_redis_cleanup` (L1567), `step_redis_data_quality` (L1650), `step_branch_plan_cleanup` (L1789), `step_feature_docs_audit` (L2008), `step_principal_staleness` (L2414), `step_disk_space_check` (L2457), `step_pr_review_audit` (L2496), `step_analytics_rollup` (L2837). Merged pipelines: `step_session_intelligence` (L2853), `step_behavioral_learning` (L2865), `step_daily_report_and_notify` (L2887). `step_popoto_index_cleanup` (L1613) is already extracted and live in yaml.
- `config/reflections.yaml` — 12 reflections confirmed.
- `models/reflection.py` — `run_history` field capped at 200.
- `models/reflections.py` — 17-line shim re-exporting `ReflectionRun`, `ReflectionIgnore`, `PRReviewAudit`.
- `com.valor.reflections.plist` — exists at repo root.
- `~/Desktop/Valor/reflections.yaml` — does NOT exist.

**Cited sibling issues/PRs re-checked:**
- PR #933 (merged 2026-04-13) — model split and scheduler quality pass. Removes original Phase 3 scope.
- PR #842 / #773 (merged 2026-04-09) — 4 sustainability reflections, out of scope.
- PR #844 / #839 (merged 2026-04-09) — 2 hibernation reflections, out of scope.
- PR #959 (open) — `memory-dedup` for #795. Assumed-landed prerequisite; `memory-dedup` is not registered here.

**Active plans in `docs/plans/` overlapping this area:**
- `memory-consolidation-reflection.md` (#795) — ships `memory-dedup` via PR #959. Non-blocking; this plan verifies its yaml entry is present.

## Prior Art

- **PR #933** (merged 2026-04-13) — Reflections quality pass: scheduler refactor, model split, field conventions. Delivered the Reflection model cleanup originally scoped by #748.
- **PR #842 / #773** (merged 2026-04-09) — Circuit-gated queue governance, 4 yaml reflections. Proves the yaml scheduler handles sub-minute intervals (30s, 60s) reliably.
- **PR #844 / #839** (merged 2026-04-09) — Worker hibernation, 2 more yaml reflections. Demonstrates function-type callables scale cleanly under the scheduler.
- **PR #389** (merged 2026-03-13) — Created the `Reflection` model and yaml scheduler. This plan finishes that migration.
- **PR #572** (merged 2026-03-27) — "Reflections Regroup: 19 steps to 14 units with string keys" — the monolith's own internal refactor. Demonstrates step-key stability.
- **PR #959** (open) — Ships `memory-dedup` for #795. Assumed-landed prerequisite.
- **#728** (open) — Agent-maintained knowledge wiki. Related to `knowledge-reindex` but out of scope.

## Data Flow

The flow for every reflection after cutover:

1. **Entry point**: `ReflectionScheduler.tick()` fires every 60 seconds inside the worker process.
2. **Due-check**: Scheduler reads each `Reflection` Redis record, compares `ran_at + interval < now`.
3. **Skip-if-running guard**: If `last_status == "running"` and age < timeout, skip.
4. **Dispatch**: For `execution_type: function`, import the dotted path and `await` it. For `execution_type: agent`, enqueue a PM `AgentSession` with the natural-language command.
5. **Callable**: A module under `reflections/` performs its work — reads filesystem, queries Redis, calls gh/subprocess, etc. Returns `{findings: [...], metrics: {...}}`.
6. **Persist**: Scheduler calls `reflection.mark_completed(duration, error)` which updates `last_status`, `last_duration`, `run_count`, and appends to `run_history` (capped at 200). Findings are embedded in the latest `run_history` entry.
7. **Dashboard**: `ui/data/reflections.py` reads `Reflection.get_all_states()` and renders the 3-column table.
8. **Bug detection**: `reflections/serious_issue_notifier.py` scans recent `run_history` for critical-severity findings and files a GitHub issue via `/do-issue`. General system health is visible on the dashboard; Telegram stays silent unless a reflection surfaces breakage.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 1-2 (mid-build extraction parity sanity-check, one final push)
- Review rounds: 1 (single PR, focused review)

The change is mechanical (extract, adapt, delete) but the diff is large: ~3100 lines removed, ~1500 added. Correctness rides on tests — every extracted unit must have a test that fails before the PR and passes after. No parallel-run safety net.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worker running yaml scheduler | `python -c "from agent.reflection_scheduler import ReflectionScheduler; print('ok')"` | Confirm scheduler module imports |
| iCloud Valor vault present | `test -d ~/Desktop/Valor && echo ok` | Config relocation target |
| `gh` CLI authenticated | `gh auth status` | Needed by `pr-review-audit` and `task-management` reflections |
| `load_local_projects` available | `python -c "from tools.projects import load_local_projects; load_local_projects()"` | Multi-project iteration |
| PR #959 merged | `grep '^  - name: memory-dedup' config/reflections.yaml` | Confirms memory-dedup already registered |

## Solution

### Key Elements

- **`reflections/` package at repo root**: One module per extracted unit. Modules export `async def run() -> dict`. Sits alongside `bridge/`, `worker/`, `agent/`, `docs/` — the main Valor subsystems.
- **`reflections/_helpers.py`**: Shared utilities extracted from `ReflectionRunner` — `load_local_projects`, `utc_now`, findings-builder.
- **Yaml registry entries**: Each module gets a `config/reflections.yaml` entry with `callable: "reflections.legacy_code_scan.run"`.
- **Three new memory reflections**: `memory_decay_prune`, `memory_quality_audit`, `knowledge_reindex`.
- **`serious_issue_notifier` reflection**: Replaces the monolith's `daily-report-and-notify` notification path. Scans recent findings; files a GitHub issue via `/do-issue` only when something is broken. No daily Telegram digest — the dashboard is the health view.
- **Config resolver**: `agent/reflection_scheduler.py` gains `_resolve_config_path()` mirroring the `projects.json` pattern (env var → `~/Desktop/Valor/reflections.yaml` → `config/reflections.yaml` fallback).
- **Deletions in the same PR**: `scripts/reflections.py`, `scripts/install_reflections.sh`, `com.valor.reflections.plist`, `models/reflection_run.py`, `tests/unit/test_reflection_run.py` if present, and the `ReflectionRun` entry in `models/reflections.py`.

### Flow

Operator POV: dashboard grows from 12 rows to 30+ rows, each with independent status/timing/history. No Telegram noise unless a reflection finds a real problem (files an issue). No 6 AM monolith run.

Developer POV: "add a reflection" = write `reflections/my_thing.py` with `async def run()`, add a yaml entry. No class, no checkpointing, no launchd.

### Technical Approach

- **Extract verbatim.** Copy each `step_*` method from the monolith, strip `self`, replace `self.state.add_finding(category, text)` with a local `findings` list, return `{findings, metrics}`. Preserve behavior — refactors come later if warranted.
- **Merged pipelines stay merged.** `session_intelligence`, `behavioral_learning`, `daily_report` become three callables whose bodies sequentially await their sub-steps. No `depends_on` in yaml.
- **`serious_issue_notifier` replaces the notification path.** The new callable reads the last 24h of `Reflection.run_history` across all reflections, filters findings tagged `critical`, and files a GitHub issue per cluster via `/do-issue`. It does NOT send a daily Telegram summary — system health lives on the dashboard; Telegram is reserved for breakage.
- **Findings schema: simple and clean.** Each `Reflection.run_history` entry gains a `findings: [{category, text}]` field (max 20 per run). The dashboard renders them inline in the run history expander. No migration from `ReflectionRun`.
- **Interval strategy.** Most extracted units use `interval: 86400` (daily). Exceptions: `disk_space_check` → 21600 (6 hours), `principal_staleness` → 604800 (weekly), `knowledge_reindex` → 86400 (daily), `memory_quality_audit` → 604800 (weekly), `memory_decay_prune` → 86400 (daily).
- **Config relocation.** Write `~/Desktop/Valor/reflections.yaml` with the current registry contents. Add `_resolve_config_path()` to the scheduler. Keep `config/reflections.yaml` as a fresh-clone fallback; it is not the primary path after this PR.
- **Update provisioning.** `/update` skill and `scripts/remote-update.sh` copy the vault yaml onto each machine and unload/delete the launchd plist (`launchctl unload … && rm -f …`, safe-noop if absent).
- **Single-PR cutover.** Everything lands together: 18 extractions, 3 new memory reflections, `serious_issue_notifier`, config relocation, monolith/plist/`ReflectionRun` deletion.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Grep `except Exception` in the monolith before extraction. Each swallowed exception that survives into an extracted unit must add a `logger.warning(...)` line and a test asserting the log fires when its dependency fails.
- [ ] Each extracted unit's test covers the "dependency raises" path (filesystem missing, Redis unreachable, `gh` non-zero).

### Empty/Invalid Input Handling
- [ ] `load_local_projects()` returning `[]` must not crash any unit. Test with monkeypatched empty list.
- [ ] Merged pipelines must skip gracefully when the first sub-step produces no input (e.g., `session_intelligence` with no sessions in window).
- [ ] `serious_issue_notifier` with zero critical findings produces zero GitHub issues and logs a single info line.

### Error State Rendering
- [ ] A deliberately broken callable produces `last_status == "error"` and a non-empty `last_error` on the `Reflection` record (already enforced by the scheduler, add regression test).
- [ ] Dashboard renders the error state (status dot red, error text in expander) — verify via `curl -s localhost:8500/dashboard.json` on a broken fixture reflection.

## Test Impact

- [ ] `tests/unit/test_reflections.py` — REPLACE: current tests exercise `ReflectionRunner` methods. Rewrite as per-unit tests importing from `reflections.*` and calling `run()` directly.
- [ ] `tests/integration/test_reflections_redis.py` — REPLACE: current tests write to `ReflectionRun`. Rewrite to assert findings land in `Reflection.run_history[-1].findings`.
- [ ] `tests/unit/test_reflections_preflight.py` — UPDATE: preflight iterates yaml-registered callables, not monolith steps.
- [ ] `tests/unit/test_reflections_multi_repo.py` — UPDATE: multi-repo logic moves to `reflections/_helpers.py`.
- [ ] `tests/unit/test_reflection_scheduler.py` — UPDATE: add config-path resolver test (env var → vault → in-repo fallback).
- [ ] `tests/unit/test_reflection_run.py` — DELETE: `ReflectionRun` is removed.
- [ ] `tests/unit/test_reflections_extracted/` — REPLACE (new directory): one test file per extracted reflection validating findings structure and error surfacing.
- [ ] `tests/unit/test_serious_issue_notifier.py` — REPLACE (new file): covers zero-findings noop, single-cluster issue creation, multi-cluster issue creation, deduplication against existing open issues.
- [ ] `tests/unit/test_monolith_removed.py` — REPLACE (new file): asserts `scripts.reflections` import fails and `ReflectionRun` cannot be imported from `models.reflections`.

## Rabbit Holes

- **Decomposing merged pipelines into separate yaml entries with `depends_on`.** The scheduler has no dependency resolver; adding one is a multi-PR project. Pipelines stay as single callables.
- **Rewriting unit logic while extracting.** "While I'm in here" refactors triple the diff. Verbatim copy-paste-plus-adapter. Cleanup is a follow-up PR if warranted.
- **Daily Telegram digest.** Dropped by design. Dashboard shows health; breakage files issues.
- **Findings schema migration.** Not migrating from `ReflectionRun`. Start fresh.
- **Replacing `ReflectionIgnore`.** Still serves its purpose (14-day finding mute). Out of scope.
- **Switching to Prefect/Temporal/APScheduler.** The yaml scheduler works. No external dependency.
- **Removing `behavioral_learning` because `cyclic_episode` is on an unmerged branch.** Its existing graceful-skip pattern handles the missing import. Keep it.

## Risks

### Risk 1: An extracted unit silently produces fewer findings than its monolith predecessor
**Impact:** Coverage regression — real bugs surface less often.
**Mitigation:** Per-unit tests assert at least one expected finding category against a controlled fixture. Code review scrutinizes each extraction diff line-by-line; the extracted body should be near-identical to the monolith step. Correctness rides on the tests.

### Risk 2: `serious_issue_notifier` creates duplicate GitHub issues on repeated runs
**Impact:** Inbox spam, noise for operators.
**Mitigation:** Before filing, `serious_issue_notifier` checks a Redis-backed "recently filed fingerprints" set (TTL 86400), then `gh issue list --search "fingerprint:{hash}"`. Matching fingerprints skip creation. Fingerprint = cluster category + stable substring of the finding text.

### Risk 3: Config relocation leaves a fresh-cloned machine unable to boot the scheduler
**Impact:** Worker fails to start; reflections don't run.
**Mitigation:** `config/reflections.yaml` stays in-repo as final fallback. Resolver logs the resolved path on startup. `/update` provisions the vault yaml during its iCloud-sync step.

### Risk 4: `launchctl unload` fails on machines without the plist
**Impact:** `/update` aborts partway through.
**Mitigation:** Use `launchctl unload 2>/dev/null` and `rm -f` for safe-noop semantics. Existing `/update` skill patterns handle missing-file cases.

### Risk 5: `knowledge_reindex` re-indexes the entire vault on every run
**Impact:** High CPU and disk I/O on standalone worker machines.
**Mitigation:** Callable compares KnowledgeDocument count + newest `indexed_at` against vault filesystem mtimes. Full reindex only runs when mtimes exceed `indexed_at` or count mismatches. Otherwise no-op with a findings line.

## Race Conditions

### Race 1: Yaml reload during scheduler tick
**Location:** `agent/reflection_scheduler.py` — yaml reload path.
**Trigger:** `/update` rewrites `~/Desktop/Valor/reflections.yaml` mid-tick; scheduler reads a truncated file.
**Data prerequisite:** Yaml is fully written before the scheduler reads it.
**State prerequisite:** Atomic replace (`write + rename`), not partial write.
**Mitigation:** `/update` writes via `tempfile.NamedTemporaryFile` + `os.replace()`. Scheduler yaml-parse failures are caught and the prior good config is reused for the current tick.

### Race 2: `serious_issue_notifier` double-files while `gh issue list` lags behind `gh issue create`
**Location:** `reflections/serious_issue_notifier.py` — issue dedup.
**Trigger:** Two consecutive runs both see no existing issue because GitHub's search index hasn't caught up.
**Data prerequisite:** Redis-backed "recently filed fingerprints" set.
**State prerequisite:** Fingerprint present within 24h window skips re-filing.
**Mitigation:** Store filed fingerprints in Redis with TTL=86400. Redis check is local and authoritative; `gh issue list` is belt-and-suspenders.

## No-Gos (Out of Scope)

- Adding new reflections beyond the 18 monolith units and 3 memory reflections (+ `serious_issue_notifier`).
- Rewriting `ReflectionIgnore` or `PRReviewAudit` models.
- Dependency-graph scheduling (`depends_on` in yaml).
- Changing the 60-second scheduler tick interval.
- External scheduler (Prefect, Temporal, etc.).
- `memory-dedup` — ships via PR #959.
- Daily Telegram digest — dropped.
- Dashboard redesign — PR #790 layout absorbs more rows.
- Reflections testing harness beyond per-unit tests.

## Update System

- **Adds:** `/update` skill and `scripts/remote-update.sh` copy `~/Desktop/Valor/reflections.yaml` from iCloud to each machine during provisioning (same pattern as `projects.json`).
- **Removes:** `/update` runs `launchctl unload ~/Library/LaunchAgents/com.valor.reflections.plist 2>/dev/null; rm -f ~/Library/LaunchAgents/com.valor.reflections.plist` on every machine. Safe-noop if absent.
- **New dependency:** None. Everything runs in the existing worker process.
- **Migration for existing installations:** First `/update` after this PR ships unloads the plist, deletes it, copies the vault yaml, and restarts the worker. Idempotent.

## Agent Integration

- **No new MCP tools.** Every extracted unit calls existing Python (Redis, filesystem, `gh`, subprocess).
- **Bridge impact:** None. Bridge does not touch the monolith (worker owns execution per PR #751).
- **`serious_issue_notifier`** is execution_type `agent` — it enqueues a PM session with an `/do-issue` prompt. Uses existing agent session infrastructure; no new integration.
- **Integration test:** Assert the worker boots cleanly, the scheduler logs all registered reflections on boot, and `len(scheduler._registry) > 25`. Extend existing `tests/unit/test_reflection_scheduler.py`.

## Documentation

### Feature Documentation
- [ ] Rewrite `docs/features/reflections.md` — describe the current state only. No "formerly", "migrated from", "legacy", or phase callouts. Registry table lists every registered reflection with its callable, interval, priority, description.
- [ ] Confirm `docs/features/README.md` index entry is accurate.

### External Documentation Site
- Not applicable.

### Inline Documentation
- [ ] Each extracted module gets a one-line docstring.
- [ ] `agent/reflection_scheduler.py` module docstring describes the scheduler as it is today — no reference to a companion monolith.

## Success Criteria

- [ ] All 18 monolith units live as standalone callables under `reflections/` and registered in yaml
- [ ] `memory_decay_prune`, `memory_quality_audit`, `knowledge_reindex` implemented and registered
- [ ] `memory-dedup` registered by PR #959 (verified, not owned by this plan)
- [ ] `serious_issue_notifier` implemented; files GitHub issues for critical findings with Redis-backed dedup
- [ ] No daily Telegram digest — dashboard is the health view
- [ ] `scripts/reflections.py` deleted
- [ ] `com.valor.reflections.plist` deleted from repo and unloaded on every deployed machine
- [ ] `scripts/install_reflections.sh` deleted
- [ ] `models/reflection_run.py` deleted; `models/reflections.py` shim re-exports only `ReflectionIgnore`, `PRReviewAudit`
- [ ] `config/reflections.yaml` remains as in-repo fallback; `~/Desktop/Valor/reflections.yaml` is primary
- [ ] `_resolve_config_path()` in scheduler resolves env var → vault → in-repo fallback
- [ ] `/update` provisions the vault yaml and removes the plist on each machine
- [ ] Dashboard renders all registered reflections with status, last run, run history, and inline findings
- [ ] `docs/features/reflections.md` describes only the current state
- [ ] Tests pass (`/do-test`)

## Team Orchestration

### Team Members

- **Builder (extraction)**
  - Name: reflections-extractor
  - Role: Extract all 18 monolith units into `reflections/` modules, adapt findings accumulator, register in yaml.
  - Agent Type: builder
  - Resume: true

- **Builder (new reflections)**
  - Name: reflections-new-builder
  - Role: Implement `memory_decay_prune`, `memory_quality_audit`, `knowledge_reindex`, `serious_issue_notifier`.
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup)**
  - Name: cleanup-builder
  - Role: Config resolver, `/update` provisioning, monolith deletion, plist removal.
  - Agent Type: builder
  - Resume: true

- **Test Engineer**
  - Name: reflections-tester
  - Role: Per-unit tests plus integration tests for scheduler boot, yaml resolution, findings persistence, issue notifier dedup.
  - Agent Type: test-engineer
  - Resume: true

- **Validator**
  - Name: reflections-validator
  - Role: Verify every acceptance criterion, confirm scheduler boots, dashboard renders, monolith files absent, no stale grep hits.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: reflections-documentarian
  - Role: Rewrite `docs/features/reflections.md` to describe current state only.
  - Agent Type: documentarian
  - Resume: true

## Step by Step Tasks

### 1. Scaffold `reflections/` package and `_helpers.py`
- **Task ID**: build-package-scaffold
- **Depends On**: none
- **Validates**: tests/unit/test_reflections_helpers.py (create)
- **Assigned To**: reflections-extractor
- **Agent Type**: builder
- **Parallel**: false
- Create `reflections/__init__.py`
- Create `reflections/_helpers.py` exporting `load_local_projects`, `utc_now`, `build_finding(category, text, metrics=None)`
- Ensure package imports cleanly from worker and tests

### 2. Extract 15 independent monolith units
- **Task ID**: build-extract-independent-units
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_reflections_extracted/*.py (one per unit)
- **Assigned To**: reflections-extractor
- **Agent Type**: builder
- **Parallel**: true
- Extract each step verbatim into `reflections/<name>.py` with `async def run() -> dict`:
  `legacy_code_scan`, `log_review`, `task_management`, `documentation_audit`, `skills_audit`, `hooks_audit`, `redis_ttl_cleanup`, `redis_data_quality`, `branch_plan_cleanup`, `feature_docs_audit`, `principal_staleness`, `disk_space_check`, `pr_review_audit`, `analytics_rollup`, `create_github_issue`
- Register each in `config/reflections.yaml`

### 3. Extract 3 merged pipelines
- **Task ID**: build-extract-pipelines
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_reflections_pipelines.py (create)
- **Assigned To**: reflections-extractor
- **Agent Type**: builder
- **Parallel**: true
- Extract `session_intelligence` (session_analysis → llm_reflection → auto_fix_bugs), `behavioral_learning` (episode_cycle_close → pattern_crystallization with cyclic_episode import-guard skip), and `daily_report` (produce_report, no notification path — `serious_issue_notifier` replaces the Telegram send)
- Register each in yaml

### 4. Implement `memory_decay_prune`
- **Task ID**: build-memory-decay-prune
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_memory_decay_prune.py (create)
- **Assigned To**: reflections-new-builder
- **Agent Type**: builder
- **Parallel**: true
- `reflections/memory_decay_prune.py` iterates Memory records with importance below `WF_MIN_THRESHOLD` (0.15) and deletes via `Memory.delete()` (NEVER raw redis — per `feedback_never_raw_delete_popoto`)
- Dry-run flag; default apply after fixture-based validation
- Register: interval 86400, priority low

### 5. Implement `memory_quality_audit`
- **Task ID**: build-memory-quality-audit
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_memory_quality_audit.py (create)
- **Assigned To**: reflections-new-builder
- **Agent Type**: builder
- **Parallel**: true
- `reflections/memory_quality_audit.py` flags memories with zero `access_count` after 30 days and chronically dismissed memories
- Emits findings only (no side effects)
- Register: interval 604800 (weekly), priority low

### 6. Implement `knowledge_reindex`
- **Task ID**: build-knowledge-reindex
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_knowledge_reindex.py (create)
- **Assigned To**: reflections-new-builder
- **Agent Type**: builder
- **Parallel**: true
- `reflections/knowledge_reindex.py` runs `tools/knowledge/indexer.py` over `~/src/work-vault/` on standalone worker machines
- Short-circuits if no filesystem mtimes exceed newest `indexed_at`
- Register: interval 86400, priority low

### 7. Implement `serious_issue_notifier`
- **Task ID**: build-serious-issue-notifier
- **Depends On**: build-package-scaffold, build-extract-pipelines, build-extract-independent-units
- **Validates**: tests/unit/test_serious_issue_notifier.py (create)
- **Assigned To**: reflections-new-builder
- **Agent Type**: builder
- **Parallel**: false
- `reflections/serious_issue_notifier.py` scans recent `Reflection.run_history` for findings tagged category `critical`
- Fingerprints clusters; checks Redis dedup set (TTL 86400) before `gh issue list` dedup
- Files GitHub issue via PM session with `/do-issue` prompt; records fingerprint in Redis
- Register: interval 3600 (hourly), priority normal, execution_type agent

### 8. Add config-path resolver and relocate yaml
- **Task ID**: build-config-resolver
- **Depends On**: build-extract-independent-units, build-extract-pipelines, build-memory-decay-prune, build-memory-quality-audit, build-knowledge-reindex, build-serious-issue-notifier
- **Validates**: tests/unit/test_reflection_scheduler.py (UPDATE)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_resolve_config_path()` to `agent/reflection_scheduler.py`: env `REFLECTIONS_YAML_PATH` → `~/Desktop/Valor/reflections.yaml` → `config/reflections.yaml`
- Log resolved path at startup
- Copy current `config/reflections.yaml` to `~/Desktop/Valor/reflections.yaml`; confirm resolver picks it up

### 9. Update `/update` skill + remote-update.sh
- **Task ID**: build-update-provisioning
- **Depends On**: build-config-resolver
- **Validates**: tests/integration/test_update_provisioning.py (update)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- `.claude/skills/update/SKILL.md` and `scripts/remote-update.sh` copy `~/Desktop/Valor/reflections.yaml` during the iCloud-sync step
- Same scripts unload and remove `~/Library/LaunchAgents/com.valor.reflections.plist` (safe-noop)

### 10. Delete monolith and supporting files
- **Task ID**: build-delete-monolith
- **Depends On**: build-update-provisioning
- **Validates**: tests/unit/test_monolith_removed.py (create)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `scripts/reflections.py`
- Delete `scripts/install_reflections.sh`
- Delete `com.valor.reflections.plist`
- Delete `models/reflection_run.py`
- Delete `tests/unit/test_reflection_run.py` if present
- Update `models/reflections.py` shim: re-export only `ReflectionIgnore`, `PRReviewAudit`
- Run full test suite; fix import errors

### 11. Rewrite feature documentation
- **Task ID**: document-reflections
- **Depends On**: build-delete-monolith
- **Validates**: manual read-through
- **Assigned To**: reflections-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Rewrite `docs/features/reflections.md` describing ONLY the current state
- No "formerly", "migrated from", "legacy", "was previously", or phase callouts
- Registry table lists every active reflection
- Confirm `docs/features/README.md` index entry is accurate

### 12. Final validation
- **Task ID**: validate-all
- **Depends On**: document-reflections
- **Assigned To**: reflections-validator
- **Agent Type**: validator
- **Parallel**: false
- Run every check in the Verification table
- Confirm all Success Criteria met
- Generate final report

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Monolith gone | `test -f scripts/reflections.py` | exit code 1 |
| Plist gone | `test -f com.valor.reflections.plist` | exit code 1 |
| Install script gone | `test -f scripts/install_reflections.sh` | exit code 1 |
| ReflectionRun gone | `python -c "from models.reflection_run import ReflectionRun" 2>&1` | output contains "ModuleNotFoundError" |
| Shim cleaned | `python -c "from models.reflections import ReflectionRun" 2>&1` | output contains "ImportError" |
| Scheduler boots | `python -c "from agent.reflection_scheduler import ReflectionScheduler; s = ReflectionScheduler(); print(len(s._registry))"` | output > 25 |
| Vault yaml resolves | `test -f ~/Desktop/Valor/reflections.yaml` | exit code 0 |
| Dashboard lists units | `curl -s localhost:8500/dashboard.json \| python -c "import sys, json; print(len(json.load(sys.stdin)['reflections']))"` | output > 25 |
| No stale grep hits | `grep -rn 'scripts.reflections\|ReflectionRun' --include='*.py' . \| grep -v 'test_monolith_removed\|.worktrees'` | exit code 1 |
| memory-dedup present | `grep '^  - name: memory-dedup' ~/Desktop/Valor/reflections.yaml config/reflections.yaml` | exit code 0 |
| No daily Telegram digest | `grep -rn 'daily.*telegram\|telegram.*digest' reflections/ \| grep -v serious_issue_notifier` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique runs. -->
