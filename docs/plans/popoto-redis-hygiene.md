---
status: Planning
type: chore
appetite: Medium
owner: Valor
created: 2026-04-03
tracking: https://github.com/tomcounsell/ai/issues/617
last_comment_id: IC_kwDOEYGa0874CPZz
---

# Popoto ORM hygiene: refactor raw Redis ops + orphaned index cleanup reflection

## Problem

Every time the session watchdog or reflection scheduler queries AgentSession records, Popoto logs repeated errors about missing Redis keys behind index references. A manual audit found 106 orphaned index entries across AgentSession indexes. Meanwhile, `agent/teammate_metrics.py` uses raw Redis commands (`incr`, `zadd`, `get`) for persistent classification metrics instead of a Popoto model, and `agent/agent_session_queue.py:_diagnose_missing_session()` uses raw `r.keys()` / `r.ttl()` / `r.exists()` for diagnostic fallback.

**Current behavior:**
- Bridge logs are polluted with `"one or more redis keys points to missing objects"` warnings on every AgentSession query
- `teammate_metrics.py` creates its own `redis.Redis` connection and uses raw commands for 5 key patterns with no cleanup path
- `_diagnose_missing_session()` uses O(N) `r.keys("*session_id*")` pattern matching on the error path
- Bridge startup calls `AgentSession.query.keys(clean=True)` which uses the blocking KEYS command (not production-safe per Popoto maintainer)
- AgentSession records accumulate indefinitely with no TTL

**Desired outcome:**
- Zero orphaned Popoto index references in steady state
- All persistent Redis data managed through Popoto models (no raw `import redis` in `agent/`)
- Automated reflection that periodically detects and cleans orphaned indexes using `rebuild_indexes()` (SCAN-based, production-safe)
- AgentSession records auto-expire via `Meta.ttl` so old sessions do not accumulate forever

## Prior Art

- **#482 / PR #507**: Migrate raw Redis anti-patterns to Popoto models -- first migration wave that moved most raw Redis to Popoto but missed `teammate_metrics.py` and the diagnostic fallback in `agent_session_queue.py`
- **#592 / PR #607**: Audit AgentSession model: fix status KeyField duplicates -- identified index corruption but did not address orphaned index cleanup
- **#609 / PR #628**: AgentSession field cleanup: proper types, structured event log, remove dead fields -- continued model cleanup, no index hygiene
- **PR #166**: Redis migration wave 1 -- established the Popoto migration pattern used across the codebase
- **#565 / PR #591**: Removed deprecated issue poller (eliminated one raw Redis user)

## Why Previous Fixes Failed

| Prior Fix | What It Did | Why It Failed / Was Incomplete |
|-----------|-------------|-------------------------------|
| PR #507 | Migrated 4 files from raw Redis to Popoto | Missed `teammate_metrics.py` (added after the migration) and `_diagnose_missing_session()` (error-path code not surfaced in audit) |
| PR #607 | Fixed KeyField duplicates on AgentSession | Addressed the creation side (preventing new duplicates) but did not clean up existing orphaned index entries |

**Root cause pattern:** Each fix addressed the creation of new problems but never added a recurring cleanup mechanism for orphans that already exist or accumulate from crashes/TTL expiry.

## Data Flow

### Teammate metrics flow
1. **Entry point**: Message arrives via bridge, classified by `agent/intent_classifier.py`
2. **`agent/sdk_client.py`**: Calls `teammate_metrics.record_classification()` and `record_response_time()` after classification
3. **`agent/teammate_metrics.py`**: Creates raw `redis.Redis` connection, writes `incr`/`zadd` to `teammate_metrics:*` keys
4. **Output**: `get_stats()` reads counters for dashboard display

### Diagnostic fallback flow
1. **Entry point**: `_enqueue_nudge()` in `agent/agent_session_queue.py` cannot find session via Popoto query
2. **`_diagnose_missing_session()`**: Creates raw `redis.Redis()`, runs `r.keys("*session_id*")` across entire keyspace, then `r.ttl()` and `r.exists()` on each match
3. **Output**: Diagnostic dict logged at ERROR level

