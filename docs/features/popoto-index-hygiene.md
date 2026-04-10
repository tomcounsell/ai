# Popoto Index Hygiene

Automated cleanup of orphaned Popoto index entries and migration of raw Redis operations to Popoto models.

## Problem

When AgentSession records expire (via TTL), crash, or are deleted without proper cleanup, their index entries remain as orphans pointing to non-existent Redis hashes. These orphans cause repeated `"one or more redis keys points to missing objects"` warnings on every Popoto query. Additionally, two modules in `agent/` used raw `import redis` connections instead of Popoto models.

## Solution

### TeammateMetrics Popoto Model

`models/teammate_metrics.py` replaces raw Redis counters in `agent/teammate_metrics.py`. Uses a single-instance pattern: one record keyed by `"global"` stores all classification counters (IntField) and response time sorted sets (SortedField). The public API (`record_classification`, `record_response_time`, `get_stats`) is preserved unchanged.

### AgentSession Meta.ttl

`AgentSession` now has `class Meta: ttl = 7776000` (90 days), matching the existing `cleanup_expired(max_age_days=90)` threshold. Popoto resets TTL on every `save()` call, so active sessions never expire -- only truly abandoned sessions are cleaned up automatically at the Redis level.

### Diagnostic Refactor

`_diagnose_missing_session()` in `agent/agent_session_queue.py` no longer uses raw `r.keys()` / `r.ttl()` / `r.exists()`. Instead it uses:
- `POPOTO_REDIS_DB.exists()` for targeted hash existence checks
- `AgentSession.query.filter()` for Popoto-native lookups

### Worker Startup (All-Model Rebuild)

Worker startup calls `run_cleanup()` from `scripts/popoto_index_cleanup` to rebuild indexes for **all** Popoto models (not just AgentSession). This runs as Step 1 of the startup sequence, before corrupted session cleanup and recovery. The total time is logged for monitoring.

### Cleanup Reflection (Scheduler)

`scripts/popoto_index_cleanup.py` provides a `run_cleanup()` function registered as the `popoto-index-cleanup` reflection in `config/reflections.yaml`. The `ReflectionScheduler` (bridge-hosted) dispatches this daily when the bridge is running.

### Cleanup Reflection (Runner)

`ReflectionRunner` in `scripts/reflections.py` includes a `step_popoto_index_cleanup` step that calls `run_cleanup()` as a standalone safety net. This step runs after `redis_ttl_cleanup` (which deletes expired records) to clean up any orphaned index entries left behind. The runner executes via launchd regardless of bridge status.

### How `run_cleanup()` Works

1. Iterates all Popoto models from `models/__init__.__all__`
2. For each model, counts orphaned index entries (dry-run scan)
3. Calls `Model.rebuild_indexes()` to clean them up
4. Logs per-model orphan counts found and cleaned

Each model is processed independently -- one model failure does not abort the sweep. The SCAN-based `rebuild_indexes()` is safe to run concurrently with normal operations.

### Three Cleanup Paths

| Path | Trigger | Scope |
|------|---------|-------|
| Worker startup | `python -m worker` | All models via `run_cleanup()` |
| ReflectionScheduler | Bridge tick (daily) | All models via `run_cleanup()` |
| ReflectionRunner | `python scripts/reflections.py` (launchd) | All models via `step_popoto_index_cleanup` → `run_cleanup()` |

## Concurrency Safety

`rebuild_indexes()` uses Redis SCAN (cursor-based, non-blocking) and only adds/removes index entries to match actual hash existence. Concurrent creates and deletes are safe and self-correcting -- any inconsistency introduced by a concurrent operation is fixed on the next run.

## Key Files

| File | Purpose |
|------|---------|
| `models/teammate_metrics.py` | TeammateMetrics Popoto model |
| `agent/teammate_metrics.py` | Refactored metrics module (uses Popoto) |
| `models/agent_session.py` | AgentSession with Meta.ttl |
| `agent/agent_session_queue.py` | Refactored diagnostic fallback |
| `worker/__main__.py` | Worker startup using `run_cleanup()` for all-model rebuild (step 1) |
| `scripts/popoto_index_cleanup.py` | Cleanup function (`run_cleanup()`) and model discovery (`_get_all_models()`) |
| `scripts/reflections.py` | `ReflectionRunner.step_popoto_index_cleanup` standalone safety net |
| `config/reflections.yaml` | Reflection registry entry for `ReflectionScheduler` |

## Verification

After the cleanup reflection runs, `grep -rn "import redis" agent/` should return zero hits, and bridge logs should show no `"one or more redis keys points to missing objects"` warnings.
