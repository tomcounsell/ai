---
status: Planning
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
- `config/reflections.yaml` lives in-repo, so any per-machine customization forks the config file.
- Four memory-management reflections proposed in #748 (`memory-dedup`, `memory-decay-prune`, `memory-quality-audit`, `knowledge-reindex`) don't exist — memory records accumulate without pruning and standalone worker machines never index the work vault.

**Desired outcome:**
- Every recurring maintenance unit is declared in one yaml registry, scheduled independently, and visible on the dashboard.
- The monolith script, its launchd plist, its install script, and the `ReflectionRun` model are deleted.
- `reflections.yaml` lives in the iCloud-synced vault alongside `projects.json` so machines can diverge without forking the repo.
- Four memory-management reflections keep the Memory and KnowledgeDocument stores healthy on every machine.

## Freshness Check

**Baseline commit:** `bb7dd3c4` (2026-04-14)
**Issue filed at:** 2026-04-06T10:08:24Z
**Disposition:** Unchanged — issue body was rewritten 2026-04-14 against current main, so recon is fresh.

**File:line references re-verified:**
- `scripts/reflections.py` — 18 unit methods confirmed via grep: `step_clean_legacy` (L837), `step_review_logs` (L890), `step_clean_tasks` (L985), `step_audit_docs` (L1057), `step_produce_report` (L1090), `step_session_analysis` (L1195), `step_llm_reflection` (L1235), `step_auto_fix_bugs` (L1255), `step_create_github_issue` (L1385), `step_skills_audit` (L1439), `step_hooks_audit` (L1483), `step_redis_cleanup` (L1567), `step_popoto_index_cleanup` (L1613), `step_redis_data_quality` (L1650), `step_branch_plan_cleanup` (L1789), `step_feature_docs_audit` (L2008), `step_episode_cycle_close` (L2140), `step_pattern_crystallization` (L2284), `step_principal_staleness` (L2414), `step_disk_space_check` (L2457), `step_pr_review_audit` (L2496), `step_analytics_rollup` (L2837), `step_session_intelligence` (L2853, pipeline), `step_behavioral_learning` (L2865, pipeline), `step_daily_report_and_notify` (L2887, pipeline).
- `config/reflections.yaml` — 12 reflections confirmed by `grep -c '^  - name:'`.
- `models/reflection.py` — `run_history` field capped at 200 confirmed at `_RUN_HISTORY_CAP`.
- `models/reflections.py` — 17-line shim re-exporting `ReflectionRun`, `ReflectionIgnore`, `PRReviewAudit`.
- `com.valor.reflections.plist` — exists at repo root.
- `~/Desktop/Valor/reflections.yaml` — does NOT exist (verified with `ls`).

**Cited sibling issues/PRs re-checked:**
- PR #933 — merged 2026-04-13, delivered model split and scheduler quality pass. Removes need for original Phase 3 work on Reflection model.
- PR #842 (#773) — merged 2026-04-09, added 4 sustainability reflections. Out of scope for this plan.
- PR #844 (#839) — merged 2026-04-09, added 2 hibernation reflections. Out of scope.
- #795 (memory-dedup) — OPEN, `docs/plans/memory-consolidation-reflection.md` at status `docs_complete`. Coordination: this plan registers `memory-dedup` in yaml only if #795 hasn't shipped first.

**Commits on main since issue filed (touching referenced files):** All relevant drift is accounted for in the rewritten issue. PR #933 is the most material — it did the Reflection model cleanup originally scoped here.