### Index orphan flow
1. **Entry point**: Session completes, crashes, or expires
2. **If Popoto `.delete()` was used**: Indexes cleaned up properly
3. **If hash expired via TTL or crash**: Index entries remain as orphans pointing to non-existent hashes
4. **On next query**: Popoto logs "one or more redis keys points to missing objects" warning

## Architectural Impact

- **New dependencies**: None -- Popoto is already a core dependency
- **Interface changes**: `teammate_metrics.py` public API (`record_classification`, `record_response_time`, `get_stats`) signatures remain identical; internal implementation changes from raw Redis to Popoto model
- **Coupling**: Decreases -- removes 2 independent `redis.Redis()` connection instantiations in `agent/` in favor of Popoto's managed connection pool
- **Data ownership**: Moves teammate metrics from ad-hoc Redis keys to a typed Popoto model; moves diagnostic info from raw Redis scanning to Popoto queries
- **Reversibility**: High -- the Popoto model is a thin wrapper; raw Redis keys can be cleaned up with a one-time migration script

## Appetite

**Size:** Medium

**Team:** Solo dev

**Interactions:**
- PM check-ins: 1 (scope alignment on TTL value for AgentSession)
- Review rounds: 1

## Prerequisites

No prerequisites -- this work has no external dependencies. All tools (Popoto, Redis) are already in the environment.

## Solution

### Key Elements

- **TeammateMetrics Popoto model**: Replaces raw Redis counters in `agent/teammate_metrics.py` with a Popoto model that stores classification counts and response time sorted sets
- **AgentSession Meta.ttl**: Adds `class Meta: ttl = 7776000` (90 days) to AgentSession so old sessions auto-expire at the Redis level
- **Diagnostic refactor**: Replaces raw `r.keys()` in `_diagnose_missing_session()` with Popoto-native queries and targeted hash existence checks
- **Bridge startup migration**: Replaces `keys(clean=True)` call in bridge startup with `rebuild_indexes()` (SCAN-based, production-safe)
- **Cleanup reflection**: New `popoto-index-cleanup` entry in `config/reflections.yaml` that runs `rebuild_indexes()` on all 14 Popoto models

### Flow

**Bridge startup** -> `rebuild_indexes()` (replaces `keys(clean=True)`) -> clean indexes -> recover sessions

**Reflection scheduler** -> `popoto-index-cleanup` fires daily -> iterates all models -> `rebuild_indexes()` each -> logs orphan counts

**Message arrives** -> classification -> `TeammateMetrics.record_classification()` (Popoto) -> counters stored as Popoto fields

### Technical Approach

- Per maintainer guidance: use `rebuild_indexes()` (SCAN-based) instead of `keys(clean=True)` (KEYS-based) everywhere
- Per maintainer guidance: use `Meta.ttl` on AgentSession for automatic expiration at the Redis level
- Build a dry-run wrapper function that scans indexes and counts orphans before cleanup runs, for logging purposes
- The cleanup reflection callable is a synchronous Python function registered in `reflections.yaml` with `execution_type: function`
- TeammateMetrics model uses a single-instance pattern (one record keyed by a fixed identifier) with counter fields, matching the current raw Redis pattern

## Failure Path Test Strategy

### Exception Handling Coverage
- [ ] `teammate_metrics.py` wraps all Popoto calls in try/except with `logger.debug` -- test that Redis connection failures do not crash message processing
- [ ] Cleanup reflection wraps each model's `rebuild_indexes()` call individually -- test that one model failure does not abort the entire sweep
- [ ] `_diagnose_missing_session()` refactored version returns diagnostic dict even when Popoto query fails

### Empty/Invalid Input Handling
- [ ] `record_classification()` with empty intent string does not crash
- [ ] `get_stats()` returns empty dict when no TeammateMetrics record exists yet
- [ ] Cleanup reflection handles models with zero records gracefully

### Error State Rendering
- [ ] Cleanup reflection logs per-model orphan counts even when some models fail
- [ ] Diagnostic fallback logs meaningful context when session is genuinely missing

## Test Impact

- [ ] `tests/unit/test_qa_metrics.py` -- REPLACE: All 10 tests mock raw Redis (`_get_redis` returns `MagicMock`). Must be rewritten to mock or use the new Popoto model instead. Same public API, different internal assertions.
- [ ] `tests/unit/test_pipeline_integrity.py` -- UPDATE: References `_diagnose_missing_session` in pipeline integrity checks. Update to reflect refactored diagnostic approach.
- [ ] `tests/integration/test_reflections_redis.py` -- UPDATE: Add test case for the new `popoto-index-cleanup` reflection entry in `reflections.yaml`.

## Rabbit Holes

- **Building a generic index health monitoring dashboard**: The dry-run orphan counter is for logging only. Do not build a UI widget or persistent metrics tracking for index health -- that is a separate initiative.
- **Migrating all 14 models to Meta.ttl**: Only AgentSession needs TTL right now. Other models have their own cleanup mechanisms (e.g., `cleanup_expired()` methods). Do not add Meta.ttl to models that already have working cleanup.
- **Upstream PR to Popoto for `check_indexes()`**: Tom may add this upstream. Do not block on it or attempt to contribute it -- the local dry-run wrapper is sufficient.
- **Migrating ephemeral queue operations**: `agent/steering.py`, `bridge/telegram_relay.py`, and `tools/send_telegram.py` use raw Redis lists for transient message queues intentionally. These are not Popoto model data and should not be migrated.

## Risks

### Risk 1: Meta.ttl causes premature session expiration
**Impact:** Active long-running sessions could expire if Meta.ttl is too short, causing data loss mid-session.
**Mitigation:** Set TTL to 90 days (7776000 seconds), matching the existing `cleanup_expired(max_age_days=90)` threshold. Sessions are updated frequently via `updated_at = DatetimeField(auto_now=True)`, and Popoto resets TTL on every `save()` call, so active sessions will never expire.

### Risk 2: rebuild_indexes() during bridge operation causes brief latency
**Impact:** SCAN-based rebuild iterates all keys for a model, which could add latency during peak message processing.
**Mitigation:** The reflection runs on a separate schedule (daily, low priority), not on the hot path. Bridge startup cleanup happens before session recovery begins, so no concurrent session processing occurs.

### Risk 3: TeammateMetrics model migration loses existing counter data
**Impact:** Historical classification counts would reset to zero.
**Mitigation:** Write a one-time migration in the build step that reads existing `teammate_metrics:*` raw Redis keys and seeds the Popoto model with current values before deleting the raw keys.

## Race Conditions

### Race 1: rebuild_indexes() concurrent with session create/delete
**Location:** Cleanup reflection callable + bridge session management
**Trigger:** Daily cleanup reflection fires while bridge is actively creating or deleting sessions
**Data prerequisite:** Index sets must be consistent with hash existence
**State prerequisite:** None -- rebuild_indexes() is designed to be run concurrently
**Mitigation:** `rebuild_indexes()` uses SCAN (cursor-based, non-blocking) and only adds/removes index entries to match actual hash existence. A concurrent create adds a hash and index entry; even if rebuild_indexes() scans before the index entry exists, the next run will fix it. A concurrent delete removes both hash and index entry; if rebuild_indexes() sees a stale index entry, it removes it. Both directions are safe and self-correcting.

## No-Gos (Out of Scope)

- Migrating ephemeral Redis queues (steering, relay, send_telegram) -- these are intentionally raw
- Adding Meta.ttl to models other than AgentSession -- each has its own cleanup mechanism
- Building an index health dashboard or monitoring UI
- Contributing `check_indexes()` upstream to Popoto
- Changing the AgentSession `cleanup_expired()` method -- Meta.ttl supplements it, does not replace it
- Touching migration scripts in `scripts/migrate_*.py` -- these are one-time operations that correctly use raw Redis

## Update System

No update system changes required -- this feature is purely internal to the Redis data layer. The cleanup reflection is registered in `config/reflections.yaml` which is already part of the standard deployment. No new dependencies, config files, or migration steps needed for existing installations.