**Active plans in `docs/plans/` overlapping this area:**
- `memory-consolidation-reflection.md` (#795) — partial overlap on `memory-dedup`. Not a blocker; coordinate yaml entry ownership.
- `reflections-quality-pass.md` — already shipped (PR #933).
- `reflections-dashboard.md` — already shipped (PR #790).

## Prior Art

- **PR #933** (closed, merged 2026-04-13) — Reflections quality pass: scheduler refactor, model split, field conventions. **Cleared the original Phase 3 scope.** Remaining work is narrower.
- **PR #842** (#773, closed, merged 2026-04-09) — Sustainable self-healing: 4 circuit-gated reflections. **Proves the yaml scheduler handles `interval: 30` and `interval: 60` reliably** — validates the <1min intervals planned here for `disk-space-check` (6 hours = 21600).
- **PR #844** (#839, closed, merged 2026-04-09) — Worker hibernation: 2 more yaml reflections. **Proves the `execution_type: function` pattern scales to mid-single-digit callables per worker tick.**
- **PR #389** (closed, merged 2026-03-13) — "Reflections as first-class objects": created the `Reflection` model and yaml scheduler. This plan finishes that 13-month-old migration.
- **PR #572** (closed, merged 2026-03-27) — "Reflections Regroup: 19 steps to 14 units with string keys": the monolith's own internal refactor. Demonstrates step-key stability; extracted callables should retain the same names.
- **#795** (open) — Memory consolidation reflection (`memory-dedup`). Partial overlap: plan exists, yaml entry missing. Coordinate to avoid double registration.
- **#728** (open) — Agent-maintained knowledge wiki. Related to `knowledge-reindex` but out of scope for this plan.

## Data Flow

The new flow for every extracted unit is identical, which is the point of the unification:

1. **Entry point**: `ReflectionScheduler.tick()` fires every 60 seconds inside the worker process (`worker/__main__.py` startup).
2. **Due-check**: Scheduler reads each `Reflection` Redis record, compares `ran_at + interval < now`.
3. **Skip-if-running guard**: If `last_status == "running"` and age < timeout, skip.
4. **Dispatch**: For `execution_type: function`, import the dotted path and `await` it. For `execution_type: agent`, enqueue a PM `AgentSession` with the natural-language command.
5. **Callable**: The extracted unit (e.g., `reflections.legacy_code_scan.run`) performs its work — reads filesystem, queries Redis, calls gh/subprocess, etc. Returns a result dict `{findings: [...], metrics: {...}}`.
6. **Persist**: Scheduler calls `reflection.mark_completed(duration, error)` which updates `last_status`, `last_duration`, `run_count`, and appends to `run_history` (capped at 200).
7. **Dashboard**: `ui/data/reflections.py` reads `Reflection.get_all_states()` and renders the 3-column table from PR #790.

The monolith's current flow (launchd → `scripts/reflections.py` → sequential steps → `ReflectionRun.step_progress`) disappears entirely at end of Phase C.

## Appetite

**Size:** Large

**Team:** Solo dev, PM

**Interactions:**
- PM check-ins: 2-3 (one per phase boundary — A→B, B→C, C→cleanup)
- Review rounds: 2+ (each phase ships as its own PR; cross-phase verification on the final monolith-deletion PR)

Communication overhead is the bottleneck. Each phase is technically straightforward but the correctness bar is high: extracted units must produce the same findings as the monolith did, or we lose coverage of real bugs.

## Prerequisites

| Requirement | Check Command | Purpose |
|-------------|---------------|---------|
| Worker running yaml scheduler | `python -c "from agent.reflection_scheduler import ReflectionScheduler; print('ok')"` | Confirm scheduler module imports |
| iCloud Valor vault present | `test -d ~/Desktop/Valor && echo ok` | Phase C config relocation target |
| `gh` CLI authenticated | `gh auth status` | Needed by `pr_review_audit` and `task_management` extracted units |
| `load_local_projects` available | `python -c "from tools.projects import load_local_projects; load_local_projects()"` | Multi-project iteration in extracted units |

## Solution

### Key Elements

- **`reflections/` package** (new, at repo root): One module per extracted unit, e.g., `reflections/legacy_code_scan.py` exporting `async def run() -> dict`. Three merged-pipeline modules (`session_intelligence.py`, `behavioral_learning.py`, `daily_report_and_notify.py`) keep their internal sub-step ordering.
- **Yaml registry entries**: Each module gets an entry in `config/reflections.yaml` (later `~/Desktop/Valor/reflections.yaml`) with `callable: "reflections.legacy_code_scan.run"`.
- **Shared helpers module** (`reflections/_helpers.py`): Extracted from the `ReflectionRunner` class — `load_local_projects`, `utc_now`, `add_finding`, `write_step_progress`. Findings now write directly to `Reflection.run_history` via a thin helper since `ReflectionRun` is going away.
- **Memory reflections** (new): `reflections/memory_decay_prune.py`, `reflections/memory_quality_audit.py`, `reflections/knowledge_reindex.py`. `memory-dedup` either lands via #795 or gets registered here as the fallback.
- **Config resolver**: `agent/reflection_scheduler.py` gains a `_resolve_config_path()` helper mirroring the projects.json pattern (env var → `~/Desktop/Valor/reflections.yaml` → `config/reflections.yaml` fallback).

### Flow

Operator POV: no change. Dashboard shows the same 3-column table, now with 30+ rows instead of 12. Every unit has independent `last_run`, `next_due`, `run_history`, status dot. Failed units surface errors via the existing failure-loop-detector pathway.

Developer POV: "add a reflection" is now a 3-step recipe — write `reflections/my_thing.py` with `async def run()`, add a yaml entry, done. No class, no checkpointing, no launchd.

### Technical Approach

- **Extract, don't rewrite.** Copy each `step_*` method from the monolith verbatim, strip the `self` parameter, replace `self.state.add_finding(category, text)` with a local `findings.append((category, text))` accumulator, and return `{findings: findings, metrics: {...}}`. Preserve behavior first; refactor later.
- **Merged pipelines stay merged.** `session_intelligence`, `behavioral_learning`, and `daily_report_and_notify` become three callables whose bodies sequentially await their sub-steps. Do not decompose them into separate yaml entries with `depends_on` — that complicates the scheduler and the current coupling is intentional (e.g., `auto_fix_bugs` reads findings produced by `llm_reflection`).
- **Findings persistence.** The monolith writes findings to `ReflectionRun.findings`. After extraction, findings go into each `Reflection.run_history[-1].findings` as a bounded list (cap at 20 findings per run). The dashboard already renders `run_history` entries; a small template update surfaces the new `findings` key.
- **Interval strategy.** Most extracted units get `interval: 86400` (daily) matching monolith behavior. Exceptions: `disk-space-check` → `21600` (6 hours), `principal-staleness` → `604800` (weekly). All finalized in a single commit once Phase A lands so operators can see the new cadence on the dashboard.
- **Ordering `daily-report-and-notify`.** It currently runs last because the monolith sequences it last. After extraction, the scheduler runs all due reflections in arbitrary order each tick, so `daily-report-and-notify` could fire before its peers. Two options: (1) give it a later `interval_offset` (e.g., run at 7 AM instead of 6 AM), or (2) check `ran_at` of the other daily units inside its callable and skip-if-premature. Option (2) is more robust; flag as open question.
- **Config relocation is purely additive until the delete.** Phase C writes `~/Desktop/Valor/reflections.yaml` and updates `_resolve_config_path()` to prefer it. The in-repo `config/reflections.yaml` remains as a fallback for fresh clones. The `/update` skill copies the vault yaml on each machine.
- **Monolith deletion is the last commit.** Only after all 18 units run green from yaml for 7 consecutive days (validated via `Reflection.run_history`) do we delete `scripts/reflections.py`, `com.valor.reflections.plist`, `scripts/install_reflections.sh`, `models/reflection_run.py`, and the shim entry in `models/reflections.py`.

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] Audit the monolith's `except Exception: pass` patterns (grep `except Exception` in `scripts/reflections.py`). Each swallowed exception in an extracted unit must add a `logger.warning()` or be promoted to a real failure. Test by mocking the failing dependency and asserting the log line appears.
- [ ] Each extracted unit's test must cover the "dependency raises" path — filesystem missing, Redis unreachable, `gh` CLI returns non-zero.

### Empty/Invalid Input Handling
- [ ] `load_local_projects()` returning an empty list must not crash any extracted unit. Test with monkeypatched empty projects list.
- [ ] The three merged-pipeline callables must skip gracefully when their first sub-step produces no input (e.g., `session_intelligence` when there are no sessions in the lookback window).
- [ ] Empty findings lists must not trigger `daily-report-and-notify` to send a blank Telegram message.

### Error State Rendering
- [ ] Scheduler already renders `last_error` in the dashboard via `models/reflection.py:last_error` (truncated to 1000 chars). Verify that after extraction, errors still surface there (not swallowed inside the callable).
- [ ] Add a regression test that a deliberately broken callable results in `last_status == "error"` and a non-empty `last_error` in the `Reflection` record.

## Test Impact

- [ ] `tests/unit/test_reflections.py` — UPDATE: tests currently import from `scripts.reflections` and exercise `ReflectionRunner` methods. Rewrite to import from `reflections.*` modules and call `run()` directly.
- [ ] `tests/integration/test_reflections_redis.py` — UPDATE: currently writes to `ReflectionRun`; rewrite to write to `Reflection.run_history` and assert findings are persisted.
- [ ] `tests/unit/test_reflections_preflight.py` (in worktree; may be on main) — UPDATE: preflight checks need to run against all yaml-registered callables, not monolith steps.
- [ ] `tests/unit/test_reflections_multi_repo.py` (in worktree; may be on main) — UPDATE: multi-repo iteration logic moves from monolith to `reflections/_helpers.py`.
- [ ] `tests/unit/test_reflection_scheduler.py` (exists) — UPDATE: add test for config-path resolution order (env var → vault → in-repo).
- [ ] `tests/unit/test_reflection_run.py` (if exists) — DELETE: `ReflectionRun` is being removed.
- [ ] Add `tests/unit/test_reflections_extracted/` — REPLACE: one test file per extracted unit, each validating findings structure and error surfacing.

## Rabbit Holes

- **Decomposing merged pipelines into separate yaml entries with `depends_on`.** Tempting because "clean." The scheduler has no dependency resolver; adding one is a multi-PR project with its own edge cases (cycles, stale-dependency skip, failure propagation). The three pipelines keep their internal ordering in a single callable. Out of scope.
- **Rewriting monolith logic while extracting.** "While I'm in here" refactors of `step_pr_review_audit` or `step_redis_data_quality` triple the diff size and make regression testing impossible. Each extraction is a verbatim copy-paste-plus-adapter. Cleanup lands in follow-up PRs if warranted.
- **Replacing `ReflectionIgnore` at the same time.** The ignore-pattern model still serves its original purpose (silencing noisy findings for 14 days). Out of scope.
- **Switching to Prefect/Temporal/APScheduler.** The yaml scheduler is 500 lines of Python doing exactly what we need. No external dependency warranted.
- **Moving `behavioral_learning` out of the pipeline because `cyclic_episode` is on an unmerged branch.** The current graceful-skip pattern works; keep it.
- **Building a "findings summarizer" LLM step.** That's `session-intelligence` already. Do not add another layer.

## Risks

### Risk 1: Extracted unit produces different findings than monolith equivalent
**Impact:** Coverage regression — real bugs surface less often, docs audits miss files, dead-import scans drop items.
**Mitigation:** For each phase A PR, run both the monolith step and the extracted unit in parallel for 48 hours (monolith stays scheduled until Phase C deletion). Compare findings. Diff driven by structural differences (e.g., finding order) is acceptable; semantic drops are blockers.

### Risk 2: `daily-report-and-notify` fires before other daily reflections complete
**Impact:** Daily Telegram summary misses new findings; GitHub issue creation runs on stale data.
**Mitigation:** Inside the callable, verify that all other daily reflections have a `ran_at` within the last 2 hours. If not, skip this tick and wait for next. Adds 24-hour latency cap on notification — acceptable.

### Risk 3: Config relocation breaks fresh clones on new machines
**Impact:** `/update` on a new machine finds no `~/Desktop/Valor/reflections.yaml`; scheduler fails to start.
**Mitigation:** Keep `config/reflections.yaml` in-repo as the final fallback. `_resolve_config_path()` logs which path it loaded at startup. `/update` copies the vault yaml during provisioning.

### Risk 4: `ReflectionRun` deletion breaks external consumers
**Impact:** Dashboards, log scrapers, or tooling that reads `ReflectionRun` records from Redis dies.
**Mitigation:** Grep the repo for `ReflectionRun` imports before Phase C deletion. Confirmed: only the monolith and its tests write to it; `ui/data/reflections.py` already reads from `Reflection` (not `ReflectionRun`). Keep the backward-compat shim import stub for one release cycle (raise `DeprecationWarning` when the class is accessed), then delete.

### Risk 5: Launchd removal leaves orphan process on deployed machines
**Impact:** Old `com.valor.reflections.plist` continues firing on machines that haven't pulled the Phase C changes.
**Mitigation:** Phase C adds a `/update` step that runs `launchctl unload ~/Library/LaunchAgents/com.valor.reflections.plist && rm ~/Library/LaunchAgents/com.valor.reflections.plist`. Safe-noop if already absent.

## Race Conditions

### Race 1: Concurrent yaml reload during tick
**Location:** `agent/reflection_scheduler.py` — yaml reload path.
**Trigger:** `/update` rewrites `~/Desktop/Valor/reflections.yaml` mid-tick; scheduler reads a truncated file.
**Data prerequisite:** Yaml file must be fully written before scheduler reads it.
**State prerequisite:** Atomic replace (`write + rename`) not partial write.
**Mitigation:** `/update` writes via `tempfile.NamedTemporaryFile` + `os.replace()`. Scheduler yaml-parse failures are caught and the prior good config is reused for this tick.

### Race 2: Extracted `popoto-index-cleanup` runs while another reflection iterates records
**Location:** `reflections/popoto_index_cleanup.py` — rebuild_indexes call.
**Trigger:** `memory-quality-audit` iterates `Memory.query.all()` while `popoto-index-cleanup` rebuilds indexes.
**Data prerequisite:** Index rebuild is atomic per model.
**State prerequisite:** Reader tolerates transient empty results.
**Mitigation:** Popoto's `rebuild_indexes()` is already used safely alongside reads. Skip-if-running guard in scheduler prevents a reflection from running twice in parallel; cross-reflection contention is tolerated by Popoto's read path. No code change required.

## No-Gos (Out of Scope)

- Adding new non-memory reflections beyond the 18 monolith units and 4 memory reflections.
- Rewriting `reflection_ignore` or `pr_review_audit` models.
- Dependency-graph scheduling (`depends_on` in yaml).
- Changing the 60-second scheduler tick interval.
- Switching to an external scheduler (Prefect, Temporal, etc.).
- Unifying `memory-dedup` work — that's #795's plan. This plan either ships `memory-dedup` as a fallback or skips it if #795 landed first.
- Rewriting the dashboard. PR #790's 3-column layout absorbs more rows without changes.
- Building a reflections testing harness. Existing `tests/unit/test_reflections.py` is adequate after update.

## Update System

- **Phase C adds:** `/update` skill and `scripts/remote-update.sh` copy `~/Desktop/Valor/reflections.yaml` from iCloud to each machine during provisioning (same pattern as `projects.json`).
- **Phase C removes:** Uninstallation of `com.valor.reflections.plist` on each machine (`launchctl unload` + `rm`). Safe-noop if absent.
- **New dependency:** None. Everything runs in the existing worker process.
- **Migration for existing installations:** First `/update` after Phase C ships unloads the plist, deletes it, and verifies the yaml-scheduler picked up the new entries. Idempotent.

## Agent Integration

- **No new MCP tools required.** Every extracted unit calls existing Python (Redis, filesystem, `gh`, subprocess) — nothing the Telegram agent needs to invoke.
- **Bridge impact:** None. Bridge doesn't touch the monolith today (worker owns execution per PR #751).
- **Integration test:** Verify the worker starts cleanly with the new yaml and the scheduler logs all registered reflections on boot. Existing `tests/unit/test_reflection_scheduler.py` exercises registration; extend with an "all 30+ reflections registered" assertion.

## Documentation

### Feature Documentation
- [ ] Update `docs/features/reflections.md` — update the "Registered Reflections" table to include every extracted unit. Remove references to the monolith.
- [ ] Delete `docs/features/reflections.md` sections describing `ReflectionRun`, `scripts/reflections.py`, `com.valor.reflections` launchd.
- [ ] Update `docs/features/README.md` index entry (confirm still accurate).

### External Documentation Site
- [ ] Not applicable — repo has no external docs site.

### Inline Documentation
- [ ] Each extracted module gets a one-line docstring explaining what it audits/cleans.
- [ ] `agent/reflection_scheduler.py` — update module docstring to remove any reference to a "companion" monolith.

## Success Criteria

- [ ] All 18 monolith units live as standalone callables under `reflections/` and registered in the yaml
- [ ] `memory-decay-prune`, `memory-quality-audit`, `knowledge-reindex` implemented, registered, and green for 7 days
- [ ] `memory-dedup` either registered by this plan or confirmed to have shipped via #795
- [ ] `scripts/reflections.py` deleted
- [ ] `com.valor.reflections.plist` deleted from repo and unloaded on every deployed machine
- [ ] `scripts/install_reflections.sh` deleted
- [ ] `models/reflection_run.py` deleted and `models/reflections.py` shim reduced to `ReflectionIgnore`, `PRReviewAudit`
- [ ] `config/reflections.yaml` present as in-repo fallback; `~/Desktop/Valor/reflections.yaml` is the primary
- [ ] `_resolve_config_path()` in scheduler resolves env var → vault → in-repo fallback
- [ ] `/update` provisions the vault yaml on each machine
- [ ] Dashboard renders all registered reflections with status, last run, and run history
- [ ] Extracted units' findings match monolith output on a 7-day parallel-run window (semantic equivalence, not byte-identical)
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (phase A — extract units)**
  - Name: reflections-extractor
  - Role: Copy each `step_*` method from the monolith into a standalone module under `reflections/`, adapt findings accumulator, add yaml entry.
  - Agent Type: builder
  - Resume: true

- **Validator (phase A — parity check)**
  - Name: reflections-parity-validator
  - Role: For each extracted unit, verify behavior parity against the monolith step via targeted integration test.
  - Agent Type: validator
  - Resume: true

- **Builder (phase B — memory reflections)**
  - Name: memory-reflections-builder
  - Role: Implement `memory-decay-prune`, `memory-quality-audit`, `knowledge-reindex` as new standalone callables under `reflections/`. Register in yaml.
  - Agent Type: builder
  - Resume: true

- **Validator (phase B — memory reflections)**
  - Name: memory-reflections-validator
  - Role: Verify each memory reflection reads/writes the expected Redis records and logs findings correctly.
  - Agent Type: validator
  - Resume: true

- **Builder (phase C — config relocation + monolith delete)**
  - Name: cleanup-builder
  - Role: Add config-path resolver, update `/update` skill, delete monolith files, remove plist.
  - Agent Type: builder
  - Resume: true

- **Validator (phase C — full-system)**
  - Name: cleanup-validator
  - Role: Run full scheduler boot, confirm all reflections register, run dashboard check, confirm monolith files absent.
  - Agent Type: validator
  - Resume: true

- **Documentarian**
  - Name: reflections-documentarian
  - Role: Update `docs/features/reflections.md` and any referenced docs.
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
- Create `reflections/__init__.py` and `reflections/_helpers.py` with `load_local_projects`, `utc_now`, findings-to-run-history helper
- Ensure package is importable from worker and tests

### 2. Extract 15 independent monolith units (Phase A)
- **Task ID**: build-extract-independent-units
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_reflections_extracted/*.py (create one per unit), tests/integration/test_reflections_parity.py (create)
- **Assigned To**: reflections-extractor
- **Agent Type**: builder
- **Parallel**: true (units can be extracted concurrently by sub-agents)
- Extract: legacy_code_scan, log_review, task_management, documentation_audit, skills_audit, hooks_audit, redis_ttl_cleanup, redis_data_quality, branch_plan_cleanup, feature_docs_audit, principal_staleness, disk_space_check, pr_review_audit, analytics_rollup, create_github_issue (standalone sub-step)
- Each module exports `async def run() -> dict` returning `{findings, metrics}`
- Register each in `config/reflections.yaml` with chosen interval/priority
- Do NOT delete the corresponding monolith step yet — parallel run for 7 days

### 3. Extract 3 merged pipelines
- **Task ID**: build-extract-pipelines
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_reflections_pipelines.py (create)
- **Assigned To**: reflections-extractor
- **Agent Type**: builder
- **Parallel**: true
- Extract `session_intelligence` (session_analysis → llm_reflection → auto_fix_bugs), `behavioral_learning` (episode_cycle_close → pattern_crystallization, with cyclic_episode import-guard skip), `daily_report_and_notify` (produce_report → create_github_issue) into three modules
- Each pipeline module preserves the monolith's internal ordering
- `daily_report_and_notify` implements "wait until peers completed today" check (mitigates Risk 2)

### 4. Phase A parity validation
- **Task ID**: validate-phase-a
- **Depends On**: build-extract-independent-units, build-extract-pipelines
- **Assigned To**: reflections-parity-validator
- **Agent Type**: validator
- **Parallel**: false
- Run monolith and extracted units side-by-side for 7 days on `Dev: Valor` machine
- Compare findings counts and semantic content per unit
- Generate parity report; block phase C until green

### 5. Implement memory-decay-prune
- **Task ID**: build-memory-decay-prune
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_memory_decay_prune.py (create)
- **Assigned To**: memory-reflections-builder
- **Agent Type**: builder
- **Parallel**: true
- New `reflections/memory_decay_prune.py` iterates Memory records with importance below `WF_MIN_THRESHOLD` (0.15) and deletes via `Memory.delete()` (never raw redis; per `feedback_never_raw_delete_popoto.md`)
- Dry-run flag defaults true for first week
- Register in yaml: interval 86400, priority low

### 6. Implement memory-quality-audit
- **Task ID**: build-memory-quality-audit
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_memory_quality_audit.py (create)
- **Assigned To**: memory-reflections-builder
- **Agent Type**: builder
- **Parallel**: true
- New `reflections/memory_quality_audit.py` flags memories with zero `access_count` after 30 days and chronically dismissed memories
- Emits findings to `Reflection.run_history` (no side effects in v1)
- Register in yaml: interval 604800 (weekly), priority low

### 7. Implement knowledge-reindex
- **Task ID**: build-knowledge-reindex
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_knowledge_reindex.py (create)
- **Assigned To**: memory-reflections-builder
- **Agent Type**: builder
- **Parallel**: true
- New `reflections/knowledge_reindex.py` runs `tools/knowledge/indexer.py` over `~/src/work-vault/` on standalone worker machines
- Detect already-indexed state via KnowledgeDocument count + mtime comparison
- Register in yaml: interval 86400, priority low

### 8. Coordinate memory-dedup with #795
- **Task ID**: build-memory-dedup-coord
- **Depends On**: build-package-scaffold
- **Validates**: tests/unit/test_memory_dedup_registration.py (create)
- **Assigned To**: memory-reflections-builder
- **Agent Type**: builder
- **Parallel**: false
- Check if #795 has shipped; if yes, verify `memory-dedup` is in yaml and skip registration here
- If no, implement minimum-viable `memory-dedup` entry per `docs/plans/memory-consolidation-reflection.md`
- Document handoff in plan comments

### 9. Phase B validation
- **Task ID**: validate-phase-b
- **Depends On**: build-memory-decay-prune, build-memory-quality-audit, build-knowledge-reindex, build-memory-dedup-coord
- **Assigned To**: memory-reflections-validator
- **Agent Type**: validator
- **Parallel**: false
- Run each memory reflection in dry-run for 7 days; verify findings look sensible
- Confirm no data loss (Memory counts stable except for intentional decay pruning)

### 10. Implement config-path resolver
- **Task ID**: build-config-resolver
- **Depends On**: validate-phase-a, validate-phase-b
- **Validates**: tests/unit/test_reflection_scheduler.py (UPDATE)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Add `_resolve_config_path()` to `agent/reflection_scheduler.py`: env var `REFLECTIONS_YAML_PATH` → `~/Desktop/Valor/reflections.yaml` → `config/reflections.yaml`
- Log resolved path on scheduler startup
- Copy current `config/reflections.yaml` to `~/Desktop/Valor/reflections.yaml` on dev machine; verify resolver picks it up

### 11. Update /update skill + remote-update.sh
- **Task ID**: build-update-provisioning
- **Depends On**: build-config-resolver
- **Validates**: tests/integration/test_update_provisioning.py (create or update)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- `.claude/skills/update/SKILL.md` copies `~/Desktop/Valor/reflections.yaml` to the target machine
- `scripts/remote-update.sh` handles the copy in the iCloud-sync step
- `/update` also runs `launchctl unload ~/Library/LaunchAgents/com.valor.reflections.plist` and `rm -f` the plist (safe-noop)

### 12. Delete monolith and supporting files
- **Task ID**: build-delete-monolith
- **Depends On**: build-update-provisioning
- **Validates**: tests/unit/test_monolith_removed.py (create — asserts imports no longer resolve)
- **Assigned To**: cleanup-builder
- **Agent Type**: builder
- **Parallel**: false
- Delete `scripts/reflections.py`
- Delete `scripts/install_reflections.sh`
- Delete `com.valor.reflections.plist`
- Delete `models/reflection_run.py`
- Update `models/reflections.py` shim to re-export only `ReflectionIgnore`, `PRReviewAudit`
- Remove `tests/unit/test_reflection_run.py` if present
- Run full test suite; fix any import errors

### 13. Phase C validation
- **Task ID**: validate-phase-c
- **Depends On**: build-delete-monolith
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Confirm scheduler boots with vault yaml
- Confirm dashboard shows all registered reflections
- Confirm no references to `scripts.reflections`, `ReflectionRun`, or `com.valor.reflections` remain in codebase
- Run `/update` on a second machine; confirm monolith files removed and yaml present

### 14. Documentation update
- **Task ID**: document-reflections-unification
- **Depends On**: validate-phase-c
- **Assigned To**: reflections-documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Update `docs/features/reflections.md` — remove monolith references, update registry table to list every registered reflection
- Confirm `docs/features/README.md` index entry
- Add a one-paragraph "Migration notes" section explaining the 2026-Q2 unification

### 15. Final validation
- **Task ID**: validate-all
- **Depends On**: document-reflections-unification
- **Assigned To**: cleanup-validator
- **Agent Type**: validator
- **Parallel**: false
- Run all success criteria checks
- Confirm tests pass, ruff clean
- Confirm docs build (if applicable)
- Generate final report with links to phase A/B/C PRs

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| Monolith gone | `test -f scripts/reflections.py` | exit code 1 |
| Plist gone | `test -f com.valor.reflections.plist` | exit code 1 |
| ReflectionRun gone | `python -c "from models.reflections import ReflectionRun" 2>&1` | output contains "ImportError" |
| Scheduler boots | `python -c "from agent.reflection_scheduler import ReflectionScheduler; s = ReflectionScheduler(); print(len(s._registry))"` | output > 25 |
| Vault yaml resolves | `test -f ~/Desktop/Valor/reflections.yaml` | exit code 0 |
| Dashboard lists units | `curl -s localhost:8500/dashboard.json \| python -c "import sys, json; print(len(json.load(sys.stdin)['reflections']))"` | output > 25 |
| No stale grep hits | `grep -rn 'scripts.reflections\|ReflectionRun' --include='*.py' . \| grep -v 'test_monolith_removed\|.worktrees'` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique. Leave empty until critique runs. -->

---

## Open Questions

1. **Package location** — should extracted units live at `reflections/` (repo root), `scripts/reflections/`, or `agent/reflections/`? Repo root is cleanest but adds a new top-level; `agent/reflections/` keeps them near the scheduler but suggests worker-only use. Recommendation: `reflections/` at root, importable as `from reflections.legacy_code_scan import run`.
2. **`daily-report-and-notify` ordering** — option (1) later interval offset or option (2) skip-if-peers-not-done check inside the callable? Option (2) is more robust. Confirm preference before phase A.
3. **Parallel-run window length** — 7 days is conservative. Is 3 days sufficient for parity confidence, or should we go longer to catch weekly-only units like `principal-staleness`?
4. **`memory-dedup` coordination with #795** — should this plan ship `memory-dedup` if #795 hasn't merged within N weeks, or always defer to #795? Recommendation: defer. Delete task 8 if #795 is clearly ahead.
5. **Findings schema change** — the monolith writes freeform `add_finding(category, text)`; the new schema writes `run_history[-1].findings = [{category, text, metrics}]`. Should we migrate historical `ReflectionRun.findings` into the new schema for continuity, or start fresh? Recommendation: start fresh — archival value of the monolith's findings is low and migration cost is nontrivial.
6. **Phase split across PRs** — ship as three PRs (one per phase) or one big PR? Recommendation: three PRs, each independently shippable and revertable.