## Agent Integration

No agent integration required -- this is a bridge-internal and reflection-system change. The cleanup reflection is a Python function registered in `config/reflections.yaml` and executed by the reflection scheduler, which already runs within the bridge process. No MCP server changes, no `.mcp.json` changes, no new tools exposed to the agent.

## Documentation

### Feature Documentation
- [ ] Create `docs/features/popoto-index-hygiene.md` describing the cleanup reflection, Meta.ttl configuration, and the dry-run orphan counter
- [ ] Add entry to `docs/features/README.md` index table

### Inline Documentation
- [ ] Docstrings on the cleanup reflection callable explaining the SCAN-based approach and concurrency safety
- [ ] Docstring on TeammateMetrics model explaining the single-instance pattern
- [ ] Update `models/__init__.py` module docstring to include TeammateMetrics

## Success Criteria

- [ ] `agent/teammate_metrics.py` uses a Popoto model instead of raw Redis commands
- [ ] `agent/agent_session_queue.py:_diagnose_missing_session()` no longer uses raw `r.keys()` / `r.ttl()` / `r.exists()`
- [ ] `bridge/telegram_bridge.py` startup uses `rebuild_indexes()` instead of `keys(clean=True)`
- [ ] AgentSession model has `class Meta: ttl = 7776000` (90 days)
- [ ] A `popoto-index-cleanup` reflection exists in `config/reflections.yaml`
- [ ] Running the cleanup reflection removes orphaned index entries and logs per-model counts
- [ ] `grep -rn "import redis" agent/` returns zero hits
- [ ] No `"one or more redis keys points to missing objects"` errors in bridge logs after cleanup runs
- [ ] Tests pass (`/do-test`)
- [ ] Documentation updated (`/do-docs`)

## Team Orchestration

### Team Members

- **Builder (metrics-model)**
  - Name: metrics-builder
  - Role: Create TeammateMetrics Popoto model and refactor teammate_metrics.py
  - Agent Type: builder
  - Resume: true

- **Builder (session-hygiene)**
  - Name: session-builder
  - Role: Add Meta.ttl to AgentSession, refactor _diagnose_missing_session, replace bridge startup keys(clean=True) with rebuild_indexes
  - Agent Type: builder
  - Resume: true

- **Builder (cleanup-reflection)**
  - Name: reflection-builder
  - Role: Create cleanup reflection callable and register in reflections.yaml
  - Agent Type: builder
  - Resume: true

- **Validator (final)**
  - Name: hygiene-validator
  - Role: Verify all raw Redis removed from agent/, cleanup reflection works, tests pass
  - Agent Type: validator
  - Resume: true

## Step by Step Tasks

### 1. Create TeammateMetrics Popoto model and refactor metrics module
- **Task ID**: build-metrics
- **Depends On**: none
- **Validates**: tests/unit/test_qa_metrics.py (rewrite), tests/unit/test_teammate_metrics_model.py (create)
- **Assigned To**: metrics-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `models/teammate_metrics.py` with a `TeammateMetrics` Popoto model using counter fields (IntField for counts, SortedField or ListField for response times)
- Use a single-instance pattern: one record keyed by a fixed identifier (e.g., `KeyField(default="global")`)
- Refactor `agent/teammate_metrics.py` to use the new model instead of raw Redis
- Preserve the existing public API: `record_classification()`, `record_response_time()`, `get_stats()`
- Add `TeammateMetrics` to `models/__init__.py`
- Rewrite `tests/unit/test_qa_metrics.py` to test via the Popoto model
- Write a one-time data migration snippet (inline in the module or as a script) that reads existing `teammate_metrics:*` raw keys and seeds the model

### 2. Add Meta.ttl to AgentSession, refactor diagnostics, fix bridge startup
- **Task ID**: build-session
- **Depends On**: none
- **Validates**: tests/unit/test_pipeline_integrity.py (update)
- **Assigned To**: session-builder
- **Agent Type**: builder
- **Parallel**: true
- Add `class Meta: ttl = 7776000` to the `AgentSession` model (90 days, matching existing cleanup threshold)
- Refactor `_diagnose_missing_session()` in `agent/agent_session_queue.py` to use Popoto queries: attempt `AgentSession.query.filter(session_id=session_id)`, then check hash existence via `POPOTO_REDIS_DB.exists(f"AgentSession:{session_id}")` if needed, instead of raw `r.keys("*session_id*")`
- Replace `AgentSession.query.keys(clean=True)` in `bridge/telegram_bridge.py` startup with `AgentSession.rebuild_indexes()`
- Update test assertions in `tests/unit/test_pipeline_integrity.py` as needed

### 3. Create cleanup reflection and register in reflections.yaml
- **Task ID**: build-reflection
- **Depends On**: none
- **Validates**: tests/unit/test_popoto_cleanup_reflection.py (create), tests/integration/test_reflections_redis.py (update)
- **Assigned To**: reflection-builder
- **Agent Type**: builder
- **Parallel**: true
- Create `scripts/popoto_index_cleanup.py` with a callable function that:
  - Iterates all Popoto models from `models/__init__.__all__`
  - For each model, builds a dry-run orphan count by scanning index sets and checking hash existence
  - Calls `Model.rebuild_indexes()` on each model
  - Logs per-model orphan counts found and cleaned
  - Returns a summary dict for the reflection scheduler
- Register `popoto-index-cleanup` in `config/reflections.yaml` with `interval: 86400` (daily), `priority: low`, `execution_type: function`, `callable: "scripts.popoto_index_cleanup.run_cleanup"`, `enabled: true`
- Create unit test verifying the cleanup function handles empty models, models with orphans, and models that raise errors gracefully
- Update `tests/integration/test_reflections_redis.py` to validate the new entry

### 4. Documentation
- **Task ID**: document-feature
- **Depends On**: build-metrics, build-session, build-reflection
- **Assigned To**: documentarian
- **Agent Type**: documentarian
- **Parallel**: false
- Create `docs/features/popoto-index-hygiene.md`
- Add entry to `docs/features/README.md` index table
- Update inline docstrings

### 5. Final Validation
- **Task ID**: validate-all
- **Depends On**: build-metrics, build-session, build-reflection, document-feature
- **Assigned To**: hygiene-validator
- **Agent Type**: validator
- **Parallel**: false
- Run `grep -rn "import redis" agent/` and verify zero hits
- Run `pytest tests/unit/test_qa_metrics.py tests/unit/test_pipeline_integrity.py -x`
- Run `python -m ruff check . && python -m ruff format --check .`
- Verify `config/reflections.yaml` contains `popoto-index-cleanup` entry
- Verify `AgentSession` model has `class Meta` with `ttl`
- Verify bridge startup uses `rebuild_indexes()` not `keys(clean=True)`

## Verification

| Check | Command | Expected |
|-------|---------|----------|
| Tests pass | `pytest tests/ -x -q` | exit code 0 |
| Lint clean | `python -m ruff check .` | exit code 0 |
| Format clean | `python -m ruff format --check .` | exit code 0 |
| No raw redis in agent/ | `grep -rn "import redis" agent/` | exit code 1 |
| Reflection registered | `grep "popoto-index-cleanup" config/reflections.yaml` | exit code 0 |
| Meta.ttl on AgentSession | `grep -A2 "class Meta" models/agent_session.py` | output contains ttl |
| Bridge uses rebuild_indexes | `grep "rebuild_indexes" bridge/telegram_bridge.py` | exit code 0 |
| No keys(clean=True) in bridge | `grep "keys(clean=True)" bridge/telegram_bridge.py` | exit code 1 |

## Critique Results

<!-- Populated by /do-plan-critique (war room). Leave empty until critique is run. -->

---

## Open Questions

1. **AgentSession TTL value**: 90 days matches the existing `cleanup_expired(max_age_days=90)` threshold. Should this be shorter (e.g., 30 days) given that most sessions complete within hours? Note: Popoto resets TTL on every `save()`, so only truly abandoned sessions would expire.
